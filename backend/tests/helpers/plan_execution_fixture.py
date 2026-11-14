"""Deterministic new-policy responses; never used as a quality judgment."""

from ainovel.workflow_plan_preparation import CHECK_ACTION, CHOICE_ACTION


def plan_execution_output(instruction):
    while isinstance(instruction.get("原始任务"), dict):
        instruction = instruction["原始任务"]
    action = instruction.get("动作")
    if action == "写前解释AI工作章纲":
        baseline = instruction["写前准备契约"].get("原始事项")
        if baseline:
            items = [{key: value for key, value in item.items() if key != "id"} for item in baseline]
            for item in items:
                item["certainty"] = "explicit"
                item["author_evidence"] = next(
                    unit["source_id"]
                    for unit in instruction["可引用原文"]
                    if item["author_evidence"] in unit["text"]
                )
        else:
            items = [
                {
                    "author_evidence": instruction["可引用原文"][0]["source_id"],
                    "actor": "沈砚",
                    "action": "测量并记录",
                    "object": "读数",
                    "direction": "",
                    "result": "保留疑点，不提前解谜",
                    "certainty": "explicit",
                    "transfer_object": "",
                    "supplier": "",
                    "recipient": "",
                    "transfer_scope": "none",
                    "recipient_scope": "unspecified",
                    "scope_condition": "",
                }
            ]
        return {"items": items, "note": "确定性工程替身，不代表真实语义保持或创作质量通过。"}
    if action == CHOICE_ACTION:
        return {
            "addendum": "按原安排记录可复查结果，疑点继续保留，不提前判定原因。",
            "rationale": "工程替身：明确本章记录动作。",
        }
    if action == CHECK_ACTION:
        return {
            "item_checks": [
                {
                    "item_id": item["id"],
                    "prepared_item_id": item["id"],
                    "result": "pass",
                    "reason": "工程替身：逐项保持，不代表真实模型评测。",
                }
                for item in instruction["初轮全部事项"]
            ],
            "constraints_check": {"result": "pass", "reason": "工程替身：原规划及作者条件保留。"},
            "summary": "工程替身保持核对。",
        }
    return None
