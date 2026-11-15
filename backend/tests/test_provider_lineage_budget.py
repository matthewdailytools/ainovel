"""Explicit account continuation must preserve separate provider envelopes."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel import source_interpretation_correction as correction
from ainovel.budget import Budget
from ainovel.db import DomainError, dumps
from ainovel.worker import Worker
from helpers.source_correction_fixture import source_correction_output
from test_source_correction_presend import queue_toy


def historical_family(settings, domain, client, *, status="settled", account="deepseek"):
    ids, auxiliary_id = queue_toy(client, domain, "historical-source-budget")
    ledger = Budget(settings)
    provider = settings.providers["openai_1"]
    parent_call = ledger.reserve(provider, ids["parent_id"], 10000, 10000)
    ledger.settle(parent_call, {"input": 10000, "output": 10000})
    auxiliary_call = ledger.reserve(provider, auxiliary_id, 20000, 20000)
    ledger.settle(auxiliary_call, {"input": 20000, "output": 20000} if status == "settled" else None)
    # Simulate later ordinary paused progress after the original source stop;
    # no production task or live call is involved in this isolated fixture.
    parent = domain.task(ids["parent_id"])
    progress = deepcopy(parent["result"])
    progress.pop("waiting_artifact")
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused',result=? WHERE id=?", (dumps(progress), parent["id"]))
        tx.execute(
            "UPDATE tasks SET status=? WHERE id=?",
            ("succeeded" if status == "settled" else "blocked_runtime", auxiliary_id),
        )
    continued = client.post(
        f"/api/tasks/{parent['id']}/continue",
        json={
            "account": account,
            "budget_limit": 7,
            "client_request_id": "explicit-account-continuation",
            "reason": "Explicit isolated provider/account continuation with its own envelope.",
            "acknowledge_unknown": True,
        },
    )
    assert continued.status_code == 202, continued.text
    child = domain.task(continued.json()["id"])
    assert child["payload"]["account"] == account
    assert child["payload"]["budget_limit"] == 7
    assert child["result"]["continued_from"] == parent["id"]
    return ids, child, ledger, auxiliary_call


def calls(ledger):
    with ledger.db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM calls ORDER BY id")]


@pytest.mark.parametrize("status", ["settled", "unknown"])
def test_formal_cross_provider_continuation_does_not_mix_usd_into_cny(settings, domain, client, status):
    _, child, ledger, old_call = historical_family(settings, domain, client, status=status)
    before = calls(ledger)
    worker = Worker(settings)
    worker.task = child
    assert correction.bounded_run_limit(worker) == 7
    assert calls(ledger) == before
    historical = next(row for row in before if row["id"] == old_call)
    assert historical["provider"] == "openai" and historical["currency"] == "USD"
    assert historical["status"] == status
    if status == "unknown":
        assert historical["charged"] is None and historical["reserved"] > 0


def test_same_provider_continuation_subtracts_auxiliary_once_not_ancestor_workflow(settings, domain, client):
    _, child, ledger, _ = historical_family(settings, domain, client, account="openai_2")
    worker = Worker(settings)
    worker.task = child
    own = ledger.reserve(settings.providers["openai_2"], child["id"], 1000, 1000)
    ledger.settle(own, {"input": 1000, "output": 1000})
    before = calls(ledger)
    assert correction.bounded_run_limit(worker) == pytest.approx(6.8)
    assert correction.bounded_run_limit(worker) == pytest.approx(6.8)
    assert calls(ledger) == before


class SourceFixtureModel:
    def __init__(self):
        self.accounts = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        self.accounts.append(account)
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        output = source_correction_output(instruction)
        assert output is not None
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": "synthetic-ds-source"}


def current_ds_auxiliary(settings, domain, client, kind):
    ids, child, ledger, old_call = historical_family(settings, domain, client, status="unknown")
    previous = domain.artifact(ids["candidate_id"])
    candidate = domain.stage(
        {
            **child["payload"],
            "branch_id": child["branch_id"],
            "chapter_id": previous["chapter_id"],
            "base_revision": 0,
        },
        "chapter_draft",
        deepcopy(previous["payload"]),
        previous["basis"],
        child["id"],
    )
    progress = {**child["result"], "waiting_artifact": candidate["id"]}
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='needs_review',result=? WHERE id=?", (dumps(progress), child["id"])
        )
    route = f"/api/artifacts/{candidate['id']}/source-interpretation-corrections"
    preview = client.get(f"/api/artifacts/{candidate['id']}/source-interpretation-correction-preview")
    assert preview.status_code == 200, preview.text
    source = client.post(
        route,
        json={
            "account": "deepseek",
            "candidate_revision": 0,
            "item_ids": ["P02"],
            "client_request_id": "current-ds-source",
            "preview_basis_sha256": preview.json()["basis_sha256"],
        },
    )
    assert source.status_code == 202, source.text
    if kind == correction.KIND:
        task = domain.task(source.json()["id"])
        previous_source = None
    else:
        model = SourceFixtureModel()
        asyncio.run(Worker(settings, model).run_once())
        assert model.accounts == ["deepseek", "deepseek"]
        previous_source = domain.task(source.json()["id"])
        assert previous_source["status"] == "succeeded", previous_source["error"]
        receipt_id = previous_source["result"]["artifact_id"]
        preview = client.get(
            f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
            params={"receipt_id": receipt_id},
        )
        assert preview.status_code == 200, preview.text
        body = client.post(
            f"/api/artifacts/{candidate['id']}/recheck-source-correction",
            json={
                "account": "deepseek",
                "candidate_revision": 0,
                "receipt_id": receipt_id,
                "client_request_id": "current-ds-body",
                "preview_basis_sha256": preview.json()["basis_sha256"],
            },
        )
        assert body.status_code == 202, body.text
        task = domain.task(body.json()["id"])
    worker = Worker(settings)
    worker.task = task
    return worker, child, ledger, old_call, previous_source


@pytest.mark.parametrize("kind", [correction.KIND, "source_corrected_candidate_recheck"])
def test_current_ds_source_and_body_keep_their_own_provider_envelope(settings, domain, client, kind):
    worker, child, ledger, old_call, prior_source = current_ds_auxiliary(settings, domain, client, kind)
    ds = settings.providers["deepseek"]
    parent_call = ledger.reserve(ds, child["id"], 10000, 10000)
    ledger.settle(parent_call, {"input": 10000, "output": 10000})
    if prior_source:
        prior = ledger.reserve(ds, prior_source["id"], 3000, 3000)
        ledger.settle(prior, {"input": 3000, "output": 3000})
    own = ledger.reserve(ds, worker.task["id"], 2000, 2000)
    ledger.settle(own, {"input": 2000, "output": 2000})
    before = calls(ledger)
    assert correction.bounded_run_limit(worker) == pytest.approx(6.87 if prior_source else 6.9)
    assert calls(ledger) == before
    historical = next(row for row in before if row["id"] == old_call)
    assert historical["provider"] == "openai" and historical["status"] == "unknown"


@pytest.mark.parametrize("kind", [correction.KIND, "source_corrected_candidate_recheck"])
def test_source_and_body_still_reject_cross_provider_selection(settings, domain, client, kind):
    worker, _, ledger, _, _ = current_ds_auxiliary(settings, domain, client, kind)
    before = calls(ledger)
    worker.task = {**worker.task, "payload": {**worker.task["payload"], "account": "openai_1"}}
    with pytest.raises(DomainError) as error:
        correction.bounded_run_limit(worker)
    assert error.value.code == "source_correction_account"
    assert calls(ledger) == before


@pytest.mark.parametrize("primary", ["task", "parent"])
@pytest.mark.parametrize("kind", [correction.KIND, "source_corrected_candidate_recheck"])
def test_current_task_and_bound_parent_foreign_provider_rows_are_not_hidden(
    settings, domain, client, kind, primary
):
    worker, child, ledger, _, _ = current_ds_auxiliary(settings, domain, client, kind)
    target = worker.task["id"] if primary == "task" else child["id"]
    corrupt = ledger.reserve(settings.providers["openai_1"], target, 1, 1)
    ledger.settle(corrupt, {"input": 1, "output": 1})
    before = calls(ledger)
    with pytest.raises(DomainError) as error:
        correction.bounded_run_limit(worker)
    assert error.value.code == "source_correction_account"
    assert calls(ledger) == before


def test_current_provider_unknown_auxiliary_still_blocks_workflow(settings, domain, client):
    worker, child, ledger, _, _ = current_ds_auxiliary(settings, domain, client, correction.KIND)
    unknown = ledger.reserve(settings.providers["deepseek"], worker.task["id"], 1, 1)
    ledger.settle(unknown, None)
    worker.task = domain.task(child["id"])
    before = calls(ledger)
    with pytest.raises(DomainError) as error:
        correction.bounded_run_limit(worker)
    assert error.value.code == "unknown_result"
    assert calls(ledger) == before


def test_native_deepseek_call_preserves_old_usd_unknown_and_replays_only_own_cache(settings, domain, client):
    import httpx
    from pydantic import BaseModel
    from ainovel.providers import ModelClient

    _, child, ledger, _ = historical_family(settings, domain, client, status="unknown")
    historical = calls(ledger)
    sent = []

    class Probe(BaseModel):
        ok: bool

    def handler(request):
        sent.append(str(request.url))
        assert request.url.host == "api.deepseek.com"
        events = [
            {"choices": [{"delta": {"content": '{"ok":true}'}, "finish_reason": None}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        ]
        content = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    worker = Worker(settings, ModelClient(settings, ledger, transport=httpx.MockTransport(handler)))
    worker.task = worker.claim()
    assert worker.task["id"] == child["id"]
    worker.attempt = worker.task["attempt"]
    instruction = {"动作": "离线核对当前服务商预算边界"}
    assert asyncio.run(worker.call(instruction, Probe, max_output=128)) == {"ok": True}
    assert asyncio.run(worker.call(instruction, Probe, max_output=128)) == {"ok": True}
    assert len(sent) == 1
    after = calls(ledger)
    assert [row for row in after if row["id"] in {old["id"] for old in historical}] == historical
    current = [row for row in after if row["task_id"] == child["id"]]
    assert len(current) == 1 and current[0]["provider"] == "deepseek" and current[0]["currency"] == "CNY"
    assert current[0]["status"] == "settled"
    assert correction.bounded_run_limit(worker) == 7
