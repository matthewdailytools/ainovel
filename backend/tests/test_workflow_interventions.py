"""Different intervention shapes must not be mistaken for rejected prose."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import dumps
from ainovel.domain import Domain
from ainovel.worker import Worker
from test_api_worker import FakeModel
from test_extraction_scenes import SceneOnlyModel
from test_workflow_scope import SegmentModel, workflow_payload


class PausingMaintenanceModel(SegmentModel):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.paused = False
        self.generations = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        action = frame["任务"].get("动作")
        if action == "按保存稿反向整理长篇结构候选":
            self.calls += 1
            return await SceneOnlyModel().generate(account, task_id, system, prompt, max_output, **kwargs)
        if action == "按章纲写完整正文":
            self.generations.append(deepcopy(frame))
        result = await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        if action == "从正文提取事实候选与章节摘要" and not self.paused:
            response = self.client.post(f"/api/tasks/{task_id}/pause")
            assert response.status_code == 200
            self.paused = True
        return result


def test_world_change_acknowledgement_maintains_pending_then_generates_next_chapter(
    client, settings, domain, story
):
    spec = workflow_payload(story, count=2)
    spec.update(structured_maintenance=True, scene_maintenance=True)
    task = domain.queue("workflow", spec, story["branch_id"])
    model = PausingMaintenanceModel(client)
    asyncio.run(Worker(settings, model).run_once())
    paused = domain.task(task["id"])
    assert paused["status"] == "paused"
    assert paused["result"]["next_chapter"] == 2
    assert paused["result"]["pending_structure"]["chapter_id"] == story["chapter_id"]
    first_chapter = deepcopy(domain.chapter(story["chapter_id"]))
    assert len(model.generations) == 1

    world = "后续核验先建立共同基准，保留既有原始记录，不能把计划当成已发生。"
    response = client.post(
        f"/api/branches/{story['branch_id']}/definitions",
        json={
            "kind": "world",
            "text": world,
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        },
    )
    assert response.status_code == 201, response.text
    assert any(
        row["kind"] == "world" and row["payload"]["text"] == world for row in response.json()["definitions"]
    )
    response = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_changes": True})
    assert response.status_code == 200, response.text
    interventions = deepcopy(response.json()["result"]["interventions"])
    assert interventions[-1]["action"] == "recheck_structure_after_author_changes"
    assert "artifact_id" not in interventions[-1]

    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "segment_complete", completed["error"]
    assert completed["result"]["interventions"] == interventions
    assert "pending_structure" not in completed["result"]
    assert len(completed["result"]["structure_history"]) == 2
    assert domain.chapter(story["chapter_id"]) == first_chapter
    assert len(model.generations) == 2
    second = model.generations[1]
    assert second["任务"]["本章"]["number"] == 2
    assert "本章前次未采纳的审查意见" not in second["任务"]
    assert any(
        row["kind"] == "world" and row["payload"]["text"] == world for row in second["上下文"]["definitions"]
    )


def draft_task(domain, story):
    spec = workflow_payload(story, scope="whole_book")
    task = domain.queue("workflow", spec, story["branch_id"])
    result = {
        "plan": {"chapters": spec["plots"], "ending": "完成核对"},
        "next_chapter": 1,
        "repair_round": 0,
        "generation_epoch": "intervention-test",
    }
    return task, result


def artifact(domain, story, task, kind):
    value = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 0,
        },
        kind,
        {"text": "旧失败正文不能作为故事事实", "review": {"summary": kind + "审稿"}, "review_passed": False}
        if kind == "chapter_draft"
        else {"records": [], "unknowns": [], "coverage": "结构候选不属于正文"},
        {},
        task["id"],
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET status='rejected' WHERE id=?", (value["id"],))
    return value


@pytest.mark.parametrize("structure_action", [None, "regenerate_structure", "regenerate_pending"])
@pytest.mark.parametrize("draft_action", [None, "regenerate_pending"])
def test_structure_interventions_never_replace_valid_rejected_prose(
    settings, domain, story, monkeypatch, structure_action, draft_action
):
    task, result = draft_task(domain, story)
    draft = artifact(domain, story, task, "chapter_draft")
    structure = artifact(domain, story, task, "story_extraction")
    older = {"artifact_id": draft["id"], "reason": "明确重做正文"}
    newer = {"artifact_id": structure["id"], "reason": "仅结构重做"}
    if draft_action is not None:
        older["action"] = draft_action
    if structure_action is not None:
        newer["action"] = structure_action
    result["interventions"] = [
        older,
        newer,
        {"action": "recheck_structure_after_author_changes", "chapter_id": story["chapter_id"]},
    ]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    read = []
    original = Domain.artifact

    def tracked(self, identifier):
        read.append(identifier)
        return original(self, identifier)

    monkeypatch.setattr(Domain, "artifact", tracked)
    model = FakeModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "production_complete", finished["error"]
    assert finished["result"]["interventions"] == result["interventions"]
    assert (
        model.generation_instructions["本章前次未采纳的审查意见"]["review"]["summary"] == "chapter_draft审稿"
    )
    assert (structure["id"] in read) is (structure_action != "regenerate_structure")
    assert "旧失败正文" not in domain.chapter(story["chapter_id"])["content"]


@pytest.mark.parametrize("action", [None, "regenerate_pending"])
def test_missing_explicit_prose_reference_is_not_silently_ignored(settings, domain, story, action):
    task, result = draft_task(domain, story)
    intervention = {"artifact_id": "missing-explicit-candidate"}
    if action is not None:
        intervention["action"] = action
    result["interventions"] = [intervention]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    model = FakeModel()
    asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    assert stored["status"] == "blocked_runtime"
    assert model.calls == 0 and domain.chapter(story["chapter_id"])["content"] == ""
    stopped = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='stopped'", (task["id"],)
    )
    assert json.loads(stopped[-1]["payload"])["code"] == "not_found"


def test_unexpected_error_diagnostics_keep_bounded_frames_without_private_message(
    settings, domain, story, caplog
):
    task, _ = draft_task(domain, story)
    secret = "sk-fake-diagnostic-key-never-persist"
    manuscript = "原稿内容假样本不应进入内部异常日志"

    class ExplodingModel:
        async def generate(self, *args, **kwargs):
            def fail(depth):
                if depth:
                    return fail(depth - 1)
                raise ValueError(secret + manuscript)

            fail(12)

    asyncio.run(Worker(settings, ExplodingModel()).run_once())
    saved = domain.task(task["id"])
    assert (
        saved["status"] == "blocked_runtime"
        and saved["error"] == "内部步骤失败，进度已保留；请查看离线诊断。"
    )
    rows = domain.db.all("SELECT kind,payload FROM task_events WHERE task_id=?", (task["id"],))
    diagnostic = next(json.loads(row["payload"]) for row in rows if row["kind"] == "internal_error")
    assert set(diagnostic) == {"task_id", "attempt", "error_type", "frames"}
    assert diagnostic["task_id"] == task["id"] and diagnostic["attempt"] == saved["attempt"]
    assert diagnostic["error_type"] == "ValueError" and len(diagnostic["frames"]) == 8
    assert all(set(frame) == {"path", "function", "line"} for frame in diagnostic["frames"])
    assert all(frame["path"].startswith(("backend/", "scripts/")) for frame in diagnostic["frames"])
    assert all(type(frame["line"]) is int and frame["line"] > 0 for frame in diagnostic["frames"])
    assert any(frame["path"].endswith("test_workflow_interventions.py") for frame in diagnostic["frames"])
    serialized = dumps(rows) + dumps(saved) + caplog.text
    assert secret not in serialized and manuscript not in serialized
    assert "ValueError" in caplog.text and "test_workflow_interventions.py" in caplog.text
