"""Explicit new-provider envelope and branch-scoped next-body boundaries."""

from copy import deepcopy

import pytest

from ainovel.budget import Budget
from ainovel.conditional_continuation import receipt_budget
from ainovel.db import dumps
from ainovel.flow_entailment import KEY
from ainovel.source_interpretation_correction import bounded_run_limit
from ainovel.worker import Worker
from test_conditional_continuation import legacy_checkpoint as legacy_checkpoint
from test_flow_policy_continuation import (
    auxiliary,
    ledger_rows,
    paid_step,
    parent as parent,
    preview,
    request,
    submit,
    update_task,
)


def formal_switch(
    client, domain, settings, parent, *, source="workflow", destination="deepseek", unknown=True
):
    original = update_task(domain, parent["id"], payload=dumps({**parent["payload"], "account": "openai_1"}))
    charged_task = (
        original
        if source == "workflow"
        else auxiliary(domain, original, status="blocked_runtime", kind=source)
    )
    ledger = Budget(settings)
    call_id = ledger.reserve(settings.providers["openai_1"], charged_task["id"], 100, 100)
    ledger.settle(call_id, None if unknown else {"input": 100, "output": 100})
    if source != "workflow":
        with domain.db.connect(True) as tx:
            domain.db.event(
                tx, charged_task["id"], "step_dispatched", {"fingerprint": "old-foreign-source-dispatch"}
            )
    switched = client.post(
        f"/api/tasks/{original['id']}/continue",
        json={
            "account": destination,
            "budget_limit": 5,
            "client_request_id": "explicit-formal-account-switch",
            "reason": "明确换服务商和新额度，旧预留保留。",
            "acknowledge_unknown": True,
        },
    )
    assert switched.status_code == 202, switched.text
    current = update_task(domain, switched.json()["id"], status="paused")
    return current, original, charged_task, call_id


@pytest.mark.parametrize(
    "source", ["workflow", "source_interpretation_correction", "source_corrected_candidate_recheck"]
)
def test_formal_foreign_unknown_is_audited_and_never_replayed_or_charged_to_new_currency(
    client, domain, settings, parent, source
):
    current, original, old_task, call_id = formal_switch(client, domain, settings, parent, source=source)
    ledger = Budget(settings)
    before = ledger_rows(ledger)
    old_before = deepcopy(domain.task(old_task["id"]))
    prepared = preview(client, current)
    assert prepared.status_code == 200, prepared.text
    record = next(row for row in prepared.json()["budget"]["foreign_unknown_calls"] if row["id"] == call_id)
    assert record == {
        "id": call_id,
        "task_id": old_task["id"],
        "account": "openai_1",
        "provider": "openai",
        "currency": "USD",
        "reserved": 0.001,
        "charged": None,
        "status": "unknown",
        "input_tokens": None,
        "output_tokens": None,
        "has_complete_step": False,
        "scope": "workflow" if source == "workflow" else "auxiliary",
    }
    assert prepared.json()["budget"]["effective_remaining_run_limit"] == 5
    body = request(client, current)
    started = submit(client, current, body)
    assert started.status_code == 202, started.text
    child = started.json()
    worker = Worker(settings)
    worker.task = child
    assert bounded_run_limit(worker) == child["payload"]["budget_limit"] == 5
    assert (
        child["result"]["flow_policy_continuation"]["budget"]["foreign_unknown_calls"]
        == prepared.json()["budget"]["foreign_unknown_calls"]
    )
    assert ledger_rows(ledger) == before
    usd = next(row for row in ledger.report() if row["provider"] == "openai")
    assert usd["reserved_unknown"] == 0.001 and usd["remaining"] == pytest.approx(19.999)
    assert domain.task(old_task["id"]) == old_before
    assert KEY not in domain.task(original["id"])["result"]
    assert domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (child["id"],)) == []


@pytest.mark.parametrize(
    "source", ["workflow", "source_interpretation_correction", "source_corrected_candidate_recheck"]
)
def test_same_provider_history_unknown_still_stops_after_explicit_account_switch(
    client, domain, settings, parent, source
):
    current, _, _, _ = formal_switch(client, domain, settings, parent, source=source, destination="openai_2")
    before = ledger_rows(Budget(settings))
    response = preview(client, current)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert ledger_rows(Budget(settings)) == before
    assert domain.task(current["id"]) == current


@pytest.mark.parametrize("account", ["deepseek", "openai_1"])
def test_current_primary_unknown_is_never_hidden_by_foreign_history_filter(
    client, domain, settings, parent, account
):
    ledger = Budget(settings)
    call_id = ledger.reserve(settings.providers[account], parent["id"], 100, 100)
    ledger.settle(call_id)
    before = ledger_rows(ledger)
    response = preview(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert ledger_rows(ledger) == before


def test_current_provider_auxiliary_missing_step_still_blocks(client, domain, settings, parent):
    task = auxiliary(domain, parent, status="blocked_runtime")
    paid_step(settings, domain, task, 0.1, complete=False)
    before = ledger_rows(Budget(settings))
    result = preview(client, parent)
    assert result.status_code == 409 and result.json()["code"] == "unknown_result"
    assert ledger_rows(Budget(settings)) == before


def test_legacy_conditional_budget_mode_still_rejects_foreign_unknown(client, domain, settings, parent):
    current, _, _, _ = formal_switch(client, domain, settings, parent)
    progress = deepcopy(current["result"])
    progress.pop("conditional_book_policy_version", None)
    current = update_task(domain, current["id"], result=progress)
    response = client.post(
        f"/api/tasks/{current['id']}/continue-conditional-policy",
        json={"client_request_id": "legacy-conditional-budget-must-stay", "reason": "旧入口继续原默认门禁"},
    )
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"


def clean_next(domain, parent):
    chapter = domain.create_chapter(parent["branch_id"], "未写第二章", 2)
    current = update_task(
        domain,
        parent["id"],
        result={**parent["result"], "expected_story_revision": chapter["story_revision"]},
    )
    return current, chapter


def other_workflow(domain, parent, *, status="cancelled", other_branch=False):
    if other_branch:
        novel = domain.create_novel("另一隔离作品")
        branch_id, novel_id = novel["branch_id"], novel["id"]
    else:
        branch_id, novel_id = parent["branch_id"], parent["novel_id"]
    task = domain.queue(
        "workflow",
        {**parent["payload"], "novel_id": novel_id, "client_request_id": "other-isolated-body-source"},
        branch_id,
    )
    return update_task(domain, task["id"], status=status)


@pytest.mark.parametrize("status", ["cancelled", "blocked_runtime"])
@pytest.mark.parametrize("event", ["chapter_started", "body_dispatch", "body_repair"])
def test_any_same_branch_workflow_next_body_blocks_even_outside_lineage(
    client, domain, parent, status, event
):
    current, chapter = clean_next(domain, parent)
    body = request(client, current)
    other = other_workflow(domain, current, status=status)
    with domain.db.connect(True) as tx:
        if event == "chapter_started":
            domain.db.event(tx, other["id"], event, {"number": 2})
        else:
            domain.db.event(
                tx,
                other["id"],
                "step_dispatched",
                {
                    "action": "按章纲写完整正文" if event == "body_dispatch" else "用局部替换修订正文",
                    "request": {"target_version": {"id": chapter["id"], "revision": 0}},
                },
            )
    response = preview(client, current)
    assert response.status_code == 409 and response.json()["code"] == "flow_checkpoint"
    late = submit(client, current, body)
    assert late.status_code == 409 and late.json()["code"] == "flow_checkpoint"
    assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (body["client_request_id"],)) == []
    assert domain.task(current["id"]) == current


@pytest.mark.parametrize(
    "scope",
    [
        "other_branch",
        "prior_chapter",
        "prior_body_dispatch",
        "workflow_review",
        "unbound_generic_step",
        "unrelated_source",
    ],
)
def test_next_body_guard_does_not_guess_scope_for_other_history(client, domain, parent, scope):
    current, chapter = clean_next(domain, parent)
    other = other_workflow(domain, current, other_branch=scope == "other_branch")
    if scope == "unrelated_source":
        other = auxiliary(domain, other, status="cancelled")
    previous_id = domain.artifact(parent["result"]["latest_artifact"])["chapter_id"]
    with domain.db.connect(True) as tx:
        if scope in {"other_branch", "prior_chapter"}:
            domain.db.event(
                tx, other["id"], "chapter_started", {"number": 2 if scope == "other_branch" else 1}
            )
        else:
            domain.db.event(
                tx,
                other["id"],
                "step_dispatched",
                {
                    "action": "审查正文" if scope == "workflow_review" else "按章纲写完整正文",
                    "request": {}
                    if scope == "unbound_generic_step"
                    else {
                        "target_version": {
                            "id": previous_id if scope == "prior_body_dispatch" else chapter["id"],
                            "revision": 0,
                        }
                    },
                },
            )
    response = preview(client, current)
    assert response.status_code == 200, response.text
    started = submit(client, current, request(client, current))
    assert started.status_code == 202, started.text


def test_new_current_provider_unknown_after_preview_is_rechecked_under_transaction(
    client, domain, settings, parent
):
    task = auxiliary(domain, parent)
    paid_step(settings, domain, task, 0.1)
    body = request(client, parent)
    ledger = Budget(settings)
    call_id = ledger.reserve(settings.providers["deepseek"], task["id"], 100, 100)
    ledger.settle(call_id)
    before = ledger_rows(ledger)
    response = submit(client, parent, body)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert ledger_rows(ledger) == before
    assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (body["client_request_id"],)) == []


def test_foreign_unknown_after_preview_is_audited_without_reservation_reset(client, domain, settings, parent):
    current, _, _, call_id = formal_switch(
        client, domain, settings, parent, source="source_interpretation_correction", unknown=False
    )
    body = request(client, current)
    ledger = Budget(settings)
    with ledger.db(True) as tx:
        tx.execute(
            "UPDATE calls SET status='unknown',charged=NULL,input_tokens=NULL,output_tokens=NULL WHERE id=?",
            (call_id,),
        )
    before = ledger_rows(ledger)
    result = submit(client, current, body)
    assert result.status_code == 202, result.text
    costs = result.json()["result"]["flow_policy_continuation"]["budget"]
    assert next(row for row in costs["foreign_unknown_calls"] if row["id"] == call_id)["charged"] is None
    assert costs["effective_remaining_run_limit"] == result.json()["payload"]["budget_limit"] == 5
    assert ledger_rows(ledger) == before


def test_legacy_default_budget_shape_and_values_do_not_gain_new_fields(domain, settings, parent):
    paid_step(settings, domain, parent, 1.25)
    ledger = Budget(settings)
    with ledger.db() as usage:
        result = receipt_budget(domain, usage, [parent], parent, settings.providers["deepseek"])
    assert set(result) == {
        "source_run_limit",
        "source_task_settled",
        "settled_calls_without_step",
        "old_call_replay",
        "lineage_task_ids",
        "provider",
        "lineage_settled",
        "lineage_by_currency",
        "remaining_run_limit",
        "unknown_reserve",
        "note",
    }
    assert result["source_run_limit"] == 5 and result["source_task_settled"] == 1.25
    assert result["remaining_run_limit"] == 3.75 and result["unknown_reserve"] == 0
    assert result["lineage_by_currency"] == [{"provider": "deepseek", "currency": "CNY", "settled": 1.25}]
    assert result["settled_calls_without_step"] == []


def test_deleted_foreign_account_configuration_is_not_needed_for_auditing_its_reserve(
    client, domain, settings, parent
):
    current, _, _, call_id = formal_switch(
        client, domain, settings, parent, source="source_corrected_candidate_recheck"
    )
    settings.providers.pop("openai_1")
    response = preview(client, current)
    assert response.status_code == 200, response.text
    assert response.json()["budget"]["foreign_unknown_calls"][0]["id"] == call_id
    assert submit(client, current, request(client, current)).status_code == 202


def test_mixed_foreign_hold_current_spend_and_auxiliary_cost_are_counted_once(
    client, domain, settings, parent
):
    current, _, _, old_call = formal_switch(
        client, domain, settings, parent, source="source_interpretation_correction"
    )
    paid_step(settings, domain, current, 1)
    task = auxiliary(domain, current)
    paid_step(settings, domain, task, 0.5)
    before = ledger_rows(Budget(settings))
    prepared = preview(client, current)
    assert prepared.status_code == 200, prepared.text
    costs = prepared.json()["budget"]
    assert costs["remaining_run_limit"] == 4 and costs["effective_remaining_run_limit"] == 3.5
    response = submit(client, current, request(client, current))
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["budget_limit"] == 4
    worker = Worker(settings)
    worker.task = child
    assert bounded_run_limit(worker) == 3.5
    assert next(row for row in costs["foreign_unknown_calls"] if row["id"] == old_call)["reserved"] == 0.001
    assert ledger_rows(Budget(settings)) == before


def test_foreign_settled_missing_step_remains_an_auditable_history_not_a_replay(
    client, domain, settings, parent
):
    current, _, _, call_id = formal_switch(
        client, domain, settings, parent, source="source_corrected_candidate_recheck", unknown=False
    )
    before = ledger_rows(Budget(settings))
    prepared = preview(client, current)
    assert prepared.status_code == 200, prepared.text
    history = next(row for row in prepared.json()["budget"]["foreign_history_calls"] if row["id"] == call_id)
    assert history["charged"] == history["reserved"] == 0.001 and history["status"] == "settled"
    assert history["has_complete_step"] is False and history["scope"] == "auxiliary"
    assert prepared.json()["budget"]["foreign_unknown_calls"] == []
    started = submit(client, current, request(client, current))
    assert started.status_code == 202, started.text
    assert ledger_rows(Budget(settings)) == before
    assert domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (started.json()["id"],)) == []
