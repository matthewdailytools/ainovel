"""Source-only identity qualifiers; candidate observations never choose the source labels."""

from copy import deepcopy
import hashlib
from typing import Literal

from pydantic import Field

from .db import DomainError
from .evidence import resolve_field, source_schema, source_units
from .models import Input

VERSION = "source-identity-context/1"


class EntityLabel(Input):
    entity_id: str
    label: str = Field(max_length=200)
    status: Literal["supported", "unknown"]
    evidence: str = Field(max_length=800)
    reason: str = Field(min_length=1, max_length=800)


class EntityLabels(Input):
    labels: list[EntityLabel] = Field(min_length=1, max_length=24)


class LabelCheck(Input):
    entity_id: str
    result: Literal["pass", "fail", "unknown"]
    evidence: str = Field(max_length=800)
    reason: str = Field(min_length=1, max_length=800)


class LabelChecks(Input):
    checks: list[LabelCheck] = Field(min_length=1, max_length=24)


def enabled(worker):
    task = getattr(worker, "task", None) or {}
    marker = task.get("result", {}).get("source_identity_context_version")
    requested = task.get("payload", {}).get("source_identity_context") is True
    if marker is None and not requested:
        return False
    if (
        marker != VERSION
        or not requested
        or task.get("result", {}).get("plot_contract_version") != "author-plot-audit/2"
    ):
        raise DomainError("source_identity_version", "角色限定核对的冻结版本无法确认，未升级旧任务。", 409)
    return True


def quotation_issues(row, sources, text):
    resolve_field(row, "evidence", sources)
    quote = row["evidence"]
    return ["限定词证据只能引用本次情节原话。"] if quote and (not quote.strip() or quote not in text) else []


async def source_identity_context(worker, text, entities):
    units = source_units(text, "IDENTITY")
    sources = {unit["source_id"]: unit for unit in units}
    if not units:
        raise DomainError("source_identity_source", "缺少情节原话，不能从候选补造身份限定。", 409)
    labels_schema = source_schema(EntityLabels.model_json_schema(), units)
    labels_schema["properties"]["labels"].update(minItems=len(entities), maxItems=len(entities))
    labels_schema["$defs"]["EntityLabel"]["properties"]["entity_id"]["enum"] = list(entities)

    def validate_labels(value):
        issues = []
        if sorted(row["entity_id"] for row in value["labels"]) != sorted(entities):
            issues.append("每个原角色编号须恰好补充一次，不得新增、遗漏或更换编号。")
        for row in value["labels"]:
            issues.extend(quotation_issues(row, sources, text))
            if row["entity_id"] not in entities:
                continue
            label = row["label"]
            if row["status"] == "supported" and (
                not label.strip()
                or entities[row["entity_id"]] not in label
                or label not in text
                or not row["evidence"].strip()
                or label not in row["evidence"]
                or any(c in label for c in "\n\r。；，：→←")
            ):
                issues.append(
                    "限定称谓须是含原称谓的连续原话名词短语，有逐字依据；不填动作、方向、结果或候选人物名。无法形成时用unknown及空label。"
                )
            if row["status"] == "unknown" and label:
                issues.append("未核实的限定称谓留空，不提供猜测名称。")
            if not row["reason"].strip():
                issues.append("补充或未知均须说明原话身份范围。")
        return issues

    labels = await worker.grounded(
        {
            "动作": "仅从情节原话补全角色称谓限定",
            "角色限定版本": VERSION,
            "情节原话": text,
            "原角色名称表": entities,
            "可引用原文": units,
            "要求": "只从这份原话补回角色称谓已包含的地点、关系或身份限定。保留连续原话的完整名词短语，不得添加原话没有的人名、地点或身份；不传递事件动作、给出取得方向或预期结果，不引入候选、设定、旧报告。原称谓已完整且为逐字原话时保持原名。原话只指定一位、某位等范围时保留该范围，不要求补出姓名或证明某地点只有一人；若同一原称谓在来源的不同事项可能混指多个角色，不能任意选其一或合并，保留unknown、label空。仅是身份解释提案，不是故事事实。",
        },
        EntityLabels,
        {"scope": "仅本次情节原话与无方向名称表；未读取候选、旧结论或全书未来"},
        validate_labels,
        contract=labels_schema,
        diagnostic_label="原话角色限定提取",
    )
    proposed = {row["entity_id"]: row for row in labels["labels"]}
    checks_schema = source_schema(LabelChecks.model_json_schema(), units)
    checks_schema["properties"]["checks"].update(minItems=len(entities), maxItems=len(entities))
    checks_schema["$defs"]["LabelCheck"]["properties"]["entity_id"]["enum"] = list(entities)

    def validate_checks(value):
        issues = []
        if sorted(row["entity_id"] for row in value["checks"]) != sorted(entities):
            issues.append("须逐个核对原角色编号，不得遗漏或重复。")
        for row in value["checks"]:
            issues.extend(quotation_issues(row, sources, text))
            label = proposed.get(row["entity_id"])
            if row["result"] == "pass" and (
                label is None
                or label["status"] != "supported"
                or not row["evidence"].strip()
                or label["label"] not in row["evidence"]
            ):
                issues.append(
                    "通过须核实原名称、全部身份限定和不含事件方向，并有包含称谓的原话依据；不能把unknown提案判通过。"
                )
            if not row["reason"].strip():
                issues.append("身份核对须有理由。")
        return issues

    checked = await worker.grounded(
        {
            "动作": "独立核对原话角色称谓限定",
            "角色限定版本": VERSION,
            "情节原话": text,
            "原角色名称表": entities,
            "限定提案": labels["labels"],
            "可引用原文": units,
            "要求": "只核对角色身份名词短语是否由原话支持、是否保留地点或关系等区分信息、是否混入事件动作/方向/预期结果。原话指定一位、某位等范围时不要求新增姓名或证明地点人口唯一；只核对来源内不同角色是否被混指。原称谓在不同事项混指多个角色而无法区分时用unknown，不能任意选其中一个。均可核实才pass；有错误fail。不能因为提案自称supported而认可，不重新决定剧情，不读取候选或旧意见。",
        },
        LabelChecks,
        {"scope": "独立核对仅原话与身份限定提案，不读取候选、旧报告或预期流向表"},
        validate_checks,
        contract=checks_schema,
        diagnostic_label="原话角色限定独立核对",
    )
    verified = {row["entity_id"]: row for row in checked["checks"]}
    unverified = [
        eid for eid in entities if proposed[eid]["status"] != "supported" or verified[eid]["result"] != "pass"
    ]
    return {
        "version": VERSION,
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "original_entities": deepcopy(entities),
        "entities": {
            eid: proposed[eid]["label"] if eid not in unverified else name for eid, name in entities.items()
        },
        "proposals": labels["labels"],
        "checks": checked["checks"],
        "unverified_ids": unverified,
        "policy": "来源限定仅用于识别同称谓角色；原解释事项/方向不变，不写正式身份或事实，未核实不支持通过。",
    }
