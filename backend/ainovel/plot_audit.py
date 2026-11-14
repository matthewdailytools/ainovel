"""Separate author-only interpretation from evidence-grounded prose comparison."""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from .evidence import resolve_field, source_schema, source_units
from .models import Input as StrictModel
from .db import DomainError

LEGACY_VERSION = "author-plot-audit/1"
VERSION = "author-plot-audit/2"


def execution_version(worker):
    marker = ((getattr(worker, "task", None) or {}).get("result") or {}).get(
        "plot_contract_version", LEGACY_VERSION
    )
    if marker not in {LEGACY_VERSION, VERSION}:
        raise DomainError("plot_version", "情节核对版本无法确认，未升级或重发旧任务。", 409)
    return marker


def source_metadata(source_kind, version=LEGACY_VERSION):
    if source_kind is None:
        # Existing reference tasks retain their frozen prompt/cache identity.
        return {}
    return {
        "情节核对契约版本": version,
        "情节来源": source_kind,
        "来源边界": "只解释本次给定的单章情节／指令，不读取全书未来、旧稿意见或候选反推条件。保留原话中的只、仅、不、尚未等范围、否定、接收者与时点限定，不能规范化后丢掉限制。workflow_plan是本批次AI工作章纲，不伪称作者原话、已发生事实或新增作者锁定；其余来源是作者本次明确输入。仅核对该来源明确叙事事件，不把润色、评价等操作要求虚构成剧情；计划安排不是已经发生的事实，歧义保留待核对。",
        **(
            {
                "接收范围契约": "recipient_scope仅依来源明示限制填写only、明确不限制为unrestricted、未说明为unspecified，不能仅凭只出现一人就判only。scope_condition须保留该限制的原话连续条件或时点，未给条件则空；无法解释时标ambiguous，不丢只/仅或新增硬条件。已发生、尝试与未来计划用transfer_scope区分。"
            }
            if version == VERSION
            else {}
        ),
    }


class PlotItem(StrictModel):
    author_evidence: str = Field(max_length=800)
    actor: str = Field(max_length=200)
    action: str = Field(max_length=200)
    object: str = Field(max_length=200)
    direction: str = Field(max_length=400)
    result: str = Field(max_length=400)
    certainty: Literal["explicit", "ambiguous"]
    transfer_object: str = Field(default="", max_length=200)
    supplier: str = Field(default="", max_length=200)
    recipient: str = Field(default="", max_length=200)
    transfer_scope: Literal["attempted", "completed", "planned", "none"] = "none"


class AuthorPlot(StrictModel):
    items: list[PlotItem] = Field(max_length=12)
    note: str = Field(max_length=2000)


class ScopedPlotItem(PlotItem):
    recipient_scope: Literal["only", "unrestricted", "unspecified"]
    scope_condition: str = Field(max_length=800)


class ScopedAuthorPlot(StrictModel):
    items: list[ScopedPlotItem] = Field(max_length=12)
    note: str = Field(max_length=2000)


class PlotCheck(StrictModel):
    item_id: str = Field(min_length=1, max_length=20)
    result: Literal["pass", "fail", "unknown"]
    observed: str = Field(max_length=800)
    evidence: str = Field(max_length=800)
    reason: str = Field(max_length=1000)
    repair: str = Field(max_length=1000)


class PlotAudit(StrictModel):
    checks: list[PlotCheck] = Field(max_length=12)
    summary: str = Field(max_length=2000)


class ObservedFlow(StrictModel):
    object_id: str
    supplier_id: str
    recipient_id: str
    stage: Literal["offered", "requested", "completed", "planned", "refused", "unknown"]
    certainty: Literal["explicit", "ambiguous"]
    evidence: str = Field(max_length=800)
    note: str = Field(max_length=800)


class FlowObservations(StrictModel):
    flows: list[ObservedFlow] = Field(max_length=24)
    note: str = Field(max_length=2000)


async def independent_flows(
    worker, items, text, checks, *, source_label="作者原话", version=LEGACY_VERSION, source_text=None
):
    expected = [
        item
        for item in items
        if item["certainty"] == "explicit"
        and item["transfer_scope"] in {"attempted", "completed"}
        and item["transfer_object"]
        and item["supplier"]
        and item["recipient"]
        and item["supplier"] != item["recipient"]
    ]
    if not expected:
        return {"status": "not_applicable", "observations": []}
    entities = {
        f"E{i:02}": name
        for i, name in enumerate(
            sorted({item[key] for item in expected for key in ("supplier", "recipient")}), 1
        )
    }
    objects = {
        f"O{i:02}": name for i, name in enumerate(sorted({item["transfer_object"] for item in expected}), 1)
    }
    entity_ids = {name: identifier for identifier, name in entities.items()}
    object_ids = {name: identifier for identifier, name in objects.items()}
    from .plot_identity_context import enabled, source_identity_context

    identity_context = None
    if enabled(worker):
        if version != VERSION:
            raise DomainError(
                "source_identity_version", "角色限定核对需要本次v2情节来源，未升级旧报告。", 409
            )
        identity_context = await source_identity_context(worker, source_text or "", entities)
        entities = identity_context["entities"]
    units = source_units(text, "FLOW")
    sources = {unit["source_id"]: unit for unit in units}
    schema = source_schema(FlowObservations.model_json_schema(), units)
    properties = schema["$defs"]["ObservedFlow"]["properties"]
    properties["object_id"]["enum"] = [*objects, "other"]
    for key in ("supplier_id", "recipient_id"):
        properties[key]["enum"] = [*entities, "unknown"]

    def validate(value):
        issues = []
        for flow in value["flows"]:
            resolve_field(flow, "evidence", sources)
            if flow["object_id"] not in {*objects, "other"} or any(
                flow[key] not in {*entities, "unknown"} for key in ("supplier_id", "recipient_id")
            ):
                issues.append("仅使用提供的实体/物品编号，不明时标unknown或other。")
            if flow["evidence"] and (not flow["evidence"].strip() or flow["evidence"] not in text):
                issues.append("流向观察只能引用本次正文。")
            if flow["certainty"] == "explicit" and not flow["evidence"].strip():
                issues.append("明确流向必须有逐字正文依据。")
        return issues

    observed = await worker.grounded(
        {
            "动作": "只从本次正文独立提取物品与信息流向",
            "本次正文": text,
            "实体别名对照": entities,
            "待识别物品名称": objects,
            "可引用原文": units,
            **({"情节核对契约版本": VERSION} if version == VERSION else {}),
            **({"角色限定版本": identity_context["version"]} if identity_context else {}),
            "要求": "只提取正文实际给出、索取、交换、借还或传递的方向，不判断是否符合任何作者要求。supplier是给出方，recipient是取得方；交易失败也须区分原本提议的方向。offered/requested是当前提出的方向，completed是已交付，未来计划为planned，拒绝项为refused。只能据原文明确信息映射实体编号；不能映射则unknown，其他物品为other。引用FLOW编号。",
        },
        FlowObservations,
        {"scope": "仅候选与无方向的名称表；未提供作者预期、方案或旧结论"},
        validate,
        contract=schema,
    )
    rows = {row["item_id"]: row for row in checks}
    for item in expected:
        relevant = [
            flow
            for flow in observed["flows"]
            if flow["object_id"] == object_ids[item["transfer_object"]]
            and flow["certainty"] == "explicit"
            and flow["stage"] in {"offered", "requested", "completed"}
        ]
        forward = [
            flow
            for flow in relevant
            if flow["supplier_id"] == entity_ids[item["supplier"]]
            and flow["recipient_id"] == entity_ids[item["recipient"]]
        ]
        reverse = [
            flow
            for flow in relevant
            if flow["supplier_id"] == entity_ids[item["recipient"]]
            and flow["recipient_id"] == entity_ids[item["supplier"]]
        ]
        row = rows[item["id"]]
        original_check = deepcopy(row) if version == VERSION else None
        if identity_context and any(
            entity_ids[item[key]] in identity_context["unverified_ids"] for key in ("supplier", "recipient")
        ):
            if row["result"] == "pass":
                row.update(
                    result="unknown",
                    reason="情节原话中的角色身份限定尚未独立核实，不能凭同称谓或候选反推身份。",
                    repair="先核对原话角色限定，保留正文和原解释。",
                )
                row.setdefault("original_model_check", original_check)
            continue
        extra = [
            flow
            for flow in relevant
            if flow["supplier_id"] == entity_ids[item["supplier"]]
            and flow["recipient_id"] != entity_ids[item["recipient"]]
        ]
        if reverse and not forward:
            row.update(
                result="fail",
                observed=f"{item['recipient']}向{item['supplier']}给出/提议给出{item['transfer_object']}",
                evidence=reverse[0]["evidence"],
                reason=f"{source_label}解析为{item['supplier']}向{item['recipient']}给出{item['transfer_object']}；本稿观察为{item['recipient']}向{item['supplier']}给出同一物品，方向相反。",
                repair="按作者原话核对取得方与给出方，保留原稿，生成待审修订。",
                guard_override={
                    "code": "reverse_transfer",
                    "expected_supplier": item["supplier"],
                    "expected_recipient": item["recipient"],
                    "object": item["transfer_object"],
                },
            )
        elif (
            version == VERSION and item.get("recipient_scope") == "only" and extra and row["result"] == "pass"
        ):
            row.update(
                result="unknown",
                observed=("存在正向观察，" if forward else "未取得完整正向依据，")
                + "同一给出方对同一内容还有接收方向未核实在限定范围内，需核对身份、条件或时点。",
                evidence=extra[0]["evidence"],
                reason="独立观察发现同一给出方对同一内容还有非限定或未能映射的接收方向。已有正向例子不证明‘仅向’成立；身份、条件或时点仍须核对，不能猜测unknown是谁或直接判违背。",
                repair="保留原稿和原模型结论，先核对接收者及限制适用的条件／时点，不自动改稿。",
                guard_override={
                    "code": "restricted_recipient_unverified",
                    "expected_supplier": item["supplier"],
                    "expected_recipient": item["recipient"],
                    "object": item["transfer_object"],
                    "recipient_scope": "only",
                    "scope_condition": item.get("scope_condition", ""),
                    "observations": deepcopy(extra),
                    "policy": "补充观察仅阻止宣称一致，不认定unknown身份或新增作者条件。",
                },
            )
        elif not forward and row["result"] == "pass":
            row.update(result="unknown", reason="未获得独立且同向的正文流向证据，不能仅凭模型结论标通过。")
        if version == VERSION and row != original_check:
            row.setdefault("original_model_check", original_check)
    return {
        "status": "observed",
        "entities": entities,
        "objects": objects,
        **observed,
        **({"source_identity_context": identity_context} if identity_context else {}),
    }


def author_contract_issues(value, author_text, *, scoped):
    """Validate resolved author-side claims without reading candidate prose."""
    issues = []
    for item in value["items"]:
        if not item["author_evidence"] or item["author_evidence"] not in author_text:
            issues.append("每项解释必须引用作者原话，不能用候选正文或参考材料佐证。")
        if not any(item[field].strip() for field in ("action", "object", "direction", "result")):
            issues.append("解释须包含原话支持的事件内容，不能仅凭空字段宣称覆盖情节。")
        if (
            item["certainty"] == "explicit"
            and item["transfer_scope"] in {"attempted", "completed"}
            and (
                not all(item[field].strip() for field in ("transfer_object", "supplier", "recipient"))
                or item["supplier"] == item["recipient"]
            )
        ):
            issues.append("明确交换须给出不同的双方和物品；不能确定时标歧义，不编造流向。")
        if scoped and item["scope_condition"] and item["scope_condition"] not in author_text:
            issues.append("scope_condition只能为来源的连续原话条件／时点，不得补造限制。")
        if (
            scoped
            and item["recipient_scope"] == "only"
            and item["certainty"] == "explicit"
            and (
                not all(item[key].strip() for key in ("transfer_object", "supplier", "recipient"))
                or item["supplier"] == item["recipient"]
                or item["transfer_scope"] == "none"
            )
        ):
            issues.append(
                "明确的仅向接收范围须有原话支持的不同双方、内容与实际／尝试／计划阶段；不能确认则保留歧义。"
            )
    return issues


async def interpret_plot(worker, author_text, *, source_kind=None, preparation=None):
    if not author_text.strip():
        return {"items": [], "note": "缺少作者情节，不能从候选反推。"}
    author_units = source_units(author_text, "AUTHOR")
    author_sources = {unit["source_id"]: unit for unit in author_units}
    version = execution_version(worker) if source_kind is not None else LEGACY_VERSION
    scoped = version == VERSION
    schema = ScopedAuthorPlot if scoped else AuthorPlot
    author_contract = schema.model_json_schema()
    author_contract["$defs"]["ScopedPlotItem" if scoped else "PlotItem"]["properties"]["author_evidence"][
        "enum"
    ] = list(author_sources)

    def validate_author(value):
        for item in value["items"]:
            resolve_field(item, "author_evidence", author_sources)
        issues = []
        if preparation is not None and preparation.get("item_count") is not None:
            if len(value["items"]) != preparation["item_count"]:
                issues.append("写前复解释必须按原P编号顺序逐项保留全部事项，不得删除、合并或遗漏歧义项。")
        issues.extend(author_contract_issues(value, author_text, scoped=scoped))
        return issues

    source_label = "本章工作章纲（AI规划）" if source_kind == "workflow_plan" else "作者原话"
    # No candidate, reference excerpt, application plan or old review reaches this call.
    contract = await worker.grounded(
        {
            "动作": "写前解释AI工作章纲" if preparation is not None else "仅依据作者原话整理本场景情节",
            source_label: author_text,
            "可引用原文": author_units,
            **source_metadata(source_kind, version),
            **(
                {
                    "写前准备契约版本": preparation["version"],
                    "写前准备契约": preparation,
                    "执行明确性": "certainty表示执行安排是否明确，而非剧情谜底是否已知。明确保留多种可能、尚未认定、继续调查，属于explicit的保留未知安排，绝不能为消除歧义提前解谜。真正未确定的行动细节保留ambiguous，不自行选定。若提供原始事项，按原P编号顺序一一解释相同事项，不删项、不合并；附注只能明确开放细节，不能改变原事件、阶段、否定、只/仅范围或作者条件。",
                }
                if preparation is not None
                else {}
            ),
            "要求": "只整理原话明确给出的事件、行动者、动作、对象、给出与取得方向和结果。每项引用AUTHOR编号；未给出的字段留空。给出/取得须填写supplier（给出方）、recipient（取得方）、transfer_object和transfer_scope：实际发起尝试为attempted，已交付为completed，仅决定/未来安排为planned，无交换传递为none并将direction留空。不能把愿望改成已发生结果；有多种解读标ambiguous并保留歧义。这里只是AI解释提案，不是作者新增锁定项或本书事实。",
        },
        schema,
        {"scope": f"仅{source_label}，尚未读取候选"},
        validate_author,
        contract=author_contract,
    )
    return contract


async def audit_plot(worker, author_text, text, number, *, source_kind=None, prepared_contract=None):
    version = execution_version(worker) if source_kind is not None else LEGACY_VERSION
    source_label = "本章工作章纲（AI规划）" if source_kind == "workflow_plan" else "作者原话"
    if prepared_contract is None:
        contract = await interpret_plot(worker, author_text, source_kind=source_kind)
    else:
        from .workflow_plan_preparation import frozen_contract

        if source_kind == "author_plot" and worker.task["kind"] == "candidate_recheck":
            from .candidate_recheck import verified_author_contract

            contract = verified_author_contract(worker, author_text, number, prepared_contract)
        elif source_kind == "workflow_plan":
            contract = frozen_contract(worker, author_text, number, prepared_contract)
        else:
            raise DomainError("plan_preparation_version", "冻结工作章纲解释不能替代作者情节解释。", 409)
    items = [{"id": f"P{index:02}", **item} for index, item in enumerate(contract["items"], 1)]
    if not items:
        return {
            "items": [],
            "checks": [],
            "findings": [],
            "status": "unverified",
            "summary": "未整理出可逐项核对的情节，不能视为通过。",
            "interpretation_note": contract["note"],
            "policy": "AI解释未形成作者新条件，正文未改变。",
            **({"version": version} if source_kind is not None else {}),
        }
    units = source_units(text, f"PLOT{number:03}P")
    sources = {unit["source_id"]: unit for unit in units}
    comparison_contract = source_schema(PlotAudit.model_json_schema(), units)
    comparison_contract["properties"]["checks"].update(minItems=len(items), maxItems=len(items))
    comparison_contract["$defs"]["PlotCheck"]["properties"]["item_id"]["enum"] = [
        item["id"] for item in items
    ]

    def validate_checks(value):
        issues = []
        if sorted(row["item_id"] for row in value["checks"]) != sorted(item["id"] for item in items):
            issues.append("每项情节解释必须恰好核对一次，不得漏项或重复。")
        for row in value["checks"]:
            resolve_field(row, "evidence", sources)
            if row["evidence"] and (not row["evidence"].strip() or row["evidence"] not in text):
                issues.append("正文证据必须来自本次候选连续原文。")
            if row["result"] != "unknown" and not row["evidence"].strip():
                issues.append("通过或不符均须有正文依据，不能定位时标unknown。")
            if not row["reason"].strip() or (row["result"] != "unknown" and not row["observed"].strip()):
                issues.append("须说明理由；通过或不符还须写出本稿实际发生了什么。")
        return issues

    compared = await worker.grounded(
        {
            "动作": "逐项对照作者情节解释与本次正文",
            source_label: author_text,
            "独立整理的解释提案": items,
            "本次候选": text,
            "可引用原文": units,
            **source_metadata(source_kind, version),
            "要求": "先写本稿实际发生了什么，再逐项比较行动者、对象、流向及结果；不要用候选重新解释作者原话来制造一致。即使交易成败相同，对象或流向反转也应指出。歧义或证据不足标unknown，不能把AI解释升级成作者新条件；偏好不判fail。只能引用PLOT原文编号，不能拿AUTHOR证据作正文证据。",
        },
        PlotAudit,
        {"scope": "情节对照，未修改或采用正文"},
        validate_checks,
        contract=comparison_contract,
    )
    original_checks = (
        {row["item_id"]: deepcopy(row) for row in compared["checks"]} if version == VERSION else {}
    )
    direction_guard = await independent_flows(
        worker,
        items,
        text,
        compared["checks"],
        source_label=source_label,
        version=version,
        source_text=author_text,
    )
    if version == VERSION:
        for row in compared["checks"]:
            original = original_checks[row["item_id"]]
            if row != original:
                row.setdefault("original_model_check", original)
        compared.setdefault("original_model_summary", compared["summary"])
    elif any(row.get("guard_override") for row in compared["checks"]):
        compared["original_model_summary"] = compared["summary"]
        compared["summary"] = (
            "独立接收范围核对仍有待确认项，原模型结论、补充观察与逐字依据保留，不能宣称一致。"
            if any(
                row.get("guard_override", {}).get("code") == "restricted_recipient_unverified"
                for row in compared["checks"]
            )
            else f"独立流向核对发现给出方与取得方反向，已列出{source_label}、解释及正文依据，请作者核对后决定修订。"
        )
    indexed = {item["id"]: item for item in items}
    findings = []
    for row in compared["checks"]:
        item = indexed[row["item_id"]]
        if row["result"] == "fail" and row["evidence"]:
            findings.append(
                {
                    "severity": "blocker" if item["certainty"] == "explicit" else "suggestion",
                    "chapter": number,
                    "quote": row["evidence"],
                    "reason": f"{source_label}：{item['author_evidence']}；AI解释（非新增条件）：{item['actor']}／{item['action']}／{item['object']}／{item['direction']}／{item['result']}。本稿观察：{row['observed']}。{row['reason']}",
                    "repair": row["repair"],
                }
            )
    status = (
        "failed"
        if any(finding["severity"] == "blocker" for finding in findings)
        else "unverified"
        if not items
        or any(item["certainty"] == "ambiguous" for item in items)
        or any(row["result"] == "unknown" for row in compared["checks"])
        else "model_pass"
    )
    if version == VERSION:
        counts = {
            result: sum(row["result"] == result for row in compared["checks"])
            for result in ("pass", "fail", "unknown")
        }
        ambiguous = sum(item["certainty"] == "ambiguous" for item in items)
        conclusion = {
            "failed": "存在明确不符，须核对后决定修订。",
            "unverified": "仍待核对，不能视为情节一致。",
            "model_pass": "模型核对通过，仍须作者核对。",
        }[status]
        compared["summary"] = (
            f"最终情节核对：通过{counts['pass']}项、不符{counts['fail']}项、未知{counts['unknown']}项；"
            f"解释歧义{ambiguous}项。{conclusion}原模型摘要另行保留。"
        )
    return {
        "items": items,
        "direction_guard": direction_guard,
        **compared,
        "interpretation_note": contract["note"],
        "status": status,
        "findings": findings,
        "policy": f"解释来自{source_label}，仍须作者核对；不写入故事事实，不自动采用候选。",
        **({"version": version} if source_kind is not None else {}),
    }
