"""Candidate rechecks retain the original v2 preparation and creative context."""

import asyncio
from copy import deepcopy

import pytest

from ainovel.candidate_recheck import source_view
from ainovel.db import dumps
from ainovel.workflow_plan_context import KEY as CONTEXT_KEY
from ainovel.workflow_plan_open_details import KEY as OPEN_KEY
from ainovel.worker import Worker
from test_candidate_recheck import (
    RecheckModel,
    assert_parent_not_resumed,
    queue_recheck,
    recheck_tasks,
    stopped_candidate as stopped_candidate,
)


def preparation(parent):
    row = parent["result"]["plot_preparations"]["1"]
    assert OPEN_KEY in row and CONTEXT_KEY in row
    return row


def test_source_view_uses_a_copy_of_the_parent_frozen_genre_profile(domain, stopped_candidate, monkeypatch):
    parent, _ = stopped_candidate
    preparation(parent)
    original = deepcopy(parent["result"]["genre_profile_snapshot"])
    monkeypatch.setattr(
        domain, "genre_profile", lambda *a, **k: pytest.fail("复检不能重读或替换原冻结频道配置")
    )
    view = source_view(domain, parent)
    profile = view.genre_snapshot()
    assert profile == original
    profile["author_notes"] = "只修改返回的副本"
    assert view.genre_snapshot() == original
    assert parent["result"]["genre_profile_snapshot"] == original


def test_v2_recheck_preserves_the_prepared_context_and_never_reinterprets(
    client, domain, settings, stopped_candidate, monkeypatch
):
    parent, candidate = stopped_candidate
    original = deepcopy(preparation(parent))
    for module, function in [
        ("ainovel.workflow_plan_context", "freeze"),
        ("ainovel.workflow_plan_preparation", "prepare"),
    ]:
        monkeypatch.setattr(
            f"{module}.{function}", lambda *a, **k: pytest.fail("复检不能重新解释或补取创作上下文")
        )
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    queued = response.json()
    snapshot = queued["result"]["candidate_recheck_snapshot"]
    assert snapshot["prepared_contract"] == original["prepared_contract"]
    assert snapshot["plot"]["plot"] == original["effective_plot"]
    assert snapshot["genre_profile_snapshot"] == parent["result"]["genre_profile_snapshot"]
    model = RecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(queued["id"])
    assert finished["status"] == "succeeded", finished["error"]
    assert finished["result"]["review_passed"] is True
    assert preparation(domain.task(parent["id"])) == original
    assert domain.artifact(candidate["id"])["payload"]["text"] == candidate["payload"]["text"]
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize("when", ["before_queue", "after_queue"])
def test_v2_recheck_rejects_changed_original_context_without_any_new_model_call(
    client, domain, settings, stopped_candidate, when
):
    parent, candidate = stopped_candidate
    preparation(parent)
    queued = None
    if when == "after_queue":
        response = queue_recheck(client, candidate)
        assert response.status_code == 202, response.text
        queued = response.json()
    changed = deepcopy(parent["result"])
    changed["plot_preparations"]["1"][CONTEXT_KEY]["data"]["scope"] = "被替换的上下文"
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(changed), parent["id"]))
    if when == "before_queue":
        response = queue_recheck(client, candidate)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "plan_preparation_context"
        assert not recheck_tasks(domain)
    else:
        model = RecheckModel()
        asyncio.run(Worker(settings, model).run_once())
        stopped = domain.task(queued["id"])
        assert stopped["status"] == "blocked_runtime", stopped["error"]
        assert model.calls == 0 and model.actions == []
        assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (queued["id"],))
    assert domain.task(parent["id"])["result"] == changed
    assert domain.artifact(candidate["id"]) == candidate
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
