"""An explicit policy child keeps the adopted checkpoint and every paid history."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import asyncio
from time import time

import pytest

from ainovel.budget import Budget
from ainovel.conditional_review import KEY as CONDITIONAL_KEY, VERSION as CONDITIONAL_VERSION
from ainovel.db import DomainError, dumps, uid
from ainovel.flow_entailment import KEY, VERSION, enabled
from ainovel.flow_policy_continuation import continue_policy
from ainovel.source_interpretation_correction import bounded_run_limit
from ainovel.worker import Worker
from test_conditional_continuation import (
    legacy_checkpoint as legacy_checkpoint,
    ledger_rows,
    paid_step,
    update_task,
)


@pytest.fixture
def parent(domain, legacy_checkpoint):
    state = deepcopy(legacy_checkpoint["result"])
    state.pop(KEY, None)
    state[CONDITIONAL_KEY] = CONDITIONAL_VERSION
    state["plot_contract_version"] = "author-plot-audit/2"
    state.update(
        repair_round=1,
        generation_epoch="keep-old-generation",
        character_field_repair_version="character-field-repair/2",
        character_repair_history=[{"original_scope": ["old-note"], "round": 1}],
        interventions=[{"reason": "原作者处理记录"}],
        policy_continuation={"source_policy": "旧条件政策历史不能覆盖"},
    )
    return update_task(
        domain,
        legacy_checkpoint["id"],
        payload=dumps({**legacy_checkpoint["payload"], "plot_contract_check": True}),
        result=state,
    )


def preview(client, task):
    return client.get(f"/api/tasks/{task['id']}/continue-flow-policy-preview")


def request(client, task, nonce="explicit-flow-policy-child"):
    response = preview(client, task)
    assert response.status_code == 200, response.text
    return {
        "client_request_id": nonce,
        "preview_basis_sha256": response.json()["preview_basis_sha256"],
        KEY: VERSION,
        "reason": "明确为后续新审查启用来源蕴含，旧结果不追认。",
    }


def submit(client, task, body):
    return client.post(f"/api/tasks/{task['id']}/continue-flow-policy", json=body)


def auxiliary(domain, task, *, status="succeeded", kind="source_interpretation_correction"):
    identifier = uid()
    key = (
        "source_correction_snapshot"
        if kind == "source_interpretation_correction"
        else "source_corrected_recheck_snapshot"
    )
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                kind,
                task["novel_id"],
                task["branch_id"],
                status,
                dumps({"account": task["payload"]["account"]}),
                dumps({key: {"source_workflow_id": task["id"]}}),
                time(),
                time(),
            ),
        )
    return domain.task(identifier)


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
def test_explicit_child_keeps_36_chapter_plan_other_policies_and_every_history(
    client, domain, settings, parent, mode
):
    payload = {**parent["payload"], "mode": mode, "chapters": 36}
    payload["plots"] = [
        {"number": i, "title": f"第{i}章", "plot": "保留原始规划。", "fixed": []} for i in range(1, 37)
    ]
    state = {**parent["result"], "plan": {"chapters": deepcopy(payload["plots"]), "ending": "完整原结局"}}
    parent = update_task(domain, parent["id"], payload=dumps(payload), result=state)
    paid_step(settings, domain, parent, 1)
    before = {
        table: domain.db.all(f"SELECT * FROM {table}")
        for table in ("chapters", "artifacts", "task_steps", "receipts")
    }
    budget_before = ledger_rows(Budget(settings))
    body = request(client, parent)
    started = submit(client, parent, body)
    assert started.status_code == 202, started.text
    child = started.json()
    assert child["id"] != parent["id"] and child["status"] == "queued"
    assert child["payload"] == {
        **parent["payload"],
        "client_request_id": body["client_request_id"],
        "budget_limit": 4,
    }
    assert child["result"][KEY] == VERSION and child["result"]["continued_from"] == parent["id"]
    for key, value in parent["result"].items():
        assert child["result"][key] == value
    assert child["result"]["flow_policy_continuation"]["request"] == body
    assert child["pause"] == child["cancel"] == 0 and child["attempt"] is None
    old = domain.task(parent["id"])
    assert KEY not in old["result"] and old["payload"] == parent["payload"]
    assert old["result"] == {**parent["result"], "continued_to": child["id"]}
    assert old["status"] == "continued"
    assert all(domain.db.all(f"SELECT * FROM {table}") == rows for table, rows in before.items())
    assert ledger_rows(Budget(settings)) == budget_before
    assert enabled(type("W", (), {"task": child})())
    assert not enabled(type("W", (), {"task": old})())


def test_conditional_v1_old_endpoint_still_rejects_and_preview_is_read_only(client, domain, parent):
    before = domain.db.all("SELECT * FROM tasks")
    assert preview(client, parent).status_code == 200
    assert domain.db.all("SELECT * FROM tasks") == before
    response = client.post(
        f"/api/tasks/{parent['id']}/continue-conditional-policy",
        json={"client_request_id": "old-conditional-must-reject", "reason": "不能用旧入口升级FLOW"},
    )
    assert response.status_code == 409 and response.json()["code"] == "conditional_checkpoint"
    assert domain.task(parent["id"]) == parent


@pytest.mark.parametrize(
    "column",
    ["waiting_artifact", "waiting_structure", "pending_structure", "pause_after_maintenance_requested"],
)
def test_pending_adoption_and_maintenance_are_not_bypassed(client, domain, parent, column):
    state = {**parent["result"], column: "still-pending"}
    current = update_task(domain, parent["id"], result=state)
    response = preview(client, current)
    assert response.status_code == 409
    assert domain.task(current["id"]) == current


@pytest.mark.parametrize("marker", [None, VERSION, "source-flow-entailment/future"])
def test_existing_or_unknown_marker_never_overwritten(client, domain, parent, marker):
    current = update_task(domain, parent["id"], result={**parent["result"], KEY: marker})
    assert preview(client, current).status_code == 409
    assert domain.task(current["id"]) == current


@pytest.mark.parametrize("status", ["needs_review", "queued", "running", "cancelled", "blocked_runtime"])
def test_only_paused_checkpoint_is_eligible(client, domain, parent, status):
    current = update_task(domain, parent["id"], status=status)
    assert preview(client, current).status_code == 409
    assert domain.task(current["id"]) == current


@pytest.mark.parametrize("change", ["text", "candidate", "dispatch", "preparation"])
def test_next_chapter_must_be_clean_before_any_prose_or_preparation(client, domain, parent, change):
    chapter = domain.create_chapter(parent["branch_id"], "第二章", 2)
    state = {**parent["result"], "expected_story_revision": chapter["story_revision"]}
    if change == "preparation":
        state["plot_preparations"] = {"2": {"status": "not_sent", "round": 0}}
    current = update_task(domain, parent["id"], result=state)
    with domain.db.connect(True) as tx:
        if change == "text":
            tx.execute("UPDATE chapters SET content='已有手写稿' WHERE id=?", (chapter["id"],))
        if change == "dispatch":
            domain.db.event(tx, current["id"], "chapter_started", {"number": 2})
    if change == "candidate":
        domain.stage(
            {
                "novel_id": current["novel_id"],
                "branch_id": current["branch_id"],
                "chapter_id": chapter["id"],
                "base_revision": 0,
            },
            "chapter_draft",
            {"text": "未采纳旧候选"},
            {},
            current["id"],
        )
    assert preview(client, current).status_code == 409
    assert domain.task(current["id"]) == current


def test_stale_basis_preserves_input_and_creates_nothing(client, domain, parent):
    body = request(client, parent)
    state = {
        **parent["result"],
        "interventions": [*parent["result"]["interventions"], {"reason": "新处理记录"}],
    }
    current = update_task(domain, parent["id"], result=state)
    response = submit(client, current, body)
    assert response.status_code == 409 and response.json()["code"] == "flow_basis_changed"
    assert domain.task(current["id"]) == current
    assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (body["client_request_id"],)) == []


def test_exact_idempotency_and_original_nonce_get_survive_parent_and_configuration_changes(
    client, domain, settings, parent
):
    body = request(client, parent)
    first = submit(client, parent, body)
    assert first.status_code == 202, first.text
    settings.providers.pop(parent["payload"]["account"])
    replay = submit(client, parent, body)
    assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]
    receipt = client.get(f"/api/tasks/{parent['id']}/flow-policy-continuations/{body['client_request_id']}")
    assert receipt.status_code == 200 and receipt.json()["id"] == first.json()["id"]
    assert submit(client, parent, {**body, "reason": "另一个决定"}).status_code == 409
    absent = client.get(f"/api/tasks/{parent['id']}/flow-policy-continuations/still-unknown-nonce")
    assert absent.status_code == 404


def test_concurrent_identical_submissions_create_one_child(client, domain, settings, parent):
    body = request(client, parent)

    def start():
        return continue_policy(domain, Budget(settings), parent, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: start(), range(2)))
    assert first["id"] == second["id"]
    assert len(domain.db.all("SELECT * FROM task_requests WHERE key=?", (body["client_request_id"],))) == 1


@pytest.mark.parametrize(
    "extra", [{"account": "openai_2"}, {"budget_limit": 10}, {"flow_source_entailment_version": "future/2"}]
)
def test_request_cannot_switch_account_raise_budget_or_choose_unknown_version(client, domain, parent, extra):
    body = request(client, parent)
    assert submit(client, parent, {**body, **extra}).status_code == 422
    assert domain.task(parent["id"]) == parent


@pytest.mark.parametrize(
    "source", ["workflow", "source_interpretation_correction", "source_corrected_candidate_recheck"]
)
def test_unknown_family_usage_preserves_ledger_and_stops_preview(client, domain, settings, parent, source):
    task = parent if source == "workflow" else auxiliary(domain, parent, kind=source)
    ledger = Budget(settings)
    call = ledger.reserve(settings.providers["deepseek"], task["id"], 1, 1)
    ledger.settle(call)
    before = ledger_rows(ledger)
    response = preview(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert ledger_rows(ledger) == before
    assert domain.task(parent["id"]) == parent


def test_auxiliary_fees_stay_in_original_envelope_without_double_subtraction(
    client, domain, settings, parent
):
    aux1 = auxiliary(domain, parent)
    aux2 = auxiliary(domain, parent, kind="source_corrected_candidate_recheck")
    paid_step(settings, domain, parent, 1)
    paid_step(settings, domain, aux1, 0.25)
    paid_step(settings, domain, aux2, 0.5)
    body = request(client, parent)
    details = preview(client, parent).json()["budget"]
    assert details["remaining_run_limit"] == 4
    assert details["auxiliary_settled"] == 0.75
    assert details["effective_remaining_run_limit"] == 3.25
    child = submit(client, parent, body).json()
    assert child["payload"]["budget_limit"] == 4
    worker = Worker(settings)
    worker.task = child
    assert bounded_run_limit(worker) == 3.25
    assert child["result"]["flow_policy_continuation"]["budget"]["effective_remaining_run_limit"] == 3.25


def test_known_not_sent_auxiliary_is_safe_but_unknown_dispatch_is_not(client, domain, parent):
    aux = auxiliary(domain, parent, status="blocked_runtime")
    with domain.db.connect(True) as tx:
        domain.db.event(tx, aux["id"], "step_dispatched", {"fingerprint": "original-fp"})
        dispatch = tx.execute("SELECT MAX(id) FROM task_events WHERE task_id=?", (aux["id"],)).fetchone()[0]
    blocked = preview(client, parent)
    assert blocked.status_code == 409 and blocked.json()["code"] == "unknown_result"
    with domain.db.connect(True) as tx:
        domain.db.event(
            tx, aux["id"], "step_not_sent", {"fingerprint": "wrong-fp", "dispatch_event_id": dispatch}
        )
    assert preview(client, parent).status_code == 409
    with domain.db.connect(True) as tx:
        domain.db.event(
            tx, aux["id"], "step_not_sent", {"fingerprint": "original-fp", "dispatch_event_id": dispatch}
        )
    assert preview(client, parent).status_code == 200


def test_transaction_rechecks_a_changed_parent_and_other_active_task(client, domain, settings, parent):
    body = request(client, parent)
    changed = update_task(domain, parent["id"], updated=parent["updated"] + 1)
    with pytest.raises(DomainError) as error:
        continue_policy(domain, Budget(settings), parent, body)
    assert error.value.code == "task_changed"
    domain.queue(
        "workflow", {**parent["payload"], "client_request_id": "competing-flow-work"}, parent["branch_id"]
    )
    response = submit(client, changed, body)
    assert response.status_code == 409 and response.json()["code"] == "active_task"


def test_ancestor_nominal_limit_and_all_family_auxiliary_fees_are_subtracted_once(
    client, domain, settings, parent
):
    ancestor = domain.queue(
        "workflow",
        {**parent["payload"], "client_request_id": "old-account-continuation-ancestor"},
        parent["branch_id"],
    )
    ancestor = update_task(domain, ancestor["id"], status="continued")
    paid_step(settings, domain, ancestor, 2)
    aux_old = auxiliary(domain, ancestor)
    paid_step(settings, domain, aux_old, 0.25)
    parent = update_task(
        domain,
        parent["id"],
        payload=dumps({**parent["payload"], "budget_limit": 3}),
        result={**parent["result"], "continued_from": ancestor["id"]},
    )
    aux_now = auxiliary(domain, parent, kind="source_corrected_candidate_recheck")
    paid_step(settings, domain, parent, 1)
    paid_step(settings, domain, aux_now, 0.5)
    body = request(client, parent)
    started = submit(client, parent, body)
    assert started.status_code == 202, started.text
    child = started.json()
    assert child["payload"]["budget_limit"] == 2
    assert child["result"]["flow_policy_continuation"]["budget"]["effective_remaining_run_limit"] == 1.25
    worker = Worker(settings)
    worker.task = child
    assert bounded_run_limit(worker) == 1.25


def test_child_next_chapter_runs_new_observation_and_source_check_without_old_replay(
    client, domain, settings, parent
):
    from test_source_identity_pipeline import offline_browser_model

    exclusive = "plot-scope-exclusive-e2e：本章中，沈砚把潮水读数只告诉师傅，并让师傅记下。"
    payload = deepcopy(parent["payload"])
    payload["plots"][1]["plot"] = exclusive
    payload["fixed"] = []
    state = deepcopy(parent["result"])
    state["plan"]["chapters"][1]["plot"] = exclusive
    parent = update_task(domain, parent["id"], payload=dumps(payload), result=state)
    before = deepcopy(parent)
    body = request(client, parent)
    child = submit(client, parent, body).json()
    model_class = offline_browser_model()
    model = model_class()
    generate = model.generate

    async def capture(account, task_id, system, prompt, max_output, **kwargs):
        assert body["reason"] not in prompt
        return await generate(account, task_id, system, prompt, max_output, **kwargs)

    model.generate = capture
    assert asyncio.run(Worker(settings, model).run_once())
    done = domain.task(child["id"])
    assert done["status"] == "needs_review", done["error"]
    assert done["result"]["next_chapter"] == 2
    candidate = domain.artifact(done["result"]["waiting_artifact"])
    audit = candidate["payload"]["plot_audit"]
    assert audit["direction_guard"]["role_observation_version"] == "source-role-observation/2"
    assert audit["direction_guard"]["source_entailment"]["version"] == VERSION
    assert audit["checks"][0]["guard_override"]["code"] == "restricted_recipient_unverified"
    assert candidate["payload"]["memory"] == {} and not candidate["payload"]["review_passed"]
    assert model.flow_review_calls == 1
    assert domain.task(parent["id"])["result"] == {**before["result"], "continued_to": child["id"]}
    assert KEY not in domain.task(parent["id"])["result"]
    assert domain.db.all(
        "SELECT id FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position", (parent["branch_id"],)
    ) == [
        {"id": domain.artifact(parent["result"]["latest_artifact"])["chapter_id"]},
        {"id": candidate["chapter_id"]},
    ]


def test_auxiliary_cost_exhaustion_cannot_be_avoided_by_a_fresh_task(client, domain, settings, parent):
    paid_step(settings, domain, parent, 4.5)
    aux = auxiliary(domain, parent)
    paid_step(settings, domain, aux, 0.6)
    before = ledger_rows(Budget(settings))
    response = preview(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "budget"
    assert ledger_rows(Budget(settings)) == before
    assert domain.task(parent["id"]) == parent


def test_openai_keys_share_family_envelope_and_provider_ledger(client, domain, settings, parent):
    parent = update_task(domain, parent["id"], payload=dumps({**parent["payload"], "account": "openai_1"}))
    aux = auxiliary(domain, parent, kind="source_corrected_candidate_recheck")
    aux = update_task(domain, aux["id"], payload=dumps({"account": "openai_2"}))
    paid_step(settings, domain, parent, 1)
    paid_step(settings, domain, aux, 0.5)
    before = ledger_rows(Budget(settings))
    body = request(client, parent)
    response = submit(client, parent, body)
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["account"] == "openai_1" and child["payload"]["budget_limit"] == 4
    worker = Worker(settings)
    worker.task = child
    assert bounded_run_limit(worker) == 3.5
    report = next(row for row in Budget(settings).report() if row["provider"] == "openai")
    assert report["limit"] == "20" and report["settled"] == 1.5
    assert ledger_rows(Budget(settings)) == before


def test_required_maintenance_and_current_adoption_are_checked_before_child(client, domain, parent):
    payload = {**parent["payload"], "structured_maintenance": True}
    parent = update_task(domain, parent["id"], payload=dumps(payload))
    missing = preview(client, parent)
    assert missing.status_code == 409 and missing.json()["code"] == "review_required"
    latest = domain.artifact(parent["result"]["latest_artifact"])
    chapter = domain.chapter(latest["chapter_id"], parent["branch_id"])
    state = {
        **parent["result"],
        "structure_history": [{"chapter_id": chapter["id"], "revision": chapter["revision"]}],
    }
    current = update_task(domain, parent["id"], result=state)
    assert preview(client, current).status_code == 200
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE chapters SET content=content||'另一窗口改稿' WHERE id=?", (chapter["id"],))
    assert preview(client, current).status_code == 409


def test_original_nonce_cannot_be_read_as_another_parent_or_mutated_decision(client, domain, parent):
    body = request(client, parent)
    first = submit(client, parent, body)
    assert first.status_code == 202
    other = domain.queue(
        "workflow",
        {**parent["payload"], "client_request_id": "other-flow-policy-source"},
        parent["branch_id"],
    )
    receipt = client.get(f"/api/tasks/{other['id']}/flow-policy-continuations/{body['client_request_id']}")
    assert receipt.status_code == 409 and receipt.json()["code"] == "idempotency"
    assert submit(client, other, body).status_code == 409
    assert submit(client, parent, {**body, "preview_basis_sha256": "f" * 64}).status_code == 409


def test_story_change_or_missing_write_permission_never_creates_child(client, domain, parent):
    body = request(client, parent)
    token = client.headers.pop("X-Ainovel-CSRF")
    assert submit(client, parent, body).status_code == 403
    client.headers["X-Ainovel-CSRF"] = token
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (parent["branch_id"],))
    changed = submit(client, parent, body)
    assert changed.status_code == 409 and changed.json()["code"] == "story_changed"
    assert domain.task(parent["id"]) == parent


def test_settled_old_usd_auxiliary_is_audited_without_deducting_cny_envelope(
    client, domain, settings, parent
):
    ancestor = domain.queue(
        "workflow",
        {**parent["payload"], "account": "openai_1", "client_request_id": "known-usd-ancestor"},
        parent["branch_id"],
    )
    ancestor = update_task(domain, ancestor["id"], status="continued")
    old_aux = auxiliary(domain, ancestor)
    paid_step(settings, domain, old_aux, 2)
    parent = update_task(domain, parent["id"], result={**parent["result"], "continued_from": ancestor["id"]})
    paid_step(settings, domain, parent, 1)
    body = request(client, parent)
    response = submit(client, parent, body)
    assert response.status_code == 202, response.text
    child = response.json()
    costs = child["result"]["flow_policy_continuation"]["budget"]
    assert costs["auxiliary_by_currency"] == [{"provider": "openai", "currency": "USD", "settled": 2}]
    assert costs["auxiliary_settled"] == 0
    assert costs["effective_remaining_run_limit"] == child["payload"]["budget_limit"] == 4
    assert next(row for row in Budget(settings).report() if row["provider"] == "openai")["settled"] == 2
