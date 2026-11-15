"""Policy upgrades create a budget-preserving child, never mutate old policy."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from dataclasses import replace
import asyncio
from time import time

import pytest

from ainovel.budget import Budget
from ainovel.conditional_continuation import continue_policy
from ainovel.conditional_review import KEY, VERSION
from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.worker import Worker


REQUEST = {
    "client_request_id": "explicit-conditional-continuation",
    "reason": "明确采用本章未触发分类，整书仍核对。",
}


def update_task(domain, identifier, *, result=None, **columns):
    if result is not None:
        columns["result"] = dumps(result)
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET " + ",".join(f"{key}=?" for key in columns) + " WHERE id=?",
            (*columns.values(), identifier),
        )
    return domain.task(identifier)


@pytest.fixture
def legacy_checkpoint(domain, story):
    payload = WorkflowInput(
        novel_id=story["id"],
        account="deepseek",
        mode="assisted",
        brief="独立隔离工程样例，当前仍在调查，末章解决主冲突。",
        fixed=["若涉及母亲，只写她失踪，不透露原因。"],
        plots=[
            {"number": i, "title": f"第{i}章", "plot": "核验渡口记录。", "fixed": []} for i in range(1, 4)
        ],
        chapters=3,
        min_chars=200,
        max_chars=600,
        budget_limit=5,
        client_request_id="legacy-conditional-parent",
    ).model_dump()
    parent = domain.queue("workflow", payload, story["branch_id"])
    request = {**story, "novel_id": story["id"], "base_revision": 0}
    candidate = domain.stage(
        request,
        "chapter_draft",
        {"text": "原创工程正文😀𠮷。" * 25, "review_passed": True, "memory": {}},
        {},
        parent["id"],
    )
    domain.accept(candidate["id"], "explicit-manual-checkpoint-adoption")
    progress = deepcopy(parent["result"])
    progress.pop(KEY, None)
    for key in ("genre_profile_snapshot", "author_contract_snapshot"):
        progress[key]["legacy_unconfigured"] = False
    progress.update(
        plan={"chapters": deepcopy(payload["plots"]), "ending": "全书调查闭合。"},
        next_chapter=2,
        latest_artifact=candidate["id"],
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
        repair_round=0,
        plot_preparations={},
    )
    return update_task(domain, parent["id"], result=progress, status="paused", pause=1)


def post(client, parent, **changes):
    return client.post(f"/api/tasks/{parent['id']}/continue-conditional-policy", json={**REQUEST, **changes})


def ledger_rows(budget):
    with budget.db() as tx:
        return [dict(row) for row in tx.execute("SELECT * FROM calls ORDER BY id")]


def paid_step(settings, domain, parent, cost, *, complete=True):
    budget = Budget(settings)
    provider = settings.providers[parent["payload"]["account"]]
    tokens = int(Decimal(str(cost)) * 1_000_000 / provider.input_rate)
    identifier = budget.reserve(provider, parent["id"], tokens, 0)
    budget.settle(identifier, {"input": tokens, "output": 0})
    if complete:
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO task_steps VALUES(?,?,?,?)",
                (
                    parent["id"],
                    identifier,
                    dumps(
                        {"call_id": identifier, "text": "完整回执", "usage": {"input": tokens, "output": 0}}
                    ),
                    time(),
                ),
            )
    return identifier


def test_explicit_child_preserves_old_policy_prose_reports_and_budget(
    client, domain, settings, legacy_checkpoint
):
    parent = legacy_checkpoint
    paid_step(settings, domain, parent, 1.25)
    budget = Budget(settings)
    calls_before = ledger_rows(budget)
    chapters_before = domain.db.all("SELECT * FROM chapters ORDER BY id")
    artifacts_before = domain.db.all("SELECT * FROM artifacts ORDER BY id")
    steps_before = domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint")
    response = post(client, parent)
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["id"] != parent["id"] and child["status"] == "queued"
    assert child["payload"] == {
        **parent["payload"],
        "client_request_id": REQUEST["client_request_id"],
        "budget_limit": 3.75,
    }
    assert child["payload"]["account"] == "deepseek"
    assert child["pause"] == child["cancel"] == 0 and child["attempt"] is None
    assert child["result"][KEY] == VERSION
    assert child["result"]["continued_from"] == parent["id"]
    for key, value in parent["result"].items():
        assert child["result"][key] == value
    provenance = child["result"]["policy_continuation"]
    assert provenance["request"] == REQUEST
    assert provenance["source_policy"] == "legacy"
    assert provenance["budget"]["source_run_limit"] == 5
    assert provenance["budget"]["source_task_settled"] == 1.25
    assert provenance["budget"]["remaining_run_limit"] == 3.75
    old = domain.task(parent["id"])
    assert old["status"] == "continued" and KEY not in old["result"]
    assert old["payload"] == parent["payload"]
    assert old["result"] == {**parent["result"], "continued_to": child["id"]}
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters_before
    assert domain.db.all("SELECT * FROM artifacts ORDER BY id") == artifacts_before
    assert domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint") == steps_before
    assert ledger_rows(budget) == calls_before
    for identifier, kind in [
        (parent["id"], "continued_conditional_policy"),
        (child["id"], "conditional_policy_continued_from"),
    ]:
        assert (
            len(domain.db.all("SELECT id FROM task_events WHERE task_id=? AND kind=?", (identifier, kind)))
            == 1
        )


def test_idempotent_after_parent_continued_even_when_account_removed(
    client, settings, domain, legacy_checkpoint
):
    first = post(client, legacy_checkpoint)
    assert first.status_code == 202
    settings.providers.pop("deepseek")
    second = post(client, domain.task(legacy_checkpoint["id"]))
    assert second.status_code == 202 and second.json()["id"] == first.json()["id"]
    changed = post(client, legacy_checkpoint, reason="另一决定")
    assert changed.status_code == 409 and changed.json()["code"] == "idempotency"


def test_concurrent_identical_requests_create_only_one_child(domain, settings, legacy_checkpoint):
    budget = Budget(settings)

    def start():
        return continue_policy(domain, budget, legacy_checkpoint, REQUEST)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: start(), range(2)))
    assert first["id"] == second["id"]
    assert len(domain.db.all("SELECT * FROM task_requests WHERE key=?", (REQUEST["client_request_id"],))) == 1


def test_prior_other_currency_is_audited_separately(client, domain, settings, legacy_checkpoint):
    ancestor = domain.queue(
        "workflow",
        {
            **legacy_checkpoint["payload"],
            "account": "openai_2",
            "client_request_id": "prior-usd-budget-ancestor",
        },
        legacy_checkpoint["branch_id"],
    )
    ancestor = update_task(domain, ancestor["id"], status="continued")
    paid_step(settings, domain, ancestor, 2)
    parent = update_task(
        domain,
        legacy_checkpoint["id"],
        result={**legacy_checkpoint["result"], "continued_from": ancestor["id"]},
    )
    paid_step(settings, domain, parent, 1)
    response = post(client, parent)
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["budget_limit"] == 4
    costs = child["result"]["policy_continuation"]["budget"]
    assert costs["lineage_settled"] == 1 and costs["provider"] == "deepseek"
    assert costs["lineage_by_currency"] == [
        {"provider": "deepseek", "currency": "CNY", "settled": 1},
        {"provider": "openai", "currency": "USD", "settled": 2},
    ]


@pytest.mark.parametrize("status", ["needs_review", "running", "queued", "blocked_runtime", "cancelled"])
def test_only_explicitly_paused_old_checkpoint_is_eligible(client, domain, legacy_checkpoint, status):
    parent = update_task(domain, legacy_checkpoint["id"], status=status)
    response = post(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "conditional_checkpoint"
    assert domain.task(parent["id"])["result"] == parent["result"]


@pytest.mark.parametrize("key", ["waiting_artifact", "waiting_structure", "pending_structure"])
def test_pending_adoption_or_maintenance_is_never_skipped(client, domain, legacy_checkpoint, key):
    progress = {
        **legacy_checkpoint["result"],
        key: {"chapter_id": "pending"} if key == "pending_structure" else "pending",
    }
    parent = update_task(domain, legacy_checkpoint["id"], result=progress)
    response = post(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "review_required"


@pytest.mark.parametrize("marker", [VERSION, "conditional-book-check/future", None])
def test_existing_policy_marker_is_not_rewritten(client, domain, legacy_checkpoint, marker):
    parent = update_task(domain, legacy_checkpoint["id"], result={**legacy_checkpoint["result"], KEY: marker})
    response = post(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "conditional_checkpoint"


def test_unknown_ledger_preserves_reservation_and_rejects_upgrade(
    client, domain, settings, legacy_checkpoint
):
    budget = Budget(settings)
    identifier = budget.reserve(settings.providers["deepseek"], legacy_checkpoint["id"], 1000, 1000)
    budget.settle(identifier)
    before = ledger_rows(budget)
    response = post(client, legacy_checkpoint)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert ledger_rows(budget) == before
    assert domain.task(legacy_checkpoint["id"])["status"] == "paused"


def test_paid_call_without_complete_step_result_is_not_replayed(client, domain, settings, legacy_checkpoint):
    identifier = paid_step(settings, domain, legacy_checkpoint, 0.25, complete=False)
    budget = Budget(settings)
    calls_before = ledger_rows(budget)
    response = post(client, legacy_checkpoint)
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["budget_limit"] == 4.75
    assert child["result"]["next_chapter"] == 2
    costs = child["result"]["policy_continuation"]["budget"]
    assert costs["settled_calls_without_step"] == [
        {"id": identifier, "account": "deepseek", "amount": 0.25, "currency": "CNY", "status": "settled"}
    ]
    assert "禁止重发或重放" in costs["old_call_replay"]
    assert ledger_rows(budget) == calls_before
    assert domain.db.all("SELECT * FROM task_steps") == []


def test_settled_cost_without_explicit_usage_is_still_unresolved(client, domain, settings, legacy_checkpoint):
    identifier = paid_step(settings, domain, legacy_checkpoint, 0.25)
    budget = Budget(settings)
    with budget.db(True) as tx:
        tx.execute("UPDATE calls SET input_tokens=NULL WHERE id=?", (identifier,))
    before = ledger_rows(budget)
    response = post(client, legacy_checkpoint)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"
    assert ledger_rows(budget) == before


def test_explicit_same_provider_profile_uses_original_spend_and_shared_ledger(
    client, domain, settings, legacy_checkpoint
):
    source = replace(settings.providers["deepseek"], id="deepseek_thinking_16k", reasoning="high")
    settings.providers[source.id] = source
    parent = update_task(
        domain,
        legacy_checkpoint["id"],
        payload=dumps({**legacy_checkpoint["payload"], "account": source.id}),
    )
    paid_step(settings, domain, parent, 1.25)
    budget = Budget(settings)
    before = ledger_rows(budget)
    response = post(client, parent, account="deepseek")
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["account"] == "deepseek" and child["payload"]["budget_limit"] == 3.75
    provenance = child["result"]["policy_continuation"]
    assert provenance["original_account"] == source.id and provenance["new_account"] == "deepseek"
    assert provenance["request"] == {**REQUEST, "account": "deepseek"}
    assert domain.task(parent["id"])["payload"]["account"] == source.id
    assert ledger_rows(budget) == before
    replay = post(client, parent, account="deepseek")
    assert replay.status_code == 202 and replay.json()["id"] == child["id"]
    changed = post(client, parent, account=source.id)
    assert changed.status_code == 409 and changed.json()["code"] == "idempotency"


@pytest.mark.parametrize(
    "account,code,status",
    [("openai_1", "conditional_account", 409), ("missing_profile", "configuration", 400)],
)
def test_optional_profile_cannot_cross_provider_or_use_unknown_account(
    client, domain, legacy_checkpoint, account, code, status
):
    response = post(client, legacy_checkpoint, account=account)
    assert response.status_code == status and response.json()["code"] == code
    assert domain.task(legacy_checkpoint["id"])["status"] == "paused"


def test_budget_exhaustion_cannot_be_reset_by_new_task(client, domain, settings, legacy_checkpoint):
    paid_step(settings, domain, legacy_checkpoint, 5)
    response = post(client, legacy_checkpoint)
    assert response.status_code == 409 and response.json()["code"] == "budget"


def test_current_remaining_limit_does_not_subtract_ancestor_twice(
    client, domain, settings, legacy_checkpoint
):
    ancestor = domain.queue(
        "workflow",
        {**legacy_checkpoint["payload"], "client_request_id": "prior-checkpoint-ancestor"},
        legacy_checkpoint["branch_id"],
    )
    ancestor = update_task(domain, ancestor["id"], status="continued")
    paid_step(settings, domain, ancestor, 2)
    payload = {**legacy_checkpoint["payload"], "budget_limit": 3}
    parent = update_task(
        domain,
        legacy_checkpoint["id"],
        payload=dumps(payload),
        result={**legacy_checkpoint["result"], "continued_from": ancestor["id"]},
    )
    paid_step(settings, domain, parent, 1)
    response = post(client, parent)
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["budget_limit"] == 2
    costs = child["result"]["policy_continuation"]["budget"]
    assert costs["source_task_settled"] == 1 and costs["lineage_settled"] == 3
    budget = Budget(settings)
    with pytest.raises(DomainError, match="本次任务预算不足"):
        budget.reserve(
            settings.providers["deepseek"],
            child["id"],
            1_500_000,
            0,
            run_limit=child["payload"]["budget_limit"],
        )


def test_another_active_task_on_branch_blocks_duplicate_writer(client, domain, legacy_checkpoint):
    domain.queue(
        "workflow",
        {**legacy_checkpoint["payload"], "client_request_id": "competing-active-writer"},
        legacy_checkpoint["branch_id"],
    )
    response = post(client, legacy_checkpoint)
    assert response.status_code == 409 and response.json()["code"] == "active_task"


@pytest.mark.parametrize("kind", ["reader", "intent"])
def test_adopted_configuration_change_stops_policy_upgrade(client, domain, legacy_checkpoint, kind):
    record = domain.stage(
        {"novel_id": legacy_checkpoint["novel_id"], "branch_id": legacy_checkpoint["branch_id"]},
        kind,
        {"text": "新配置"},
        {},
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET status='accepted' WHERE id=?", (record["id"],))
    response = post(client, legacy_checkpoint)
    assert response.status_code == 409
    assert response.json()["code"] == (
        "genre_profile_changed" if kind == "reader" else "author_contract_changed"
    )


@pytest.mark.parametrize("change", ["task", "story"])
def test_concurrent_change_before_transaction_is_rejected(
    domain, settings, legacy_checkpoint, monkeypatch, change
):
    from ainovel import conditional_continuation as module

    original = module.checkpoint

    def changed_checkpoint(*args):
        branch = original(*args)
        with domain.db.connect(True) as tx:
            if change == "task":
                tx.execute("UPDATE tasks SET updated=updated+1 WHERE id=?", (legacy_checkpoint["id"],))
            else:
                tx.execute(
                    "UPDATE branches SET story_revision=story_revision+1 WHERE id=?",
                    (legacy_checkpoint["branch_id"],),
                )
        return branch

    monkeypatch.setattr(module, "checkpoint", changed_checkpoint)
    with pytest.raises(DomainError) as error:
        continue_policy(domain, Budget(settings), legacy_checkpoint, REQUEST)
    assert error.value.code == ("task_changed" if change == "task" else "story_changed")
    assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (REQUEST["client_request_id"],)) == []


def test_missing_receipt_and_incomplete_plan_remain_stopped(client, domain, legacy_checkpoint):
    with domain.db.connect(True) as tx:
        tx.execute("DELETE FROM receipts")
    response = post(client, legacy_checkpoint)
    assert response.status_code == 409 and response.json()["code"] == "review_required"


def test_maintenance_history_required_even_without_pending_flag(client, domain, legacy_checkpoint):
    payload = {**legacy_checkpoint["payload"], "structured_maintenance": True}
    parent = update_task(domain, legacy_checkpoint["id"], payload=dumps(payload))
    response = post(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "review_required"


def test_started_plan_phase_without_response_is_unknown(client, domain, legacy_checkpoint):
    parent = update_task(
        domain,
        legacy_checkpoint["id"],
        result={
            **legacy_checkpoint["result"],
            "plot_preparations": {"1": {"phases": {"choice": {"status": "started"}}}},
        },
    )
    response = post(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "unknown_result"


@pytest.mark.parametrize("changes", [{"reason": " "}, {"account": "openai_1"}, {"budget_limit": 50}])
def test_request_cannot_silently_change_model_or_budget(client, legacy_checkpoint, changes):
    response = post(client, legacy_checkpoint, **changes)
    assert response.status_code in (409, 422)


def test_new_write_route_uses_existing_csrf_boundary(client, legacy_checkpoint):
    response = client.post(
        f"/api/tasks/{legacy_checkpoint['id']}/continue-conditional-policy",
        json=REQUEST,
        headers={"X-Ainovel-CSRF": "invalid"},
    )
    assert response.status_code == 403


class NoGeneration:
    async def generate(self, *args, **kwargs):
        raise AssertionError("维护后暂停不得派发下一章或任何模型调用")


def waiting_after_adoption(domain, parent):
    progress = {
        **parent["result"],
        "waiting_artifact": parent["result"]["latest_artifact"],
        "next_chapter": 1,
    }
    return update_task(domain, parent["id"], result=progress, status="needs_review")


def test_resume_stops_after_adopted_chapter_maintenance_before_next_generation(
    client, domain, settings, legacy_checkpoint
):
    parent = waiting_after_adoption(domain, legacy_checkpoint)
    before = domain.chapter(domain.artifact(parent["result"]["latest_artifact"])["chapter_id"])
    response = client.post(f"/api/tasks/{parent['id']}/resume", json={"pause_after_maintenance": True})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["pause_after_maintenance_requested"] is True
    asyncio.run(Worker(settings, NoGeneration()).run_once())
    stopped = domain.task(parent["id"])
    assert stopped["status"] == "paused" and stopped["result"]["next_chapter"] == 2
    assert "waiting_artifact" not in stopped["result"]
    assert "pause_after_maintenance_requested" not in stopped["result"]
    assert KEY not in stopped["result"]
    assert domain.chapter(before["id"]) == before
    child_response = post(client, stopped)
    assert child_response.status_code == 202, child_response.text
    child = child_response.json()
    assert child["result"]["next_chapter"] == 2
    assert KEY in child["result"] and "pause_after_maintenance_requested" not in child["result"]


def test_maintenance_failure_preserves_one_shot_pause_request_until_success(
    client, domain, settings, legacy_checkpoint, monkeypatch
):
    parent = waiting_after_adoption(domain, legacy_checkpoint)
    calls = []

    async def injected_maintenance(worker, result):
        calls.append(worker.task["id"])
        if len(calls) == 1:
            worker.checkpoint(result, "needs_review", "隔离注入：维护尚需核对，保留暂停请求。")
            return False
        return True

    monkeypatch.setattr("ainovel.workflow_structure.maintain", injected_maintenance)
    response = client.post(f"/api/tasks/{parent['id']}/resume", json={"pause_after_maintenance": True})
    assert response.status_code == 200
    asyncio.run(Worker(settings, NoGeneration()).run_once())
    failed = domain.task(parent["id"])
    assert failed["status"] == "needs_review"
    assert failed["result"]["pause_after_maintenance_requested"] is True
    assert failed["result"]["next_chapter"] == 2
    # A plain resume preserves the pending request; it does not consume/reset it.
    response = client.post(f"/api/tasks/{parent['id']}/resume", json={})
    assert response.status_code == 200
    assert response.json()["result"]["pause_after_maintenance_requested"] is True
    asyncio.run(Worker(settings, NoGeneration()).run_once())
    stopped = domain.task(parent["id"])
    assert stopped["status"] == "paused" and len(calls) == 2
    assert "pause_after_maintenance_requested" not in stopped["result"]
    assert stopped["result"]["next_chapter"] == 2


@pytest.mark.parametrize("change", ["non_workflow", "regenerate"])
def test_pause_after_maintenance_rejects_incompatible_actions(client, domain, legacy_checkpoint, change):
    if change == "non_workflow":
        update_task(domain, legacy_checkpoint["id"], kind="module")
    response = client.post(
        f"/api/tasks/{legacy_checkpoint['id']}/resume",
        json={
            "pause_after_maintenance": True,
            **({"regenerate_pending": True} if change == "regenerate" else {}),
        },
    )
    assert response.status_code == 409 and response.json()["code"] == "pause_after_maintenance"


@pytest.mark.parametrize("value", [1, "true"])
def test_pause_after_maintenance_flag_requires_boolean(client, legacy_checkpoint, value):
    response = client.post(
        f"/api/tasks/{legacy_checkpoint['id']}/resume", json={"pause_after_maintenance": value}
    )
    assert response.status_code == 422


def test_unconsumed_pause_request_is_not_inherited_as_completed_checkpoint(client, domain, legacy_checkpoint):
    parent = update_task(
        domain,
        legacy_checkpoint["id"],
        result={**legacy_checkpoint["result"], "pause_after_maintenance_requested": True},
    )
    response = post(client, parent)
    assert response.status_code == 409 and response.json()["code"] == "conditional_checkpoint"
