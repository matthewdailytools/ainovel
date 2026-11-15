"""Transport routing only; dedicated scope tests own semantic inventory examples."""

from ainovel import authored_source_scope as authored


def authored_inventory(task):
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
    if task.get(authored.KEY) == authored.VERSION and task.get("动作") == "仅依据作者原话整理本场景情节":
        for item in value["items"]:
            item.update(
                scope_observation_ids=[],
                scope_binding_status="supported",
                scope_binding_reason="无范围观察的确定性工程样例。",
            )
    return value
