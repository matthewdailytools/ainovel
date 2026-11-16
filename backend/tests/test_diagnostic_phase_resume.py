"""Formal resume, real Worker/ModelClient, and anonymous isolated wire evidence."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel import authored_source_execution as execution
from ainovel import authored_source_scope as authored
from ainovel.evidence import source_units
from ainovel.worker import Worker
from helpers.diagnostic_phase_resume_fixture import (
    INTERPRET,
    SOURCE,
    interpretation,
    known_interpret_failure,
    native,
    observation,
)

RECOVERY = "diagnostic_feedback_recovery"


def test_diagnostic_values_only_change_item_label_and_never_validate_bad_scope():
    inventory = observation({"可引用原文": authored.core.units(SOURCE), "input_sha256": "a" * 64})
    value = interpretation({"可引用原文": source_units(SOURCE, "AUTHOR")}, invalid=True, attempt=1)
    wrapper = {"inventory": inventory}
    legacy = authored.binding_issues(deepcopy(value), wrapper, legacy_diagnostic_labels=True)
    current = authored.binding_issues(deepcopy(value), wrapper)
    assert legacy and current
    assert legacy == [row.replace("P03", "P01", 1) for row in current]
    assert all(row.startswith("P03") for row in current)
    valid = interpretation({"可引用原文": source_units(SOURCE, "AUTHOR")}, invalid=False, attempt=1)
    assert authored.binding_issues(valid, wrapper) == []


@pytest.fixture
def failed(settings, domain, story, client):
    return known_interpret_failure(settings, domain, story, client=client)


def evidence(worker, domain, task_id):
    with worker.budget.db() as usage:
        calls = [
            dict(row) for row in usage.execute("SELECT * FROM calls WHERE task_id=? ORDER BY id", (task_id,))
        ]
        metadata = [
            dict(row)
            for row in usage.execute(
                "SELECT m.* FROM call_metadata m JOIN calls c ON c.id=m.call_id WHERE c.task_id=? ORDER BY m.call_id",
                (task_id,),
            )
        ]
    return {
        "steps": domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (task_id,)),
        "events": domain.db.all("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)),
        "calls": calls,
        "metadata": metadata,
        "chapters": domain.db.all("SELECT * FROM chapters ORDER BY id"),
    }


def assert_originals(before, after):
    for key, primary in (
        ("steps", "fingerprint"),
        ("events", "id"),
        ("calls", "id"),
        ("metadata", "call_id"),
    ):
        indexed = {row[primary]: row for row in after[key]}
        assert all(indexed.get(row[primary]) == row for row in before[key]), key
    assert after["chapters"] == before["chapters"]


def resume(client, task_id, **changes):
    return client.post(f"/api/tasks/{task_id}/resume", json={"acknowledge_unknown": True, **changes})


def test_fixture_has_four_real_returned_settled_calls_and_only_legacy_label_fault(domain, failed):
    worker, task, frames = failed
    old = evidence(worker, domain, task["id"])
    assert len(old["calls"]) == len(old["metadata"]) == len(old["steps"]) == 4
    assert all(row["status"] == "settled" and row["charged"] is not None for row in old["calls"])
    assert len(task["result"]["plan"]["chapters"]) == len(task["payload"]["plots"]) == 12
    assert task["result"]["plan"]["chapters"] == task["payload"]["plots"]
    assert [frame["任务"]["动作"] for frame in frames] == [authored.ACTION, INTERPRET, INTERPRET, INTERPRET]
    repairs = [json.loads(row["payload"]) for row in old["events"] if row["kind"] == "citation_repair"]
    assert len(repairs) == 3 and all(
        any(issue.startswith("P01的only") for issue in row["issues"]) for row in repairs
    )
    record = task["result"][authored.RECEIPTS]["chapter:1"]
    assert record["phases"]["observe"]["status"] == "completed"
    assert record["phases"]["interpret"]["status"] == "started"
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (task["id"],))


@pytest.mark.parametrize("new_failures, expected_interpret_wires", [(0, 1), (1, 2)])
def test_formal_resume_allows_only_bounded_new_interpret_then_normal_preservation(
    settings, domain, client, failed, new_failures, expected_interpret_wires
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    old_slot = deepcopy(stopped["result"][authored.RECEIPTS]["chapter:1"]["phases"]["interpret"])
    response = resume(client, stopped["id"])
    assert response.status_code == 200, response.text
    queued = response.json()
    assert queued["id"] == stopped["id"] and queued["payload"] == stopped["payload"]
    assert queued["result"]["plan"] == stopped["result"]["plan"]
    requested = domain.db.all(
        "SELECT * FROM task_events WHERE task_id=? AND kind='resume_requested' ORDER BY id", (stopped["id"],)
    )
    assert len(requested) == 1 and isinstance(json.loads(requested[0]["payload"]).get(RECOVERY), dict)
    assert not any(
        row["kind"] == "step_dispatched" and row["id"] > requested[0]["id"]
        for row in evidence(old_worker, domain, stopped["id"])["events"]
    )
    worker = Worker(settings)
    frames = native(worker, failures=new_failures)
    assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    assert current["status"] == "needs_review", current["error"]
    assert current["payload"] == stopped["payload"]
    for key in ("plan", "repair_round", authored.SNAPSHOT, execution.SNAPSHOT):
        assert current["result"][key] == stopped["result"][key]
    assert current["result"].get("generation_epoch") == stopped["result"].get("generation_epoch")
    record = current["result"][authored.RECEIPTS]["chapter:1"]
    assert record["state"] == "ready"
    assert (
        record["phases"]["observe"] == stopped["result"][authored.RECEIPTS]["chapter:1"]["phases"]["observe"]
    )
    assert record["phases"]["interpret"]["status"] == record["phases"]["preserve"]["status"] == "completed"
    recovery = record["phases"]["interpret"][RECOVERY]
    assert recovery["original_slot"] == old_slot
    assert recovery["resume_event_id"] == requested[0]["id"]
    assert recovery["attempt"] == current["attempt"]
    assert recovery["max_new_wire_calls"] == 2
    assert len(recovery["dispatches"]) == expected_interpret_wires
    assert len([frame for frame in frames if frame["任务"]["动作"] == INTERPRET]) == expected_interpret_wires
    assert all(frame["任务"]["动作"] != authored.ACTION for frame in frames)
    assert any(issue.startswith("P03的only") for issue in frames[0]["任务"]["定位错误"])
    assert_originals(before, evidence(worker, domain, stopped["id"]))
    candidate = domain.artifact(current["result"]["waiting_artifact"])
    assert candidate["status"] == "candidate"
    assert not domain.db.all("SELECT * FROM receipts WHERE artifact_id=?", (candidate["id"],))


def test_two_invalid_new_returns_do_not_authorize_a_third_wire(settings, domain, client, failed):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = native(worker, failures=99)
    assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    assert current["status"] == "blocked_runtime"
    assert len(frames) == 2 and all(frame["任务"]["动作"] == INTERPRET for frame in frames)
    assert current["result"][authored.RECEIPTS]["chapter:1"]["state"] != "ready"
    assert len(evidence(worker, domain, stopped["id"])["calls"]) == len(before["calls"]) + 2
    assert_originals(before, evidence(worker, domain, stopped["id"]))


def test_nested_structure_repairs_share_the_same_two_wire_limit(settings, domain, client, failed):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = native(worker, malformed_interpret=True)
    assert asyncio.run(worker.run_once())
    assert [frame["任务"]["动作"] for frame in frames] == [INTERPRET, "仅修正结构化产物格式"]
    current = domain.task(stopped["id"])
    assert current["status"] == "blocked_runtime"
    assert current["result"][authored.RECEIPTS]["chapter:1"]["state"] != "ready"
    after = evidence(worker, domain, stopped["id"])
    assert len(after["calls"]) == len(before["calls"]) + 2
    assert_originals(before, after)


@pytest.mark.parametrize("missing", ["step", "metadata", "unknown_zero_reserve"])
def test_missing_or_unknown_old_witness_never_dispatches_new_wire(settings, domain, client, failed, missing):
    old_worker, stopped, _ = failed
    interpretation = next(
        row
        for row in domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (stopped["id"],))
        if "items" in json.loads(json.loads(row["result"])["text"])
    )
    call_id = json.loads(interpretation["result"])["call_id"]
    if missing == "step":
        with domain.db.connect(True) as tx:
            tx.execute(
                "DELETE FROM task_steps WHERE task_id=? AND fingerprint=?",
                (stopped["id"], interpretation["fingerprint"]),
            )
    else:
        with old_worker.budget.db() as usage:
            if missing == "metadata":
                usage.execute("DELETE FROM call_metadata WHERE call_id=?", (call_id,))
            else:
                usage.execute(
                    "UPDATE calls SET status='unknown',charged=NULL,reserved=0 WHERE id=?", (call_id,)
                )
    baseline = evidence(old_worker, domain, stopped["id"])
    response = resume(client, stopped["id"])
    assert response.status_code in {200, 409}
    worker = Worker(settings)
    frames = native(worker)
    if response.status_code == 200:
        assert asyncio.run(worker.run_once())
    assert frames == []
    after = evidence(worker, domain, stopped["id"])
    assert after["calls"] == baseline["calls"]
    assert after["steps"] == baseline["steps"]
    assert after["metadata"] == baseline["metadata"]
    assert after["chapters"] == baseline["chapters"]


def test_unknown_new_result_is_not_repeated_after_another_acknowledged_resume(
    settings, domain, client, failed
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = native(worker, timeout_interpret=True)
    assert asyncio.run(worker.run_once())
    after_timeout = evidence(worker, domain, stopped["id"])
    new_calls = [
        row for row in after_timeout["calls"] if row["id"] not in {row["id"] for row in before["calls"]}
    ]
    assert len(frames) == len(new_calls) == 1
    assert new_calls[0]["charged"] is None
    response = resume(client, stopped["id"])
    assert response.status_code in {200, 409}
    second = Worker(settings)
    second_frames = native(second)
    if response.status_code == 200:
        assert asyncio.run(second.run_once())
    assert second_frames == []
    assert evidence(second, domain, stopped["id"])["calls"] == after_timeout["calls"]
    assert_originals(before, evidence(second, domain, stopped["id"]))


def test_resume_requires_ack_and_queued_double_submit_has_one_marker(domain, client, failed):
    worker, stopped, _ = failed
    before = evidence(worker, domain, stopped["id"])
    assert client.post(f"/api/tasks/{stopped['id']}/resume", json={}).status_code == 409
    assert resume(client, stopped["id"]).status_code == 200
    assert resume(client, stopped["id"]).status_code == 409
    events = domain.db.all(
        "SELECT * FROM task_events WHERE task_id=? AND kind='resume_requested'", (stopped["id"],)
    )
    assert len(events) == 1
    assert evidence(worker, domain, stopped["id"])["calls"] == before["calls"]
