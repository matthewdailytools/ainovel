"""Explicit first-round structure output sizing never raises an existing task's policy."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
import json

import httpx
import pytest
from pydantic import ValidationError

from ainovel.budget import Budget
from ainovel.db import DomainError, dumps
from ainovel.domain import same_task_payload
from ainovel.models import WorkflowInput
from ainovel.providers import ModelClient
from ainovel.story_extraction import StartExtraction
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain
from test_api_worker import FakeModel
from test_openai_schema import completed


def workflow(novel_id, **options):
    return WorkflowInput(
        novel_id=novel_id,
        mode="automatic",
        brief="离线结构输出上限验证",
        account="openai_1",
        structured_maintenance=True,
        **options,
    ).model_dump()


def extraction_request(domain, story, **options):
    return {
        "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "chapter_ids": [story["chapter_id"]],
        "account": "openai_1",
        "client_request_id": "structure-output-limit",
        **options,
    }


@pytest.mark.parametrize("limit", [64, 8192, 16384])
def test_declared_structure_limits_accept_only_bounded_integer_values(limit):
    assert (
        StartExtraction(
            base_story_revision=0,
            chapter_ids=["chapter"],
            account="openai_1",
            client_request_id="explicit-output",
            max_output_tokens=limit,
        ).max_output_tokens
        == limit
    )
    assert workflow("novel", structure_max_output_tokens=limit)["structure_max_output_tokens"] == limit


@pytest.mark.parametrize("limit", [None, True, False, 0, 63, 16385, -1, 8192.0, "16384", float("inf")])
def test_invalid_structure_limits_reject_before_task_creation(limit):
    with pytest.raises(ValidationError):
        StartExtraction(
            base_story_revision=0,
            chapter_ids=["chapter"],
            account="openai_1",
            client_request_id="invalid-output",
            max_output_tokens=limit,
        )
    with pytest.raises(ValidationError):
        workflow("novel", structure_max_output_tokens=limit)


@pytest.mark.parametrize("limit", [None, True, 63, 16385, 8192.0, "16384"])
def test_invalid_api_limit_never_queues_a_task(domain, story, client, limit):
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions",
        json=extraction_request(domain, story, max_output_tokens=limit),
    )
    assert response.status_code == 422
    payload = {**workflow(story["id"]), "structure_max_output_tokens": limit}
    assert client.post("/api/workflows", json=payload).status_code == 422
    assert not domain.db.all("SELECT id FROM tasks")


def test_structure_default_is_still_8192_and_normal_writing_setting_is_separate():
    assert (
        StartExtraction(
            base_story_revision=0, chapter_ids=["c"], account="openai_1", client_request_id="default-output"
        ).max_output_tokens
        == 8192
    )
    assert workflow("novel")["structure_max_output_tokens"] == 8192
    assert same_task_payload({}, {"structure_max_output_tokens": 8192})
    assert not same_task_payload({}, {"structure_max_output_tokens": 16384})
    assert not same_task_payload({}, {"structure_max_output_tokens": 8192.0})
    assert not same_task_payload(
        {"action": "writing.advise"}, {"action": "writing.advise", "max_output_tokens": 8192}
    )


@pytest.mark.parametrize("limit", [8192, 16384])
def test_explicit_extraction_limit_reaches_actual_http_and_its_money_reservation(
    settings, domain, story, client, limit
):
    domain.save(story["chapter_id"], 0, "😀𠮷站在渡口，只观察当前保存稿。")
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return completed(dumps({"records": [], "unknowns": [], "coverage": "仅当前保存稿"}))

    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions",
        json=extraction_request(domain, story, max_output_tokens=limit),
    )
    assert response.status_code == 202, response.text
    assert response.json()["payload"]["max_output_tokens"] == limit
    ledger = Budget(settings)
    asyncio.run(Worker(settings, ModelClient(settings, ledger, httpx.MockTransport(handler))).run_once())
    finished = domain.task(response.json()["id"])
    assert finished["status"] == "succeeded", finished["error"]
    assert len(bodies) == 1 and json.loads(bodies[0])["max_output_tokens"] == limit
    provider = settings.providers["openai_1"]
    estimate = int(
        (
            Decimal(len(bodies[0]) + 2048) * provider.input_rate + Decimal(limit) * provider.output_rate
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    assert ledger.report()[0]["calls"][0]["reserved"] == estimate
    assert domain.chapter(story["chapter_id"])["content"] == "😀𠮷站在渡口，只观察当前保存稿。"


@pytest.mark.parametrize("gate", ["window", "budget"])
def test_selected_16k_keeps_final_body_window_and_task_money_guards(settings, domain, story, client, gate):
    domain.save(story["chapter_id"], 0, "只读保存稿。")
    queued = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions",
        json=extraction_request(domain, story, max_output_tokens=16384),
    ).json()
    ledger = Budget(settings)
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return completed(dumps({"records": [], "unknowns": [], "coverage": "仅本章"}))

    worker = Worker(settings, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    # Measure the real body first, without accepting or changing manuscript state.
    asyncio.run(worker.module())
    assert domain.task(queued["id"])["status"] == "succeeded"
    body = bodies[0]
    actual = json.loads(body)
    before = deepcopy(ledger.report())
    if gate == "window":
        settings.providers["openai_1"] = replace(
            settings.providers["openai_1"],
            context_window_tokens=len(body) + 8192 + 4096,
            context_source="explicit 8k-sized test window",
        )
        run_limit = None
    else:
        provider = settings.providers["openai_1"]
        eight_k = (
            Decimal(len(body) + 2048) * provider.input_rate + Decimal(8192) * provider.output_rate
        ) / Decimal(1_000_000)
        run_limit = float(eight_k + Decimal("0.000001"))
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            worker.client.generate(
                "openai_1",
                "new-16k-financial-gate",
                actual["input"][0]["content"],
                actual["input"][1]["content"],
                max_output=16384,
                schema=actual["text"]["format"]["schema"],
                run_limit=run_limit,
            )
        )
    assert caught.value.code == ("context_limit" if gate == "window" else "budget")
    assert len(bodies) == 1 and ledger.report() == before


def test_extraction_default_nonce_replay_preserves_legacy_payload_and_rejects_16k(
    settings, domain, story, client
):
    domain.save(story["chapter_id"], 0, "原稿不受参数重放影响。")
    request = extraction_request(domain, story)
    first = client.post(f"/api/branches/{story['branch_id']}/story-extractions", json=request).json()
    legacy = {key: value for key, value in first["payload"].items() if key != "max_output_tokens"}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(legacy), first["id"]))
    repeated = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions", json={**request, "max_output_tokens": 8192}
    )
    assert repeated.status_code == 202 and repeated.json()["id"] == first["id"]
    assert "max_output_tokens" not in repeated.json()["payload"]
    different = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions", json={**request, "max_output_tokens": 16384}
    )
    assert different.status_code == 409 and different.json()["code"] == "idempotency"
    assert len(domain.db.all("SELECT id FROM tasks")) == 1
    outputs = []

    class LegacyExtraction:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            outputs.append(max_output)
            return {
                "text": dumps({"records": [], "unknowns": [], "coverage": "旧任务原范围"}),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-legacy",
            }

    asyncio.run(Worker(settings, LegacyExtraction()).run_once())
    assert domain.task(first["id"])["status"] == "succeeded"
    assert outputs == [8192]
    assert "max_output_tokens" not in domain.task(first["id"])["payload"]


def test_workflow_default_nonce_does_not_materialize_or_change_legacy_configuration(domain, story, client):
    original_payload = workflow(story["id"], client_request_id="legacy-workflow-structure-output")
    original_payload.pop("structure_max_output_tokens")
    first = domain.queue("workflow", original_payload, story["branch_id"])
    response = client.post("/api/workflows", json={**original_payload, "structure_max_output_tokens": 8192})
    assert response.status_code == 202 and response.json()["id"] == first["id"]
    assert "structure_max_output_tokens" not in response.json()["payload"]
    assert (
        client.post(
            "/api/workflows", json={**original_payload, "structure_max_output_tokens": 16384}
        ).status_code
        == 409
    )
    assert len(domain.db.all("SELECT id FROM tasks")) == 1


@pytest.mark.parametrize("limit", [None, 64, 8192, 16384])
def test_continuation_preserves_frozen_first_round_limit_including_missing_legacy_field(
    domain, story, client, limit
):
    payload = workflow(story["id"])
    if limit is None:
        payload.pop("structure_max_output_tokens")
    else:
        payload["structure_max_output_tokens"] = limit
    task = domain.queue("workflow", payload, story["branch_id"])
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (task["id"],))
    request = {
        "account": "openai_2",
        "client_request_id": "continue-structure-output-" + str(limit),
        "reason": "保留输出上限换账户",
    }
    response = client.post(f"/api/tasks/{task['id']}/continue", json=request)
    assert response.status_code == 202, response.text
    continued = response.json()
    if limit is None:
        assert "structure_max_output_tokens" not in continued["payload"]
    else:
        assert continued["payload"]["structure_max_output_tokens"] == limit
    assert domain.task(task["id"])["payload"] == payload
    assert client.post(f"/api/tasks/{task['id']}/continue", json=request).json()["id"] == continued["id"]


def test_domain_continuation_cannot_silently_raise_frozen_limit(domain, story):
    task = domain.queue(
        "workflow", workflow(story["id"], structure_max_output_tokens=8192), story["branch_id"]
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (task["id"],))
    current = domain.task(task["id"])
    with pytest.raises(DomainError, match="保留原任务冻结"):
        domain.continue_workflow(
            current,
            {
                **current["payload"],
                "account": "openai_2",
                "client_request_id": "reject-continuation-raise",
                "structure_max_output_tokens": 16384,
            },
            {"reason": "不能在续跑中改额", "acknowledge_unknown": False},
        )
    assert len(domain.db.all("SELECT id FROM tasks")) == 1
    assert domain.task(task["id"])["payload"]["structure_max_output_tokens"] == 8192


@pytest.mark.parametrize("limit", [64, 8192, 16384])
def test_omitted_continuation_field_inherits_the_previously_selected_limit(domain, story, limit):
    task = domain.queue(
        "workflow", workflow(story["id"], structure_max_output_tokens=limit), story["branch_id"]
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (task["id"],))
    current = domain.task(task["id"])
    incoming = {
        key: value for key, value in current["payload"].items() if key != "structure_max_output_tokens"
    }
    incoming.update(account="openai_2", client_request_id="continue-omitted-" + str(limit))
    decision = {"reason": "省略字段仍继承原冻结参数", "acknowledge_unknown": False}
    continued = domain.continue_workflow(current, incoming, decision)
    assert continued["payload"]["structure_max_output_tokens"] == limit
    assert "structure_max_output_tokens" not in incoming
    assert domain.continue_workflow(current, incoming, decision)["id"] == continued["id"]


@pytest.mark.parametrize("limit", [None, 16384])
def test_first_and_later_chapter_maintenance_use_the_same_frozen_setting(settings, domain, story, limit):
    chapter = domain.save(story["chapter_id"], 0, "首次保存稿。")
    payload = workflow(story["id"], adoption="automatic")
    if limit is None:
        payload.pop("structure_max_output_tokens")
    else:
        payload["structure_max_output_tokens"] = limit
    domain.queue("workflow", payload, story["branch_id"])
    outputs = []

    class EmptyExtraction:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            outputs.append(max_output)
            return {
                "text": dumps({"records": [], "unknowns": [], "coverage": "只核对所选保存稿"}),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }

    worker = Worker(settings, EmptyExtraction())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    progress = worker.task["result"]
    for turn in range(2):
        progress.update(
            expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
            pending_structure={
                "chapter_id": chapter["id"],
                "revision": chapter["revision"],
                "epoch": str(turn),
            },
        )
        worker.checkpoint(progress)
        assert asyncio.run(maintain(worker, progress))
        if not turn:
            chapter = domain.save(chapter["id"], chapter["revision"], "前稿修改后的维护。")
    assert outputs == [limit or 8192, limit or 8192]
    assert len(domain.task(worker.task["id"])["result"]["structure_history"]) == 2


def test_workflow_structure_setting_does_not_change_planning_writing_or_review_limits(
    settings, domain, story
):
    calls = []

    class Capture(FakeModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            action = instruction.get("动作")
            calls.append((action, max_output))
            if action == "按保存稿反向整理长篇结构候选":
                return {
                    "text": dumps({"records": [], "unknowns": [], "coverage": "本章"}),
                    "usage": {"input": 1, "output": 1},
                    "call_id": "structure",
                }
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    task = domain.queue(
        "workflow",
        workflow(
            story["id"],
            chapters=2,
            min_chars=200,
            max_chars=600,
            adoption="automatic",
            structure_max_output_tokens=16384,
        ),
        story["branch_id"],
    )
    asyncio.run(Worker(settings, Capture()).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "production_complete", finished["error"]
    structured = [output for action, output in calls if action == "按保存稿反向整理长篇结构候选"]
    assert structured == [16384, 16384]
    assert all(output != 16384 for action, output in calls if action != "按保存稿反向整理长篇结构候选")
    assert ("全书滚动规划", 8192) in calls
