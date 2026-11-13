"""Final conclusions must remain consistent with code-adjusted plot checks."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from ainovel.plot_audit import LEGACY_VERSION, VERSION, audit_plot

AUTHOR = "甲只向乙告知线索。乙只向甲出示账册。"
TEXT = "甲把线索告诉乙，乙把同一条线索还给甲，甲也向另一人提及线索。乙把账册展示给另一人。"
MODEL_SUMMARY = "原模型声称各项均已通过；这是需要保留的原始模型摘要。"


def plot_item(*, certainty="explicit", object_name="线索", supplier="甲", recipient="乙"):
    return {
        "author_evidence": "AUTHOR0001",
        "actor": supplier,
        "action": "告知",
        "object": object_name,
        "direction": f"{supplier}向{recipient}",
        "result": "仅告知指定接收者",
        "certainty": certainty,
        "transfer_object": object_name,
        "supplier": supplier,
        "recipient": recipient,
        "transfer_scope": "completed",
        "recipient_scope": "only",
        "scope_condition": "",
    }


def model_check(identifier="P01", result="pass"):
    return {
        "item_id": identifier,
        "result": result,
        "observed": "原模型本稿观察",
        "evidence": "PLOT001P0001",
        "reason": "原模型逐项理由",
        "repair": "原模型建议",
    }


def flow(supplier="甲", recipient="乙", object_name="线索"):
    return {
        "object_id": object_name,
        "supplier_id": supplier,
        "recipient_id": recipient,
        "stage": "completed",
        "certainty": "explicit",
        "evidence": "FLOW0001",
        "note": "仅为确定性工程观察，不代表真实模型识别质量。",
    }


SCENARIOS = {
    "no_forward": [],
    "extra": [flow(), flow(recipient="unknown")],
    "reverse": [flow("乙", "甲")],
    "reverse_extra": [flow("乙", "甲"), flow(recipient="unknown")],
    "both_extra": [flow(), flow("乙", "甲"), flow(recipient="unknown")],
    "forward": [flow()],
}


class AuditFixture:
    def __init__(self, scenario="forward", *, version=VERSION, result="pass", certainty="explicit"):
        self.task = {"result": {"plot_contract_version": version}}
        self.items = [plot_item(certainty=certainty)]
        self.checks = [model_check(result=result)]
        self.flows = deepcopy(SCENARIOS[scenario])
        self.requests = []
        self.comparison = None

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        self.requests.append(deepcopy(instruction))
        action = instruction["动作"]
        if action == "仅依据作者原话整理本场景情节":
            entries = deepcopy(self.items)
            if "情节核对契约版本" not in instruction or instruction["情节核对契约版本"] == LEGACY_VERSION:
                for entry in entries:
                    entry.pop("recipient_scope")
                    entry.pop("scope_condition")
            value = {"items": entries, "note": "原始解释提案，歧义不自动消失。"}
        elif action == "逐项对照作者情节解释与本次正文":
            value = {"checks": deepcopy(self.checks), "summary": MODEL_SUMMARY}
        else:
            assert action == "只从本次正文独立提取物品与信息流向"
            entities = {name: key for key, name in instruction["实体别名对照"].items()}
            objects = {name: key for key, name in instruction["待识别物品名称"].items()}
            rows = deepcopy(self.flows)
            for row in rows:
                row["object_id"] = objects[row["object_id"]]
                for key in ("supplier_id", "recipient_id"):
                    row[key] = entities.get(row[key], "unknown")
            value = {"flows": rows, "note": "工程替身，原文引用与结论处理分别验证。"}
        value = schema.model_validate(value).model_dump()
        assert not validate(value)
        if action == "逐项对照作者情节解释与本次正文":
            self.comparison = deepcopy(value)
        return value


def run(worker, *, source_kind="author_instruction"):
    return asyncio.run(audit_plot(worker, AUTHOR, TEXT, 1, source_kind=source_kind))


@pytest.mark.parametrize(
    "scenario,result,expected,guard",
    [
        ("no_forward", "pass", "unknown", None),
        ("extra", "pass", "unknown", "restricted_recipient_unverified"),
        ("reverse", "pass", "fail", "reverse_transfer"),
        ("reverse", "fail", "fail", "reverse_transfer"),
        ("reverse_extra", "pass", "fail", "reverse_transfer"),
        ("reverse_extra", "unknown", "fail", "reverse_transfer"),
        ("both_extra", "pass", "unknown", "restricted_recipient_unverified"),
    ],
)
def test_v2_code_adjustments_preserve_the_original_check_and_summary(scenario, result, expected, guard):
    worker = AuditFixture(scenario, result=result)
    audit = run(worker)
    row = audit["checks"][0]
    assert row["result"] == expected
    assert row["original_model_check"] == worker.comparison["checks"][0]
    assert "original_model_check" not in row["original_model_check"]
    assert audit["original_model_summary"] == MODEL_SUMMARY
    assert audit["summary"] != MODEL_SUMMARY
    assert row.get("guard_override", {}).get("code") == guard
    assert audit["status"] == ("failed" if expected == "fail" else "unverified")
    assert f"不符{int(expected == 'fail')}项" in audit["summary"]
    assert f"未知{int(expected == 'unknown')}项" in audit["summary"]
    assert len(worker.requests) == 3


@pytest.mark.parametrize(
    "result,certainty,status,counts",
    [
        ("unknown", "explicit", "unverified", "通过0项、不符0项、未知1项；解释歧义0项"),
        ("pass", "ambiguous", "unverified", "通过1项、不符0项、未知0项；解释歧义1项"),
        ("fail", "ambiguous", "unverified", "通过0项、不符1项、未知0项；解释歧义1项"),
        ("pass", "explicit", "model_pass", "通过1项、不符0项、未知0项；解释歧义0项"),
    ],
)
def test_v2_summary_uses_final_checks_and_interpretation_without_rewriting_model_conclusions(
    result, certainty, status, counts
):
    worker = AuditFixture(result=result, certainty=certainty)
    audit = run(worker)
    assert audit["checks"] == worker.comparison["checks"]
    assert audit["original_model_summary"] == MODEL_SUMMARY
    assert counts in audit["summary"] and audit["status"] == status
    assert ("仍待核对" in audit["summary"]) is (status == "unverified")
    assert ("模型核对通过" in audit["summary"]) is (status == "model_pass")
    assert len(worker.requests) == (2 if certainty == "ambiguous" else 3)


def test_v2_explicit_failure_retains_priority_over_another_item_with_unknown_recipient():
    worker = AuditFixture()
    worker.items.append(plot_item(object_name="账册", supplier="乙", recipient="甲"))
    worker.checks.append(model_check("P02"))
    worker.flows = [flow("乙", "甲"), flow("乙", "甲", "账册"), flow("乙", "unknown", "账册")]
    audit = run(worker)
    assert [row["result"] for row in audit["checks"]] == ["fail", "unknown"]
    assert audit["status"] == "failed"
    assert "不符1项、未知1项" in audit["summary"] and "存在明确不符" in audit["summary"]
    assert len(audit["findings"]) == 1 and audit["findings"][0]["severity"] == "blocker"
    assert audit["original_model_summary"] == MODEL_SUMMARY


# Complete v1 reports frozen before the fix, including legacy reference reports
# without source metadata. A v2 presentation fix must not rewrite these outputs.
LEGACY_OUTPUT_SHA256 = {
    ("no_forward", None): "9e8770f0a10b970498007db3b9c7093ee7ea39256474042add165997d0d6fc73",
    ("no_forward", "author_instruction"): "73638c2303b7d29404d2d8f0056744968aacd44ccc32d78e509d020a64dea0bc",
    ("extra", None): "71ccb175084e62b201b78387f76de37f5540686fbeaf6de3952248411ba6b8e7",
    ("extra", "author_instruction"): "62b9869e320cc916c343cba96dec1b0adbedb47440d7e3cbfd6b9da8bc7cfd5a",
    ("reverse", None): "1faea1b57dc3b3017a53bfa7b348f84a23a6c0242c424e12c9d9325052af2791",
    ("reverse", "author_instruction"): "992249b3a99a08808833085d763f73d49d78321ce79abfc172cc8b35e2ff3938",
    ("reverse_extra", None): "ceea8a125dfa9dbaa7298d6f72d14282d397ab9182748ced5f3b944841167776",
    (
        "reverse_extra",
        "author_instruction",
    ): "a180a89d85a6d35c097887126b85e89ef5ab10a780653b823f4e53dc57a6773f",
    ("both_extra", None): "e90c195478bc00f018bea7853616eb6f42ee8d64e6789956ab8cabeedd711efb",
    ("both_extra", "author_instruction"): "1ff853cc1fac2a556728ae099625a1ebfdb7cf4d0eac06804ef584f80f59d7e5",
    ("forward", None): "0d2f6312d25d998028494049694f2c1b7e7eac9d43fbe4c852578c530339c554",
    ("forward", "author_instruction"): "3ad02976f9726ef7c354ec77ec8fc9499f85ffe7a9859d4aedabdb8b33dbc763",
}


@pytest.mark.parametrize("source_kind", [None, "author_instruction"])
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_v1_entire_saved_report_remains_exactly_compatible(scenario, source_kind):
    audit = run(AuditFixture(scenario, version=LEGACY_VERSION), source_kind=source_kind)
    serialized = json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(serialized.encode()).hexdigest() == LEGACY_OUTPUT_SHA256[(scenario, source_kind)]
