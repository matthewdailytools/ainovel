"""Actual failed HTTP bodies remain auditable before a bounded repair can fail."""

import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel.openai_schema import VERSION
from ainovel.models import ModuleInput
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from test_budget_provider import stream
from test_openai_schema import Answer, completed, worker_for


def events(domain, kind):
    return [
        json.loads(row["payload"])
        for row in domain.db.all("SELECT payload FROM task_events WHERE kind=? ORDER BY id", (kind,))
    ]


def incomplete():
    return httpx.Response(
        200,
        content=stream(
            [
                {"type": "response.output_text.delta", "delta": '{"answer":"保留的片段😀𠮷'},
                {
                    "type": "response.incomplete",
                    "response": {
                        "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"},
                        "usage": {"input_tokens": 21, "output_tokens": 8192},
                    },
                },
            ]
        ),
    )


def assert_actual_trace(payload, body, ledger, task_id):
    assert payload["sent"] is True
    metadata = payload["request_metadata"]
    assert set(metadata) == {"wire_schema_version", "wire_schema_sha256", "request_sha256", "request_bytes"}
    assert metadata["wire_schema_version"] == VERSION
    assert metadata["request_sha256"] == hashlib.sha256(body).hexdigest()
    assert metadata["request_bytes"] == len(body)
    schema = json.loads(body)["text"]["format"]["schema"]
    expected = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    assert metadata["wire_schema_sha256"] == hashlib.sha256(expected).hexdigest()
    call = next(call for call in ledger.report()[0]["calls"] if call["id"] == payload["call_id"])
    assert call["task_id"] == task_id and call["account"] == "openai_1"


def test_partial_actual_body_survives_automatic_repair_pre_reservation_budget_failure(
    settings, domain, story
):
    captured = []

    def handler(request):
        captured.append(request.content)
        return incomplete()

    ledger = Budget(settings)
    worker = worker_for(settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    worker.task["payload"]["budget_limit"] = 0.1
    with pytest.raises(DomainError) as caught:
        asyncio.run(worker.call({"动作": "离线验证部分输出及有限格式修复"}, Answer))
    assert caught.value.code == "budget"
    assert len(captured) == 1
    partial = events(domain, "partial_retained")
    assert len(partial) == 1 and partial[0]["text"] == '{"answer":"保留的片段😀𠮷'
    assert_actual_trace(partial[0], captured[0], ledger, worker.task["id"])
    assert len(events(domain, "step_dispatched")) == 2  # Second is a plan, not another HTTP call.
    assert not events(domain, "request_failed") and not events(domain, "step_completed")
    assert all("call_id" not in event for event in events(domain, "step_dispatched"))
    assert not domain.db.all("SELECT * FROM task_steps")
    report = ledger.report()[0]
    assert len(report["calls"]) == 1 and report["settled"] > 0 and report["reserved_unknown"] == 0


def test_partial_trace_is_committed_before_successful_automatic_repair(settings, domain, story):
    captured = []

    def handler(request):
        captured.append(request.content)
        return incomplete() if len(captured) == 1 else completed()

    ledger = Budget(settings)
    worker = worker_for(settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    assert asyncio.run(worker.call({"动作": "离线验证已知截断的有限修复"}, Answer)) == {"answer": "完成"}
    assert len(captured) == 2
    retained = events(domain, "partial_retained")
    assert len(retained) == 1
    assert_actual_trace(retained[0], captured[0], ledger, worker.task["id"])
    rows = domain.db.all("SELECT id,kind FROM task_events ORDER BY id")
    partial_index = next(i for i, row in enumerate(rows) if row["kind"] == "partial_retained")
    second_dispatch = [i for i, row in enumerate(rows) if row["kind"] == "step_dispatched"][1]
    assert partial_index < second_dispatch
    assert len(domain.db.all("SELECT * FROM task_steps")) == 1


@pytest.mark.parametrize("failure", ["timeout", "network", "http400"])
def test_no_partial_failure_persists_real_request_summary_without_auto_retry(
    settings, domain, story, failure
):
    captured = []

    def handler(request):
        captured.append(request.content)
        if failure == "timeout":
            raise httpx.ReadTimeout("not logged")
        if failure == "network":
            raise httpx.ConnectError("not logged")
        return httpx.Response(400, json={"error": {"message": "schema rejection"}})

    ledger = Budget(settings)
    worker = worker_for(settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    with pytest.raises(DomainError):
        asyncio.run(worker.call({"动作": "只发送一次的离线失败验证"}, Answer))
    assert len(captured) == 1
    failed = events(domain, "request_failed")
    assert len(failed) == 1 and "text" not in failed[0]
    assert_actual_trace(failed[0], captured[0], ledger, worker.task["id"])
    assert not events(domain, "partial_retained") and not domain.db.all("SELECT * FROM task_steps")
    assert (ledger.report()[0]["reserved_unknown"] > 0) is (failure != "http400")
    if failure == "http400":
        assert ledger.report()[0]["settled"] == 0


@pytest.mark.parametrize("late", ["attempt", "cancel", "status", "lease"])
@pytest.mark.parametrize("with_partial", [False, True])
def test_stopped_attempt_preserves_only_same_attempt_received_text_without_live_trace(
    settings, domain, story, late, with_partial
):
    captured = []
    stopped = []
    original = domain.chapter(story["chapter_id"])
    worker = None

    def stop():
        assignments = {
            "attempt": "attempt='new-attempt'",
            "cancel": "cancel=1",
            "status": "status='blocked_runtime'",
            "lease": "lease_until=0",
        }
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET " + assignments[late] + " WHERE id=?", (worker.task["id"],))
        stopped.append(domain.task(worker.task["id"]))

    class LatePartial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield stream([{"type": "response.output_text.delta", "delta": "旧尝试已读到的片段"}])
            stop()
            raise httpx.ReadTimeout("late response after partial")

    def handler(request):
        captured.append(request.content)
        if with_partial:
            return httpx.Response(200, stream=LatePartial())
        stop()
        raise httpx.ReadTimeout("late response")

    ledger = Budget(settings)
    worker = worker_for(settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    with pytest.raises(DomainError) as caught:
        asyncio.run(worker.call({"动作": "旧尝试晚返回核对"}, Answer))
    assert bool(caught.value.partial_text) is with_partial
    assert len(captured) == 1
    retained = events(domain, "partial_retained")
    if with_partial and late != "attempt":
        assert len(retained) == 1
        assert set(retained[0]) == {"text", "fingerprint"}
        assert retained[0]["text"] == "旧尝试已读到的片段"
    else:
        assert not retained
    assert not events(domain, "request_failed")
    assert not {"call_id", "sent", "request_metadata"} & caught.value.provider_details.keys()
    assert domain.task(worker.task["id"]) == stopped[0]
    assert domain.chapter(story["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM artifacts")
    assert not domain.db.all("SELECT * FROM task_steps")
    assert ledger.report()[0]["reserved_unknown"] > 0


def test_pre_reservation_budget_failure_does_not_invent_call_or_sent(settings, domain, story):
    ledger = Budget(settings)

    def forbidden(request):
        pytest.fail("budget rejection must not send HTTP")

    worker = worker_for(
        settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(forbidden))
    )
    worker.task["payload"]["budget_limit"] = 0.000001
    with pytest.raises(DomainError, match="本次任务预算不足"):
        asyncio.run(worker.call({"动作": "预算前拒绝不伪造调用"}, Answer))
    assert ledger.report()[0]["calls"] == []
    assert not events(domain, "partial_retained") and not events(domain, "request_failed")
    assert "call_id" not in events(domain, "step_dispatched")[0]


def test_pre_http_cancel_trace_is_explicitly_not_sent_or_charged(settings, domain, story, monkeypatch):
    ledger = Budget(settings)

    def forbidden(request):
        pytest.fail("cancelled before HTTP")

    worker = worker_for(
        settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(forbidden))
    )
    checks = []

    def cancel_before_http():
        checks.append(True)
        return len(checks) > 1

    monkeypatch.setattr(worker, "cancelled", cancel_before_http)
    with pytest.raises(DomainError, match="发送前已取消"):
        asyncio.run(worker.call({"动作": "本地发送前取消核对"}, Answer))
    failed = events(domain, "request_failed")
    assert len(failed) == 1 and failed[0]["sent"] is False
    row = ledger.report()[0]["calls"][0]
    assert failed[0]["call_id"] == row["id"] and row["charged"] == 0
    assert ledger.report()[0]["reserved_unknown"] == 0


@pytest.mark.parametrize("cancelled", [False, True])
def test_legacy_fake_without_metadata_keeps_original_partial_shape(settings, domain, story, cancelled):
    class Offline:
        async def generate(self, *args, **kwargs):
            if cancelled:
                with domain.db.connect(True) as tx:
                    tx.execute("UPDATE tasks SET cancel=1 WHERE id=?", (worker.task["id"],))
            error = DomainError("cancelled" if cancelled else "incomplete", "仅离线错误")
            error.partial_text = "旧部分输出"
            raise error

    worker = worker_for(settings, domain, story, Offline())
    with pytest.raises(DomainError):
        asyncio.run(worker.call({"动作": "旧替身兼容"}, Answer))
    retained = events(domain, "partial_retained")
    assert len(retained) == 1 and set(retained[0]) == {"text", "fingerprint"}
    assert not events(domain, "request_failed")


def test_cancelled_real_adapter_keeps_history_without_trace_bypassing_outer_stopped(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "只核对当前保存稿，原文不得改变。")
    task = domain.queue(
        "module",
        ModuleInput(
            novel_id=story["id"],
            branch_id=story["branch_id"],
            chapter_id=chapter["id"],
            base_revision=chapter["revision"],
            account="openai_1",
            action="memory.extract",
            instruction="离线验证取消后历史与正式结果的边界",
        ).model_dump(),
    )
    captured = []

    class CancelStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield stream([{"type": "response.output_text.delta", "delta": '{"facts":['}])
            with domain.db.connect(True) as tx:
                tx.execute("UPDATE tasks SET cancel=1 WHERE id=?", (task["id"],))
            yield stream([{"type": "response.output_text.delta", "delta": "不会被读取的晚返回"}])

    def handler(request):
        captured.append(request.content)
        return httpx.Response(200, stream=CancelStream())

    ledger = Budget(settings)
    worker = Worker(settings, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    assert asyncio.run(worker.run_once())
    assert len(captured) == 1 and domain.task(task["id"])["status"] == "cancelled"
    retained = events(domain, "partial_retained")
    assert len(retained) == 1 and set(retained[0]) == {"text", "fingerprint"}
    assert retained[0]["text"] == '{"facts":['
    stopped = events(domain, "stopped")
    assert len(stopped) == 1 and stopped[0]["code"] == "cancelled"
    assert stopped[0]["provider_details"] == {}
    assert not events(domain, "request_failed") and not domain.db.all("SELECT * FROM task_steps")
    assert not domain.db.all("SELECT * FROM artifacts") and not domain.db.all("SELECT * FROM facts")
    assert domain.chapter(chapter["id"]) == chapter
    assert ledger.report()[0]["reserved_unknown"] > 0


@pytest.mark.parametrize(
    "damage",
    [
        "extras",
        "bad_hash",
        "boolean_bytes",
        "foreign_task",
        "foreign_account",
        "missing_call",
        "bad_sent",
        "raw_details",
    ],
)
def test_untrusted_metadata_cannot_log_credentials_or_invent_ledger_joins(settings, domain, story, damage):
    secret = settings.providers["openai_1"].key
    domain.queue(
        "module",
        ModuleInput(
            novel_id=story["id"],
            branch_id=story["branch_id"],
            account="openai_1",
            action="writing.advise",
            instruction="离线敏感信息日志验证",
        ).model_dump(),
    )
    ledger = Budget(settings)
    calls = []

    class Malicious:
        async def generate(self, account, task_id, *args, **kwargs):
            calls.append(task_id)
            call_id = ledger.reserve(
                settings.providers["openai_2" if damage == "foreign_account" else account],
                "another-task" if damage == "foreign_task" else task_id,
                1,
                1,
            )
            raw = {
                "wire_schema_version": VERSION,
                "wire_schema_sha256": "a" * 64,
                "request_sha256": "b" * 64,
                "request_bytes": 10,
                "schema": {"key": secret},
                "prompt": secret,
                "config": secret,
            }
            details = {
                "request_metadata": raw,
                "call_id": call_id,
                "sent": True,
                "key": secret,
                "raw_schema": secret,
                "provider_code": secret,
            }
            if damage == "bad_hash":
                raw["request_sha256"] = secret
            elif damage == "boolean_bytes":
                raw["request_bytes"] = True
            elif damage == "missing_call":
                details["call_id"] = "0" * 32
            elif damage == "bad_sent":
                details["sent"] = secret
            elif damage == "raw_details":
                details = secret
            error = DomainError("provider_error", "模拟失败，不包含敏感数据")
            error.provider_details = deepcopy(details)
            raise error

    worker = Worker(settings, Malicious())
    assert asyncio.run(worker.run_once())
    assert len(calls) == 1
    logs = domain.db.all("SELECT kind,payload FROM task_events")
    assert secret not in json.dumps(logs, ensure_ascii=False)
    failed = events(domain, "request_failed")
    if damage == "extras":
        assert len(failed) == 1
        assert set(failed[0]) == {"fingerprint", "call_id", "sent", "request_metadata"}
        assert set(failed[0]["request_metadata"]) == {
            "wire_schema_version",
            "wire_schema_sha256",
            "request_sha256",
            "request_bytes",
        }
    else:
        assert not failed
    assert domain.db.one("SELECT status FROM tasks")["status"] == "blocked_runtime"
