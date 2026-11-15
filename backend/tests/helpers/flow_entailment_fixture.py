"""Explicit engineering fixture for the isolated flow-source support contract."""

from ainovel.flow_entailment import ACTION, FIELDS


def flow_entailment_output(instruction):
    if instruction.get("动作") != ACTION:
        return None
    source = instruction["可引用原文"][0]["source_id"]
    return {
        "input_sha256": instruction["input_sha256"],
        "checks": [
            {
                "flow_id": row["flow_id"],
                "result": "supported",
                "verified_fields": list(FIELDS),
                "evidence": source,
                "reason": "隔离替身的已知流向，非真实模型检测能力。",
            }
            for row in instruction["待核对观察提案"]
        ],
        "note": "只验证新来源核对的接线与持久化，不声称内容质量。",
        **(
            {"participants": [], "instances": []}
            if instruction.get("流向来源蕴含版本") == "source-flow-entailment/2"
            else {}
        ),
    }
