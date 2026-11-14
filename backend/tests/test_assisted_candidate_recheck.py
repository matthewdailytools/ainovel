"""A semi-automatic draft keeps its original author-side interpretation on recheck."""

import asyncio
from copy import deepcopy

import pytest

from ainovel.db import dumps
from ainovel.worker import Worker
from test_candidate_recheck import (
    MEMORY,
    PLOT,
    REVIEW,
    RecheckModel,
    assert_parent_not_resumed,
    queue_recheck,
)
from test_plot_review_shared import PlotModel
from test_workflow_scope import workflow_payload


@pytest.fixture
def assisted_candidate(settings, domain, story, client):
    payload = workflow_payload(story, mode="assisted")
    payload.update(plot_contract_check=True, client_request_id="assisted-recheck-original")
    task = domain.queue("workflow", payload, story["branch_id"])
    asyncio.run(Worker(settings, PlotModel("unknown")).run_once())
    parent = domain.task(task["id"])
    assert parent["status"] == "needs_review", parent["error"]
    candidate = domain.artifact(parent["result"]["waiting_artifact"])
    original = deepcopy(candidate)
    edited = client.patch(
        f"/api/artifacts/{candidate['id']}",
        json={"candidate_revision": 0, "text": candidate["payload"]["text"] + "\n另存复核记录😀𠮷。"},
    )
    assert edited.status_code == 200
    return domain.task(parent["id"]), edited.json(), original


def test_assisted_recheck_keeps_author_source_without_interpreting_again(
    settings, client, domain, assisted_candidate
):
    parent, candidate, original = assisted_candidate
    chapter = domain.chapter(candidate["chapter_id"])
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    model = RecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    assert model.actions == [REVIEW, PLOT, MEMORY]
    saved = domain.artifact(candidate["id"])
    assert saved["payload"]["text"] == candidate["payload"]["text"]
    assert saved["candidate_revision"] == 1 and saved["payload"]["review_passed"]
    audit = saved["payload"]["plot_audit"]
    assert audit["source_kind"] == "author_plot"
    assert audit["source"] == original["payload"]["plot_audit"]["source"]
    assert audit["items"] == original["payload"]["plot_audit"]["items"]
    assert saved["payload"]["previous_plot_audit"] == original["payload"]["plot_audit"]
    assert domain.chapter(candidate["chapter_id"]) == chapter
    assert not domain.db.all("SELECT * FROM receipts")
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize("verdict", ["unknown", "fail"])
def test_assisted_nonpass_does_not_generate_memory_or_adopt(
    settings, client, domain, assisted_candidate, verdict
):
    parent, candidate, _ = assisted_candidate
    queued = queue_recheck(client, candidate)
    assert queued.status_code == 202, queued.text
    model = RecheckModel(verdict)
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    assert model.actions == [REVIEW, PLOT]
    assert not domain.artifact(candidate["id"])["payload"]["review_passed"]
    assert not domain.db.all("SELECT * FROM facts")
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize(
    "damage", ["hash", "kind", "empty", "id", "quote", "scope", "source_plot", "missing"]
)
def test_assisted_missing_or_changed_author_contract_is_rejected_before_dispatch(
    client, domain, assisted_candidate, damage
):
    parent, candidate, _ = assisted_candidate
    payload = deepcopy(candidate["payload"])
    audit = payload["previous_plot_audit"]
    if damage == "hash":
        audit["source"]["text_sha256"] = "0" * 64
    elif damage == "kind":
        audit["source_kind"] = "workflow_plan"
    elif damage == "empty":
        audit["items"] = []
    elif damage == "id":
        audit["items"][0]["id"] = "P02"
    elif damage == "quote":
        audit["items"][0]["author_evidence"] = "借用候选来解释作者的新剧情"
    elif damage == "scope":
        audit["items"][0]["scope_condition"] = "未由作者给出的新条件"
    elif damage == "source_plot":
        source = deepcopy(parent["payload"])
        source["plots"][0]["plot"] += "改变作者原话"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(source), parent["id"]))
    else:
        payload.pop("previous_plot_audit")
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))
    response = queue_recheck(client, candidate)
    assert response.status_code == 409, response.text
    assert not domain.db.all("SELECT * FROM tasks WHERE kind='candidate_recheck'")


def test_assisted_author_source_change_while_running_keeps_old_candidate(
    settings, client, domain, assisted_candidate
):
    parent, candidate, _ = assisted_candidate
    queued = queue_recheck(client, candidate)
    assert queued.status_code == 202

    def hook(action, _):
        if action != REVIEW:
            return
        payload = deepcopy(parent["payload"])
        payload["plots"][0]["plot"] += "改变作者原情节"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(payload), parent["id"]))

    model = RecheckModel(hook=hook)
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(queued.json()["id"])["status"] == "blocked_runtime"
    assert model.actions == [REVIEW]
    assert domain.artifact(candidate["id"]) == candidate
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (queued.json()["id"],))
