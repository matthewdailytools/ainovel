import asyncio
from copy import deepcopy

import pytest

from ainovel.db import DomainError, dumps
from ainovel.story_graph import RECORD
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain, safe_automatic


def test_automatic_promise_extension_preserves_evidence_and_author_decisions():
    source = {"chapter_id": "chapter", "revision": 1, "start": 0, "end": 1, "quote": "线"}
    before = RECORD.validate_python(
        {"kind": "promise", "title": "线索", "sources": [source], "stages": [{"status": "planned"}]}
    ).model_dump()
    after = deepcopy(before)
    after["stages"].append({"status": "resolved", "event_id": None, "sources": [source], "note": "已解释"})
    proposal = {"target_id": "promise", "previous_record": {"record": before}, "record": after}
    assert safe_automatic(proposal)
    after["sources"] = []
    assert not safe_automatic(proposal)
    after["sources"] = [source]
    after["stages"][-1]["status"] = "abandoned"
    assert not safe_automatic(proposal)


@pytest.fixture
def pending(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "宁禾在渡口停步。")
    task = domain.queue(
        "workflow",
        {
            "novel_id": story["id"],
            "account": "openai_1",
            "adoption": "automatic",
            "structured_maintenance": True,
        },
        story["branch_id"],
    )
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = {
        "next_chapter": 2,
        "expected_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "pending_structure": {"chapter_id": chapter["id"], "revision": chapter["revision"]},
    }
    worker.checkpoint(result)
    return worker, result, task


def stage_empty(worker, result):
    source = result["pending_structure"]
    return worker.domain.stage(
        {"novel_id": worker.task["novel_id"], "branch_id": worker.task["branch_id"]},
        "story_extraction",
        {"records": [], "unknowns": ["当前章没有新增可确认结构"], "coverage": "本章"},
        {
            "story_revision": result["expected_story_revision"],
            "maintenance_chapter": {"id": source["chapter_id"], "revision": source["revision"]},
            "maintenance_epoch": "initial",
        },
        worker.task["id"],
        worker.attempt,
    )


def test_staged_structure_recovers_without_another_model_call(pending, monkeypatch):
    worker, result, task = pending

    async def unexpected_call(*args, **kwargs):
        pytest.fail("已保存的结构产物恢复不应再次调用模型")

    monkeypatch.setattr(worker, "call", unexpected_call)
    artifact = stage_empty(worker, result)
    # Simulate process loss between artifact persistence and saving its identifier.
    assert "artifact_id" not in worker.domain.task(task["id"])["result"]["pending_structure"]
    assert asyncio.run(maintain(worker, result))
    stored = worker.domain.task(task["id"])["result"]
    assert "pending_structure" not in stored
    assert stored["structure_history"][0]["artifact_id"] == artifact["id"]
    assert worker.domain.artifact(artifact["id"])["status"] == "accepted"


def test_pause_preserves_pending_and_cancellation_blocks_adoption(pending):
    worker, result, task = pending
    artifact = stage_empty(worker, result)
    with worker.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET pause=1 WHERE id=?", (task["id"],))
    assert not asyncio.run(maintain(worker, result))
    assert worker.domain.task(task["id"])["status"] == "paused"
    assert worker.domain.artifact(artifact["id"])["status"] == "candidate"
    with worker.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET pause=0,cancel=1 WHERE id=?", (task["id"],))
    with pytest.raises(DomainError):
        asyncio.run(maintain(worker, result))
    assert worker.domain.artifact(artifact["id"])["status"] == "candidate"


def test_structure_retry_requires_reason_and_version_and_changes_epoch(pending, client):
    worker, result, task = pending
    artifact = stage_empty(worker, result)
    result["pending_structure"]["artifact_id"] = artifact["id"]
    result["waiting_structure"] = artifact["id"]
    worker.checkpoint(result, "needs_review")
    path = f"/api/tasks/{task['id']}/resume"
    assert client.post(path, json={}).status_code == 409
    assert client.post(path, json={"regenerate_pending": True}).status_code == 409
    body = {"regenerate_pending": True, "reason": "需要补上来源", "candidate_revision": 99}
    assert client.post(path, json=body).status_code == 409
    response = client.post(path, json={**body, "candidate_revision": 0})
    assert response.status_code == 200, response.text
    progress = response.json()["result"]
    assert progress["pending_structure"]["epoch"] != "initial"
    assert "artifact_id" not in progress["pending_structure"]
    assert "waiting_structure" not in progress
    assert worker.domain.artifact(artifact["id"])["status"] == "rejected"
    assert progress["interventions"][-1]["reason"] == body["reason"]
    assert dumps(result) != dumps(progress)


def test_edited_pending_chapter_requires_acknowledgement_and_new_structure_epoch(pending, client):
    worker, result, task = pending
    chapter_id = result["pending_structure"]["chapter_id"]
    worker.checkpoint(result, "paused")
    chapter = worker.domain.chapter(chapter_id)
    edited = worker.domain.save(chapter_id, chapter["revision"], "作者改稿：宁禾改在山口停步。")
    path = f"/api/tasks/{task['id']}/resume"
    assert client.post(path, json={}).status_code == 409
    response = client.post(path, json={"acknowledge_changes": True})
    assert response.status_code == 200, response.text
    pending = response.json()["result"]["pending_structure"]
    assert pending["revision"] == edited["revision"]
    assert pending["epoch"]
    assert "artifact_id" not in pending


def test_completion_cannot_bypass_pending_maintenance_or_unresolved_promises(pending):
    worker, result, task = pending
    with pytest.raises(DomainError, match="结构维护尚未完成"):
        worker.checkpoint(result, "production_complete")
    from ainovel.story_graph import SaveRecord, save_record

    saved = save_record(
        worker.domain,
        worker.task["branch_id"],
        SaveRecord(
            base_story_revision=result["expected_story_revision"],
            record={"kind": "promise", "title": "未解释的暗号", "stages": [{"status": "planned"}]},
        ),
    )
    result.pop("pending_structure")
    result["expected_story_revision"] = saved["story_revision"]
    worker.checkpoint(result, "production_complete")
    stored = worker.domain.task(task["id"])
    assert stored["status"] == "blocked_creative"
    assert stored["result"]["maintained_promises"]["unresolved"][0]["payload"]["title"] == "未解释的暗号"


@pytest.mark.parametrize("saved_pointer", [False, True])
def test_account_continuation_recovers_prior_structure_without_rebilling(pending, monkeypatch, saved_pointer):
    worker, result, task = pending
    artifact = stage_empty(worker, result)
    if saved_pointer:
        result["pending_structure"]["artifact_id"] = artifact["id"]
    worker.checkpoint(result, "paused")
    current = worker.domain.task(task["id"])
    continued = worker.domain.continue_workflow(
        current,
        {
            **current["payload"],
            "account": "deepseek",
            "client_request_id": "continue-structure-account",
        },
        {"acknowledge_unknown": True, "reason": "原账户预算不足，保留已生成结果"},
    )
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == continued["id"]

    async def unexpected_call(*args, **kwargs):
        pytest.fail("换账户后已有结果不应再次计费")

    monkeypatch.setattr(worker, "call", unexpected_call)
    progress = worker.task["result"]
    assert asyncio.run(maintain(worker, progress))
    stored = worker.domain.task(continued["id"])
    assert "pending_structure" not in stored["result"]
    assert stored["result"]["structure_history"][0]["artifact_id"] == artifact["id"]
    assert worker.domain.artifact(artifact["id"])["task_id"] == task["id"]
    assert worker.domain.task(task["id"])["status"] == "continued"
