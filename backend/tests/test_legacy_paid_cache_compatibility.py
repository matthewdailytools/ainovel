"""Paid old/new feedback cache compatibility; anonymous ASGI and MockTransport."""

import asyncio
from contextlib import ExitStack
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from ainovel import authored_source_scope as authored
from ainovel.models import WorkflowInput
from ainovel.worker import Worker
from helpers import diagnostic_phase_resume_fixture as fixture


def evidence(worker, domain, task):
    with worker.budget.db() as usage:
        calls = [
            dict(row) for row in usage.execute("SELECT * FROM calls WHERE task_id=? ORDER BY id", (task,))
        ]
    return {
        "steps": domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (task,)),
        "calls": calls,
    }


@pytest.mark.parametrize("history", ["legacy", "current", "absent", "unknown"])
def test_paid_cache_current_first_then_legacy_only_with_no_grant(
    settings, domain, story, client, history, tmp_path
):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="whole_book",
        brief="匿名旧有效缓存中断恢复兼容专项。",
        chapters=12,
        plots=[
            {
                "number": n,
                "title": f"匿名第{n}章",
                "plot": fixture.SOURCE if n == 1 else "继续整理原有记录，不新增披露。",
                "fixed": ["不得向管理员披露刻度记录。"],
            }
            for n in range(1, 13)
        ],
        account="deepseek",
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id="anonymous-paid-cache-" + history,
    ).model_dump(exclude_none=True)
    queued = client.post("/api/workflows", json=payload)
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["id"]
    worker = Worker(settings)
    old_frames = fixture.native(worker, failures=1, response_tag=history)
    original_grounded, original_call = Worker.grounded, Worker.call
    current_binding = authored.binding_issues

    def legacy_labels(value, inventory, **_kwargs):
        return current_binding(value, inventory, legacy_diagnostic_labels=True)

    async def interrupt_after_paid_valid(self, instruction, *args, **kwargs):
        value = await original_grounded(self, instruction, *args, **kwargs)
        if instruction.get("动作") == fixture.INTERPRET:
            raise RuntimeError("Anonymous stop after paid valid interpretation before phase normalization")
        return value

    async def interrupt_before_repair(self, instruction, *args, **kwargs):
        if instruction.get("动作") == fixture.INTERPRET and "定位错误" in instruction:
            raise RuntimeError("Anonymous stop before any repair dispatch; both repair cache keys absent")
        return await original_call(self, instruction, *args, **kwargs)

    with ExitStack() as stack:
        if history != "current":
            stack.enter_context(patch.object(authored, "binding_issues", legacy_labels))
        stack.enter_context(
            patch.object(Worker, "call", interrupt_before_repair)
            if history == "absent"
            else patch.object(Worker, "grounded", interrupt_after_paid_valid)
        )
        assert asyncio.run(worker.run_once())
    stopped = domain.task(task_id)
    old_slot = stopped["result"][authored.RECEIPTS]["chapter:1"]["phases"]["interpret"]
    assert stopped["status"] == "blocked_runtime" and old_slot["status"] == "started"
    assert "response" not in old_slot
    before = evidence(worker, domain, task_id)
    expected_old_calls = 2 if history == "absent" else 3
    assert len(old_frames) == len(before["steps"]) == len(before["calls"]) == expected_old_calls
    assert all(row["status"] == "settled" and row["charged"] is not None for row in before["calls"])
    repairs = domain.db.all(
        "SELECT * FROM task_events WHERE task_id=? AND kind='citation_repair' ORDER BY id", (task_id,)
    )
    expected_prefix = "P03" if history == "current" else "P01"
    assert len(repairs) == 1
    assert any(
        issue.startswith(expected_prefix + "的only") for issue in json.loads(repairs[0]["payload"])["issues"]
    )
    if history == "unknown":
        with worker.budget.db(True) as usage:
            row = dict(
                usage.execute(
                    "SELECT * FROM calls WHERE task_id=? ORDER BY id LIMIT 1", (task_id,)
                ).fetchone()
            )
            row.update(id="anonymous-paid-cache-unknown", status="unknown", charged=None, reserved=0)
            names = list(row)
            usage.execute(
                f"INSERT INTO calls ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                [row[key] for key in names],
            )
        before = evidence(worker, domain, task_id)
    response = client.post(f"/api/tasks/{task_id}/resume", json={"acknowledge_unknown": True})
    assert response.status_code == 200, response.text
    requested = domain.db.all(
        "SELECT * FROM task_events WHERE task_id=? AND kind='resume_requested' ORDER BY id", (task_id,)
    )
    assert len(requested) == 1 and "diagnostic_feedback_recovery" not in json.loads(requested[0]["payload"])
    resumed = Worker(settings)
    new_frames = fixture.native(resumed)
    cache_queries = []

    async def trace_cache_query(self, instruction, *args, **kwargs):
        if instruction.get("动作") == fixture.INTERPRET and "定位错误" in instruction:
            cache_queries.append(list(instruction["定位错误"]))
        return await original_call(self, instruction, *args, **kwargs)

    with patch.object(Worker, "call", trace_cache_query):
        assert asyncio.run(resumed.run_once())
    current = domain.task(task_id)
    after = evidence(resumed, domain, task_id)
    record = current["result"][authored.RECEIPTS]["chapter:1"]
    interpret_wires = [row for row in new_frames if row["任务"]["动作"] == fixture.INTERPRET]
    summary = {
        "history": history,
        "old_call_count": expected_old_calls,
        "grant_created": "diagnostic_feedback_recovery" in record["phases"]["interpret"],
        "new_interpret_wires": len(interpret_wires),
        "new_all_wires": len(new_frames),
        "cache_query_issue_prefixes": [[value[:3] for value in row] for row in cache_queries],
        "task_status": current["status"],
        "error": current["error"],
        "old_steps_preserved": all(row in after["steps"] for row in before["steps"]),
        "old_fees_preserved": all(row in after["calls"] for row in before["calls"]),
        "actual_api_database_native_model_actions": 0,
    }
    (tmp_path / ("result-" + history + ".json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    assert not summary["grant_created"] and interpret_wires == []
    assert summary["old_steps_preserved"] and summary["old_fees_preserved"]
    assert current["payload"] == stopped["payload"] and current["result"]["plan"] == deepcopy(
        stopped["result"]["plan"]
    )
    current_issues = [
        issue
        for row in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair' AND id>?",
            (task_id, requested[0]["id"]),
        )
        for issue in json.loads(row["payload"])["issues"]
    ]
    assert any(issue.startswith("P03") for issue in current_issues)
    assert all(not issue.startswith("P01") for issue in current_issues)
    if history in {"absent", "unknown"}:
        assert new_frames == [] and current["status"] == "blocked_runtime"
        assert record["phases"]["interpret"]["status"] == "started"
        if history == "unknown":
            assert all(not issue.startswith("P01") for row in cache_queries for issue in row)
    else:
        assert current["status"] == "needs_review", current["error"]
        assert record["phases"]["interpret"]["status"] == "completed" and record["state"] == "ready"
        assert record["phases"]["preserve"]["status"] == "completed"
        assert any(row["任务"]["动作"] == fixture.classification.ACTION for row in new_frames)
        if history == "current":
            assert all(not issue.startswith("P01") for row in cache_queries for issue in row)
        else:
            assert any(issue.startswith("P01") for row in cache_queries for issue in row)
