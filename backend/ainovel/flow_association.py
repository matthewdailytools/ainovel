"""Candidate-only, independently checked exclusions for coarse flow labels.

Different physical copies do not imply different information. In particular,
these relations never exempt an extra disclosure from an author's only scope.
"""

from typing import Literal

from pydantic import Field

from .evidence import resolve_field
from .models import Input


class UnmappedParticipant(Input):
    flow_id: str
    side: Literal["supplier_id", "recipient_id"]
    status: Literal["known_other", "unknown"]
    label: str = Field(max_length=200)
    excluded_entity_ids: list[str] = Field(max_length=24)
    evidence: str = Field(max_length=800)
    reason: str = Field(min_length=1, max_length=1200)


class InstanceRelation(Input):
    left_flow_id: str
    right_flow_id: str
    result: Literal["same", "different", "unknown"]
    kind: Literal["physical", "content", "unknown"]
    evidence: str = Field(max_length=800)
    reason: str = Field(min_length=1, max_length=1200)


GUIDANCE = {
    "参与者关联": (
        "participants仅补充原观察编号unknown的端点，不改原观察。known_other表示本次正文可明确区分的"
        "表外参与者，并用连续原文核实其不是excluded_entity_ids所列实体；名字不在表、姓名字面不同或"
        "原观察note自称不同都不足以排除。若可能是别名、同一人的不同身份、代表机构执行或回指不明，"
        "保持unknown、excluded_entity_ids为空。不能从预期方向猜谁。known_other须对应supported观察，"
        "给非空label、被原文明确排除的实体编号、证据及理由；不把局部身份升级为正式人物事实。"
    ),
    "副本实例关联": (
        "instances仅核实本次观察之间的same/different/unknown，不比较任何情节要求。名称相同不代表同一份："
        "同类抄件可有已寄出与后来另抄随身两份；必须由连续原文确认，不能仅看note或有两个交接动作。"
        "kind=physical指具体那一份实物，kind=content指传递的信息内容。不同纸张仍可含同一内容，不能"
        "据此排除相同信息的披露。两项都supported且原文足以确认才给same/different，否则unknown。"
        "不得因为已有正向例子就忽略别的未知，不得把未核实参与者或副本自动认定不同。"
    ),
    "关联输出": (
        "participants与instances各最多24项，可为空；没有有源区分就不作排除。所有非unknown关系须引用"
        "本次ENTAIL原文编号，理由说明连续原文怎样支持身份或实例区分；不从名字不在表推断known_other。"
        "关联结果与原观察、逐项来源复核一起保留，不删除观察，不接收规划、预期方向或旧guard。"
    ),
}


def issues(value, proposals, entities, sources, text):
    """Validate bindings, not just a quote match; semantic checks stay independent."""
    flows = {row["flow_id"]: row for row in proposals}
    checks = {row["flow_id"]: row for row in value["checks"]}
    errors, participant_keys, pairs = [], set(), set()

    def evidence(row, required):
        resolve_field(row, "evidence", sources)
        quote = row["evidence"]
        if quote and (not quote.strip() or quote not in text):
            errors.append("关联证据须为本次正文连续原文。")
        if required and not quote.strip():
            errors.append("明确关联排除须有本次正文依据。")
        if not row["reason"].strip():
            errors.append("关联结论须说明原文如何支持，不能仅凭名称或note。")

    for row in value["participants"]:
        key = (row["flow_id"], row["side"])
        if key in participant_keys:
            errors.append("同一观察端点只能核实一次。")
        participant_keys.add(key)
        flow, check = flows.get(row["flow_id"]), checks.get(row["flow_id"])
        excluded = row["excluded_entity_ids"]
        if not flow or flow[row["side"]] != "unknown":
            errors.append("参与者关联只能绑定本次原观察的unknown端点。")
        if len(excluded) != len(set(excluded)) or any(identifier not in entities for identifier in excluded):
            errors.append("排除身份须使用本次名称表编号且不能重复。")
        if row["status"] == "known_other":
            if not row["label"].strip() or not excluded:
                errors.append("明确表外参与者须给正文称谓和有源排除编号。")
            if not check or check["result"] != "supported":
                errors.append("未支持的原观察不能凭关联结论跳过核对。")
        elif excluded:
            errors.append("参与者未知不能排除任何实体。")
        evidence(row, row["status"] == "known_other")

    for row in value["instances"]:
        left, right = row["left_flow_id"], row["right_flow_id"]
        pair = tuple(sorted((left, right)))
        if left == right or pair in pairs:
            errors.append("实例关系须为不同观察且同一对只能核实一次。")
        pairs.add(pair)
        if left not in flows or right not in flows or flows[left]["object_id"] != flows[right]["object_id"]:
            errors.append("实例关系只能绑定本次同类物品的两项观察。")
        if row["result"] != "unknown":
            if row["kind"] == "unknown" or any(
                checks.get(identifier, {}).get("result") != "supported" for identifier in (left, right)
            ):
                errors.append("明确实例关系须核实内容类型及两项原观察，不能跳过未获支持项。")
        evidence(row, row["result"] != "unknown")
    return errors


def excluded_participant(report, flow, side, entity):
    if report.get("version") != "source-flow-entailment/2":
        return False
    return any(
        row["flow_id"] == flow["flow_id"]
        and row["side"] == side
        and row["status"] == "known_other"
        and entity in row["excluded_entity_ids"]
        for row in report.get("participants", [])
    )


def different_physical_instance(report, flow, anchors):
    """Exclude a reverse-copy association, never an only-content disclosure."""
    if report.get("version") != "source-flow-entailment/2":
        return False
    identifiers = {row["flow_id"] for row in anchors}
    return any(
        row["result"] == "different"
        and row["kind"] == "physical"
        and (
            row["left_flow_id"] == flow["flow_id"]
            and row["right_flow_id"] in identifiers
            or row["right_flow_id"] == flow["flow_id"]
            and row["left_flow_id"] in identifiers
        )
        for row in report.get("instances", [])
    )


def anchors(report, item, object_id, entity_ids):
    """Body-supported matching endpoints identify an instance, not completion."""
    supported = {row["flow_id"] for row in report["checks"] if row["result"] == "supported"}
    return [
        row
        for row in report["proposals"]
        if row["flow_id"] in supported
        and row["object_id"] == object_id
        and row["supplier_id"] == entity_ids[item["supplier"]]
        and row["recipient_id"] == entity_ids[item["recipient"]]
        and row["certainty"] == "explicit"
    ]
