"""Scene-order failures are precise diagnostics, never automatic chronology edits."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.evidence import source_units
from ainovel.extraction_scenes import scene_issues
from ainovel.story_extraction import SCENE_GROUNDING_VERSION, _grounded_extraction
from ainovel.story_graph import RECORD
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain


def scene(ids, order):
    return {
        "title": "场景",
        "source_ids": ids,
        "story_order": order,
        "story_time": "未知",
        "audience": "author",
        "character_id": None,
        "note": "待核对",
    }


def proposals(units, orders=(1, 2, 3, 4)):
    return [
        {
            "local_id": f"event-{index}",
            "target_id": None,
            "record": RECORD.validate_python(
                {
                    "kind": "event",
                    "title": f"事件{index}",
                    "status": "actual",
                    "story_order": order,
                    "sources": [
                        {**{k: u[k] for k in ("chapter_id", "revision", "start", "end")}, "quote": u["text"]}
                    ],
                }
            ).model_dump(),
        }
        for index, (u, order) in enumerate(zip(units, orders, strict=True))
    ]


def units_for(text="井\n渡船\n😀𠮷到岸\n归"):
    return [{**u, "chapter_id": "chapter", "revision": 1} for u in source_units(text, "S1P")]


@pytest.mark.parametrize(
    "orders,provided,valid", [((1, 2), 1, False), ((1, 1), 1, True), ((1, 2), None, True)]
)
def test_multi_event_scene_keeps_unique_order_policy_and_never_changes_output(orders, provided, valid):
    units = units_for("井\n渡船")
    rows = proposals(units, orders)
    value = [scene([u["source_id"] for u in units], provided)]
    before = deepcopy(value)
    issues = scene_issues(value, units, {}, rows)
    assert not issues if valid else len(issues) == 1
    assert value == before
    if not valid:
        assert "scenes[0].story_order 当前值=1" in issues[0]
        assert "S1P0001" in issues[0] and "S1P0002" in issues[0]
        assert "event-0/order=1" in issues[0] and "event-1/order=2" in issues[0]
        assert "null与未知说明" in issues[0] and "合理拆分" in issues[0]
        assert "井" not in issues[0] and "渡船" not in issues[0]


@pytest.mark.parametrize(
    "order,status,description",
    [(3, "actual", "event-0/order=3"), (None, "actual", "event-0/order=None"), (3, "planned", "无实际事件")],
)
def test_wrong_or_missing_actual_order_names_field_and_basis(order, status, description):
    units = units_for("渡船")
    rows = proposals(units, (order,))
    rows[0]["record"]["status"] = status
    value = [scene([units[0]["source_id"]], 2)]
    before = deepcopy(value)
    issues = scene_issues(value, units, {}, rows)
    assert len(issues) == 1 and "scenes[0].story_order 当前值=2" in issues[0]
    assert description in issues[0] and "S1P0001" in issues[0]
    assert value == before


class SceneDiagnosticModel:
    def __init__(self, fix_after=None, bad_source=False):
        self.calls = []
        self.outputs = []
        self.fix_after, self.bad_source = fix_after, bad_source

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        self.calls.append(deepcopy(frame))
        round_number = len(self.calls) - 1
        units = instruction["可引用原文"]
        if instruction.get("前次产物"):
            value = deepcopy(instruction["前次产物"])
            # Keep previously restored literal quotes and their exact UTF-16 locations.
            if self.bad_source:
                value["records"][0]["record"]["sources"][0]["quote"] = units[0]["source_id"]
        else:
            value = {
                "records": [{**p, "reasoning": "隔离工程来源"} for p in proposals(units)],
                "unknowns": [],
                "coverage": "只读样本",
                "scenes": [
                    scene([u["source_id"] for u in units[:2]], 1),
                    scene([units[2]["source_id"]], 2),
                    scene([units[3]["source_id"]], 3),
                ],
            }
            if self.bad_source:
                value["records"][0]["record"]["sources"][0]["quote"] = "invalid-source"
        value["coverage"] = f"工程校验轮次{round_number}"  # Distinct invalid responses exercise all 3 calls.
        if self.fix_after is not None and round_number >= self.fix_after:
            value["scenes"][0].update(story_order=None, note="本场跨两个实际事件顺序，保留未知边界")
            value["scenes"][1]["story_order"] = 3
            value["scenes"][2]["story_order"] = 4
        self.outputs.append(deepcopy(value))
        return {
            "text": dumps(value),
            "usage": {"input": 1, "output": 1},
            "call_id": f"offline-scene-{round_number}",
        }


def pending(settings, domain, story, model):
    chapter = domain.save(story["chapter_id"], 0, "井\n渡船\n😀𠮷到岸\n归")
    domain.queue(
        "workflow",
        {
            "novel_id": story["id"],
            "account": "deepseek",
            "adoption": "automatic",
            "structured_maintenance": True,
            "scene_maintenance": True,
        },
        story["branch_id"],
    )
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    progress = {
        "next_chapter": 2,
        "expected_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "pending_structure": {"chapter_id": chapter["id"], "revision": chapter["revision"]},
    }
    worker.task["result"] = progress
    worker.checkpoint(progress)
    return worker, progress, deepcopy(chapter)


def test_real_worker_scene_failure_stops_after_three_calls_with_precise_last_issues(settings, domain, story):
    model = SceneDiagnosticModel()
    worker, progress, chapter = pending(settings, domain, story, model)
    with pytest.raises(DomainError) as caught:
        asyncio.run(maintain(worker, progress))
    error = caught.value
    assert error.code == "evidence" and error.status == 400
    assert "结构候选在两轮引用修正后仍未通过校验（3项）" in str(error)
    assert all(f"scenes[{index}].story_order" in str(error) for index in range(3))
    assert "无法回到原文" not in str(error)
    assert len(model.calls) == 3
    assert all(c["任务"]["场景证据契约版本"] == SCENE_GROUNDING_VERSION for c in model.calls)
    assert "不是场景编号" in model.calls[0]["任务"]["场景顺序语义"]
    for request in model.calls[1:]:
        assert len(request["任务"]["定位错误"]) == 3
        assert "event-0/order=1" in request["任务"]["定位错误"][0]
        assert "event-1/order=2" in request["任务"]["定位错误"][0]
    repairs = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair' ORDER BY id",
        (worker.task["id"],),
    )
    assert [json.loads(row["payload"])["round"] for row in repairs] == [0, 1, 2]
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.db.all("SELECT * FROM scene_times") == []
    assert domain.chapter(chapter["id"]) == chapter
    assert "artifact_id" not in domain.task(worker.task["id"])["result"]["pending_structure"]


def test_repaired_quote_survives_another_round_and_valid_scene_can_maintain(settings, domain, story):
    model = SceneDiagnosticModel(fix_after=2, bad_source=True)
    worker, progress, chapter = pending(settings, domain, story, model)
    assert asyncio.run(maintain(worker, progress))
    assert len(model.calls) == 3
    assert any("sources[0].quote" in issue for issue in model.calls[1]["任务"]["定位错误"])
    assert not any("sources[0].quote" in issue for issue in model.calls[2]["任务"]["定位错误"])
    # All other quotes were restored in pass 0; pass 1's repaired first quote also
    # remains a valid literal in pass 2, without a new source mapping or a rewrite.
    prior = model.calls[2]["任务"]["前次产物"]
    assert [p["record"]["sources"][0]["quote"] for p in prior["records"]] == chapter["content"].splitlines()
    assert [p["record"]["sources"][0]["start"] for p in prior["records"]] == [0, 2, 5, 12]
    assert [p["record"]["sources"][0]["end"] for p in prior["records"]] == [1, 4, 11, 13]
    history = domain.task(worker.task["id"])["result"]["structure_history"]
    artifact = domain.artifact(history[0]["artifact_id"])
    assert artifact["status"] == "accepted"
    assert [s["scene"]["story_order"] for s in artifact["payload"]["scenes"]] == [None, 3, 4]
    assert domain.chapter(chapter["id"]) == chapter


def test_extraction_error_wrapper_preserves_other_errors_and_bounds_summary():
    original = DomainError("network_unknown", "保留原外部错误", 502)

    class Failing:
        async def grounded(self, instruction, schema, context, validator, **kwargs):
            validator({})
            raise original

    with pytest.raises(DomainError) as caught:
        asyncio.run(_grounded_extraction(Failing(), {}, None, {}, lambda _: ["定位信息"]))
    assert caught.value is original
    original = DomainError("evidence", "原最终错误", 409)
    with pytest.raises(DomainError) as caught:
        asyncio.run(_grounded_extraction(Failing(), {}, None, {}, lambda _: ["x" * 5000] * 20))
    assert caught.value.code == "evidence" and caught.value.status == 409
    assert len(str(caught.value)) < 5000 and "另14项" in str(caught.value)
    with pytest.raises(DomainError) as caught:
        asyncio.run(_grounded_extraction(Failing(), {}, None, {}, lambda _: []))
    assert caught.value is original
