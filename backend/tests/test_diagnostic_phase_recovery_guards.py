"""Lease/late/fee/next-phase boundaries for the one diagnostic migration."""

import asyncio
from unittest.mock import patch

import pytest

from ainovel import authored_source_scope as authored
from ainovel import diagnostic_phase_recovery as recovery
from ainovel.db import DomainError
from ainovel.worker import Worker
from helpers.diagnostic_phase_resume_fixture import (
    INTERPRET,
    known_interpret_failure,
    native,
)
from test_diagnostic_phase_resume import assert_originals, evidence, resume


@pytest.fixture
def failed(settings, domain, story, client):
    return known_interpret_failure(settings, domain, story, client=client)


def test_no_diagnostic_difference_preserves_ordinary_exact_cache_failure(settings, domain, client, failed):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    current_binding = authored.binding_issues

    def unchanged_labels(value, inventory, **kwargs):
        # Both views deliberately use the same strict validator/rendering. This
        # never changes the invalid truth; no changed-feedback permit is due.
        return current_binding(value, inventory, legacy_diagnostic_labels=True)

    with patch.object(authored, "binding_issues", unchanged_labels):
        assert resume(client, stopped["id"]).status_code == 200
        worker = Worker(settings)
        frames = native(worker)
        assert asyncio.run(worker.run_once())
    current = domain.task(stopped["id"])
    assert frames == []
    assert current["status"] == "blocked_runtime" and current["error"] == stopped["error"]
    slot = current["result"][authored.RECEIPTS]["chapter:1"]["phases"]["interpret"]
    assert recovery.MARK not in slot
    assert_originals(before, evidence(worker, domain, stopped["id"]))


@pytest.mark.parametrize("change", ["story", "old_fee"])
def test_source_or_fee_change_after_resume_candidate_blocks_new_wire(
    settings, domain, client, failed, change
):
    old_worker, stopped, _ = failed
    assert resume(client, stopped["id"]).status_code == 200
    if change == "story":
        with domain.db.connect(True) as tx:
            tx.execute(
                "UPDATE branches SET story_revision=story_revision+1 WHERE id=?",
                (stopped["branch_id"],),
            )
    else:
        with old_worker.budget.db(True) as usage:
            call = usage.execute(
                "SELECT id FROM calls WHERE task_id=? ORDER BY id LIMIT 1", (stopped["id"],)
            ).fetchone()
            usage.execute("UPDATE calls SET charged=charged+1 WHERE id=?", (call["id"],))
    before = evidence(old_worker, domain, stopped["id"])
    worker = Worker(settings)
    frames = native(worker)
    assert asyncio.run(worker.run_once())
    assert frames == []
    current = domain.task(stopped["id"])
    assert current["status"] == "blocked_runtime"
    after = evidence(worker, domain, stopped["id"])
    for key in ("steps", "calls", "metadata", "chapters"):
        assert after[key] == before[key]


def test_persisted_allowance_before_dispatch_is_not_regranted_after_worker_restart(
    settings, domain, client, failed
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    original = recovery.permit_dispatch

    def interrupted(worker, fingerprint, instruction):
        assert original(worker, fingerprint, instruction)
        raise DomainError("interrupted", "匿名隔离进程在已持久占额后、真正派发前退出。", 409)

    worker = Worker(settings)
    frames = native(worker)
    with patch.object(recovery, "permit_dispatch", interrupted):
        assert asyncio.run(worker.run_once())
    assert frames == []
    current = domain.task(stopped["id"])
    slot = current["result"][authored.RECEIPTS]["chapter:1"]["phases"]["interpret"]
    assert len(slot[recovery.MARK]["dispatches"]) == 1
    original_grant = slot[recovery.MARK]
    assert_originals(before, evidence(worker, domain, stopped["id"]))
    assert resume(client, stopped["id"]).status_code == 200
    next_worker = Worker(settings)
    next_frames = native(next_worker)
    assert asyncio.run(next_worker.run_once())
    assert next_frames == []
    slot = domain.task(stopped["id"])["result"][authored.RECEIPTS]["chapter:1"]["phases"]["interpret"]
    assert slot[recovery.MARK] == original_grant
    assert_originals(before, evidence(next_worker, domain, stopped["id"]))


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
def test_independent_preserve_has_no_exemption_from_same_provider_zero_reserve_unknown(
    settings, domain, client, failed, provider
):
    old_worker, stopped, _ = failed
    before = evidence(old_worker, domain, stopped["id"])
    assert resume(client, stopped["id"]).status_code == 200
    worker = Worker(settings)
    frames = native(worker)
    original = worker.call
    inserted = False

    async def call(instruction, *args, **kwargs):
        nonlocal inserted
        value = await original(instruction, *args, **kwargs)
        if instruction.get("动作") == INTERPRET and "前次产物" in instruction and not inserted:
            inserted = True
            with worker.budget.db(True) as usage:
                old = dict(
                    usage.execute(
                        "SELECT * FROM calls WHERE task_id=? ORDER BY id LIMIT 1", (stopped["id"],)
                    ).fetchone()
                )
                old.update(
                    id="anonymous-zero-reserve-unknown",
                    task_id="unrelated-isolated-owner",
                    provider=provider,
                    currency="CNY" if provider == "deepseek" else "USD",
                    status="unknown",
                    charged=None,
                    reserved=0,
                )
                names = list(old)
                usage.execute(
                    f"INSERT INTO calls ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                    [old[key] for key in names],
                )
        return value

    worker.call = call
    assert asyncio.run(worker.run_once())
    assert inserted
    actions = [frame["任务"]["动作"] for frame in frames]
    current = domain.task(stopped["id"])
    if provider == "deepseek":
        assert actions == [INTERPRET]
        assert current["status"] == "blocked_runtime"
        assert current["result"][authored.RECEIPTS]["chapter:1"]["state"] != "ready"
    else:
        assert "独立核对作者来源分类与完整原意保持" in actions
        assert current["status"] == "needs_review", current["error"]
    assert_originals(before, evidence(worker, domain, stopped["id"]))
