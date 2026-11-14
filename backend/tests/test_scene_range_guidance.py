"""Scope diagnostics cannot widen or silently repair scene time evidence."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import dumps
from ainovel.models import ModuleInput
from ainovel.scene_order import effective_records, linked_scene_issues, resolve_basis
from ainovel.scene_range_guidance import KEY, VERSION, RangeEvidence
from ainovel.story_extraction import adopt
from ainovel.worker import Worker
from test_extraction_scene_diagnostics import proposals, units_for
from test_extraction_scenes import SceneModel, data_for
from test_scene_guidance import replace_result, source_task
from test_scene_order import scene
from test_workflow_scope import workflow_payload


@pytest.mark.parametrize("basis", ["unknown", "event_refs"])
@pytest.mark.parametrize(
    "case", ["valid", "missing", "kind", "planned", "chapter", "revision", "range", "archived", "invalid"]
)
def test_new_diagnostics_preserve_all_decisions_and_inputs(basis, case):
    units = units_for("😀𠮷私有第一处原文\n第二处原文不进诊断")
    records = proposals(units, (4, 4))
    nodes = effective_records({}, records)
    selected = [units[1]]
    ref = "event-1"
    if case == "missing":
        ref = "not-declared"
    elif case == "range":
        ref = "event-0"
    else:
        r = nodes[ref]
        if case == "kind":
            r["record"]["kind"] = "character"
        if case == "planned":
            r["record"]["status"] = "planned"
        if case == "chapter":
            r["record"]["sources"][0]["chapter_id"] = "another-chapter"
        if case == "revision":
            r["record"]["sources"][0]["revision"] = 2
        if case == "archived":
            r["archived"] = 1
        if case == "invalid":
            r["invalid"] = ["源稿已变更"]
    s = scene(selected, [ref], "无法确认精确点" if basis == "unknown" else None)
    before = deepcopy((s, selected, nodes))
    old = resolve_basis(s, selected, nodes)
    new = resolve_basis(s, selected, nodes, range_context=RangeEvidence(units))
    assert (s, selected, nodes) == before
    assert old[1:] == new[1:]
    assert len(old[0]) == len(new[0])
    if case == "valid":
        assert new[0] == []
    else:
        text = "；".join(new[0])
        assert 'refs["' in text
        assert all(u["text"] not in text for u in units)
        expected = {
            "missing": "未在当前",
            "kind": "需要event",
            "planned": "status=planned",
            "chapter": "没有本章",
            "revision": "来源修订",
            "range": "覆盖交集=0",
            "archived": "已归档或失效",
            "invalid": "已归档或失效",
        }[case]
        assert expected in text


def test_one_bad_ref_does_not_erase_another_valid_ref_or_guess_its_point():
    units = units_for("第一处\n😀𠮷第二处")
    records = proposals(units, (8, 9))
    nodes = effective_records({}, records)
    value = scene([units[1]], ["event-0", "event-1"], "仍保留未知")
    before = deepcopy(value)
    errors, order, refs, covered = resolve_basis(value, [units[1]], nodes, range_context=RangeEvidence(units))
    assert len(errors) == 1 and "S1P0001" in errors[0] and "S1P0002" in errors[0]
    assert order is None and [r for r, _ in refs] == ["event-1"]
    assert [n["id"] for n in covered] == ["event-1"]
    assert value == before and value["order_basis"]["refs"] == ["event-0", "event-1"]


def test_effective_added_source_can_support_another_presentation_without_duplicate_event():
    units = units_for("😀𠮷第一次呈现\n同一事件第二次呈现")
    records = proposals(units[:1], (4,))
    scenes = [scene(units[:1], ["event-0"]), scene(units[1:], ["event-0"])]
    assert linked_scene_issues(scenes, units, {}, records, range_guidance=True)
    original = deepcopy(records)
    records[0]["record"]["sources"].append(
        {**{k: units[1][k] for k in ("chapter_id", "revision", "start", "end")}, "quote": units[1]["text"]}
    )
    assert linked_scene_issues(scenes, units, {}, records, range_guidance=True) == []
    assert len(records) == 1 and records[0]["local_id"] == original[0]["local_id"]
    assert records[0]["record"]["story_order"] == 4
    assert records[0]["record"]["sources"][0] == original[0]["record"]["sources"][0]


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_new_scene_runs_freeze_range_version_without_an_author_input_field(domain, story, kind):
    t = (
        source_task(domain, story)
        if kind == "module"
        else domain.queue(
            "workflow",
            {**workflow_payload(story), "structured_maintenance": True, "scene_maintenance": True},
            story["branch_id"],
        )
    )
    assert t["result"][KEY] == VERSION and KEY not in t["payload"]


class RangeModel(SceneModel):
    def __init__(self, fix=True):
        super().__init__()
        self.fix = fix

    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        instruction = self.calls[-1]["任务"]
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        units = instruction["可引用原文"]
        value["scenes"][1]["order_basis"] = {
            "kind": "unknown",
            "reason": "事件在本段再次呈现但无唯一精确点",
            "refs": ["e"],
        }
        if self.fix and len(self.calls) > 1:
            event = next(p["record"] for p in value["records"] if p["local_id"] == "e")
            event["sources"].append(
                {
                    **{k: units[1][k] for k in ("chapter_id", "revision", "start", "end")},
                    "quote": units[1]["source_id"],
                }
            )
        value["coverage"] += f"轮次{len(self.calls)}"
        return {**result, "text": dumps(value)}


def test_real_worker_repairs_evidence_only_then_author_can_adopt_one_event(settings, domain, story):
    domain.save(
        story["chapter_id"],
        0,
        "\n😀也叫小宁的渡口来客宁禾，在掌柜钱叔面前读出了铜牌上的暗号。\n\n钱叔复述宁禾方才读出这段暗号的经过。\n",
    )
    task = domain.queue(
        "module",
        ModuleInput(
            action="story.extract",
            novel_id=story["id"],
            branch_id=story["branch_id"],
            story_chapter_ids=[story["chapter_id"]],
            include_scene_ranges=True,
            instruction="整理同一事件的两处原文呈现。",
            account="openai_1",
            client_request_id="scene-range-evidence-repair",
        ).model_dump(),
    )
    chapter = domain.chapter(story["chapter_id"])
    model = RangeModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "succeeded", finished["error"]
    assert len(model.calls) == 2
    assert "覆盖交集=0" in "；".join(model.calls[1]["任务"]["定位错误"])
    instruction = model.calls[0]["任务"]
    assert instruction["场景范围提示版本"] == VERSION
    assert "refs:可选相关事件id列表" not in instruction["场景顺序语义"]
    assert "本场景直接原文依据" in instruction["场景顺序语义"]
    metadata = json.loads(
        domain.db.one(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id LIMIT 1",
            (task["id"],),
        )["payload"]
    )["request"]
    assert metadata[KEY] == VERSION
    artifact = domain.artifact(finished["result"]["artifact_id"])
    assert artifact["status"] == "candidate"
    assert not domain.db.all("SELECT * FROM story_records")
    e = next(p["record"] for p in artifact["payload"]["records"] if p["local_id"] == "e")
    assert e["story_order"] == 10 and len(e["sources"]) == 2
    adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    assert len(domain.db.all("SELECT * FROM story_records WHERE kind='event'")) == 1
    assert domain.chapter(chapter["id"]) == chapter


@pytest.mark.parametrize("legacy", [False, True])
def test_exhausted_scope_failure_keeps_old_prompt_and_errors_when_frozen_legacy(
    settings, domain, story, legacy
):
    task = source_task(domain, story)
    if legacy:
        old = deepcopy(task["result"])
        old.pop(KEY)
        replace_result(domain, task, old)
        assert domain.queue("module", task["payload"])["result"] == old
    model = RangeModel(fix=False)
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "blocked_runtime" and len(model.calls) == 3
    instruction = model.calls[0]["任务"]
    assert ("场景范围提示版本" in instruction) is not legacy
    assert ("refs:可选相关事件id列表" in instruction["场景顺序语义"]) is legacy
    assert ("覆盖交集=0" in finished["error"]) is not legacy
    if legacy:
        assert "须为本场景原文支持的实际事件" in finished["error"]
    assert not domain.db.all("SELECT * FROM artifacts")


@pytest.mark.parametrize("marker", [None, False, "future/2"])
def test_unrecognized_range_version_never_calls_model(settings, domain, story, marker):
    task = source_task(domain, story)
    replace_result(domain, task, {**task["result"], KEY: marker})
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and model.calls == []


def test_range_hint_cannot_enable_scenes_or_change_another_contract(settings, domain, story):
    task = source_task(domain, story, include_scene_ranges=False)
    assert KEY not in task["result"]
    replace_result(
        domain, task, {**task["result"], KEY: VERSION, "scene_contract_version": "scene-event-order/1"}
    )
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and model.calls == []
