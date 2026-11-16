"""Explicit source-scope transport fixtures for the disposable browser server."""

from ainovel.authored_source_scope import ACTION as OBSERVE_ACTION
from ainovel.authored_source_classification import ACTION as CLASSIFICATION_ACTION
from helpers.authored_source_fixture import authored_inventory, bind_authored, classification_preservation

EXCLUSIVE_SENTINEL = "plot-scope-exclusive-e2e"


def source_observation(instruction):
    if instruction.get("动作") != OBSERVE_ACTION:
        return None
    value = authored_inventory(instruction)
    if value is None or EXCLUSIVE_SENTINEL not in instruction["作者本次情节原话"]:
        return value
    source_id = instruction["可引用原文"][0]["source_id"]
    value["observations"] = [
        {
            "observation_id": "O01",
            "kind": "recipient_exclusivity",
            "supplier": "沈砚",
            "recipient": "师傅",
            "transfer_object": "潮水读数",
            "transfer_scope": "completed",
            "evidence": source_id,
            "restriction_evidence": source_id,
            "condition": "本章中",
            "reason": "固定原创工程样例明确只告诉师傅；不模拟通用自然语言理解。",
        }
    ]
    value["unit_checks"][0].update(status="covered", observation_ids=["O01"])
    return value


def source_classification(instruction):
    if instruction.get("动作") != CLASSIFICATION_ACTION:
        return None
    return classification_preservation(instruction)


def bind_interpretation(instruction, value):
    value = bind_authored(instruction, value)
    if instruction.get("authored_source_scope_version") in {
        "authored-source-scope/1",
        "authored-source-scope/2",
    } and EXCLUSIVE_SENTINEL in instruction.get("作者原话", ""):
        for item in value["items"]:
            item.update(
                scope_observation_ids=["O01"],
                scope_binding_reason="保持固定原创样例的同一读数、双方、阶段及本章范围。",
            )
    return value
