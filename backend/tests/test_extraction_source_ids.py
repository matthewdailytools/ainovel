"""Source-only wire choices retain full, versioned domain evidence."""

import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from ainovel.db import dumps
from ainovel.evidence import source_schema
from ainovel.extraction_sources import KEY, VERSION, SourceIdSchema
from ainovel.story_extraction import Extraction
from ainovel.story_graph import RECORD
from ainovel.worker import Worker
from test_extraction_scene_diagnostics import units_for
from test_extraction_scenes import SceneModel
from test_scene_guidance import replace_result, source_task
from test_workflow_scope import workflow_payload


def extraction():
    units = units_for("😀𠮷宁禾读到暗号。\n她收起铜牌。")
    record = RECORD.validate_python(
        {
            "kind": "promise",
            "title": "铜牌的来历",
            "sources": [],
            "stages": [{"status": "planted", "sources": []}],
        }
    ).model_dump()
    record["sources"] = [{"quote": units[0]["source_id"]}]
    record["stages"][0]["sources"] = [{"quote": units[1]["source_id"]}]
    value = {
        "records": [{"local_id": "promise", "record": record, "reasoning": "仅依据所选原文"}],
        "unknowns": ["铜牌来历待核对"],
        "coverage": "全部选择的两段",
    }
    return value, units


def test_compact_wire_keeps_all_record_fields_and_restores_independent_stage_evidence():
    value, units = extraction()
    before = deepcopy((value, units))
    adapter = SourceIdSchema(Extraction, units)
    wire = source_schema(adapter.model_json_schema(), units)
    assert set(wire["$defs"]["Source"]["properties"]) == {"quote"}
    assert wire["$defs"]["Source"]["required"] == ["quote"]
    assert wire["$defs"]["Source"]["additionalProperties"] is False
    assert wire["$defs"]["Source"]["properties"]["quote"]["enum"] == [u["source_id"] for u in units]
    original = Extraction.model_json_schema()
    assert {k: v for k, v in wire["$defs"].items() if k != "Source"} == {
        k: v for k, v in original["$defs"].items() if k != "Source"
    }
    result = adapter.model_validate(value).model_dump()
    for sources, unit in zip(
        [result["records"][0]["record"]["sources"], result["records"][0]["record"]["stages"][0]["sources"]],
        units,
        strict=True,
    ):
        assert sources == [
            {**{k: unit[k] for k in ("chapter_id", "revision", "start", "end")}, "quote": unit["text"]}
        ]
    assert result["records"][0]["record"]["stages"][0]["sources"][0]["start"] == 12
    assert result["unknowns"] == value["unknowns"]
    assert (value, units) == before
    # The wire contract is request-local, never a change to stored/public data.
    assert Extraction.model_json_schema() == original


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"quote": ""},
        {"quote": None},
        {"quote": "S1P0001", "start": 0},
        {"quote": "S1P0001", "execute": "ignored?"},
        "S1P0001",
    ],
)
def test_malformed_source_choice_is_not_silently_completed_or_dropped(source):
    value, units = extraction()
    value["records"][0]["record"]["sources"] = [source]
    before = deepcopy(value)
    with pytest.raises(ValidationError):
        SourceIdSchema(Extraction, units).model_validate(value)
    assert value == before


def test_unknown_choice_has_no_guessed_position_and_no_implicit_main_to_stage_source():
    value, units = extraction()
    value["records"][0]["record"]["sources"] = [{"quote": "other-chapter-source"}]
    value["records"][0]["record"]["stages"][0]["sources"] = []
    result = SourceIdSchema(Extraction, units).model_validate(value).model_dump()
    source = result["records"][0]["record"]["sources"][0]
    assert source == {"chapter_id": "", "revision": 0, "start": 0, "end": 1, "quote": "other-chapter-source"}
    assert result["records"][0]["record"]["stages"][0]["sources"] == []


def test_full_legacy_sources_are_readable_without_changing_any_proposed_value():
    value, units = extraction()
    adapter = SourceIdSchema(Extraction, units)
    full = adapter.model_validate(value).model_dump()
    assert adapter.model_validate(full).model_dump() == full
    # Positions supplied in a full object remain visible to the existing
    # evidence validator, rather than getting a guessed correction here.
    full["records"][0]["record"]["sources"][0]["end"] = 999
    assert adapter.model_validate(full).model_dump() == full


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_new_structure_runs_freeze_source_output_without_a_new_author_option(domain, story, kind):
    if kind == "module":
        task = source_task(domain, story)
    else:
        task = domain.queue(
            "workflow", {**workflow_payload(story), "structured_maintenance": True}, story["branch_id"]
        )
    assert task["result"][KEY] == VERSION
    assert KEY not in task["payload"]


class CompactSceneModel(SceneModel):
    def __init__(self, bad=False):
        super().__init__()
        self.bad = bad

    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        for proposal in value["records"]:
            record = proposal["record"]
            for owner in [record, *(record.get("stages", []) if record["kind"] == "promise" else [])]:
                owner["sources"] = [{"quote": s["quote"]} for s in owner["sources"]]
        if self.bad:
            value["records"][0]["record"]["sources"][0]["quote"] = "S9P9999"
        value["coverage"] += f"轮次{len(self.calls)}"
        return {**result, "text": dumps(value)}


def test_real_worker_restores_choice_and_leaves_formal_story_and_manuscript_unchanged(
    settings, domain, story
):
    task = source_task(domain, story)
    chapter = domain.chapter(story["chapter_id"])
    model = CompactSceneModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "succeeded", finished["error"]
    assert model.calls[0]["任务"]["来源输出契约版本"] == VERSION
    artifact = domain.artifact(finished["result"]["artifact_id"])
    assert artifact["status"] == "candidate"
    for proposal in artifact["payload"]["records"]:
        for s in proposal["record"]["sources"]:
            assert (
                chapter["content"].encode("utf-16-le")[s["start"] * 2 : s["end"] * 2].decode("utf-16-le")
                == s["quote"]
            )
            assert (s["chapter_id"], s["revision"]) == (chapter["id"], chapter["revision"])
    dispatched = domain.db.one(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id LIMIT 1",
        (task["id"],),
    )
    assert json.loads(dispatched["payload"])["request"][KEY] == VERSION
    assert domain.chapter(chapter["id"]) == chapter
    assert not domain.db.all("SELECT * FROM story_records")
    assert not domain.db.all("SELECT * FROM scene_times")


def test_wrong_choice_still_stops_after_two_semantic_repairs_without_artifact(settings, domain, story):
    task = source_task(domain, story)
    model = CompactSceneModel(bad=True)
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "blocked_runtime"
    assert "sources[0].quote" in finished["error"]
    assert len(model.calls) == 3
    assert not domain.db.all("SELECT * FROM artifacts")
    assert not domain.db.all("SELECT * FROM story_records")


@pytest.mark.parametrize("marker", [None, False, "future/2"])
def test_unsupported_frozen_source_version_has_no_model_call(settings, domain, story, marker):
    task = source_task(domain, story)
    replace_result(domain, task, {**task["result"], KEY: marker})
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.calls == []


def test_legacy_nonce_and_prompt_remain_legacy(settings, domain, story):
    task = source_task(domain, story)
    old = deepcopy(task["result"])
    old.pop(KEY)
    replace_result(domain, task, old)
    assert domain.queue("module", task["payload"])["result"] == old
    model = SceneModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "succeeded"
    assert "来源输出契约版本" not in model.calls[0]["任务"]
    assert "来源输出格式" not in model.calls[0]["任务"]
    assert KEY not in domain.task(task["id"])["result"]


def test_unrelated_runs_never_gain_source_output_marker(domain, story):
    task = domain.queue("workflow", {"novel_id": story["id"]}, story["branch_id"])
    assert KEY not in task["result"]
