"""The two source-only stops recheck once, using real Worker and offline wires."""

import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from ainovel import authored_source_classification as classification
from ainovel import source_observation_recheck as recheck
from ainovel import workflow_plan_recipient_scope as scope
from ainovel.db import DomainError
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from ainovel.workflow_plan_preparation import phase
from test_author_clarification_format_failure import (
    failed as failed,
)
from test_author_clarification_format_failure import (
    post,
    preview,
)
from test_author_clarification_format_failure import (
    request as clarify_request,
)
from test_preparation_context_refresh import model_history, stopped_preparation
from test_workflow_plan_source_roles import deepseek_completed


@pytest.fixture(params=["automatic", "assisted", "clarification_child"])
def stopped(settings, domain, story, client, request):
    if request.param == "automatic":
        worker, task, _, _ = stopped_preparation(settings, domain, story, client, rewrite=False)
    else:
        worker, task, _ = request.getfixturevalue("failed")
        if request.param == "clarification_child":
            body = clarify_request(preview(client, task))
            response = post(client, task, body)
            assert response.status_code == 202, response.text
            from helpers.author_observation_retry_fixture import wrong_unit
            from helpers.authored_classification_fixture import native

            native(worker, transform=wrong_unit)
            assert asyncio.run(worker.run_once())
            task = domain.task(response.json()["id"])
            assert task["status"] == "blocked_runtime", task["error"]
    return worker, task


def opt_in(task, **changes):
    return {
        "source_observation_recheck_version": recheck.VERSION,
        "source_observation_basis_sha256": recheck.basis(task),
        "acknowledge_unknown": True,
        "reason": "明确只用原有效来源独立新核对，旧返回和费用保留。",
        **changes,
    }


def authorize(client, task, **changes):
    response = client.post(f"/api/tasks/{task['id']}/resume", json=opt_in(task, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def native_output(worker, *, unknown=True, mixed=False, overflow=False, timeout=False):
    frames = []

    def handler(request):
        instruction = json.loads(json.loads(request.content)["messages"][1]["content"].split("\n只返回")[0])[
            "任务"
        ]
        frames.append(instruction)
        root = instruction
        while root.get("动作") in {"仅修正结构化产物格式", "修正已知达到输出上限的结构化产物"}:
            root = root["原始任务"]
        assert root["来源观察输出契约版本"] == recheck.OUTPUT_VERSION
        assert "旧观察" not in root and "旧错误" not in root
        if timeout:
            raise httpx.ReadTimeout("isolated unknown result", request=request)
        if overflow and len(frames) == 1:
            from test_budget_provider import stream

            return httpx.Response(
                200,
                content=stream(
                    [
                        {
                            "model": "isolated-deepseek-flash",
                            "choices": [
                                {"delta": {"content": '{"input_sha256":'}, "finish_reason": "length"}
                            ],
                        },
                        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
                    ],
                    True,
                ),
            )
        if mixed and len(frames) > 1:
            return deepseek_completed(
                {"input_sha256": root["input_sha256"], "units": [], "note": "故意缺少单元。"}
            )
        rows = [
            {
                "source_id": u["source_id"],
                "classification": "unknown" if unknown and i == 0 else "not_scope_related",
                "observations": [],
                "reason": "仅工程替身，真实未知保持待审。",
            }
            for i, u in enumerate(root["可引用原文"])
        ]
        if mixed:
            rows[0]["classification"] = "scope_related"  # Valid shape, invalid source anchor coverage.
        return deepseek_completed(
            {
                "input_sha256": root["input_sha256"],
                "units": rows,
                "note": "工程范围核对，不作语义或文学验收。",
            }
        )

    worker.client = ModelClient(worker.settings, worker.budget, httpx.MockTransport(handler))
    return frames


def claimed(settings, domain, queued):
    worker = Worker(settings)
    worker.task = worker.claim()
    assert worker.task["id"] == queued["id"]
    worker.attempt = worker.task["attempt"]
    return worker


def observe(worker):
    _, _, key, row, source = recheck.selected(worker.task)

    def invoke():
        if worker.task["payload"]["mode"] == "automatic":
            return scope.observe(worker, source, version=row[scope.KEY])
        return classification.observe(worker, row["source"])

    return asyncio.run(phase(worker, row, key, "observing", invoke))


def test_explicit_same_task_recheck_preserves_old_stop_and_paid_unknown(settings, domain, client, stopped):
    old_worker, task = stopped
    old_history = model_history(old_worker, domain, task["id"])
    _, _, phase_key, old_row, _ = recheck.selected(task)
    old_row = deepcopy(old_row)
    before_count = len(domain.db.all("SELECT id FROM tasks"))
    queued = authorize(client, task)
    receipt = queued["result"][recheck.KEY]
    assert receipt["original_row"] == old_row
    assert receipt["original_slot"] == old_row["phases"][phase_key]
    assert queued["payload"] == task["payload"] and queued["result"]["plan"] == task["result"]["plan"]
    assert len(domain.db.all("SELECT id FROM tasks")) == before_count
    assert model_history(old_worker, domain, task["id"]) == old_history
    worker = claimed(settings, domain, queued)
    frames = native_output(worker)
    value = observe(worker)
    assert value["status"] == "needs_review" and len(frames) == 1
    current = domain.task(task["id"])
    record = current["result"][recheck.KEY]
    assert record["original_row"] == old_row
    grants = record["phases"]
    assert len(grants) == 1 and len(next(iter(grants.values()))["dispatches"]) == 1
    assert current["payload"] == task["payload"]
    after = model_history(worker, domain, task["id"])
    assert all(c in after["calls"] for c in old_history["calls"])
    assert all(s in after["steps"] for s in old_history["steps"])
    assert all(m in after["metadata"] for m in old_history["metadata"])
    # A completed source phase can replay; it never opens another allowance.
    assert observe(worker) == value and len(frames) == 1
    assert recheck.output_version(worker) is None
    worker.checkpoint(worker.task["result"], "needs_review", "独立观察仍未知。")
    response = client.post(f"/api/tasks/{task['id']}/resume", json=opt_in(domain.task(task["id"])))
    assert response.status_code == 409


def test_format_and_grounded_repairs_share_three_dispatches_and_memory_checkpoint(
    settings, domain, client, stopped
):
    _, task = stopped
    worker = claimed(settings, domain, authorize(client, task))
    frames = native_output(worker, mixed=True)
    with pytest.raises(DomainError):
        observe(worker)
    assert len(frames) == 3
    grant = next(iter(worker.task["result"][recheck.KEY]["phases"].values()))
    assert len(grant["dispatches"]) == 3
    # This is the original overwrite hazard: a normal later phase checkpoint
    # must retain the dispatches written immediately before each model call.
    worker.checkpoint(worker.task["result"], "blocked_runtime", "三次来源格式纠正均已保留。")
    saved = domain.task(task["id"])
    assert saved["result"][recheck.KEY]["phases"] == worker.task["result"][recheck.KEY]["phases"]
    assert len(next(iter(saved["result"][recheck.KEY]["phases"].values()))["dispatches"]) == 3
    response = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True})
    assert response.status_code == 200, response.text
    resumed = claimed(settings, domain, response.json())
    later = native_output(resumed)
    with pytest.raises(DomainError):
        observe(resumed)
    assert later == []


@pytest.mark.parametrize(
    "fault", ["no_ack", "blank_reason", "wrong_basis", "changes", "unknown_zero", "body", "missing_metadata"]
)
def test_explicit_basis_and_safe_source_only_shape_are_required(domain, client, stopped, fault):
    worker, task = stopped
    changes = {}
    if fault == "no_ack":
        changes["acknowledge_unknown"] = False
    elif fault == "blank_reason":
        changes["reason"] = " \n "
    elif fault == "wrong_basis":
        changes["source_observation_basis_sha256"] = "0" * 64
    elif fault == "changes":
        changes["acknowledge_changes"] = True
    elif fault == "unknown_zero":
        identifier = worker.budget.reserve(
            worker.settings.providers[task["payload"]["account"]], "another-owner", 0, 0
        )
        worker.budget.settle(identifier)
    elif fault == "body":
        chapter = domain.db.one(
            "SELECT id FROM chapters WHERE branch_id=? AND position=? AND archived=0",
            (task["branch_id"], task["result"]["next_chapter"]),
        )
        domain.save(chapter["id"], 0, "作者已经手写正文。")
    elif fault == "missing_metadata":
        with worker.budget.db(True) as usage:
            usage.execute("DELETE FROM call_metadata")
    before = domain.task(task["id"]), model_history(worker, domain, task["id"])
    response = client.post(f"/api/tasks/{task['id']}/resume", json=opt_in(task, **changes))
    assert response.status_code == 409, response.text
    assert (domain.task(task["id"]), model_history(worker, domain, task["id"])) == before


@pytest.mark.parametrize("failure", ["known_output_limit", "unknown_timeout"])
def test_known_usage_output_limit_can_repair_but_unknown_timeout_cannot(
    settings, domain, client, stopped, failure
):
    _, task = stopped
    worker = claimed(settings, domain, authorize(client, task))
    frames = native_output(
        worker, overflow=failure == "known_output_limit", timeout=failure == "unknown_timeout"
    )
    if failure == "known_output_limit":
        assert observe(worker)["status"] == "needs_review"
        assert len(frames) == 2 and frames[1]["动作"] == "修正已知达到输出上限的结构化产物"
    else:
        with pytest.raises(Exception):
            observe(worker)
        assert len(frames) == 1
        worker.checkpoint(worker.task["result"], "blocked_runtime", "新的来源调用结果未知。")
        response = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True})
        assert response.status_code == 200, response.text
        resumed = claimed(settings, domain, response.json())
        later = native_output(resumed)
        with pytest.raises(DomainError):
            observe(resumed)
        assert later == []


def test_cross_task_context_and_unrelated_phase_do_not_inherit_allowance(settings, domain, client, stopped):
    _, task = stopped
    worker = claimed(settings, domain, authorize(client, task))
    _, _, key, row, _ = recheck.selected(worker.task)
    recheck.prepare_phase(worker, row, key)
    assert recheck.output_version(worker) == recheck.OUTPUT_VERSION
    original = worker.task
    worker.task = {**original, "id": "another-task"}
    assert recheck.before_dispatch(worker, "unused", {}) is False
    assert recheck.output_version(worker) is None
    worker.task = original
    recheck.prepare_phase(worker, row, key)
    recheck.prepare_phase(worker, row, "interpret")
    assert recheck.output_version(worker) is None


def test_old_attempt_cannot_dispatch_or_overwrite_new_claim(settings, domain, client, stopped):
    _, task = stopped
    old = claimed(settings, domain, authorize(client, task))
    _, _, key, row, source = recheck.selected(old.task)
    recheck.prepare_phase(old, row, key)
    old.checkpoint(old.task["result"], "needs_review", "准备已落盘，尚未派发。")
    response = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True})
    assert response.status_code == 200, response.text
    current = claimed(settings, domain, response.json())
    before = domain.task(task["id"]), model_history(current, domain, task["id"])
    from ainovel import authored_source_scope as authored
    from ainovel import source_scope_observation as output

    token = (
        scope.sha({"version": row[scope.KEY], "source": source})
        if task["payload"]["mode"] == "automatic"
        else authored.token(row["source"], authored.CURRENT_VERSION)
    )
    instruction = output.observation_request(
        source,
        token=token,
        source_kind="workflow_plan" if task["payload"]["mode"] == "automatic" else row["source"]["kind"],
    )["instruction"]
    with pytest.raises(DomainError):
        recheck.before_dispatch(old, "late-original-attempt", instruction)
    with pytest.raises(DomainError):
        old.checkpoint(old.task["result"])
    assert (domain.task(task["id"]), model_history(current, domain, task["id"])) == before


@pytest.mark.parametrize("round_number", [1, 2, 3])
def test_output_contract_does_not_create_new_choice_rounds(settings, domain, story, client, round_number):
    _, task, _, _ = stopped_preparation(settings, domain, story, client, rewrite=False)
    worker = claimed(settings, domain, authorize(client, task))
    row = worker.task["result"]["plot_preparations"]["2"]
    row["round"] = round_number
    worker.checkpoint(worker.task["result"])
    before = model_history(worker, domain, task["id"])
    with pytest.raises(DomainError):
        recheck.prepare_phase(worker, row, f"recipient_scope:{round_number}")
    assert recheck.output_version(worker) is None
    assert model_history(worker, domain, task["id"]) == before
    assert worker.task["result"][recheck.KEY]["phases"] == {}
