"""Known complete citation failures, isolated SQLite and MockTransport only."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from ainovel import authored_source_scope as authored
from ainovel import authored_source_execution as execution
from ainovel import author_source_observation_retry as retry
from ainovel.budget import micro
from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.plot_audit import interpret_plot
from ainovel.worker import Worker
from helpers.authored_classification_fixture import SOURCE, native
from helpers.author_observation_retry_fixture import known_returned_failure, wrong_unit


@pytest.fixture
def failed(settings, domain, story, client):
    return known_returned_failure(settings, domain, story, client=client)


def read(client, parent):
    response = client.get(f"/api/tasks/{parent['id']}/author-source-observation-retry-preview")
    assert response.status_code == 200, response.text
    return response.json()


def body(preview, nonce="one-known-returned-observation-child", **changes):
    return {
        "version": retry.VERSION,
        "client_request_id": nonce,
        "preview_basis_sha256": preview["request_basis"]["preview_basis_sha256"],
        "reason": "已核对三次完整引用失败，显式开始一次新执行模板；不是新增情节。",
        **changes,
    }


def test_exact_preview_child_paid_history_and_three_new_source_phases(settings, domain, client, failed):
    worker, parent, first, old_frames = failed
    old_steps = domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint")
    chapters = domain.db.all("SELECT * FROM chapters ORDER BY id")
    with worker.budget.db() as usage:
        old_calls = [dict(r) for r in usage.execute("SELECT * FROM calls ORDER BY id")]
    value = read(client, parent)
    assert value["source_policy"] == authored.CURRENT_VERSION
    assert value["old_execution_version"] == execution.LEGACY_VERSION
    assert value["new_execution_version"] == execution.VERSION
    assert value["eligible"] and value["starts_child"] and value["source_unchanged"]
    assert value["current_source"]["text"] == SOURCE
    proof = value["known_returned_proof"]
    assert len(proof["steps"]) == 3 and len(set(proof["call_ids"])) == 3
    assert (
        proof["attempt"] == parent["result"][authored.RECEIPTS]["chapter:1"]["phases"]["observe"]["attempt"]
    )
    own = sum(c["charged"] for c in old_calls if c["task_id"] == parent["id"])
    all_spent = sum(c["charged"] for c in old_calls)
    assert micro(value["budget"]["remaining_run_limit"]) == micro(parent["payload"]["budget_limit"]) - own
    assert (
        micro(value["budget"]["remaining_run_limit"]) == micro(first["payload"]["budget_limit"]) - all_spent
    )
    assert [c["id"] for c in proof["calls"]] == [s["call_id"] for s in proof["steps"]]
    assert sum(micro(c["charged"]) for c in proof["calls"]) == own
    assert all(c["status"] == "settled" and c["has_complete_step"] for c in proof["calls"])
    request = body(value)
    posted = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=request)
    assert posted.status_code == 202, posted.text
    child = posted.json()
    receipt = child["result"][retry.RECEIPT]
    assert receipt["previous_source_receipt"] == parent["result"][authored.RECEIPTS]["chapter:1"]
    assert child["result"]["plan"] == parent["result"]["plan"]
    assert child["payload"]["plots"] == parent["payload"]["plots"]
    assert child["payload"]["adoption"] == parent["payload"]["adoption"]
    assert child["result"]["repair_round"] == parent["result"]["repair_round"]
    assert child["payload"]["account"] == parent["payload"]["account"]
    assert child["result"][authored.KEY] == authored.CURRENT_VERSION
    assert child["result"][execution.KEY] == execution.VERSION
    assert child["payload"]["budget_limit"] == value["budget"]["remaining_run_limit"]
    assert retry.validate_task(child, domain) == receipt
    retry.ensure_runtime(child, domain, settings)
    parent_after = domain.task(parent["id"])
    assert parent_after["status"] == "continued" and parent_after["error"] == parent["error"]
    assert {k: v for k, v in parent_after["result"].items() if k != "continued_to"} == parent["result"]
    assert parent_after["result"]["continued_to"] == child["id"]
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters
    assert domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint") == old_steps
    with worker.budget.db() as usage:
        assert [dict(r) for r in usage.execute("SELECT * FROM calls ORDER BY id")] == old_calls
    new_worker = Worker(settings)
    new_worker.task = new_worker.claim()
    new_worker.attempt = new_worker.task["attempt"]
    assert new_worker.task["id"] == child["id"]
    new_frames = native(new_worker)
    contract = asyncio.run(interpret_plot(new_worker, SOURCE, source_kind="author_plot", source_position=1))
    assert contract["items"] and len(new_frames) == 3
    assert new_frames[0]["任务"][execution.KEY] == execution.VERSION
    assert all(request["reason"] not in dumps(f) for f in new_frames)
    assert all("前次产物" not in f["任务"] for f in new_frames)
    assert new_worker.task["result"][authored.RECEIPTS]["chapter:1"]["state"] == "ready"
    assert len(old_frames) == 3
    assert domain.db.all(
        "SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (parent["id"],)
    ) == [s for s in old_steps if s["task_id"] == parent["id"]]
    assert not domain.db.all("SELECT id FROM artifacts")
    assert client.get(f"/api/tasks/{child['id']}/author-source-observation-retry-preview").status_code == 409


def test_exact_nonce_replay_and_query_only_after_response_loss(settings, domain, client, failed):
    worker, parent, _, _ = failed
    value = read(client, parent)
    nonce = "opaque/nonce\nwith%2Fescape"
    request = body(value, nonce)
    first = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=request)
    assert first.status_code == 202, first.text
    before = worker.budget.report()
    rows = domain.db.all("SELECT id,status,result FROM tasks ORDER BY id")
    # Query is the recovery after a lost POST response, without another dispatch.
    queried = client.get(
        f"/api/tasks/{parent['id']}/author-source-observation-retries", params={"client_request_id": nonce}
    )
    assert queried.status_code == 200 and queried.json()["task"]["id"] == first.json()["id"]
    repeated = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=request)
    assert repeated.status_code == 202 and repeated.json()["id"] == first.json()["id"]
    assert domain.db.all("SELECT id,status,result FROM tasks ORDER BY id") == rows
    assert worker.budget.report() == before
    for changed in ({"reason": "另一决定"}, {"preview_basis_sha256": "0" * 64}):
        assert (
            client.post(
                f"/api/tasks/{parent['id']}/author-source-observation-retries", json={**request, **changed}
            ).status_code
            == 409
        )
    absent = client.get(
        f"/api/tasks/{parent['id']}/author-source-observation-retries",
        params={"client_request_id": "never-posted-nonce"},
    )
    assert absent.status_code == 200 and absent.json()["task"] is None
    assert (
        client.post(
            f"/api/tasks/{parent['id']}/author-source-observation-retries",
            json=body(value, "another-new-nonce-after-parent-continued"),
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_step",
        "missing_call",
        "unknown_zero",
        "wrong_usage",
        "duplicate_call",
        "malformed_json",
        "wrong_schema",
        "wrong_prompt",
        "wrong_wire_version",
        "wrong_context",
        "wrong_round",
        "wrong_error",
        "missing_completion",
        "other_attempt",
        "missing_checkpoint",
        "normalized_response",
        "not_sent",
        "valid_raw",
        "body",
        "candidate",
        "later_dispatch",
        "later_source",
        "active",
        "ending",
        "source",
        "story",
        "account",
    ],
)
def test_every_incomplete_or_ineligible_proof_refuses_without_writing(
    settings, domain, client, failed, fault
):
    worker, parent, _, _ = failed
    events = domain.db.all(
        "SELECT id,kind,payload FROM task_events WHERE task_id=? ORDER BY id", (parent["id"],)
    )
    steps = domain.db.all(
        "SELECT fingerprint,result FROM task_steps WHERE task_id=? ORDER BY fingerprint", (parent["id"],)
    )
    if fault in {"missing_call", "unknown_zero", "wrong_usage"}:
        with worker.budget.db(True) as usage:
            call = usage.execute(
                "SELECT id FROM calls WHERE task_id=? ORDER BY id LIMIT 1", (parent["id"],)
            ).fetchone()[0]
            if fault == "missing_call":
                usage.execute("DELETE FROM calls WHERE id=?", (call,))
            elif fault == "unknown_zero":
                usage.execute("UPDATE calls SET status='unknown',reserved=0,charged=NULL WHERE id=?", (call,))
            else:
                usage.execute("UPDATE calls SET output_tokens=output_tokens+1 WHERE id=?", (call,))
    elif fault == "account":
        settings.providers["deepseek"] = replace(
            settings.providers["deepseek"], model="silently-different-model"
        )
    elif fault == "body":
        domain.save(
            parent["result"][authored.SNAPSHOT]["sources"]
            and domain.db.all("SELECT id FROM chapters WHERE branch_id=?", (parent["branch_id"],))[0]["id"],
            0,
            "已有正文。",
        )
    else:
        with domain.db.connect(True) as tx:
            if fault == "missing_step":
                tx.execute(
                    "DELETE FROM task_steps WHERE task_id=? AND fingerprint=?",
                    (parent["id"], steps[0]["fingerprint"]),
                )
            elif fault in {"duplicate_call", "malformed_json", "valid_raw"}:
                target_step = steps[0]
                if fault == "valid_raw":
                    first_sent = json.loads(
                        next(e for e in events if e["kind"] == "step_dispatched")["payload"]
                    )
                    target_step = next(s for s in steps if s["fingerprint"] == first_sent["fingerprint"])
                raw = json.loads(target_step["result"])
                if fault == "duplicate_call":
                    raw["call_id"] = json.loads(steps[1]["result"])["call_id"]
                elif fault == "malformed_json":
                    raw["text"] = "{not-complete-json"
                else:
                    value = json.loads(raw["text"])
                    value["unit_checks"][2].update(status="not_scope_related", observation_ids=[])
                    raw["text"] = dumps(value)
                tx.execute(
                    "UPDATE task_steps SET result=? WHERE task_id=? AND fingerprint=?",
                    (dumps(raw), parent["id"], target_step["fingerprint"]),
                )
            elif fault in {"wrong_schema", "wrong_prompt", "wrong_wire_version", "wrong_context"}:
                event = next(e for e in events if e["kind"] == "step_dispatched")
                data = json.loads(event["payload"])
                if fault == "wrong_schema":
                    data["schema"] = "OtherSchema"
                elif fault == "wrong_prompt":
                    data["request"]["prompt_sha256"] = "0" * 64
                elif fault == "wrong_wire_version":
                    data["request"]["wire_schema_version"] = "invented-wire/1"
                else:
                    data["request"]["semantic_prompt_sha256"] = "0" * 64
                tx.execute("UPDATE task_events SET payload=? WHERE id=?", (dumps(data), event["id"]))
            elif fault in {"wrong_round", "wrong_error"}:
                event = next(e for e in events if e["kind"] == "citation_repair")
                data = json.loads(event["payload"])
                data["round" if fault == "wrong_round" else "issues"] = (
                    8 if fault == "wrong_round" else ["伪造错误"]
                )
                tx.execute("UPDATE task_events SET payload=? WHERE id=?", (dumps(data), event["id"]))
            elif fault in {"missing_completion", "missing_checkpoint"}:
                kind = "step_completed" if fault == "missing_completion" else "checkpoint"
                tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (parent["id"], kind))
            elif fault == "other_attempt":
                domain.db.event(tx, parent["id"], "running", {"attempt": "different-attempt"})
            elif fault in {"candidate", "later_dispatch"}:
                chapter = domain.db.all("SELECT id FROM chapters WHERE branch_id=?", (parent["branch_id"],))[
                    0
                ]["id"]
                if fault == "candidate":
                    tx.execute(
                        "INSERT INTO artifacts(id,task_id,kind,novel_id,branch_id,chapter_id,base_revision,status,payload,basis,created) VALUES('other-candidate',?,'chapter_draft',?,?,?,0,'candidate','{}','{}',0)",
                        (parent["id"], parent["novel_id"], parent["branch_id"], chapter),
                    )
                else:
                    domain.db.event(tx, parent["id"], "chapter_started", {"number": 1})
            elif fault == "active":
                tx.execute(
                    "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('active-test','module',?,?,'queued','{}','{}',0,0)",
                    (parent["novel_id"], parent["branch_id"]),
                )
            else:
                state = deepcopy(parent["result"])
                record = state[authored.RECEIPTS]["chapter:1"]
                if fault == "normalized_response":
                    record["phases"]["observe"]["response"] = {}
                elif fault == "not_sent":
                    record["phases"]["observe"]["status"] = "not_sent"
                elif fault == "ending":
                    state["plan"]["ending"] = "改变原结局"
                elif fault == "source":
                    record["source"]["text"] += "偷加情节"
                elif fault == "later_source":
                    state[authored.RECEIPTS]["chapter:2"] = {"source": {"number": 2}}
                else:
                    state["expected_story_revision"] -= 1
                tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(state), parent["id"]))
    before = domain.db.all("SELECT * FROM tasks ORDER BY id")
    response = client.get(f"/api/tasks/{parent['id']}/author-source-observation-retry-preview")
    assert response.status_code == 409, (fault, response.text)
    if fault == "valid_raw":
        assert "原缓存已有可诚实通过" in response.json()["message"]
    assert domain.db.all("SELECT * FROM tasks ORDER BY id") == before


def test_zero_reserve_foreign_unknown_is_preserved_but_same_provider_unknown_stops(
    settings, domain, client, failed
):
    worker, parent, _, _ = failed
    foreign = worker.budget.reserve(settings.providers["openai_1"], "unrelated-old-foreign", 0, 0, None)
    assert read(client, parent)["eligible"]
    with worker.budget.db() as usage:
        assert usage.execute("SELECT reserved,charged FROM calls WHERE id=?", (foreign,)).fetchone()[:] == (
            0,
            None,
        )
    own = worker.budget.reserve(settings.providers["deepseek"], "unrelated-old-same-provider", 0, 0, None)
    response = client.get(f"/api/tasks/{parent['id']}/author-source-observation-retry-preview")
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    with worker.budget.db() as usage:
        assert usage.execute("SELECT reserved,charged FROM calls WHERE id=?", (own,)).fetchone()[:] == (
            0,
            None,
        )


def test_fresh_basis_and_transaction_state_are_required(settings, domain, client, failed):
    worker, parent, _, _ = failed
    value = read(client, parent)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET updated=updated+1 WHERE id=?", (parent["id"],))
    response = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=body(value))
    assert response.status_code == 409
    assert response.json()["code"] == "author_observation_retry_basis"
    with pytest.raises(DomainError) as error:
        retry.retry(domain, worker.budget, parent, body(value, "stale-object-transaction-attempt"))
    assert error.value.code == "task_changed"
    assert not domain.db.all(
        "SELECT id FROM tasks WHERE json_extract(result,'$.author_source_observation_retry_version') IS NOT NULL"
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"reason": "   "},
        {"version": "author-source-observation-retry/2"},
        {"account": "openai_1"},
        {"clarification": "新剧情"},
        {"budget_limit": 12},
    ],
)
def test_no_payload_policy_account_budget_or_story_override(client, failed, changed):
    _, parent, _, _ = failed
    response = client.post(
        f"/api/tasks/{parent['id']}/author-source-observation-retries",
        json=body(read(client, parent), **changed),
    )
    assert response.status_code in {409, 422}


def test_old_clarification_recheck_and_plain_resume_are_not_weakened(settings, domain, client, failed):
    _, parent, _, _ = failed
    assert client.get(f"/api/tasks/{parent['id']}/author-source-recheck-preview").status_code == 409
    assert client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview").status_code == 409
    assert (
        client.post(
            f"/api/tasks/{parent['id']}/resume",
            json={"regenerate_pending": True, "acknowledge_unknown": True, "reason": "无正文不重做"},
        ).status_code
        == 409
    )
    value = read(client, parent)
    response = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=body(value))
    assert response.status_code == 202
    child = response.json()
    with domain.db.connect(True) as tx:
        state = deepcopy(child["result"])
        state.pop(retry.KEY)
        state.pop(retry.RECEIPT)
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(state), child["id"]))
    with pytest.raises(DomainError):
        authored.validate_task(domain.task(child["id"]), domain)


@pytest.mark.parametrize("status", ["queued", "running", "paused", "needs_review", "cancelled", "continued"])
def test_only_final_known_blocked_runtime_is_eligible(domain, client, failed, status):
    _, parent, _, _ = failed
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status=? WHERE id=?", (status, parent["id"]))
    assert client.get(f"/api/tasks/{parent['id']}/author-source-observation-retry-preview").status_code == 409


def test_creation_rollback_is_atomic_across_nonce_parent_and_child(
    settings, domain, client, failed, monkeypatch
):
    worker, parent, _, _ = failed
    request = body(read(client, parent), "transaction-rollback-observation-retry")
    before = domain.db.all("SELECT * FROM tasks ORDER BY id")
    requests = domain.db.all("SELECT * FROM task_requests ORDER BY key")
    fees = worker.budget.report()

    def fail(*args):
        raise DomainError("injected_transaction", "隔离事务故障。", 409)

    monkeypatch.setattr(authored, "event", fail)
    response = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=request)
    assert response.status_code == 409
    assert domain.db.all("SELECT * FROM tasks ORDER BY id") == before
    assert domain.db.all("SELECT * FROM task_requests ORDER BY key") == requests
    assert worker.budget.report() == fees


@pytest.mark.parametrize(
    "fault", ["account", "parent_fee", "parent_raw", "late_body", "missing_receipt_event"]
)
def test_every_call_rechecks_recovery_proof_and_late_boundary(settings, domain, client, failed, fault):
    worker, parent, _, _ = failed
    response = client.post(
        f"/api/tasks/{parent['id']}/author-source-observation-retries", json=body(read(client, parent))
    )
    assert response.status_code == 202, response.text
    child = response.json()
    if fault == "account":
        settings.providers["deepseek"] = replace(settings.providers["deepseek"], model="not-original-model")
    elif fault == "parent_fee":
        with worker.budget.db(True) as usage:
            usage.execute("UPDATE calls SET charged=charged+1 WHERE task_id=?", (parent["id"],))
    else:
        with domain.db.connect(True) as tx:
            if fault == "parent_raw":
                row = tx.execute(
                    "SELECT fingerprint,result FROM task_steps WHERE task_id=? ORDER BY fingerprint LIMIT 1",
                    (parent["id"],),
                ).fetchone()
                raw = json.loads(row["result"])
                value = json.loads(raw["text"])
                value["note"] += "非原始返回。"
                raw["text"] = dumps(value)
                tx.execute(
                    "UPDATE task_steps SET result=? WHERE task_id=? AND fingerprint=?",
                    (dumps(raw), parent["id"], row["fingerprint"]),
                )
            elif fault == "missing_receipt_event":
                tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (child["id"], retry.EVENT))
            else:
                tx.execute(
                    "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('late-bystander','module',?,?,'blocked_runtime','{}','{}',0,0)",
                    (parent["novel_id"], parent["branch_id"]),
                )
                domain.db.event(tx, "late-bystander", "chapter_started", {"number": 1})
    fresh = Worker(settings)
    fresh.task = fresh.claim()
    fresh.attempt = fresh.task["attempt"]
    frames = native(fresh)
    # Even a direct body call cannot bypass the runtime hook after preparation.
    with pytest.raises(DomainError):
        asyncio.run(fresh.call({"动作": "按章纲写完整正文"}, context={}))
    assert frames == []
    assert not domain.db.all("SELECT id FROM artifacts")


def test_new_unknown_result_retains_reserve_and_cannot_purchase_another_retry(
    settings, domain, client, failed
):
    _, parent, _, _ = failed
    request = body(read(client, parent))
    response = client.post(f"/api/tasks/{parent['id']}/author-source-observation-retries", json=request)
    child = response.json()
    first = Worker(settings)
    frames = native(first, timeout_action=authored.ACTION)
    assert asyncio.run(first.run_once()) and len(frames) == 1
    stopped = domain.task(child["id"])
    assert stopped["status"] == "blocked_runtime"
    fees = first.budget.report()
    assert next(row for row in fees if row["provider"] == "deepseek")["reserved_unknown"] > 0
    assert client.get(f"/api/tasks/{child['id']}/author-source-observation-retry-preview").status_code == 409
    assert (
        client.post(f"/api/tasks/{child['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    second = Worker(settings)
    second_frames = native(second)
    assert asyncio.run(second.run_once())
    assert second_frames == [] and second.budget.report() == fees
    query = client.get(
        f"/api/tasks/{parent['id']}/author-source-observation-retries",
        params={"client_request_id": request["client_request_id"]},
    )
    assert query.status_code == 200 and query.json()["task"]["id"] == child["id"]
    assert not domain.db.all("SELECT id FROM artifacts")


def test_full_worker_preserves_manual_adoption_and_original_revision_zero(settings, domain, client, failed):
    _, parent, _, _ = failed
    response = client.post(
        f"/api/tasks/{parent['id']}/author-source-observation-retries", json=body(read(client, parent))
    )
    child = response.json()
    worker = Worker(settings)
    frames = native(worker)
    assert asyncio.run(worker.run_once())
    result = domain.task(child["id"])
    assert result["status"] == "needs_review", result["error"]
    artifact = domain.artifact(result["result"]["waiting_artifact"])
    assert artifact["status"] == "candidate" and artifact["task_id"] == child["id"]
    chapter = domain.chapter(artifact["chapter_id"], child["branch_id"])
    assert chapter["revision"] == 0 and chapter["content"] == ""
    assert not domain.db.all("SELECT * FROM receipts WHERE artifact_id=?", (artifact["id"],))
    assert [f["任务"]["动作"] for f in frames[:3]] == [
        authored.ACTION,
        "仅依据作者原话整理本场景情节",
        "独立核对作者来源分类与完整原意保持",
    ]


def test_later_chapter_keeps_prior_paid_steps_and_deducts_all_current_task_costs(
    settings, domain, story, client
):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="segment",
        brief="匿名后章费用与来源检查点。",
        chapters=2,
        plots=[{"number": n, "title": f"匿名第{n}章", "plot": SOURCE, "fixed": ["不动手"]} for n in (1, 2)],
        account="deepseek",
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id="historical-source2-two-chapters",
    ).model_dump(exclude_none=True)
    # Reproduce a historical server-created /2 task, never a client marker.
    with patch.object(execution, "NEW_TASK_VERSION", execution.LEGACY_VERSION):
        queued = client.post("/api/workflows", json=payload)
    assert queued.status_code == 202, queued.text
    first = Worker(settings)
    native(first)
    assert asyncio.run(first.run_once())
    first_stop = domain.task(queued.json()["id"])
    assert first_stop["status"] == "needs_review", first_stop["error"]
    artifact = domain.artifact(first_stop["result"]["waiting_artifact"])
    domain.accept(
        artifact["id"], "accept-isolated-first-chapter", candidate_revision=artifact["candidate_revision"]
    )
    assert (
        client.post(f"/api/tasks/{first_stop['id']}/resume", json={"acknowledge_changes": True}).status_code
        == 200
    )
    target_token = authored.token(
        {"key": "chapter:2", "kind": "author_plot", "text": SOURCE}, authored.CURRENT_VERSION
    )

    def second_only(frame, value):
        return wrong_unit(frame, value) if frame["任务"].get("input_sha256") == target_token else value

    second = Worker(settings)
    frames = native(second, transform=second_only)
    assert asyncio.run(second.run_once())
    parent = domain.task(first_stop["id"])
    assert parent["status"] == "blocked_runtime" and parent["result"]["next_chapter"] == 2
    assert len(frames) == 3
    slot = parent["result"][authored.RECEIPTS]["chapter:2"]["phases"]["observe"]
    assert slot["before_steps"] and slot["before_calls"]
    value = read(client, parent)
    assert value["chapter"] == 2 and len(value["known_returned_proof"]["calls"]) == 3
    three_costs = sum(micro(c["charged"]) for c in value["known_returned_proof"]["calls"])
    own = micro(value["budget"]["source_task_settled"])
    assert three_costs < own
    assert micro(value["budget"]["remaining_run_limit"]) == micro(parent["payload"]["budget_limit"]) - own
    assert domain.chapter(artifact["chapter_id"], parent["branch_id"])["revision"] == 1
