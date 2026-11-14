"""Scene repair guidance is frozen per run and never changes time evidence."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import dumps
from ainovel.models import ModuleInput
from ainovel.scene_guidance import KEY, VERSION
from ainovel.scene_order import linked_scene_issues
from ainovel.worker import Worker
from test_extraction_scene_diagnostics import proposals, units_for
from test_extraction_scenes import SceneModel
from test_scene_order import scene
from test_workflow_scope import workflow_payload


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_new_scene_runs_freeze_guidance_without_changing_input_contract(domain, story, kind):
    if kind == "module":
        domain.save(story["chapter_id"], 0, "他先登船，随后到达对岸。")
        payload = ModuleInput(
            action="story.extract",
            novel_id=story["id"],
            branch_id=story["branch_id"],
            instruction="整理选定原文。",
            story_chapter_ids=[story["chapter_id"]],
            include_scene_ranges=True,
            account="openai_1",
        ).model_dump()
    else:
        payload = workflow_payload(story)
        payload.update(structured_maintenance=True, scene_maintenance=True)
    queued = domain.queue(kind, payload, story["branch_id"])
    assert queued["result"]["scene_order_guidance_version"] == "scene-order-guidance/1"
    assert queued["result"]["scene_contract_version"] == "scene-event-refs/2"
    assert queued["payload"] == payload


def source_task(domain, story, **changes):
    domain.save(story["chapter_id"], 0, "\n😀宁禾读到暗号。\n\n她收起铜牌。\n")
    payload = ModuleInput(
        action="story.extract",
        novel_id=story["id"],
        branch_id=story["branch_id"],
        instruction="仅整理本次保存稿。",
        story_chapter_ids=[story["chapter_id"]],
        include_scene_ranges=True,
        account="openai_1",
        client_request_id="scene-guidance-request",
    ).model_dump()
    return domain.queue("module", {**payload, **changes})


def replace_result(domain, task, result):
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))


@pytest.mark.parametrize(
    "orders,reason",
    [
        ((3,), None),
        ((3, 3), None),
        ((3, 4), None),
        ((3, None), None),
        ((None,), None),
        ((3, 4), "覆盖多个已知时点，不是单一精确点"),
    ],
)
def test_guidance_changes_only_diagnostics_never_orders_refs_or_acceptance(orders, reason):
    units = units_for("\n".join(f"😀𠮷原文事件{i}" for i in range(len(orders))))
    records = proposals(units, orders)
    scenes = [scene(units, [r["local_id"] for r in records], reason)]
    before = deepcopy((records, scenes))
    legacy = linked_scene_issues(scenes, units, {}, records)
    current = linked_scene_issues(scenes, units, {}, records, guidance=True)
    assert (records, scenes) == before
    assert len(legacy) == len(current)
    if legacy:
        assert all(
            "order_basis.kind=unknown" in message and "order_basis.reason" in message for message in current
        )
        assert all("order_basis.kind=unknown" not in message for message in legacy)
        assert all(u["text"] not in "".join(current) for u in units)
    else:
        assert current == []


def test_unknown_with_bad_refs_is_still_rejected_under_new_guidance():
    units = units_for("仍是本章原文。")
    records = proposals(units, (3,))
    value = [scene(units, ["missing-event"], "不能凭空引用")]
    assert linked_scene_issues(value, units, {}, records, guidance=True)


@pytest.mark.parametrize("legacy", [False, True])
def test_worker_uses_frozen_prompt_and_trace_without_adopting(settings, domain, story, legacy):
    task = source_task(domain, story)
    if legacy:
        old = deepcopy(task["result"])
        old.pop(KEY)
        replace_result(domain, task, old)
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "succeeded", finished["error"]
    instruction = model.calls[0]["任务"]
    assert instruction["场景证据契约版本"] == "scene-event-refs/2"
    assert ("场景时点提示版本" in instruction) is not legacy
    assert ("精确场景点与事件事实" in instruction) is not legacy
    if not legacy:
        assert instruction["场景时点提示版本"] == VERSION
        assert "order_basis.kind=unknown" in instruction["精确场景点与事件事实"]
        assert "事件story_order" in instruction["精确场景点与事件事实"]
    event = domain.db.one(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id LIMIT 1",
        (task["id"],),
    )
    metadata = json.loads(event["payload"])["request"]
    assert metadata.get(KEY) == (None if legacy else VERSION)
    assert not domain.db.all("SELECT * FROM story_records")
    assert not domain.db.all("SELECT * FROM scene_times")


@pytest.mark.parametrize("marker", [None, False, "future/2", 17])
def test_unsupported_declared_guidance_stops_before_model(settings, domain, story, marker):
    task = source_task(domain, story)
    replace_result(domain, task, {**task["result"], KEY: marker})
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "blocked_runtime"
    assert model.calls == []
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


@pytest.mark.parametrize("contract", [None, "scene-event-order/1", "future/2"])
def test_new_guidance_requires_original_explicit_scene_contract(settings, domain, story, contract):
    task = source_task(domain, story)
    replace_result(domain, task, {**task["result"], "scene_contract_version": contract})
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.calls == []


def test_guidance_cannot_enable_scenes_in_a_non_scene_request(settings, domain, story):
    task = source_task(domain, story, include_scene_ranges=False)
    assert KEY not in task["result"]
    replace_result(
        domain, task, {**task["result"], KEY: VERSION, "scene_contract_version": "scene-event-refs/2"}
    )
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.calls == []


def test_replaying_old_nonce_does_not_upgrade_guidance(domain, story):
    task = source_task(domain, story)
    old = deepcopy(task["result"])
    old.pop(KEY)
    replace_result(domain, task, old)
    replay = domain.queue("module", task["payload"])
    assert replay["id"] == task["id"] and replay["result"] == old
    fresh = domain.queue("module", {**task["payload"], "client_request_id": "new-scene-guidance-request"})
    assert fresh["result"][KEY] == VERSION
    assert domain.task(task["id"])["result"] == old


def test_unrelated_workflow_does_not_gain_or_pay_for_guidance(domain, story):
    task = domain.queue("workflow", {"novel_id": story["id"], "scene_maintenance": True}, story["branch_id"])
    assert KEY not in task["result"]
