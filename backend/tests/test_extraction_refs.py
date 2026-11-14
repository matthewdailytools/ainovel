"""Repair diagnostics identify fields and kinds without guessing person identity."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import dumps
from ainovel.extraction_refs import KEY, VERSION, fields, reference_issues
from ainovel.scene_order import effective_records
from ainovel.story_graph import RECORD, references
from ainovel.worker import Worker
from test_extraction_scenes import SceneModel
from test_scene_guidance import replace_result, source_task
from test_workflow_scope import workflow_payload


def record(kind, **values):
    base = {"kind": kind, "title": "工程隔离记录"}
    if kind == "state":
        base.update(
            character_id="person",
            event_id="event",
            category="knowledge",
            key="暗号",
            value="未知",
            audience="character",
        )
    elif kind == "relationship":
        base.update(from_id="person", to_id="other", relation="合作")
    elif kind == "promise":
        base["stages"] = [{"status": "planned", "event_id": "event"}]
    return RECORD.validate_python({**base, **values}).model_dump()


def proposal(value, **changes):
    return {"local_id": "subject", "target_id": None, "record": value, **changes}


@pytest.mark.parametrize("kind", ["character", "event", "state", "relationship", "promise"])
def test_field_inventory_matches_all_domain_references(kind):
    values = {"depends_on": ["dependency"]}
    if kind == "event":
        values.update(participants=["person"], after=["event"])
    if kind == "state":
        values["until_event_id"] = "later"
    if kind == "relationship":
        values["event_id"] = "event"
    r = record(kind, **values)
    assert {ref for _, ref, _ in fields(r)} == references(r)


@pytest.mark.parametrize(
    "kind,changes,field,expected",
    [
        ("event", {"participants": ["wrong"]}, "participants[0]", "character"),
        ("event", {"after": ["wrong"]}, "after[0]", "event"),
        ("state", {"character_id": "wrong"}, "character_id", "character"),
        ("state", {"event_id": "wrong"}, "event_id", "event"),
        ("state", {"until_event_id": "wrong"}, "until_event_id", "event"),
        ("relationship", {"from_id": "wrong"}, "from_id", "character"),
        ("relationship", {"to_id": "wrong"}, "to_id", "character"),
        ("relationship", {"event_id": "wrong"}, "event_id", "event"),
        ("promise", {"stages": [{"status": "planned", "event_id": "wrong"}]}, "stages[0].event_id", "event"),
    ],
)
def test_wrong_kind_points_to_exact_field_without_rebinding(kind, changes, field, expected):
    p = proposal(record(kind, **changes))
    index = {
        "person": {"kind": "character"},
        "other": {"kind": "character"},
        "event": {"kind": "event"},
        "wrong": {"kind": "state"},
    }
    before = deepcopy((p, index))
    issues = reference_issues(p, index, "records[3](local_id=subject).record")
    assert len(issues) == 1
    assert f'.record.{field} 值="wrong"' in issues[0]
    assert f"实际类型state，需要{expected}" in issues[0]
    assert (p, index) == before


def test_unresolved_name_is_not_mapped_by_matching_title_and_id_examples_are_bounded():
    p = proposal(record("event", participants=["同名管事"]))
    index = {
        f"id-{i}": {"kind": "character", "title": "同名管事", "profile": "private profile never needed"}
        for i in range(25)
    }
    before = deepcopy((p, index))
    issue = reference_issues(p, index, "records[0].record")[0]
    assert 'participants[0] 值="同名管事"' in issue
    assert '"id-0"' in issue and '"id-11"' in issue and '"id-12"' not in issue
    assert "共25项" in issue and "private profile" not in issue
    assert (p, index) == before


@pytest.mark.parametrize("own", ["subject", "formal-target"])
def test_both_local_and_update_target_self_dependencies_are_refused(own):
    p = proposal(record("event", depends_on=[own]), target_id="formal-target")
    issue = reference_issues(p, {own: {"kind": "event"}}, "records[0].record")[0]
    assert "depends_on[0]" in issue and "不能依赖自身" in issue


def test_optional_empty_links_keep_domain_meaning_and_diagnostics_escape_untrusted_ids():
    for kind in ("state", "relationship"):
        r = record(kind, **({"until_event_id": ""} if kind == "state" else {"event_id": ""}))
        assert {ref for _, ref, _ in fields(r)} == references(r)
    text = "坏标识\n<script>" + "😀" * 200
    issues = reference_issues(proposal(record("event", participants=[text])), {}, "records[0].record")
    assert len(issues) == 1 and "\n" not in issues[0] and "\\n" in issues[0] and "…" in issues[0]
    assert len(issues[0]) < 500


def test_reference_validation_uses_effective_update_fields_and_preserves_formal_records():
    before = {
        "person": {"id": "person", "kind": "character", "revision": 0, "payload": record("character")},
        "event": {
            "id": "event",
            "kind": "event",
            "revision": 2,
            "payload": record("event", participants=["person"]),
        },
    }
    update = proposal(
        record("event", participants=["unapplied-value"], notes="只补说明"),
        target_id="event",
        update_fields=["notes"],
        append_stages=False,
    )
    original = deepcopy((before, update))
    nodes = effective_records(before, [update])
    index = {i: n["record"] for i, n in nodes.items()}
    assert reference_issues({**update, "record": index["subject"]}, index, "records[0].record") == []
    update["update_fields"].append("participants")
    nodes = effective_records(before, [update])
    index = {i: n["record"] for i, n in nodes.items()}
    assert (
        "participants[0]"
        in reference_issues({**update, "record": index["subject"]}, index, "records[0].record")[0]
    )
    assert before == original[0]


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_new_runs_freeze_reference_contract_without_a_request_option(domain, story, kind):
    t = (
        source_task(domain, story)
        if kind == "module"
        else domain.queue(
            "workflow", {**workflow_payload(story), "structured_maintenance": True}, story["branch_id"]
        )
    )
    assert t["result"][KEY] == VERSION and KEY not in t["payload"]


class LinkModel(SceneModel):
    def __init__(self, value="s", fix=False):
        super().__init__()
        self.value, self.fix = value, fix

    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        event = next(r["record"] for r in value["records"] if r["local_id"] == "e")
        event["participants"] = ["n", "q"] if self.fix and len(self.calls) > 1 else [self.value]
        value["coverage"] += f"轮次{len(self.calls)}"
        return {**result, "text": dumps(value)}


@pytest.mark.parametrize("legacy", [False, True])
def test_real_worker_refuses_mistyped_links_only_under_new_contract(settings, domain, story, legacy):
    task = source_task(domain, story)
    if legacy:
        old = deepcopy(task["result"])
        old.pop(KEY)
        replace_result(domain, task, old)
        assert domain.queue("module", task["payload"])["result"] == old
    chapter = domain.chapter(story["chapter_id"])
    model = LinkModel()
    asyncio.run(Worker(settings, model).run_once())
    result = domain.task(task["id"])
    assert result["status"] == ("succeeded" if legacy else "blocked_runtime"), result["error"]
    assert ("关联核对契约版本" in model.calls[0]["任务"]) is not legacy
    if not legacy:
        assert ".participants[0]" in result["error"] and "需要character" in result["error"]
        assert len(model.calls) == 3 and not domain.db.all("SELECT * FROM artifacts")
    event = domain.db.one(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id LIMIT 1",
        (task["id"],),
    )
    assert json.loads(event["payload"])["request"].get(KEY) == (None if legacy else VERSION)
    assert domain.chapter(chapter["id"]) == chapter and not domain.db.all("SELECT * FROM story_records")


def test_field_diagnostic_reaches_next_repair_and_valid_result_remains_unadopted(settings, domain, story):
    task = source_task(domain, story)
    model = LinkModel(value="未确认的称谓", fix=True)
    asyncio.run(Worker(settings, model).run_once())
    result = domain.task(task["id"])
    assert result["status"] == "succeeded", result["error"]
    issues = model.calls[1]["任务"]["定位错误"]
    assert any("participants[0]" in issue and "未确认的称谓" in issue for issue in issues)
    assert len(model.calls) == 2
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert artifact["status"] == "candidate"
    assert not domain.db.all("SELECT * FROM story_records")


@pytest.mark.parametrize("marker", [None, False, "future/2"])
def test_unknown_reference_contract_stops_without_dispatch(settings, domain, story, marker):
    task = source_task(domain, story)
    replace_result(domain, task, {**task["result"], KEY: marker})
    model = LinkModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and model.calls == []
