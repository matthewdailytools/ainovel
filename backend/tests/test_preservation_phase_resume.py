"""Explicit preservation recovery uses real transports and preserves paid history."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import httpx
import pytest

from ainovel import authored_source_classification as classification
from ainovel import authored_source_execution as execution
from ainovel import authored_source_scope as authored
from ainovel import diagnostic_phase_recovery as recovery
from ainovel.db import DomainError
from ainovel.models import WorkflowInput
from ainovel.worker import Worker
from helpers import diagnostic_phase_resume_fixture as fixture
from helpers.authored_source_fixture import classification_preservation
from test_diagnostic_phase_resume import assert_originals, evidence, resume


def preservation_reply(*, failures=0, result="pass", malformed=False, tag="new"):
    count = 0

    def answer(task):
        nonlocal count
        count += 1
        if malformed:
            return "匿名错误根类型，修正仍须计入同一两次上限。"
        value = classification_preservation(task)
        value["summary"] = f"匿名{tag}保持返回{count}，仅工程证据。"
        if count <= failures:
            value["source_checks"][1]["evidence_ids"].append("O01")
            value["source_checks"][2]["evidence_ids"].append("O02")
        elif result != "pass":
            value["source_checks"][1]["result"] = result
        return value

    return answer


def known_preservation_failure(settings, domain, story, client, *, historical=False):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="whole_book",
        brief="匿名十二章来源保持恢复，不是文学评估。",
        chapters=12,
        plots=[
            {
                "number": number,
                "title": f"匿名第{number}章",
                "plot": fixture.SOURCE if number == 1 else f"第{number}章继续整理原有记录。",
                "fixed": ["不得向管理员披露刻度记录。"],
            }
            for number in range(1, 13)
        ],
        account="deepseek",
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id="anonymous-known-preservation-stop",
    ).model_dump(exclude_none=True)
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 202, response.text
    worker = Worker(settings)
    if historical:
        task_id = response.json()["id"]
        # A separate, already billed old maintenance outcome remains missing.
        # It predates this phase and must never be promoted to a complete step.
        old_call = worker.budget.reserve(settings.providers["deepseek"], task_id, 1, 1)
        worker.budget.settle(old_call, {"input": 1, "output": 1})
        worker.budget.metadata(old_call, "test-model", "anonymous-old-maintenance")
        with domain.db.connect(True) as tx:
            domain.db.event(
                tx,
                task_id,
                "resume_requested",
                {
                    "acknowledge_unknown": True,
                    recovery.MARK: {
                        "fix": recovery.FIX,
                        "phase": "interpret",
                        "source_key": "chapter:1",
                    },
                },
            )
    frames = fixture.native(worker)
    with (
        patch.object(classification, "detailed_issues", classification.issues, create=True),
        patch.object(fixture, "classification_preservation", preservation_reply(failures=3, tag="old")),
    ):
        assert asyncio.run(worker.run_once())
    stopped = domain.task(response.json()["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    record = stopped["result"][authored.RECEIPTS]["chapter:1"]
    assert set(record["phases"]) == {"observe", "interpret", "preserve"}
    assert record["phases"]["interpret"]["status"] == "completed"
    assert record["phases"]["preserve"]["status"] == "started"
    assert "response" not in record["phases"]["preserve"]
    before = evidence(worker, domain, stopped["id"])
    assert len(frames) == len(before["steps"]) == 5
    assert len(before["calls"]) == 5 + historical
    assert all(row["status"] == "settled" for row in before["calls"])
    return worker, stopped, frames


@pytest.fixture
def failed(settings, domain, story, client):
    return known_preservation_failure(settings, domain, story, client)


@pytest.mark.parametrize("failures, expected", [(0, 1), (1, 2)])
def test_formal_preservation_resume_preserves_paid_steps_and_only_uses_bounded_new_feedback(
    settings, domain, client, failed, failures, expected
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    original = deepcopy(stopped["result"][authored.RECEIPTS]["chapter:1"])
    response = resume(client, stopped["id"])
    assert response.status_code == 200, response.text
    worker = Worker(settings)
    frames = fixture.native(worker)
    with patch.object(fixture, "classification_preservation", preservation_reply(failures=failures)):
        assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    assert current["status"] == "needs_review", current["error"]
    record = current["result"][authored.RECEIPTS]["chapter:1"]
    assert record["state"] == "ready"
    assert record["phases"]["observe"] == original["phases"]["observe"]
    assert record["phases"]["interpret"] == original["phases"]["interpret"]
    slot = record["phases"]["preserve"]
    grant = slot[recovery.MARK]
    assert grant["marker"]["fix"] == recovery.PRESERVE_FIX
    assert grant["marker"]["phase"] == "preserve"
    assert grant["original_slot"] == original["phases"]["preserve"]
    assert grant["max_new_wire_calls"] == 2
    assert len(grant["dispatches"]) == expected
    preservation_frames = [f for f in frames if f["任务"]["动作"] == classification.ACTION]
    assert len(preservation_frames) == expected
    assert all(f["任务"]["动作"] not in {authored.ACTION, fixture.INTERPRET} for f in frames)
    assert any("source_checks" in row and "O01" in row for row in preservation_frames[0]["任务"]["定位错误"])
    assert current["payload"] == stopped["payload"]
    for key in ("plan", "repair_round", authored.SNAPSHOT, execution.SNAPSHOT):
        assert current["result"][key] == stopped["result"][key]
    assert current["result"].get("generation_epoch") == stopped["result"].get("generation_epoch")
    assert_originals(before, evidence(worker, domain, stopped["id"]))


def test_old_other_phase_marker_and_billed_missing_maintenance_do_not_replace_current_phase_proof(
    settings, domain, story, client
):
    old_worker, stopped, _ = known_preservation_failure(settings, domain, story, client, historical=True)
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = fixture.native(worker)
    assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    assert current["status"] == "needs_review", current["error"]
    slot = current["result"][authored.RECEIPTS]["chapter:1"]["phases"]["preserve"]
    assert slot[recovery.MARK]["marker"]["fix"] == recovery.PRESERVE_FIX
    assert len([f for f in frames if f["任务"]["动作"] == classification.ACTION]) == 1
    assert_originals(before, evidence(worker, domain, stopped["id"]))


@pytest.mark.parametrize("result", ["unknown", "fail"])
def test_valid_repaired_citations_do_not_turn_semantic_unknown_or_fail_into_ready(
    settings, domain, client, failed, result
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = fixture.native(worker)
    with patch.object(fixture, "classification_preservation", preservation_reply(result=result)):
        assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    record = current["result"][authored.RECEIPTS]["chapter:1"]
    assert record["state"] == "unverified"
    assert record["phases"]["preserve"]["response"]["source_checks"][1]["result"] == result
    assert [f["任务"]["动作"] for f in frames] == [classification.ACTION]
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (stopped["id"],))
    assert_originals(before, evidence(worker, domain, stopped["id"]))


def test_two_invalid_new_returns_and_another_resume_do_not_reset_the_phase_allowance(
    settings, domain, client, failed
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = fixture.native(worker)
    with patch.object(fixture, "classification_preservation", preservation_reply(failures=99)):
        assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    assert current["status"] == "blocked_runtime"
    assert [f["任务"]["动作"] for f in frames] == [classification.ACTION] * 2
    slot = current["result"][authored.RECEIPTS]["chapter:1"]["phases"]["preserve"]
    grant = deepcopy(slot[recovery.MARK])
    assert len(grant["dispatches"]) == 2
    after = evidence(worker, domain, stopped["id"])
    assert len(after["calls"]) == len(before["calls"]) + 2
    assert resume(client, stopped["id"]).status_code == 200
    second = Worker(settings)
    second_frames = fixture.native(second)
    assert asyncio.run(second.run_once())
    assert second_frames == []
    slot = domain.task(stopped["id"])["result"][authored.RECEIPTS]["chapter:1"]["phases"]["preserve"]
    assert slot[recovery.MARK] == grant
    assert evidence(second, domain, stopped["id"])["calls"] == after["calls"]
    assert_originals(before, evidence(second, domain, stopped["id"]))


def test_nested_json_repairs_share_the_preservation_two_call_limit(settings, domain, client, failed):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = fixture.native(worker)
    with patch.object(fixture, "classification_preservation", preservation_reply(malformed=True)):
        assert asyncio.run(worker.run_once())
    assert [f["任务"]["动作"] for f in frames] == [classification.ACTION, "仅修正结构化产物格式"]
    current = domain.task(stopped["id"])
    assert current["status"] == "blocked_runtime"
    assert current["result"][authored.RECEIPTS]["chapter:1"]["state"] != "ready"
    assert len(evidence(worker, domain, stopped["id"])["calls"]) == len(before["calls"]) + 2
    assert_originals(before, evidence(worker, domain, stopped["id"]))


@pytest.mark.parametrize("missing", ["step", "metadata", "unknown_zero_reserve"])
def test_missing_or_unknown_preservation_witness_never_grants_a_new_call(
    settings, domain, client, failed, missing
):
    old_worker, stopped, _ = failed
    old_step = next(
        row
        for row in domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (stopped["id"],))
        if "source_checks" in json.loads(json.loads(row["result"])["text"])
    )
    call_id = json.loads(old_step["result"])["call_id"]
    if missing == "step":
        with domain.db.connect(True) as tx:
            tx.execute(
                "DELETE FROM task_steps WHERE task_id=? AND fingerprint=?",
                (stopped["id"], old_step["fingerprint"]),
            )
    else:
        with old_worker.budget.db(True) as usage:
            if missing == "metadata":
                usage.execute("DELETE FROM call_metadata WHERE call_id=?", (call_id,))
            else:
                usage.execute(
                    "UPDATE calls SET status='unknown',charged=NULL,reserved=0 WHERE id=?", (call_id,)
                )
    before = evidence(old_worker, domain, stopped["id"])
    response = resume(client, stopped["id"])
    assert response.status_code in {200, 409}
    worker = Worker(settings)
    frames = fixture.native(worker)
    if response.status_code == 200:
        assert asyncio.run(worker.run_once())
    assert frames == []
    after = evidence(worker, domain, stopped["id"])
    for key in ("steps", "calls", "metadata", "chapters"):
        assert after[key] == before[key]


@pytest.mark.parametrize("changed", ["source", "old_fee"])
def test_changed_frozen_source_or_original_fee_after_resume_never_dispatches(
    settings, domain, client, failed, changed
):
    old_worker, stopped, _ = failed
    assert resume(client, stopped["id"]).status_code == 200
    if changed == "source":
        payload = deepcopy(stopped["payload"])
        payload["plots"][0]["plot"] += "新增未授权来源。"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload), stopped["id"]))
    else:
        with old_worker.budget.db(True) as usage:
            row = usage.execute("SELECT id FROM calls WHERE task_id=? LIMIT 1", (stopped["id"],)).fetchone()
            usage.execute("UPDATE calls SET charged=charged+1 WHERE id=?", (row["id"],))
    before = evidence(old_worker, domain, stopped["id"])
    worker = Worker(settings)
    frames = fixture.native(worker)
    assert asyncio.run(worker.run_once())
    assert frames == []
    after = evidence(worker, domain, stopped["id"])
    for key in ("steps", "calls", "metadata", "chapters"):
        assert after[key] == before[key]


def test_worker_exit_after_persisting_dispatch_allowance_does_not_regrant_after_restart(
    settings, domain, client, failed
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    permit = recovery.permit_dispatch

    def interrupt(worker, fingerprint, instruction):
        assert permit(worker, fingerprint, instruction)
        raise DomainError("interrupted", "匿名进程在持久占额后退出。", 409)

    worker = Worker(settings)
    frames = fixture.native(worker)
    with patch.object(recovery, "permit_dispatch", interrupt):
        assert asyncio.run(worker.run_once())
    assert frames == []
    slot = domain.task(stopped["id"])["result"][authored.RECEIPTS]["chapter:1"]["phases"]["preserve"]
    grant = deepcopy(slot[recovery.MARK])
    assert len(grant["dispatches"]) == 1
    assert resume(client, stopped["id"]).status_code == 200
    second = Worker(settings)
    second_frames = fixture.native(second)
    assert asyncio.run(second.run_once())
    assert second_frames == []
    slot = domain.task(stopped["id"])["result"][authored.RECEIPTS]["chapter:1"]["phases"]["preserve"]
    assert slot[recovery.MARK] == grant
    assert_originals(before, evidence(second, domain, stopped["id"]))


def test_unknown_new_preservation_result_is_not_resent_on_acknowledged_resume(
    settings, domain, client, failed
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = fixture.native(worker)
    with patch.object(
        fixture, "classification_preservation", side_effect=httpx.ReadTimeout("anonymous unknown")
    ):
        assert asyncio.run(worker.run_once())
    after = evidence(worker, domain, stopped["id"])
    new_calls = [row for row in after["calls"] if row["id"] not in {r["id"] for r in before["calls"]}]
    assert len(frames) == len(new_calls) == 1
    assert new_calls[0]["charged"] is None
    response = resume(client, stopped["id"])
    assert response.status_code in {200, 409}
    second = Worker(settings)
    second_frames = fixture.native(second)
    if response.status_code == 200:
        assert asyncio.run(second.run_once())
    assert second_frames == []
    assert evidence(second, domain, stopped["id"])["calls"] == after["calls"]
    assert_originals(before, evidence(second, domain, stopped["id"]))
