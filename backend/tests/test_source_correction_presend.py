"""Real adapter preflight evidence differs from sent requests with no result."""

import asyncio
from decimal import Decimal
import json

import httpx

from ainovel.budget import Budget
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.source_correction_fixture import create_source_correction_fixture, source_correction_output
from test_openai_schema import completed


def queue_toy(client, domain, nonce):
    ids = create_source_correction_fixture(domain, test_mode=True)
    preview = client.get(
        f"/api/artifacts/{ids['candidate_id']}/source-interpretation-correction-preview"
    ).json()
    queued = client.post(
        f"/api/artifacts/{ids['candidate_id']}/source-interpretation-corrections",
        json={
            "candidate_revision": 0,
            "preview_basis_sha256": preview["basis_sha256"],
            "account": "openai_1",
            "client_request_id": nonce,
            "item_ids": ["P02"],
        },
    )
    assert queued.status_code == 202, queued.text
    return ids, queued.json()["id"]


def instruction_for(request):
    wire = json.loads(request.content)
    return json.loads(wire["input"][1]["content"])["任务"]


def test_native_budget_rejection_before_http_can_explicitly_resume(settings, domain, client):
    ids, task_id = queue_toy(client, domain, "presend-budget-native-test")
    ledger = Budget(settings)
    http_calls = []

    def handler(request):
        http_calls.append(request.content)
        output = source_correction_output(instruction_for(request))
        assert output is not None
        return completed(json.dumps(output, ensure_ascii=False))

    adapter = ModelClient(settings, ledger, transport=httpx.MockTransport(handler))
    settings.limits["openai"] = Decimal("0")
    asyncio.run(Worker(settings, adapter).run_once())
    stopped = domain.task(task_id)
    assert stopped["status"] == "blocked_runtime" and "共享预算不足" in stopped["error"]
    assert http_calls == [] and ledger.report()[0]["calls"] == []
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_not_sent'", (task_id,)
    )
    assert len(events) == 1
    evidence = json.loads(events[0]["payload"])
    assert evidence["phase"] == "budget_reserve" and evidence["dispatch_event_id"] > 0
    settings.limits["openai"] = Decimal("20")
    assert client.post(f"/api/tasks/{task_id}/resume", json={}).status_code == 409
    assert client.post(f"/api/tasks/{task_id}/resume", json={"acknowledge_unknown": True}).status_code == 200
    asyncio.run(Worker(settings, adapter).run_once())
    done = domain.task(task_id)
    assert done["status"] == "succeeded", done["error"]
    assert done["result"]["source_correction_state"] == "ready"
    assert len(http_calls) == 2 and len(ledger.report()[0]["calls"]) == 2
    assert domain.chapter(ids["chapter_id"])["content"] == ""
    assert domain.task(ids["parent_id"])["status"] == "needs_review"


def test_sent_unknown_remains_blocked_and_completed_paid_step_replays_from_cache(settings, domain, client):
    ids, task_id = queue_toy(client, domain, "sent-unknown-native-test")
    ledger = Budget(settings)
    http_calls = []

    def handler(request):
        http_calls.append(request.content)
        if len(http_calls) == 2:
            raise httpx.ReadTimeout("isolated sent timeout", request=request)
        output = source_correction_output(instruction_for(request))
        assert output is not None
        return completed(json.dumps(output, ensure_ascii=False))

    adapter = ModelClient(settings, ledger, transport=httpx.MockTransport(handler))
    asyncio.run(Worker(settings, adapter).run_once())
    assert domain.task(task_id)["status"] == "blocked_runtime"
    assert len(http_calls) == 2
    before = domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task_id,))
    assert len(before) == 1
    assert not domain.db.all(
        "SELECT id FROM task_events WHERE task_id=? AND kind='step_not_sent'", (task_id,)
    )
    # A known fee never proves that the missing model result was received.
    for call in ledger.report()[0]["calls"]:
        if call["status"] == "unknown":
            ledger.settle(call["id"], {"input": 21, "output": 7})
    assert client.post(f"/api/tasks/{task_id}/resume", json={"acknowledge_unknown": True}).status_code == 200
    asyncio.run(Worker(settings, adapter).run_once())
    stopped = domain.task(task_id)
    assert stopped["status"] == "blocked_runtime" and "未盲目重发" in stopped["error"]
    assert len(http_calls) == 2 and len(ledger.report()[0]["calls"]) == 2
    assert domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task_id,)) == before
    assert domain.chapter(ids["chapter_id"])["content"] == ""
