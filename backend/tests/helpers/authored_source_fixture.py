"""Transport routing only; dedicated scope tests own semantic inventory examples."""

from ainovel import authored_source_scope as authored


def authored_inventory(task):
    from ainovel.authored_source_classification import ACTION

    if task.get("动作") == ACTION:
        return classification_preservation(task)
    if task.get("动作") != authored.ACTION:
        return None
    return {
        "input_sha256": task["input_sha256"],
        "observations": [],
        "unit_checks": [
            {
                "source_id": u["source_id"],
                "status": "not_scope_related",
                "observation_ids": [],
                "reason": "确定性路由替身；范围语义另由专项样例验证。",
            }
            for u in task["可引用原文"]
        ],
        "note": "仅验证新增独立阶段的工程接线，不作限制语义评价。",
    }


def bind_authored(task, value):
    if task.get(authored.KEY) in authored.SUPPORTED and task.get("动作") == "仅依据作者原话整理本场景情节":
        for item in value["items"]:
            item.update(
                scope_observation_ids=[],
                scope_binding_status="supported",
                scope_binding_reason="无范围观察的确定性工程样例。",
            )
    return value


def classification_preservation(task, result="pass", classification="pass"):
    sources = task["全部原话连续单元"]
    return {
        "input_sha256": task["input_sha256"],
        "source_checks": [
            {
                "source_id": s["source_id"],
                "result": result,
                "classification_result": classification,
                "evidence_ids": [s["source_id"]],
                "reason": "仅确定性工程路由，完整语义另有专项反例。",
            }
            for s in sources
        ],
        "item_checks": [
            {
                "item_id": f"P{i:02}",
                "result": result,
                "evidence_ids": [sources[0]["source_id"]],
                "reason": "仅工程路由的完整P保持，不作文学或模型质量评价。",
            }
            for i, _ in enumerate(task["完整P解释（AI提案非新条件）"]["items"], 1)
        ],
        "fixed_checks": [
            {
                "source_id": s["source_id"],
                "result": "pass",
                "evidence_ids": [s["source_id"]],
                "reason": "仅保持约定。",
            }
            for s in task["本章及全书固定条件"]
        ],
        "author_contract_check": {"result": "pass", "reason": "仅工程路由保持。"},
        "summary": "确定性替身结果，不代表文学或真实模型验收。",
    }
