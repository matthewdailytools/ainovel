"""An explicit clarification of three known invalid returns, using isolated data only."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel import author_source_clarification as bridge
from ainovel import authored_source_classification as classification
from ainovel import authored_source_execution as execution
from ainovel import authored_source_scope as authored
from ainovel.budget import micro
from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.worker import Worker
from helpers.authored_classification_fixture import SOURCE, native
from helpers.author_observation_retry_fixture import wrong_unit


@pytest.fixture
def failed(settings, domain, story, client, request):
    parameter = getattr(request, "param", "deepseek")
    account = parameter.get("account", "deepseek") if isinstance(parameter, dict) else parameter
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="segment",
        brief="只测已知返回的写前停点。",
        chapters=1,
        plots=[{"number": 1, "title": "原创验证", "plot": SOURCE, "fixed": ["不动手"]}],
        account=account,
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id="current-observe-format-failure",
    ).model_dump(exclude_none=True)
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 202, response.text
    worker = Worker(settings)
    if isinstance(parameter, dict) and parameter.get("old_settled_without_output"):
        old_call = worker.budget.reserve(settings.providers[account], response.json()["id"], 10, 10, None)
        worker.budget.metadata(old_call, settings.providers[account].model, "offline-old-maintenance")
        worker.budget.settle(old_call, {"input": 10, "output": 10})
        with domain.db.connect(True) as tx:
            domain.db.event(
                tx,
                response.json()["id"],
                "step_dispatched",
                {"fingerprint": "old-incomplete-maintenance", "action": "从正文提取事实候选与章节摘要"},
            )
    frames = native(worker, transform=wrong_unit)
    assert asyncio.run(worker.run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "blocked_runtime" and len(frames) == 3
    assert task["result"][execution.KEY] == execution.VERSION
    return worker, task, frames


def preview(client, task):
    response = client.get(f"/api/tasks/{task['id']}/author-source-clarification-preview")
    assert response.status_code == 200, response.text
    return response.json()


def request(value, **changes):
    return {
        "version": bridge.VERSION,
        "client_request_id": "explicit-format-failure-clarification",
        "preview_basis_sha256": value["request_basis"]["preview_basis_sha256"],
        "clarification": "交易失败与双方未交付仍须保持，借尺仍须按约归还；没有另改结果。",
        "actor": "authorized_operator",
        "reason": "核对完整原话，保留三个原始格式失败及费用。",
        **changes,
    }


def post(client, task, body):
    return client.post(f"/api/tasks/{task['id']}/author-source-clarifications", json=body)


def ledger(worker):
    with worker.budget.db() as usage:
        return [dict(r) for r in usage.execute("SELECT * FROM calls ORDER BY id")]


@pytest.mark.parametrize("failed", ["deepseek", "openai_1"], indirect=True)
@pytest.mark.parametrize("actor", ["author", "authorized_operator"])
def test_preview_does_not_manufacture_inventory_or_completed_receipt_and_child_keeps_history(
    settings, domain, client, failed, actor
):
    worker, task, frames = failed
    old_steps = domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint")
    old_chapters = domain.db.all("SELECT * FROM chapters ORDER BY id")
    old_calls = ledger(worker)
    old_tasks = domain.db.all("SELECT * FROM tasks ORDER BY id")
    value = preview(client, task)
    assert domain.db.all("SELECT * FROM tasks ORDER BY id") == old_tasks
    review = value["source_review"]
    assert review["state"] == "pending"
    assert review["inventory"] is None and review["contract"] is None and review["bindings"] is None
    failure = review["observation_failure"]
    assert failure["kind"] == "known_returned_invalid_observation"
    assert len(failure["raw_returns"]) == 3
    assert all(row["issues"] and json.loads(row["text"]) for row in failure["raw_returns"])
    proof = failure["proof"]
    assert all(row["status"] == "settled" and row["has_complete_step"] for row in proof["calls"])
    assert len(set(proof["call_ids"])) == 3
    assert micro(value["budget"]["remaining_run_limit"]) == micro(task["payload"]["budget_limit"]) - sum(
        call["charged"] for call in old_calls
    )
    body = request(value, actor=actor)
    response = post(client, task, body)
    assert response.status_code == 202, response.text
    child = response.json()
    receipt = child["result"][bridge.RECEIPT]
    assert child["payload"]["plots"] == task["payload"]["plots"]
    assert child["result"]["plan"] == task["result"]["plan"]
    assert child["payload"]["budget_limit"] == value["budget"]["remaining_run_limit"]
    assert receipt["previous_source_receipt"] == task["result"][authored.RECEIPTS]["chapter:1"]
    assert receipt["previous_source_receipt"]["phases"]["observe"]["status"] == "started"
    assert "response" not in receipt["previous_source_receipt"]["phases"]["observe"]
    assert receipt["source_entry"]["text"] == bridge.combine(receipt["previous_source_entry"], body)["text"]
    assert receipt["observation_failure"] == proof == receipt["budget"]["observation_failure"]
    assert child["result"][authored.RECEIPTS] == {}
    assert bridge.validate_task(child, domain) == receipt
    bridge.ensure_runtime(child, domain, settings)
    parent = domain.task(task["id"])
    assert parent["error"] == task["error"] and parent["status"] == "continued"
    assert {k: v for k, v in parent["result"].items() if k != "continued_to"} == task["result"]
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == old_chapters
    assert domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint") == old_steps
    assert ledger(worker) == old_calls and len(frames) == 3
    assert post(client, parent, body).json()["id"] == child["id"]
    assert post(client, parent, {**body, "client_request_id": "another-nonce"}).status_code == 409
    lookup = client.get(
        f"/api/tasks/{task['id']}/author-source-clarifications",
        params={"client_request_id": body["client_request_id"]},
    )
    assert lookup.json()["task"]["id"] == child["id"]


@pytest.mark.parametrize("fault", ["empty", "stale_basis"])
def test_explicit_content_and_current_basis_required(domain, client, failed, fault):
    worker, task, _ = failed
    value = preview(client, task)
    changes = {
        "empty": {"clarification": " \n "},
        "stale_basis": {"preview_basis_sha256": "0" * 64},
    }[fault]
    before = domain.db.all("SELECT * FROM tasks ORDER BY id"), ledger(worker)
    response = post(client, task, request(value, **changes))
    assert response.status_code == 409
    assert (domain.db.all("SELECT * FROM tasks ORDER BY id"), ledger(worker)) == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_step",
        "unknown_zero",
        "incomplete_json",
        "valid_raw",
        "wrong_dispatch",
        "wrong_repair",
        "missing_completion",
        "different_attempt",
        "fake_response",
        "changed_source",
        "body",
        "future_body",
        "active",
        "story",
        "wrong_execution",
    ],
)
def test_incomplete_unknown_changed_or_late_stops_are_not_admitted(domain, client, failed, fault):
    worker, task, _ = failed
    events = domain.db.all(
        "SELECT id,kind,payload FROM task_events WHERE task_id=? ORDER BY id", (task["id"],)
    )
    step = domain.db.all(
        "SELECT fingerprint,result FROM task_steps WHERE task_id=? ORDER BY fingerprint", (task["id"],)
    )[0]
    if fault == "unknown_zero":
        with worker.budget.db(True) as usage:
            usage.execute(
                "UPDATE calls SET status='unknown',reserved=0,charged=NULL WHERE id=?",
                (json.loads(step["result"])["call_id"],),
            )
    elif fault == "body":
        chapter = domain.db.all("SELECT id FROM chapters WHERE branch_id=?", (task["branch_id"],))[0]
        domain.save(chapter["id"], 0, "已经保存正文。")
    else:
        with domain.db.connect(True) as tx:
            if fault == "missing_step":
                tx.execute(
                    "DELETE FROM task_steps WHERE task_id=? AND fingerprint=?",
                    (task["id"], step["fingerprint"]),
                )
            elif fault in {"incomplete_json", "valid_raw"}:
                raw = json.loads(step["result"])
                if fault == "incomplete_json":
                    raw["text"] = "{incomplete"
                else:
                    output = json.loads(raw["text"])
                    output["unit_checks"][2].update(status="not_scope_related", observation_ids=[])
                    raw["text"] = dumps(output)
                tx.execute(
                    "UPDATE task_steps SET result=? WHERE task_id=? AND fingerprint=?",
                    (dumps(raw), task["id"], step["fingerprint"]),
                )
            elif fault in {"wrong_dispatch", "wrong_repair"}:
                event = next(
                    e
                    for e in events
                    if e["kind"] == ("step_dispatched" if fault == "wrong_dispatch" else "citation_repair")
                )
                data = json.loads(event["payload"])
                if fault == "wrong_dispatch":
                    data["request"]["semantic_prompt_sha256"] = "0" * 64
                else:
                    data["issues"] = ["invented validation issue"]
                tx.execute("UPDATE task_events SET payload=? WHERE id=?", (dumps(data), event["id"]))
            elif fault == "missing_completion":
                tx.execute("DELETE FROM task_events WHERE task_id=? AND kind='step_completed'", (task["id"],))
            elif fault == "different_attempt":
                domain.db.event(tx, task["id"], "running", {"attempt": "other-attempt"})
            elif fault == "future_body":
                domain.db.event(tx, task["id"], "chapter_started", {"number": 2})
            elif fault == "active":
                tx.execute(
                    "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('active','module',?,?,'queued','{}','{}',0,0)",
                    (task["novel_id"], task["branch_id"]),
                )
            else:
                state = deepcopy(task["result"])
                record = state[authored.RECEIPTS]["chapter:1"]
                if fault == "fake_response":
                    record["phases"]["observe"]["response"] = {}
                elif fault == "changed_source":
                    record["source"]["text"] += "新增剧情。"
                elif fault == "wrong_execution":
                    state[execution.KEY] = execution.LEGACY_VERSION
                else:
                    state["expected_story_revision"] -= 1
                tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(state), task["id"]))
    before = domain.db.all("SELECT * FROM tasks ORDER BY id"), ledger(worker)
    response = client.get(f"/api/tasks/{task['id']}/author-source-clarification-preview")
    assert response.status_code == 409, (fault, response.text)
    assert (domain.db.all("SELECT * FROM tasks ORDER BY id"), ledger(worker)) == before


@pytest.mark.parametrize(
    "outcome",
    ["pass", "unknown", "fail", "addition_unknown", "addition_change", "observe_unknown", "invalid_again"],
)
def test_child_still_requires_independent_original_and_addition_preservation(
    settings, domain, client, failed, outcome
):
    _, task, _ = failed
    value = preview(client, task)
    response = post(client, task, request(value))
    assert response.status_code == 202, response.text
    child = response.json()
    worker = Worker(settings)

    def transform(frame, value):
        action = frame["任务"]["动作"]
        if outcome == "invalid_again":
            return wrong_unit(frame, value)
        if outcome == "observe_unknown" and action == authored.ACTION:
            value["observations"][0]["kind"] = "unknown"
        if outcome in {"addition_unknown", "addition_change"} and action == bridge.ACTION:
            value["addition_checks"][0]["classification"] = (
                "unknown" if outcome == "addition_unknown" else "direction_change"
            )
        return value

    frames = native(
        worker, preserve=outcome if outcome in {"unknown", "fail"} else "pass", transform=transform
    )
    assert asyncio.run(worker.run_once())
    current = domain.task(child["id"])
    actions = [f["任务"]["动作"] for f in frames]
    if outcome == "pass":
        assert current["status"] == "needs_review", current["error"]
        assert actions[:5] == [
            authored.ACTION,
            "仅依据作者原话整理本场景情节",
            classification.ACTION,
            bridge.ACTION,
            "按章纲写完整正文",
        ]
        assert current["result"][bridge.CHECKS]["status"] == "ready"
        assert current["result"]["waiting_artifact"]
    else:
        assert current["status"] == "blocked_runtime", current["error"]
        assert "按章纲写完整正文" not in actions
        assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (child["id"],))
        if outcome == "observe_unknown":
            record = current["result"][authored.RECEIPTS]["chapter:1"]
            assert len(frames) == 1 and record["state"] == "unverified"
            assert (
                record["phases"]["observe"]["response"]["inventory"]["observations"][0]["kind"] == "unknown"
            )
        if outcome in {"addition_unknown", "addition_change"}:
            assert actions[-1] == bridge.ACTION
            assert current["result"][bridge.CHECKS]["status"] == "unverified"
        if outcome == "invalid_again":
            assert len(frames) == 3
            # A fresh nonce must not buy the same chapter another clarification.
            assert (
                client.get(f"/api/tasks/{child['id']}/author-source-clarification-preview").status_code == 409
            )


@pytest.mark.parametrize("fault", ["raw_changed", "unknown_zero", "late_body"])
def test_queued_child_rechecks_known_failure_and_body_boundary(settings, domain, client, failed, fault):
    worker, task, _ = failed
    value = preview(client, task)
    child = post(client, task, request(value)).json()
    step = domain.db.all(
        "SELECT fingerprint,result FROM task_steps WHERE task_id=? ORDER BY fingerprint", (task["id"],)
    )[0]
    if fault == "unknown_zero":
        worker.budget.reserve(
            settings.providers[task["payload"]["account"]], "unrelated-zero-reserve", 0, 0, None
        )
    else:
        with domain.db.connect(True) as tx:
            if fault == "late_body":
                domain.db.event(tx, task["id"], "chapter_started", {"number": 1})
            else:
                raw = json.loads(step["result"])
                raw["text"] += "\n"
                tx.execute(
                    "UPDATE task_steps SET result=? WHERE task_id=? AND fingerprint=?",
                    (dumps(raw), task["id"], step["fingerprint"]),
                )
    before = ledger(worker)
    with pytest.raises(DomainError):
        bridge.ensure_runtime(child, domain, settings)
    assert ledger(worker) == before


@pytest.mark.parametrize("failed", [{"old_settled_without_output": True}], indirect=True)
def test_old_settled_missing_output_is_cost_history_not_current_source_return_proof(
    settings, domain, client, failed
):
    worker, task, _ = failed
    record = task["result"][authored.RECEIPTS]["chapter:1"]
    old_call_id = record["phases"]["observe"]["before_calls"][0]
    before_calls = ledger(worker)
    before_steps = domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint")
    old_call = next(call for call in before_calls if call["id"] == old_call_id)
    assert old_call["status"] == "settled" and old_call["charged"] > 0
    assert all(json.loads(step["result"])["call_id"] != old_call_id for step in before_steps)
    # The completed-unverified route retains its stricter full-owner proof.
    with worker.budget.db() as usage, pytest.raises(DomainError, match="完整步骤回执"):
        bridge.known_source_proof(domain, usage, record)
    value = preview(client, task)
    proof = value["source_review"]["observation_failure"]["proof"]
    assert old_call_id not in proof["call_ids"] and len(proof["call_ids"]) == 3
    assert value["budget"]["source_steps"] == proof == value["budget"]["observation_failure"]
    assert micro(value["budget"]["source_task_settled"]) == sum(call["charged"] for call in before_calls)
    assert micro(value["budget"]["remaining_run_limit"]) == micro(task["payload"]["budget_limit"]) - sum(
        call["charged"] for call in before_calls
    )
    response = post(client, task, request(value))
    assert response.status_code == 202, response.text
    bridge.ensure_runtime(response.json(), domain, settings)
    assert ledger(worker) == before_calls
    assert domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint") == before_steps
    assert domain.task(task["id"])["result"][authored.RECEIPTS]["chapter:1"] == record


@pytest.mark.parametrize("failed", [{"old_settled_without_output": True}], indirect=True)
def test_old_missing_output_with_unknown_cost_still_refuses_and_keeps_reserve(domain, client, failed):
    worker, task, _ = failed
    old_call = task["result"][authored.RECEIPTS]["chapter:1"]["phases"]["observe"]["before_calls"][0]
    with worker.budget.db(True) as usage:
        usage.execute("UPDATE calls SET status='unknown',charged=NULL,reserved=0 WHERE id=?", (old_call,))
    before = domain.db.all("SELECT * FROM tasks ORDER BY id"), ledger(worker)
    response = client.get(f"/api/tasks/{task['id']}/author-source-clarification-preview")
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert (domain.db.all("SELECT * FROM tasks ORDER BY id"), ledger(worker)) == before
