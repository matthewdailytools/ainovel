"""Keep specific recipient-scope evidence ahead of generic source uncertainty."""

import pytest

from test_flow_source_entailment import Model, compare, flow, item, TEXT


@pytest.mark.parametrize("condition", ["", "核验前"])
def test_supported_extra_unknown_recipient_retains_specific_scope_guard(condition):
    source = item(scope="only")
    source["scope_condition"] = condition
    report, rows, _ = compare(
        Model([flow(), flow("甲", "未识别者")]),
        items=[source],
        text=TEXT + "甲随后把同一个问题又问了门边另一人，那人的身份尚未认清。",
    )
    row = rows[0]
    assert row["result"] == "unknown"
    assert row["original_model_check"]["result"] == "pass"
    guard = row["guard_override"]
    assert guard["code"] == "restricted_recipient_unverified"
    assert guard["recipient_scope"] == "only" and guard["scope_condition"] == condition
    assert guard["expected_supplier"] == "甲" and guard["expected_recipient"] == "乙"
    assert guard["observations"] == [report["flows"][1]]
    assert guard["observations"][0]["recipient_id"] == "unknown"
    assert "不认定unknown身份" in guard["policy"]
    assert row["evidence"] == report["flows"][1]["evidence"]
    assert all(check["result"] == "supported" for check in report["source_entailment"]["checks"])


def test_unsupported_extra_still_holds_with_generic_source_guard_without_relabeling_as_specific_evidence():
    report, rows, _ = compare(
        Model([flow(), flow("甲", "未识别者")], ["supported", "unknown"]),
        items=[item(scope="only")],
    )
    row = rows[0]
    assert row["result"] == "unknown" and row["original_model_check"]["result"] == "pass"
    assert row["guard_override"]["code"] == "flow_source_unverified"
    assert row["guard_override"]["unresolved"][0]["check"]["result"] == "unknown"
    assert len(report["source_entailment"]["supported_observations"]) == 1
    assert report["source_entailment"]["checks"][1]["result"] == "unknown"


def test_supported_reverse_remains_a_failure_before_either_unknown_guard():
    _, rows, _ = compare(Model([flow("乙", "甲")]), items=[item(scope="only")], text="乙向甲问起桥规。")
    assert rows[0]["result"] == "fail"
    assert rows[0]["guard_override"]["code"] == "reverse_transfer"
    assert rows[0]["original_model_check"]["result"] == "pass"


def test_rejected_extra_stays_in_report_and_is_not_promoted_to_actual_scope_evidence():
    report, rows, _ = compare(
        Model([flow(), flow("甲", "未识别者")], ["supported", "contradicted"]),
        items=[item(scope="only")],
        text=TEXT + "乙答完便离开，甲没有再向其他人发问。",
    )
    assert rows[0]["result"] == "pass" and "guard_override" not in rows[0]
    assert len(report["flows"]) == 2
    assert report["source_entailment"]["checks"][1]["result"] == "contradicted"
    assert len(report["source_entailment"]["supported_observations"]) == 1


def test_specific_scope_guard_does_not_erase_other_unverified_source_observations():
    report, rows, _ = compare(
        Model([flow(), flow("甲", "未识别者"), flow("乙", "甲")], ["supported", "supported", "unknown"]),
        items=[item(scope="only")],
    )
    assert rows[0]["result"] == "unknown"
    assert rows[0]["guard_override"]["code"] == "restricted_recipient_unverified"
    assert rows[0]["guard_override"]["observations"] == [report["flows"][1]]
    assert len(report["source_entailment"]["checks"]) == 3
    assert report["source_entailment"]["checks"][2]["result"] == "unknown"
