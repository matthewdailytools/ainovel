"""Deterministic contract responses; these do not assess literary entailment."""

CHARACTER_REVIEW_ACTION = "独立核对人物自由文本的本项来源蕴含"
CHARACTER_REVIEW_VERSION = "character-field-entailment/1"


def character_review_output(instruction):
    while "原始任务" in instruction:
        instruction = instruction["原始任务"]
    if instruction.get("动作") != CHARACTER_REVIEW_ACTION:
        return None
    assert instruction["字段复核契约版本"] == CHARACTER_REVIEW_VERSION
    return {
        "version": CHARACTER_REVIEW_VERSION,
        "input_sha256": instruction["input_sha256"],
        "fields": [
            {
                "record_id": target["record_id"],
                "field": target["field"],
                "result": "supported",
                "reason": "确定性工程替身，仅验证流程",
                "source_ids": target["source_ids"][:1],
            }
            for target in instruction["人物字段"]
        ],
    }
