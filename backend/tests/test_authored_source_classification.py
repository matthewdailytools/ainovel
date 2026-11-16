"""New sources use explicit /2 classification and complete independent preservation."""

import asyncio
from copy import deepcopy

import pytest

from ainovel import authored_source_scope as authored
from ainovel import authored_source_classification as classification
from ainovel.db import DomainError, dumps
from ainovel.models import ModuleInput
from ainovel.plot_audit import interpret_plot
from ainovel.worker import Worker
from helpers.authored_classification_fixture import SOURCE, native
from helpers.authored_source_fixture import classification_preservation


def module_worker(settings, domain, story):
    payload = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.expand_plot",
        instruction=SOURCE,
        account="openai_1",
        fixed=["不动手"],
        plot_contract_check=True,
        client_request_id="new-classification-source",
    ).model_dump(exclude_none=True)
    queued = domain.queue("module", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == queued["id"]
    return worker, payload


def run(worker):
    return asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))


def test_new_default_keeps_inventory_shape_and_full_unknown_knowledge_meaning(settings, domain, story):
    worker, payload = module_worker(settings, domain, story)
    assert worker.task["result"][authored.KEY] == classification.VERSION
    assert authored.KEY not in payload
    frames = native(worker)
    result = run(worker)
    record = worker.task["result"][authored.RECEIPTS]["module"]
    assert record["state"] == "ready" and authored.receipt_valid(record, SOURCE, {worker.task["id"]})
    assert [i["certainty"] for i in result["items"]] == ["explicit"] * 7
    assert any("不知道缺页原因" in i["result"] for i in result["items"])
    assert any("不等于" in i["result"] for i in result["items"])
    assert any("均未交付" in i["result"] or "都未交付" in i["result"] for i in result["items"])
    assert (
        record["phases"]["observe"]["response"]["inventory"]["unit_checks"][4]["status"]
        == "not_scope_related"
    )
    final = frames[-1]["任务"]
    assert len(final["全部原话连续单元"]) == 7 and len(final["完整P解释（AI提案非新条件）"]["items"]) == 7
    assert final["本章及全书固定条件"] == [{"source_id": "FIX001", "text": "不动手"}]
    assert final[authored.KEY] == classification.VERSION
    assert "not_scope_related" in frames[0]["任务"]["范围分类边界"]
    assert "请求行为与请求所求许可" in frames[0]["任务"]["范围分类边界"]
    assert "scope_observation_ids" not in dumps(frames[0])
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert domain.queue("module", payload, story["branch_id"])["id"] == worker.task["id"]


@pytest.mark.parametrize("bad_field", ["source_checks", "item_checks", "fixed_checks"])
def test_omitted_any_source_p_or_fixed_item_is_never_accepted_after_finite_citation_repairs(
    settings, domain, story, bad_field
):
    worker, _ = module_worker(settings, domain, story)

    def change(frame, value):
        if frame["任务"]["动作"] == classification.ACTION:
            value[bad_field] = []
        return value

    frames = native(worker, transform=change)
    with pytest.raises(DomainError, match="引用|原文"):
        run(worker)
    # Identical second repair input reads the exact cache instead of paying twice.
    assert len([f for f in frames if f["任务"]["动作"] == classification.ACTION]) == 2
    assert worker.task["result"][authored.RECEIPTS]["module"]["state"] != "ready"
    assert not domain.db.all("SELECT * FROM artifacts")
    assert all("本步骤没有正文" in f["任务"]["引用修正要求"] for f in frames[3:])


@pytest.mark.parametrize(
    "fault", ["phase_missing", "classification_unknown", "item_fail", "source_fail", "input_hash"]
)
def test_saved_ready_receipt_requires_every_independent_phase_and_bound_meaning(
    settings, domain, story, fault
):
    worker, _ = module_worker(settings, domain, story)
    native(worker)
    run(worker)
    record = deepcopy(worker.task["result"][authored.RECEIPTS]["module"])
    if fault == "phase_missing":
        record["phases"].pop("preserve")
    else:
        value = record["phases"]["preserve"]["response"]
        if fault == "classification_unknown":
            value["source_checks"][0]["classification_result"] = "unknown"
        elif fault == "item_fail":
            value["item_checks"][0]["result"] = "fail"
        elif fault == "source_fail":
            value["source_checks"][0]["result"] = "fail"
        else:
            value["input_sha256"] = "0" * 64
        record["phases"]["preserve"]["response_sha256"] = authored.sha(value)
    assert not authored.receipt_valid(record, SOURCE, {worker.task["id"]})


def test_only_not_scope_classification_is_not_a_plot_waiver_and_no_reason_heuristic(settings, domain, story):
    worker, _ = module_worker(settings, domain, story)
    frames = native(worker, classification_result="unknown")
    with pytest.raises(DomainError):
        run(worker)
    final = frames[-1]["任务"]
    assert len(final["全部原话连续单元"]) == 7
    record = worker.task["result"][authored.RECEIPTS]["module"]
    assert record["state"] == "unverified"
    # A reassuring reason never overrides the typed independent unknown.
    value = record["phases"]["preserve"]["response"]
    assert value["source_checks"][0]["result"] == "pass"
    assert value["source_checks"][0]["classification_result"] == "unknown"


def test_true_scope_unknown_still_blocks_before_interpret_and_no_raw_guess(settings, domain, story):
    worker, _ = module_worker(settings, domain, story)
    frames = native(worker, observe_unknown=True)
    with pytest.raises(DomainError, match="未核实"):
        run(worker)
    assert len(frames) == 1
    record = worker.task["result"][authored.RECEIPTS]["module"]
    assert record["phases"]["observe"]["response"]["inventory"]["observations"][1]["kind"] == "unknown"
    assert not authored.core.inventory_supported(record["phases"]["observe"]["response"]["inventory"])


def test_independent_coverage_checker_anchors_each_pass_to_its_original_unit(settings, domain, story):
    worker, _ = module_worker(settings, domain, story)
    frames = native(worker)
    run(worker)
    record = worker.task["result"][authored.RECEIPTS]["module"]
    value = classification_preservation(frames[-1]["任务"])
    value["source_checks"][1]["evidence_ids"] = ["SCOPE0001"]
    assert classification.issues(value, record["source"], record["contract"], record["preservation_input"])
    value = classification_preservation(frames[-1]["任务"])
    value["item_checks"][0]["evidence_ids"] = ["FIX001"]
    assert classification.issues(value, record["source"], record["contract"], record["preservation_input"])
