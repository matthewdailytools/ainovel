"""New explicit source-role generation owns the same remaining plan scope as FLOWv4."""

import asyncio
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from ainovel.workflow_plan_restart import restart
from ainovel.source_interpretation_correction import bounded_run_limit
from test_workflow_plan_source_roles import seed_failed, restart_request


@pytest.mark.parametrize(
    "position,scope,action,blocked",
    [
        (5, "same", "writing.expand_plot", True),
        (6, "same", "writing.expand_plot", True),
        (36, "same", "writing.expand_plot", True),
        (6, "same", "review.chapter", False),
        (1, "same", "writing.expand_plot", False),
        (6, "other", "writing.expand_plot", False),
    ],
)
def test_native_unknown_body_dispatch_checks_remaining_scope_and_preserves_ledger(
    client, domain, settings, story, position, scope, action, blocked
):
    worker, current, _ = seed_failed(settings, domain, story, chapters=36, number=5, future_positions=(6, 36))
    target = next(
        c
        for c in domain.db.all("SELECT * FROM chapters WHERE branch_id=?", (current["branch_id"],))
        if c["position"] == position
    )
    if scope == "other":
        novel = domain.create_novel("独立工作版本")
        target = domain.create_chapter(novel["branch_id"], "独立后章", position)
    request = restart_request(domain, current)
    response = client.post(
        "/api/modules",
        json={
            "action": action,
            "novel_id": domain.branch(target["branch_id"])["novel_id"],
            "branch_id": target["branch_id"],
            "chapter_id": target["id"],
            "base_revision": target["revision"],
            "instruction": "原创隔离边界验证；不修改计划安排。",
            "account": "deepseek",
            "client_request_id": "plan-source-real-module-dispatch",
            "budget_limit": 1,
            "max_output_tokens": 256,
        },
    )
    assert response.status_code == 202, response.text
    module_id = response.json()["id"]
    sent = []

    def timeout(wire):
        sent.append(str(wire.url))
        raise httpx.ReadTimeout("isolated sent timeout", request=wire)

    model = ModelClient(settings, Budget(settings), httpx.MockTransport(timeout))
    asyncio.run(Worker(settings, model).run_once())
    module = domain.task(module_id)
    assert module["status"] == "blocked_runtime" and len(sent) == 1
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (module_id,)
    )
    assert len(events) == 1
    event = json.loads(events[0]["payload"])
    assert event["request"]["target_version"]["id"] == target["id"]
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (module_id,))
    with worker.budget.db() as tx:
        ledger = [dict(r) for r in tx.execute("SELECT * FROM calls ORDER BY id")]
    unknown = [c for c in ledger if c["task_id"] == module_id]
    assert len(unknown) == 1 and unknown[0]["status"] == "unknown" and unknown[0]["charged"] is None
    chapters_before = domain.db.all("SELECT * FROM chapters ORDER BY id")
    preview = client.get(f"/api/tasks/{current['id']}/reprepare-plan-preview")
    started = client.post(f"/api/tasks/{current['id']}/reprepare-plan", json=request)
    assert preview.status_code == (409 if blocked else 200), preview.text
    assert started.status_code == (409 if blocked else 202), started.text
    if blocked:
        assert started.json()["code"] == "plan_preparation_late"
        assert domain.task(current["id"]) == current
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters_before
    with worker.budget.db() as tx:
        assert [dict(r) for r in tx.execute("SELECT * FROM calls ORDER BY id")] == ledger
    # Unknown dispatch remains held and is never automatically resent.
    assert len(sent) == 1 and domain.task(module_id) == module


@pytest.mark.parametrize("late", ["preparation", "text", "candidate", "started"])
def test_future_preparation_text_candidate_or_started_refuses_new_generation(
    client, domain, settings, story, late
):
    worker, current, _ = seed_failed(settings, domain, story, chapters=36, number=5, future_positions=(6,))
    chapter = domain.db.all(
        "SELECT * FROM chapters WHERE branch_id=? AND position=6", (current["branch_id"],)
    )[0]
    if late == "preparation":
        current["result"]["plot_preparations"]["6"] = {"status": "interpreting"}
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(current["result"]), current["id"]))
    elif late == "text":
        domain.save(chapter["id"], 0, "未来章已有作者稿。")
    elif late == "candidate":
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO artifacts(id,task_id,novel_id,branch_id,chapter_id,kind,status,basis,payload,created) VALUES('future-candidate',?,?,?,?,'text_patch','rejected','{}','{}',0)",
                (current["id"], current["novel_id"], current["branch_id"], chapter["id"]),
            )
    else:
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('unrelated-old-workflow','workflow',?,?,'cancelled','{}','{}',0,0)",
                (current["novel_id"], current["branch_id"]),
            )
            domain.db.event(tx, "unrelated-old-workflow", "chapter_started", {"number": 36})
    current = domain.task(current["id"])
    response = client.post(
        f"/api/tasks/{current['id']}/reprepare-plan", json=restart_request(domain, current)
    )
    assert response.status_code == 409, response.text
    assert domain.task(current["id"]) == current


def test_restart_nominal_limit_and_effective_auxiliary_limit_do_not_double_charge_ancestors(
    settings, domain, story
):
    worker, current, _ = seed_failed(settings, domain, story)
    account = current["payload"]["account"]
    provider = settings.providers[account]
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('ancestor','workflow',?,?,'continued',?,'{}',0,0)",
            (current["novel_id"], current["branch_id"], dumps(current["payload"])),
        )
        tx.execute(
            "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('auxiliary','source_interpretation_correction',?,?,'blocked_runtime',?, ?,0,0)",
            (
                current["novel_id"],
                current["branch_id"],
                dumps({"account": account}),
                dumps({"source_correction_snapshot": {"source_workflow_id": "ancestor"}}),
            ),
        )
    ancestor = worker.budget.reserve(provider, "ancestor", 50000, 0, run_limit=1)
    worker.budget.settle(ancestor, {"input": 50000, "output": 0})
    aux = worker.budget.reserve(provider, "auxiliary", 100000, 0, run_limit=1)
    worker.budget.settle(aux, {"input": 100000, "output": 0})
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO task_steps VALUES('ancestor','ancestor-complete',?,0)",
            (dumps({"call_id": ancestor, "text": "{}"}),),
        )
        current["result"]["continued_from"] = "ancestor"
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(current["result"]), current["id"]))
    current = domain.task(current["id"])
    with worker.budget.db() as tx:
        before = [dict(r) for r in tx.execute("SELECT * FROM calls ORDER BY id")]
    own = sum(c["charged"] for c in before if c["task_id"] == current["id"]) / 1_000_000
    child = restart(domain, worker.budget, current, restart_request(domain, current))
    assert child["payload"]["budget_limit"] == 1 - own
    worker.task = child
    assert bounded_run_limit(worker) == pytest.approx(1 - own - 0.2)
    assert child["result"]["plan_preparation_restart"]["budget"]["lineage_settled"] == pytest.approx(
        own + 0.1
    )
    with worker.budget.db() as tx:
        assert [dict(r) for r in tx.execute("SELECT * FROM calls ORDER BY id")] == before
