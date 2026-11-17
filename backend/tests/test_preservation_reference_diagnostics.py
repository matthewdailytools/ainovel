"""Precise feedback keeps source-preservation validation and raw returns intact."""

from copy import deepcopy

import pytest

from ainovel import authored_source_classification as classification
from ainovel import authored_source_scope as authored


def case():
    entry = {"text": "林舟沿堤记录水线。\n师傅接过副本。\n掌柜保留铜印。\n管理员不知道缺页原因。"}
    sources = authored.core.units(entry["text"])
    assert len(sources) == 4
    contract = {"items": [{} for _ in sources]}
    basis = {"fixed": [{"source_id": "FIX001", "text": "不动手"}]}
    value = {
        "input_sha256": authored.sha(basis),
        "source_checks": [
            {
                "source_id": s["source_id"],
                "classification_result": "pass",
                "result": "pass",
                "evidence_ids": [s["source_id"]],
                "reason": "保留原句。",
            }
            for s in sources
        ],
        "item_checks": [
            {
                "item_id": f"P{i:02}",
                "result": "pass",
                "evidence_ids": [s["source_id"]],
                "reason": "保留原句。",
            }
            for i, s in enumerate(sources, 1)
        ],
        "fixed_checks": [
            {
                "source_id": "FIX001",
                "result": "pass",
                "evidence_ids": ["FIX001"],
                "reason": "固定项保持。",
            }
        ],
        "author_contract_check": {"result": "pass", "reason": "没有新增约定。"},
        "summary": "独立来源保持。",
    }
    classification.Preservation.model_validate(value)
    return value, entry, contract, basis


def compare(value, entry, contract, basis):
    before = deepcopy((value, entry, contract, basis))
    legacy = classification.issues(value, entry, contract, basis)
    detailed = classification.detailed_issues(value, entry, contract, basis)
    assert len(detailed) == len(legacy)
    assert bool(detailed) == bool(legacy)
    assert (value, entry, contract, basis) == before
    return legacy, detailed


def test_observation_ids_get_exact_source_field_without_becoming_original_evidence():
    value, entry, contract, basis = case()
    value["source_checks"][2]["evidence_ids"].append("O01")
    value["source_checks"][3]["evidence_ids"].append("O02")
    legacy, detailed = compare(value, entry, contract, basis)
    assert legacy == ["保持只引用本次完整连续来源编号并说明理由。"] * 2
    assert "source_checks.SCOPE0003.evidence_ids含非法编号O01" in detailed[0]
    assert "source_checks.SCOPE0004.evidence_ids含非法编号O02" in detailed[1]
    assert all("O编号属于AI观察，不是原句证据" in e for e in detailed)
    assert all("SCOPE／FIX原句" in e for e in detailed)


@pytest.mark.parametrize("field", ["source_checks", "fixed_checks", "item_checks"])
@pytest.mark.parametrize("fault", ["observation", "duplicate", "blank_reason", "combined"])
def test_reference_faults_keep_legacy_error_count_and_do_not_rewrite_return(field, fault):
    value, entry, contract, basis = case()
    row = value[field][0]
    if fault in {"observation", "combined"}:
        row["evidence_ids"].append("O01")
    if fault in {"duplicate", "combined"}:
        row["evidence_ids"].append(row["evidence_ids"][0])
    if fault in {"blank_reason", "combined"}:
        row["reason"] = " "
    legacy, detailed = compare(value, entry, contract, basis)
    assert len(legacy) == 1
    assert legacy[0] in detailed[0]
    key = row.get("source_id", row.get("item_id"))
    if fault in {"observation", "combined"}:
        assert f"{field}.{key}.evidence_ids含非法编号O01" in detailed[0]
    if fault in {"duplicate", "combined"}:
        assert f"{field}.{key}.evidence_ids有重复编号" in detailed[0]
    if fault in {"blank_reason", "combined"}:
        assert f"{field}.{key}.reason为空" in detailed[0]


@pytest.mark.parametrize("field", ["source_checks", "fixed_checks", "item_checks"])
def test_unknown_never_waives_illegal_reference_and_stays_unknown(field):
    value, entry, contract, basis = case()
    row = value[field][0]
    row["result"] = "unknown"
    if field == "source_checks":
        row["classification_result"] = "unknown"
    row["evidence_ids"].append("O01")
    legacy, detailed = compare(value, entry, contract, basis)
    assert len(legacy) == 1 and "O01" in detailed[0]
    assert row["result"] == "unknown"
    assert not classification.passed(value)


def test_valid_unknown_is_not_promoted_and_format_valid_pass_remains_unchanged():
    value, entry, contract, basis = case()
    assert compare(value, entry, contract, basis) == ([], [])
    assert classification.passed(value)
    row = value["source_checks"][0]
    row.update(result="unknown", classification_result="unknown", evidence_ids=[])
    assert compare(value, entry, contract, basis) == ([], [])
    assert not classification.passed(value)


def test_fixed_id_does_not_become_original_plot_evidence_and_empty_pass_still_fails():
    value, entry, contract, basis = case()
    value["item_checks"][0]["evidence_ids"] = ["FIX001"]
    value["item_checks"][1]["evidence_ids"] = []
    legacy, detailed = compare(value, entry, contract, basis)
    assert legacy == ["P通过须回到本次原话，不能从观察、固定项或候选补造情节依据。"] * 2
    assert "item_checks.P01.evidence_ids含非法编号FIX001" in detailed[0]
    assert "只允许本次SCOPE原句编号" in detailed[0]
    assert "item_checks.P02.evidence_ids为空" in detailed[1]


def test_other_validation_diagnostics_stay_byte_exact():
    value, entry, contract, basis = case()
    value["input_sha256"] = "0" * 64
    value["source_checks"].reverse()
    value["source_checks"][0]["evidence_ids"] = ["SCOPE0001"]
    value["item_checks"].pop()
    value["author_contract_check"]["reason"] = " "
    legacy, detailed = compare(value, entry, contract, basis)
    assert legacy == detailed
    assert len(legacy) == 5


def test_pure_builder_keeps_original_request_frame_fingerprint_and_legacy_validator():
    value, entry, contract, basis = case()
    basis["author_contract"] = {"configured": False}
    value["input_sha256"] = authored.sha(basis)
    record = {"source": entry, "contract": contract, "preservation_input": basis}
    inventory = {
        "sources": authored.core.units(entry["text"]),
        "inventory": {"observations": [], "unit_checks": []},
    }
    before = deepcopy((record, inventory, value))
    request = classification.preservation_request(record, inventory)
    frame = {
        "instruction": request["instruction"],
        "schema": request["schema"].__name__,
        "context": request["context"],
        "max_output": request["max_output"],
        "contract": request["contract"],
        "diagnostic_label": request["diagnostic_label"],
        "repair_feedback": request["repair_feedback"](),
    }
    # Captured from independent_preserve's original grounded arguments before extraction.
    assert authored.sha(frame) == "6ac444662a3e9fea8d508ea3713372b23f41b9da903c96c41544ebb6f5277a2b"
    assert request["validator"](value) == []
    value["source_checks"][2]["evidence_ids"].append("O01")
    assert request["validator"](value) == ["保持只引用本次完整连续来源编号并说明理由。"]
    assert (record, inventory) == before[:2]
    assert request["instruction"]["全部原话连续单元"] == inventory["sources"]
    assert "可引用原文" not in request["instruction"]
    assert request["schema"] is classification.Preservation
    assert request["max_output"] == 4096
    request["repair_feedback"]()["引用修正要求"] = "changed local copy"
    assert request["repair_feedback"]() == frame["repair_feedback"]
