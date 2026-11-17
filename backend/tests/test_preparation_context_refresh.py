"""An acknowledged prior rewrite refreshes context, never a source verdict."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel import workflow_plan_context as context
from ainovel.db import dumps
from ainovel.models import WorkflowInput
from ainovel.providers import ModelClient
from ainovel.scene_time import SaveScene, save_scene
from ainovel.worker import Worker
from ainovel.workflow_plan_preparation import prepare
from test_workflow_plan_source_roles import deepseek_completed
from test_workflow_plan_preparation import PreparationModel
from test_workflow_scope import workflow_payload


def source_model(worker):
    frames = []

    def handler(request):
        frame = json.loads(json.loads(request.content)["messages"][1]["content"].split("\n只返回")[0])
        frames.append(frame)
        task = frame["任务"]
        assert task["动作"] == "独立观察AI章纲接收范围来源"
        units = task["可引用原文"]
        return deepseek_completed(
            {
                "input_sha256": task["input_sha256"],
                "observations": [
                    {
                        "observation_id": "O01",
                        "kind": "unknown",
                        "supplier": "学徒",
                        "recipient": "",
                        "transfer_object": "记录",
                        "transfer_scope": "none",
                        "evidence": units[0]["source_id"],
                        "restriction_evidence": units[0]["source_id"],
                        "condition": "",
                        "reason": "匿名旧观察保留未交出记录的来源待核，不作文学判断。",
                    }
                ],
                "unit_checks": [
                    {
                        "source_id": u["source_id"],
                        "status": "covered",
                        "observation_ids": ["O01"],
                        "reason": "仅确定性工程来源观察。",
                    }
                    for u in units
                ],
                "note": "这个已付未知不能借前章改稿自动重判或重发。",
            }
        )

    worker.client = ModelClient(worker.settings, worker.budget, httpx.MockTransport(handler))
    return frames


def stopped_preparation(
    settings, domain, story, client, *, scene_maintenance=False, maintain=True, rewrite=True
):
    prior = domain.save(story["chapter_id"], 0, "OLD_PRIOR：老师检查旧灯，量具尚未校核。")
    scene = None
    if scene_maintenance:
        scene = save_scene(
            domain,
            story["branch_id"],
            prior["id"],
            SaveScene(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                scene={
                    "title": "老师检查旧灯",
                    "source_revision": 1,
                    "start": 0,
                    "end": len(prior["content"].encode("utf-16-le")) // 2,
                    "audience": "author",
                    "story_order": None,
                },
            ),
        )["scenes"][0]
    chapter = domain.create_chapter(story["branch_id"], "第二章", 2)
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="automatic",
        brief="匿名两章改稿上下文测试。",
        chapters=2,
        account="deepseek",
        min_chars=200,
        max_chars=600,
        plot_contract_check=True,
        scene_maintenance=scene_maintenance,
        client_request_id="context-refresh-existing-workflow",
    ).model_dump(exclude_none=True)
    queued = domain.queue("workflow", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == queued["id"]
    plot = {"number": 2, "title": "第二章", "plot": "学徒没有交出记录。", "fixed": []}
    worker.task["result"].update(
        plan={
            "chapters": [{"number": 1, "title": "第一章", "plot": "检查旧灯。", "fixed": []}, plot],
            "ending": "保留有据的记录。",
        },
        next_chapter=2,
        repair_round=0,
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
    )
    worker.checkpoint(worker.task["result"])
    frames = source_model(worker)
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    assert len(frames) == 1
    assert domain.task(queued["id"])["status"] == "needs_review"
    assert client.post(f"/api/tasks/{queued['id']}/pause", json={}).status_code == 200
    old = domain.task(queued["id"])
    rewritten = (
        domain.save(prior["id"], 1, "NEW_PRIOR：老师保留量具校核记录，疑点仍待查。") if rewrite else prior
    )
    if scene and maintain:
        save_scene(
            domain,
            story["branch_id"],
            prior["id"],
            SaveScene(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                base_revision=scene["revision"],
                scene={
                    **scene["payload"],
                    "source_revision": rewritten["revision"],
                    "end": len(rewritten["content"].encode("utf-16-le")) // 2,
                },
            ),
            scene["id"],
        )
    return worker, old, chapter, rewritten


def model_history(worker, domain, task_id):
    with worker.budget.db() as usage:
        calls = [dict(r) for r in usage.execute("SELECT * FROM calls ORDER BY id")]
        metadata = [dict(r) for r in usage.execute("SELECT * FROM call_metadata ORDER BY call_id")]
    return {
        "calls": calls,
        "metadata": metadata,
        "steps": domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (task_id,)),
    }


@pytest.mark.parametrize("scene_maintenance", [False, True])
def test_explicit_ack_refreshes_only_context_and_keeps_paid_unknown_and_history(
    settings, domain, story, client, scene_maintenance
):
    worker, old, _, rewritten = stopped_preparation(
        settings, domain, story, client, scene_maintenance=scene_maintenance
    )
    old_row = deepcopy(old["result"]["plot_preparations"]["2"])
    before = model_history(worker, domain, old["id"])
    old_events = domain.db.all("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (old["id"],))
    assert client.post(f"/api/tasks/{old['id']}/resume", json={}).status_code == 409
    response = client.post(
        f"/api/tasks/{old['id']}/resume",
        json={"acknowledge_changes": True, "reason": "前章正文和维护已核对。"},
    )
    assert response.status_code == 200, response.text
    queued = response.json()
    row = queued["result"]["plot_preparations"]["2"]
    assert {k: v for k, v in row.items() if k != context.KEY} == {
        k: v for k, v in old_row.items() if k != context.KEY
    }
    assert row["phases"] == old_row["phases"] and row["status"] == "needs_review"
    assert row[context.KEY]["basis"]["story_revision"] == domain.branch(old["branch_id"])["story_revision"]
    assert row[context.KEY] != old_row[context.KEY]
    assert row[context.KEY]["data"]["recent"][0]["content"] == rewritten["content"]
    assert "OLD_PRIOR" not in dumps(row[context.KEY])
    assert queued["payload"] == old["payload"]
    for name in ("plan", "repair_round"):
        assert queued["result"][name] == old["result"][name]
    audit = queued["result"]["preparation_context_refreshes"]
    assert len(audit) == 1 and audit[0]["previous_preparation"] == old_row
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='preparation_context_refreshed'",
        (old["id"],),
    )
    assert [json.loads(e["payload"]) for e in events] == audit
    assert model_history(worker, domain, old["id"]) == before
    assert (
        domain.db.all("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (old["id"],))[
            : len(old_events)
        ]
        == old_events
    )
    assert (
        client.post(f"/api/tasks/{old['id']}/resume", json={"acknowledge_changes": True}).status_code == 409
    )
    resumed = Worker(settings)
    frames = source_model(resumed)
    assert asyncio.run(resumed.run_once())
    assert frames == []
    current = domain.task(old["id"])
    assert current["status"] == "needs_review"
    assert current["result"]["plot_preparations"]["2"]["phases"] == old_row["phases"]
    assert current["result"]["preparation_context_refreshes"] == audit
    assert model_history(resumed, domain, old["id"]) == before


@pytest.mark.parametrize(
    "blocker",
    [
        "zero_unknown",
        "missing_step",
        "missing_metadata",
        "partial_step",
        "interpretation",
        "round_one",
        "future_preparation",
        "body_saved",
        "body_dispatched",
        "source_changed",
        "plan_missing",
    ],
)
def test_refresh_rejects_unresolved_or_started_preparation_without_side_effects(
    settings, domain, story, client, blocker
):
    worker, old, chapter, _ = stopped_preparation(settings, domain, story, client)
    result = deepcopy(old["result"])
    row = result["plot_preparations"]["2"]
    if blocker == "zero_unknown":
        identifier = worker.budget.reserve(settings.providers["deepseek"], "another-task", 0, 0)
        worker.budget.settle(identifier)
    elif blocker == "missing_step":
        with domain.db.connect(True) as tx:
            tx.execute("DELETE FROM task_steps WHERE task_id=?", (old["id"],))
    elif blocker == "missing_metadata":
        with worker.budget.db(True) as tx:
            tx.execute("DELETE FROM call_metadata")
    elif blocker == "partial_step":
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE task_steps SET result='{}' WHERE task_id=?", (old["id"],))
    elif blocker == "interpretation":
        row["interpretations"] = [{"round": 0}]
    elif blocker == "round_one":
        row["round"] = 1
    elif blocker == "future_preparation":
        result["plot_preparations"]["3"] = {}
    elif blocker == "body_saved":
        domain.save(chapter["id"], 0, "未经该流程采纳的手写正文。")
    elif blocker == "body_dispatched":
        domain.db.emit(old["id"], "chapter_started", {"number": 2, "chapter_id": chapter["id"]})
    elif blocker == "source_changed":
        row["original_plot"] += "来源被改动。"
    elif blocker == "plan_missing":
        result["plan"]["chapters"] = result["plan"]["chapters"][:1]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), old["id"]))
    unchanged = domain.task(old["id"])
    events = domain.db.all("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (old["id"],))
    before = model_history(worker, domain, old["id"])
    response = client.post(f"/api/tasks/{old['id']}/resume", json={"acknowledge_changes": True})
    assert response.status_code == 409, response.text
    assert domain.task(old["id"]) == unchanged
    assert domain.db.all("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (old["id"],)) == events
    assert model_history(worker, domain, old["id"]) == before


@pytest.mark.parametrize("blocker", ["scene_stale", "unrelated_revision"])
def test_refresh_requires_actual_prior_rewrite_and_completed_maintenance(
    settings, domain, story, client, blocker
):
    worker, old, _, _ = stopped_preparation(
        settings,
        domain,
        story,
        client,
        scene_maintenance=blocker == "scene_stale",
        maintain=False,
        rewrite=blocker != "unrelated_revision",
    )
    if blocker == "unrelated_revision":
        # A new unrelated chapter changes the branch version without rewriting
        # any manuscript present in the frozen prior-context snapshot.
        domain.create_chapter(story["branch_id"], "第三章", 3)
    unchanged = domain.task(old["id"])
    before = model_history(worker, domain, old["id"])
    response = client.post(f"/api/tasks/{old['id']}/resume", json={"acknowledge_changes": True})
    assert response.status_code == 409, response.text
    assert domain.task(old["id"]) == unchanged
    assert model_history(worker, domain, old["id"]) == before


@pytest.mark.parametrize("acknowledge_changes", [False, True])
def test_accepted_ready_candidate_resumes_existing_structure_maintenance(
    settings, domain, story, client, acknowledge_changes
):
    class MaintainedModel(PreparationModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            if instruction.get("动作") == "按保存稿反向整理长篇结构候选":
                return {
                    "text": dumps({"records": [], "unknowns": [], "coverage": "仅当前已采纳章节。"}),
                    "usage": {"input": 1, "output": 1},
                    "call_id": "offline-maintenance",
                    "model": "offline-fixture",
                }
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    payload = workflow_payload(story, mode="automatic", count=2, adoption="manual")
    payload.update(plot_contract_check=True, structured_maintenance=True)
    queued = domain.queue("workflow", payload, story["branch_id"])
    model = MaintainedModel()
    assert asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(queued["id"])
    assert stopped["status"] == "needs_review", stopped["error"]
    old_row = deepcopy(stopped["result"]["plot_preparations"]["1"])
    assert old_row["status"] == "ready" and old_row[context.KEY]
    identifier = stopped["result"]["waiting_artifact"]
    domain.accept(identifier, "accept-before-normal-resume")
    response = client.post(
        f"/api/tasks/{queued['id']}/resume",
        json={"acknowledge_changes": acknowledge_changes, "pause_after_maintenance": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["plot_preparations"]["1"] == old_row
    assert "preparation_context_refreshes" not in response.json()["result"]
    assert asyncio.run(Worker(settings, model).run_once())
    maintained = domain.task(queued["id"])
    assert maintained["status"] == "paused", maintained["error"]
    assert maintained["result"]["plot_preparations"]["1"] == old_row
    assert maintained["result"]["next_chapter"] == 2
    assert "waiting_artifact" not in maintained["result"]
    assert not maintained["result"].get("pending_structure")
    assert not maintained["result"].get("waiting_structure")
    history = maintained["result"]["structure_history"]
    assert len(history) == 1 and history[0]["chapter_id"] == story["chapter_id"]
    artifact = domain.artifact(history[0]["artifact_id"])
    assert artifact["kind"] == "story_extraction" and artifact["status"] == "accepted"
    assert domain.chapter(story["chapter_id"])["revision"] == 1
    assert not domain.db.all(
        "SELECT id FROM task_events WHERE task_id=? AND kind='preparation_context_refreshed'", (queued["id"],)
    )
