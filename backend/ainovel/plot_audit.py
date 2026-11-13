"""Separate author-only interpretation from evidence-grounded prose comparison."""

from typing import Literal

from pydantic import Field

from .evidence import resolve_field, source_schema, source_units
from .models import Input as StrictModel

VERSION = "author-plot-audit/1"


def source_metadata(source_kind):
    if source_kind is None:
        # Existing reference tasks retain their frozen prompt/cache identity.
        return {}
    return {
        "情节核对契约版本": VERSION,
        "情节来源": source_kind,
        "来源边界": "只解释本次给定的单章情节／指令，不读取全书未来、旧稿意见或候选反推条件。保留原话中的只、仅、不、尚未等范围、否定、接收者与时点限定，不能规范化后丢掉限制。workflow_plan是本批次AI工作章纲，不伪称作者原话、已发生事实或新增作者锁定；其余来源是作者本次明确输入。仅核对该来源明确叙事事件，不把润色、评价等操作要求虚构成剧情；计划安排不是已经发生的事实，歧义保留待核对。",
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


async def independent_flows(worker, items, text, checks, *, source_label="作者原话"):
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
            if flow["evidence"] and flow["evidence"] not in text:
                issues.append("流向观察只能引用本次正文。")
            if flow["certainty"] == "explicit" and not flow["evidence"]:
                issues.append("明确流向必须有逐字正文依据。")
        return issues

    observed = await worker.grounded(
        {
            "动作": "只从本次正文独立提取物品与信息流向",
            "本次正文": text,
            "实体别名对照": entities,
            "待识别物品名称": objects,
            "可引用原文": units,
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
        elif not forward and row["result"] == "pass":
            row.update(result="unknown", reason="未获得独立且同向的正文流向证据，不能仅凭模型结论标通过。")
    return {"status": "observed", "entities": entities, "objects": objects, **observed}


async def interpret_plot(worker, author_text, *, source_kind=None):
    if not author_text.strip():
        return {"items": [], "note": "缺少作者情节，不能从候选反推。"}
    author_units = source_units(author_text, "AUTHOR")
    author_sources = {unit["source_id"]: unit for unit in author_units}
    author_contract = AuthorPlot.model_json_schema()
    author_contract["$defs"]["PlotItem"]["properties"]["author_evidence"]["enum"] = list(author_sources)

    def validate_author(value):
        issues = []
        for item in value["items"]:
            resolve_field(item, "author_evidence", author_sources)
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
        return issues

    source_label = "本章工作章纲（AI规划）" if source_kind == "workflow_plan" else "作者原话"
    # No candidate, reference excerpt, application plan or old review reaches this call.
    contract = await worker.grounded(
        {
            "动作": "仅依据作者原话整理本场景情节",
            source_label: author_text,
            "可引用原文": author_units,
            **source_metadata(source_kind),
            "要求": "只整理原话明确给出的事件、行动者、动作、对象、给出与取得方向和结果。每项引用AUTHOR编号；未给出的字段留空。给出/取得须填写supplier（给出方）、recipient（取得方）、transfer_object和transfer_scope：实际发起尝试为attempted，已交付为completed，仅决定/未来安排为planned，无交换传递为none并将direction留空。不能把愿望改成已发生结果；有多种解读标ambiguous并保留歧义。这里只是AI解释提案，不是作者新增锁定项或本书事实。",
        },
        AuthorPlot,
        {"scope": f"仅{source_label}，尚未读取候选"},
        validate_author,
        contract=author_contract,
    )
    return contract


async def audit_plot(worker, author_text, text, number, *, source_kind=None):
    source_label = "本章工作章纲（AI规划）" if source_kind == "workflow_plan" else "作者原话"
    contract = await interpret_plot(worker, author_text, source_kind=source_kind)
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
            if row["evidence"] and row["evidence"] not in text:
                issues.append("正文证据必须来自本次候选连续原文。")
            if row["result"] != "unknown" and not row["evidence"]:
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
            **source_metadata(source_kind),
            "要求": "先写本稿实际发生了什么，再逐项比较行动者、对象、流向及结果；不要用候选重新解释作者原话来制造一致。即使交易成败相同，对象或流向反转也应指出。歧义或证据不足标unknown，不能把AI解释升级成作者新条件；偏好不判fail。只能引用PLOT原文编号，不能拿AUTHOR证据作正文证据。",
        },
        PlotAudit,
        {"scope": "情节对照，未修改或采用正文"},
        validate_checks,
        contract=comparison_contract,
    )
    direction_guard = await independent_flows(
        worker, items, text, compared["checks"], source_label=source_label
    )
    if any(row.get("guard_override") for row in compared["checks"]):
        compared["original_model_summary"] = compared["summary"]
        compared["summary"] = (
            f"独立流向核对发现给出方与取得方反向，已列出{source_label}、解释及正文依据，请作者核对后决定修订。"
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
    return {
        "items": items,
        "direction_guard": direction_guard,
        **compared,
        "interpretation_note": contract["note"],
        "status": "failed"
        if any(finding["severity"] == "blocker" for finding in findings)
        else "unverified"
        if not items
        or any(item["certainty"] == "ambiguous" for item in items)
        or any(row["result"] == "unknown" for row in compared["checks"])
        else "model_pass",
        "findings": findings,
        "policy": f"解释来自{source_label}，仍须作者核对；不写入故事事实，不自动采用候选。",
    }
