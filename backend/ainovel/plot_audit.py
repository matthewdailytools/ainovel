"""Separate author-only interpretation from evidence-grounded prose comparison."""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from .evidence import resolve_field, source_schema, source_units
from .models import Input as StrictModel
from .db import DomainError

LEGACY_VERSION = "author-plot-audit/1"
VERSION = "author-plot-audit/2"
COMPARISON_GUIDANCE_VERSION = "stage-aware-comparison/1"
OBSERVATION_GUIDANCE_VERSION = "source-role-observation/1"


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


class OpenPlanItem(ScopedPlotItem):
    open_fields: list[Literal["supplier", "recipient", "transfer_object"]] = Field(
        default_factory=list, max_length=3
    )
    open_reason: str = Field(default="", max_length=1200)


class OpenPlanInterpretation(StrictModel):
    items: list[OpenPlanItem] = Field(max_length=12)
    note: str = Field(max_length=2000)


class ScopeBoundAuthorItem(ScopedPlotItem):
    scope_observation_ids: list[str] = Field(max_length=24)
    scope_binding_status: Literal["supported", "unknown"]
    scope_binding_reason: str = Field(min_length=1, max_length=800)


class ScopeBoundAuthorPlot(StrictModel):
    items: list[ScopeBoundAuthorItem] = Field(max_length=12)
    note: str = Field(max_length=2000)


class ScopeBoundOpenPlanItem(OpenPlanItem):
    scope_observation_ids: list[str] = Field(max_length=24)


class ScopeBoundOpenPlanItemV2(ScopeBoundOpenPlanItem):
    scope_binding_status: Literal["supported", "unknown"]
    scope_binding_reason: str = Field(min_length=1, max_length=800)


class ScopeBoundOpenPlanInterpretationV2(StrictModel):
    items: list[ScopeBoundOpenPlanItemV2] = Field(max_length=12)
    note: str = Field(max_length=2000)


class ScopeBoundOpenPlanInterpretation(StrictModel):
    items: list[ScopeBoundOpenPlanItem] = Field(max_length=12)
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
    from .flow_entailment import enabled as entailment_enabled

    verify_sources = entailment_enabled(worker, version)
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
    if verify_sources:
        from .flow_entailment import VERSION as ENTAILMENT_VERSION, execution_version as flow_version

        if flow_version(worker) == ENTAILMENT_VERSION:
            # Source roles need not assert a handover to be available as names.
            # The body observer still verifies aliases; a role is not a fact.
            names = {item[key] for item in expected for key in ("supplier", "recipient")}
            extra_names = sorted({item.get("actor", "") for item in items} - names - {""})
            names.update(extra_names[: max(0, 24 - len(names))])
            entities = {f"E{i:02}": name for i, name in enumerate(sorted(names), 1)}
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

    observation_instruction = {
        "动作": "只从本次正文独立提取物品与信息流向",
        "本次正文": text,
        "实体别名对照": entities,
        "待识别物品名称": objects,
        "可引用原文": units,
        **({"情节核对契约版本": VERSION} if version == VERSION else {}),
        **({"角色限定版本": identity_context["version"]} if identity_context else {}),
        **(
            {
                "角色流向观察版本": OBSERVATION_GUIDANCE_VERSION,
                "独立角色映射": (
                    "名称表中的原话称谓可以与正文中的姓名、尊称或简称不同。只依据本次正文核对其身份范围，"
                    "例如本次已核实限定是‘某地的一位工匠’，正文明确此人在该地点从事对应手艺，且当前来源范围内"
                    "能够区分该角色时，应引用对应正文将其映射到该称谓编号；不能仅因姓名不同一律填unknown。"
                    "‘一位／某位’不要求证明该地点只有一人，也不要求新增姓名。确有多位符合者、身份范围"
                    "不符或依据不足才填unknown，并说明无法区分之处。不能因物品交给谁或看似符合预期就猜身份；"
                    "此调用未提供预期方向，不得补造。不得把正文姓名或动作回填为原话身份限定，"
                    "也不得把未核实限定称为已核实。supplier始终是给出方、recipient始终是取得方，索取者是recipient。"
                ),
            }
            if version == VERSION
            else {}
        ),
        "要求": "只提取正文实际给出、索取、交换、借还或传递的方向，不判断是否符合任何作者要求。supplier是给出方，recipient是取得方；交易失败也须区分原本提议的方向。offered/requested是当前提出的方向，completed是已交付，未来计划为planned，拒绝项为refused。只能据原文明确信息映射实体编号；不能映射则unknown，其他物品为other。引用FLOW编号。",
    }
    if verify_sources:
        from .flow_entailment import OBJECT_GUIDANCE, execution_version as flow_version, observation_version

        observation_instruction["角色流向观察版本"] = observation_version(worker)
        observation_instruction["流向来源蕴含版本"] = flow_version(worker)
        observation_instruction["独立角色映射"] = observation_instruction["独立角色映射"].replace(
            "supplier始终是给出方、recipient始终是取得方，索取者是recipient。",
            "supplier与recipient按待识别内容本身的给出及接收方向识别，不能无条件把发问人当取得方。",
        )
        observation_instruction["要求"] += "内容所指口径优先于笼统索取者口径。" + OBJECT_GUIDANCE
    observed = await worker.grounded(
        observation_instruction,
        FlowObservations,
        {"scope": "仅候选与无方向的名称表；未提供作者预期、方案或旧结论"},
        validate,
        contract=schema,
    )
    source_review = None
    usable_flows = observed["flows"]
    if verify_sources:
        from .flow_entailment import review_flows, unresolved_for_item

        source_review = await review_flows(worker, text, entities, objects, observed["flows"])
        usable_flows = source_review.get("supported_observations", [])
    rows = {row["item_id"]: row for row in checks}
    for item in expected:
        relevant = [
            flow
            for flow in usable_flows
            if flow["object_id"] == object_ids[item["transfer_object"]]
            and flow["certainty"] == "explicit"
            and flow["stage"] in {"offered", "requested", "completed"}
        ]
        stages = (
            {"completed"}
            if version == VERSION and item["transfer_scope"] == "completed"
            else {"offered", "requested", "completed"}
        )
        forward = [
            flow
            for flow in relevant
            if flow["supplier_id"] == entity_ids[item["supplier"]]
            and flow["recipient_id"] == entity_ids[item["recipient"]]
            and flow["stage"] in stages
        ]
        reverse = [
            flow
            for flow in relevant
            if flow["supplier_id"] == entity_ids[item["recipient"]]
            and flow["recipient_id"] == entity_ids[item["supplier"]]
            and flow["stage"] in stages
        ]
        if source_review is not None:
            from .flow_association import anchors, different_physical_instance

            references = anchors(source_review, item, object_ids[item["transfer_object"]], entity_ids)
            reverse = [
                flow
                for flow in reverse
                if not different_physical_instance(
                    source_review,
                    flow,
                    references,
                )
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
        elif (
            source_review is not None
            and row["result"] == "pass"
            and (
                unresolved := unresolved_for_item(
                    source_review, item, object_ids[item["transfer_object"]], entity_ids
                )
            )
        ):
            row.update(
                result="unknown",
                reason="相关流向观察的对象、双方、阶段或原文蕴含仍无法确认；已有正向例子不能掩盖未核实观察。",
                repair="保留原稿、独立观察和来源复核，不自动改写正文；先核对未确认的原文支持。",
                guard_override={
                    "code": "flow_source_unverified",
                    "expected_supplier": item["supplier"],
                    "expected_recipient": item["recipient"],
                    "object": item["transfer_object"],
                    "unresolved": unresolved,
                },
            )
        elif not forward and row["result"] == "pass":
            row.update(
                result="unknown",
                reason=(
                    "未获得独立且同向的已完成交付证据；提出、索取或反向提议不证明原约定已完成，不能仅凭模型结论标通过。"
                    if version == VERSION and item["transfer_scope"] == "completed"
                    else "未获得独立且同向的正文流向证据，不能仅凭模型结论标通过。"
                ),
            )
        if version == VERSION and row != original_check:
            row.setdefault("original_model_check", original_check)
    return {
        "status": "observed",
        "entities": entities,
        "objects": objects,
        **observed,
        **(
            {
                "role_observation_version": observation_version(worker)
                if verify_sources
                else OBSERVATION_GUIDANCE_VERSION
            }
            if version == VERSION
            else {}
        ),
        **({"source_entailment": source_review} if source_review is not None else {}),
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


def normalize_open_plan(value):
    """Keep the unresolved slot visible and non-executable, never fill it."""
    result, slots = deepcopy(value), []
    for index, item in enumerate(result["items"], 1):
        fields = item.pop("open_fields")
        reason = item.pop("open_reason")
        if fields:
            slots.append(
                {
                    "item_id": f"P{index:02}",
                    "fields": fields,
                    "reason": reason,
                    "author_evidence": item["author_evidence"],
                    "source_certainty": item["certainty"],
                }
            )
            item["certainty"] = "ambiguous"
    return {"contract": ScopedAuthorPlot.model_validate(result).model_dump(), "open_details": slots}


def open_plan_issues(value, source):
    problems = []
    for item in value["items"]:
        fields = item["open_fields"]
        if len(fields) != len(set(fields)):
            problems.append("同一事项的开放槽不得重复。")
        if fields and not item["open_reason"].strip():
            problems.append("开放槽须说明原章纲未确定哪项执行细节，并保留连续原话证据。")
        if fields and item["transfer_scope"] == "none":
            problems.append(
                "无交换传递的事项不能声明传递双方或物品开放槽；剧情未知按原含义保留，不制造可选细节。"
            )
        if any(item[field].strip() for field in fields):
            problems.append("开放槽只能指向尚未填写的字段；原话已指明的角色／关系不能换成开放对象。")
    # Only the preparation artifact is ambiguous; all ordinary source and
    # scope checks still run. The final ready contract uses the strict gate.
    problems.extend(author_contract_issues(normalize_open_plan(value)["contract"], source, scoped=True))
    return problems


async def interpret_plot(
    worker, author_text, *, source_kind=None, preparation=None, source_position=None, authored_inventory=None
):
    from . import authored_source_scope as authored
    from .authored_source_classification import GUIDANCE as CLASSIFICATION_GUIDANCE

    authored_enabled = (
        preparation is None
        and source_kind in {"author_plot", "author_instruction"}
        and authored.validate_task(worker.task, getattr(worker, "domain", None))
    )
    if authored_enabled and authored_inventory is None:
        return await authored.prepare(worker, author_text, source_kind, source_position)
    if authored_inventory is not None and not authored_enabled:
        raise DomainError("authored_scope_version", "旧任务不能临时加入作者来源范围观察。", 409)
    if not author_text.strip():
        return {"items": [], "note": "缺少作者情节，不能从候选反推。"}
    author_units = source_units(author_text, "AUTHOR")
    author_sources = {unit["source_id"]: unit for unit in author_units}
    version = execution_version(worker) if source_kind is not None else LEGACY_VERSION
    scoped = version == VERSION
    schema = ScopedAuthorPlot if scoped else AuthorPlot
    from .workflow_plan_open_details import (
        GUIDANCE as OPEN_GUIDANCE,
        KEY as OPEN_KEY,
        VERSION as OPEN_VERSION,
        enabled as open_enabled,
    )

    open_details = preparation is not None and OPEN_KEY in preparation
    if open_details:
        if (
            source_kind != "workflow_plan"
            or preparation[OPEN_KEY] != OPEN_VERSION
            or not open_enabled(worker)
        ):
            raise DomainError("plan_open_details_version", "开放槽仅用于已冻结政策的全自动写前解释。", 409)
        schema = OpenPlanInterpretation
    from .workflow_plan_source_roles import (
        KEY as ROLE_KEY,
        VERSION as ROLE_VERSION,
        GUIDANCE as ROLE_GUIDANCE,
        RepairState,
        validate_task as role_task,
    )

    roles = role_task(worker.task, getattr(worker, "domain", None)) if preparation is not None else False
    if preparation is not None and (
        (ROLE_KEY in preparation) != roles or (roles and preparation[ROLE_KEY] != ROLE_VERSION)
    ):
        raise DomainError("plan_source_roles_version", "写前来源角色解释输入与冻结政策不一致。", 409)
    if roles and not open_details:
        raise DomainError("plan_source_roles_version", "来源角色解释仅适用新版开放细节写前准备。", 409)
    from . import workflow_plan_recipient_scope as recipient_scope

    scope_execution = preparation is not None and recipient_scope.KEY in preparation
    inventory = preparation.get("recipient_scope_inventory") if scope_execution else None
    if scope_execution:
        if (
            preparation[recipient_scope.KEY] not in recipient_scope.SUPPORTED
            or not isinstance(inventory, dict)
            or inventory.get("version") != preparation[recipient_scope.KEY]
            or not roles
            or not recipient_scope.inventory_valid(inventory, author_text)
            or inventory["status"] != "supported"
        ):
            raise DomainError("plan_scope_version", "写前范围来源观察缺失、未知或版本不符，未派发解释。", 409)
        schema = (
            ScopeBoundOpenPlanInterpretationV2
            if preparation[recipient_scope.KEY]
            in {recipient_scope.CURRENT_VERSION, recipient_scope.NEW_VERSION}
            else ScopeBoundOpenPlanInterpretation
        )
    authored_execution = authored_inventory is not None
    if authored_execution:
        entry = authored.select(worker.task, author_text, source_kind, source_position)
        record = worker.task["result"].get(authored.RECEIPTS, {}).get(entry["key"], {})
        if (
            not authored.inventory_valid(authored_inventory, entry)
            or authored_inventory["status"] != "supported"
            or record.get("phases", {}).get("observe", {}).get("response") != authored_inventory
        ):
            raise DomainError("authored_scope_changed", "先行作者来源观察不是本任务已冻结回执。", 409)
        schema = ScopeBoundAuthorPlot
    repair_state = RepairState(author_text, open_plan_issues) if roles else None
    author_contract = schema.model_json_schema()
    item_type = (
        "ScopeBoundAuthorItem"
        if authored_execution
        else "ScopeBoundOpenPlanItemV2"
        if scope_execution
        and preparation[recipient_scope.KEY] in {recipient_scope.CURRENT_VERSION, recipient_scope.NEW_VERSION}
        else "ScopeBoundOpenPlanItem"
        if scope_execution
        else "OpenPlanItem"
        if open_details
        else "ScopedPlotItem"
        if scoped
        else "PlotItem"
    )
    author_contract["$defs"][item_type]["properties"]["author_evidence"]["enum"] = list(author_sources)

    if roles:
        fields = author_contract["$defs"][item_type]["properties"]
        fields["recipient_scope"]["description"] = (
            "仅指本项实际／尝试／计划传递的接收范围；许可／禁止动作或物品范围的只，不自动是only接收范围。"
        )
        fields["transfer_scope"]["description"] = (
            "依原传递本身区分completed、attempted、planned、none；许可另抄／携带不等于许可者交付副本，回执未到不撤销已寄出。"
        )
        for key in ("supplier", "transfer_object"):
            fields[key]["description"] = (
                "只填本项原话支持的传递角色／内容；纯许可无传递时留空，不能为满足only守卫臆造。"
            )

    if scope_execution:
        fields = author_contract["$defs"][item_type]["properties"]
        ids = [o["observation_id"] for o in inventory["inventory"]["observations"]]
        if ids:
            fields["scope_observation_ids"]["items"]["enum"] = ids
        else:
            fields["scope_observation_ids"]["maxItems"] = 0
        fields["recipient_scope"]["description"] += recipient_scope.GUIDANCE

    if authored_execution:
        fields = author_contract["$defs"][item_type]["properties"]
        ids = [o["observation_id"] for o in authored_inventory["inventory"]["observations"]]
        if ids:
            fields["scope_observation_ids"]["items"]["enum"] = ids
        else:
            fields["scope_observation_ids"]["maxItems"] = 0
        fields["recipient_scope"]["description"] = recipient_scope.GUIDANCE

    def validate_author(value):
        for item in value["items"]:
            resolve_field(item, "author_evidence", author_sources)
        issues = []
        if preparation is not None and preparation.get("item_count") is not None:
            if len(value["items"]) != preparation["item_count"]:
                issues.append("写前复解释必须按原P编号顺序逐项保留全部事项，不得删除、合并或遗漏歧义项。")
        issues.extend(
            repair_state.validate(recipient_scope.core_value(value) if scope_execution else value)
            if roles
            else open_plan_issues(value, author_text)
            if open_details
            else author_contract_issues(value, author_text, scoped=scoped)
        )
        if scope_execution:
            issues.extend(recipient_scope.binding_issues(value, inventory))
        if authored_execution:
            issues.extend(authored.binding_issues(value, authored_inventory))
        return issues

    def repair_feedback():
        result = repair_state.feedback()
        if scope_execution:
            result.update(
                {
                    "接收范围核对执行版本": preparation[recipient_scope.KEY],
                    **(
                        {"完整AI章纲分类边界": PLAN_CLASSIFICATION_GUIDANCE}
                        if preparation[recipient_scope.KEY] == recipient_scope.NEW_VERSION
                        else {}
                    ),
                    "接收范围修正边界": recipient_scope.GUIDANCE,
                }
            )
        return result

    from .workflow_plan_scope_classification import GUIDANCE as PLAN_CLASSIFICATION_GUIDANCE

    source_label = "本章工作章纲（AI规划）" if source_kind == "workflow_plan" else "作者原话"
    # No candidate, reference excerpt, application plan or old review reaches this call.
    contract = await worker.grounded(
        {
            "动作": "写前解释AI工作章纲" if preparation is not None else "仅依据作者原话整理本场景情节",
            source_label: author_text,
            "可引用原文": author_units,
            **source_metadata(source_kind, version),
            **({OPEN_KEY: OPEN_VERSION, "开放执行槽解释": OPEN_GUIDANCE} if open_details else {}),
            **({ROLE_KEY: ROLE_VERSION, "来源角色与限制范围解释": ROLE_GUIDANCE} if roles else {}),
            **(
                {
                    authored.KEY: authored_inventory["version"],
                    "先行作者来源范围观察（非作者条件）": authored_inventory,
                    **(
                        {"完整来源分类边界": CLASSIFICATION_GUIDANCE}
                        if authored_inventory["version"] == authored.CURRENT_VERSION
                        else {}
                    ),
                    "作者来源解释边界": recipient_scope.GUIDANCE
                    + "每项scope_observation_ids绑定先行O编号；所有观察均保持。只解析本段已经明确的主体与回指，真正未明保持ambiguous，不输出open_fields，不自动选择参与者、补写源事项或调用开放选择；scope_binding_status只有同一原话支持观察及本项范围才supported，先行AI观察不是原话真值，来源冲突或无法确认时填unknown并说明，保留本项原义，不为了绑定修改原事件、角色或阶段。",
                }
                if authored_execution
                else {}
            ),
            **(
                {
                    "接收范围核对执行版本": preparation[recipient_scope.KEY],
                    **(
                        {"完整AI章纲分类边界": PLAN_CLASSIFICATION_GUIDANCE}
                        if preparation[recipient_scope.KEY] == recipient_scope.NEW_VERSION
                        else {}
                    ),
                    "先行来源范围观察（AI观察，不是作者条件）": inventory,
                    "接收范围来源绑定": recipient_scope.GUIDANCE
                    + "每项scope_observation_ids只引用先行观察O编号，全部观察须被原义对应事项保持；没有相关观察可空。only/unrestricted须有同一物品、双方和阶段的来源观察，不可借别项许可或禁止；先行观察仍非原话真值，发现来源歧义应保留而非伪造通过。"
                    + (
                        "scope_binding_status仅当原话支持本项与观察的绑定才supported；先行AI观察非真值，若与原话冲突或归属确实未明，可unknown并在scope_binding_reason保留双方差异；不为满足先行观察改变原事项或把unknown当格式错误修成一致。unknown会在开放选择、保持核对及正文前停止。"
                        if preparation[recipient_scope.KEY]
                        in {recipient_scope.CURRENT_VERSION, recipient_scope.NEW_VERSION}
                        else ""
                    ),
                }
                if scope_execution
                else {}
            ),
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
        **({"diagnostic_label": "AI工作章纲写前解释"} if open_details else {}),
        **(
            {"repair_feedback": repair_feedback if scope_execution else repair_state.feedback}
            if roles
            else {}
        ),
    )
    if authored_execution:
        return {
            "contract": authored.core_value(contract),
            "bindings": authored.binding_data(contract, authored_inventory["version"]),
        }
    if roles:
        return {
            **normalize_open_plan(recipient_scope.core_value(contract) if scope_execution else contract),
            "source_role_repair": repair_state.receipt(),
            **(
                {
                    "recipient_scope_bindings": recipient_scope.bindings(
                        contract, preparation[recipient_scope.KEY]
                    )
                }
                if scope_execution
                else {}
            ),
        }
    return normalize_open_plan(contract) if open_details else contract


def authored_metadata(worker, text, kind, number):
    if kind not in {"author_plot", "author_instruction"}:
        return {}
    from . import authored_source_scope as authored

    if not authored.validate_task(worker.task, getattr(worker, "domain", None)):
        return {}
    return authored.metadata(worker, text, kind, number)


async def audit_plot(worker, author_text, text, number, *, source_kind=None, prepared_contract=None):
    version = execution_version(worker) if source_kind is not None else LEGACY_VERSION
    source_label = "本章工作章纲（AI规划）" if source_kind == "workflow_plan" else "作者原话"
    if prepared_contract is None:
        contract = await interpret_plot(worker, author_text, source_kind=source_kind, source_position=number)
    else:
        from .workflow_plan_preparation import frozen_contract

        if source_kind == "author_plot" and worker.task["kind"] == "candidate_recheck":
            from .candidate_recheck import verified_author_contract

            contract = verified_author_contract(worker, author_text, number, prepared_contract)
        elif source_kind == "author_plot" and worker.task["kind"] == "source_corrected_candidate_recheck":
            from .source_corrected_candidate_recheck import verified_author_contract

            contract = verified_author_contract(worker, author_text, number, prepared_contract)
        elif source_kind == "workflow_plan" and worker.task["kind"] == "source_corrected_candidate_recheck":
            from .prepared_source_scope_correction import verified_contract

            contract = verified_contract(worker, author_text, number, prepared_contract)
        elif source_kind == "workflow_plan":
            contract = frozen_contract(worker, author_text, number, prepared_contract)
        else:
            raise DomainError("plan_preparation_version", "冻结工作章纲解释不能替代作者情节解释。", 409)
    source_execution = authored_metadata(worker, author_text, source_kind, number)
    if source_execution.get("authored_source_scope", {}).get("state") == "unverified":
        return {
            "items": [],
            "checks": [],
            "findings": [],
            "status": "unverified",
            "summary": "作者来源范围仍待确认，未把AI解释当作正文硬条件；原话和提案保留。",
            "interpretation_note": contract["note"],
            "policy": "没有重新定义作者要求、比较或自动修改正文。",
            "version": version,
            **source_execution,
        }
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
            **authored_metadata(worker, author_text, source_kind, number),
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

    source_correction = (
        worker.task["result"]["source_corrected_recheck_snapshot"].get("source_correction_metadata")
        if (getattr(worker, "task", None) or {}).get("kind") == "source_corrected_candidate_recheck"
        else None
    )
    scope_correction = (
        worker.task["result"]
        .get("source_corrected_recheck_snapshot", {})
        .get("source_scope_correction_metadata")
        if (getattr(worker, "task", None) or {}).get("kind") == "source_corrected_candidate_recheck"
        else None
    )
    compared = await worker.grounded(
        {
            "动作": "逐项对照作者情节解释与本次正文",
            source_label: author_text,
            "独立整理的解释提案": items,
            **(
                {"显式旧AI章纲范围纠偏回执": deepcopy(scope_correction)}
                if scope_correction is not None
                else {}
            ),
            "本次候选": text,
            "可引用原文": units,
            **source_metadata(source_kind, version),
            **(
                {
                    "显式来源解释纠偏元数据": deepcopy(source_correction),
                    "纠偏解释边界": (
                        "元数据只说明已独立核对的事项性质和主体回指，不改原事件字段。"
                        "narrative_requirement核对文本是否按原话交代内容与顺序，不新增某个角色必须讲话的义务；"
                        "未绑定角色不等于事件未知，也不把真正未定的行动者选择改成文本指令。"
                        "正文履约仍须本次PLOT原文证明，未知保持，不从候选反改源意。"
                    ),
                }
                if source_correction is not None
                else {}
            ),
            **(
                {
                    "情节阶段对照版本": COMPARISON_GUIDANCE_VERSION,
                    "按冻结事项区分行动阶段": (
                        "逐项保持transfer_scope及原话的阶段。planned只要求原话明确的意愿、决定、准备或未来安排，"
                        "不能仅因尚未实际索取、尝试受阻、被拦停或尚未交付而判不符；若原话还明确要求本章已经"
                        "发生的独立结果，仍须核对该结果，不用计划覆盖它。attempted要求实际发起尝试，交易失败"
                        "不等于没有尝试，也不能把原本失败的结果改为成交。completed必须有实际交付或取得的正文依据，"
                        "只有提出、索取、准备、被拒绝或约定未来交付不证明已经完成。无法判断阶段仍unknown，"
                        "不得按正文反改冻结事项。"
                    ),
                    "最终判断一致性": (
                        "每项只输出核对后的同一个最终判断，result、observed、reason和repair须一致。"
                        "先在内部完成比较，再输出结果；不要先写fail，又在reason或repair反复自问并建议改成pass。"
                        "pass说明已符合的阶段和原文，unknown说明尚缺哪项依据，fail说明本稿哪处明确违背及如何保留原约定修订。"
                        "不能靠改写标签、猜身份或删除真实问题制造通过。"
                    ),
                }
                if version == VERSION
                else {}
            ),
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
        **({"source_scope_correction": deepcopy(scope_correction)} if scope_correction is not None else {}),
        **({"source_correction": deepcopy(source_correction)} if source_correction is not None else {}),
        "status": status,
        "findings": findings,
        "policy": f"解释来自{source_label}，仍须作者核对；不写入故事事实，不自动采用候选。",
        **({"version": version} if source_kind is not None else {}),
        **({"comparison_guidance_version": COMPARISON_GUIDANCE_VERSION} if version == VERSION else {}),
        **authored_metadata(worker, author_text, source_kind, number),
    }
