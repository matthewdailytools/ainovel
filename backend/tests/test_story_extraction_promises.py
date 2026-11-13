"""Promise stages must pass grounding before durable structure maintenance stages them."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.story_extraction import promise_stage_issues
from ainovel.story_graph import RECORD, SaveRecord, save_record
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain
from test_extraction_scenes import SceneModel
from test_story_extraction import ExtractionModel, adoption, produce


class StageRepairModel(SceneModel):
    def __init__(self, status="planted", defect="missing_sources", always_invalid=False):
        super().__init__()
        self.status, self.defect, self.always_invalid = status, defect, always_invalid
        self.outputs = []

    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        instruction = self.calls[-1]["任务"]
        promise = next(item for item in value["records"] if item["local_id"] == "p")
        stage = promise["record"]["stages"][0]
        stage["status"] = self.status
        if self.always_invalid or not instruction.get("定位错误"):
            if self.defect == "missing_sources":
                stage["sources"] = []
            elif self.defect == "invalid_source":
                stage["sources"][0]["quote"] = "not-a-source-id"
            elif self.defect == "event_kind":
                stage["event_id"] = "n"  # Existing local character, not an event.
            elif self.defect == "event_unknown":
                stage["event_id"] = "missing-event"
        self.outputs.append(deepcopy(value))
        result["text"] = dumps(value)
        return result


def pending_workflow(settings, domain, story, model):
    chapter = domain.save(story["chapter_id"], 0, "😀宁禾看懂暗号，钱叔收起铜牌。")
    task = domain.queue(
        "workflow",
        {
            "novel_id": story["id"],
            "account": "openai_1",
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
    return worker, progress, task, deepcopy(domain.chapter(chapter["id"]))


@pytest.mark.parametrize(
    "status,defect,field",
    [
        ("planted", "missing_sources", "sources"),
        ("reinforced", "missing_sources", "sources"),
        ("resolved", "missing_sources", "sources"),
        ("planted", "invalid_source", "sources[0].quote"),
        ("planted", "event_kind", "event_id"),
        ("planted", "event_unknown", "event_id"),
    ],
)
def test_stage_feedback_repairs_then_maintains_atomically(settings, domain, story, status, defect, field):
    model = StageRepairModel(status, defect)
    worker, progress, task, chapter = pending_workflow(settings, domain, story, model)
    assert asyncio.run(maintain(worker, progress))
    assert len(model.calls) == 2
    assert model.calls[0]["任务"]["阶段证据契约版本"] == "promise-stage-grounding/2"
    feedback = model.calls[1]["任务"]["定位错误"]
    location = f"records[4](local_id=p).record.stages[0]({status}).{field}"
    assert any(location in issue for issue in feedback)
    if defect in {"missing_sources", "invalid_source"}:
        assert any("S1P0001" in issue and "source_id" in issue for issue in feedback)
    else:
        assert any("可用事件id示例：e" in issue for issue in feedback)
    assert model.outputs[0]["records"][4]["record"]["sources"]  # Main sources alone were insufficient.
    if defect == "missing_sources":
        assert model.outputs[0]["records"][4]["record"]["stages"][0]["sources"] == []
    saved = domain.task(task["id"])["result"]
    assert "pending_structure" not in saved
    assert len(saved["structure_history"]) == 1
    artifact = domain.artifact(saved["structure_history"][0]["artifact_id"])
    assert artifact["status"] == "accepted"
    promise = next(item for item in artifact["payload"]["records"] if item["record"]["kind"] == "promise")
    assert promise["record"]["stages"][0]["sources"][0]["quote"] == chapter["content"]
    assert len(domain.db.all("SELECT * FROM story_records")) == 6
    assert len(domain.db.all("SELECT * FROM scene_times")) == 1
    assert domain.chapter(chapter["id"]) == chapter


@pytest.mark.parametrize("status", ["planted", "reinforced", "resolved"])
def test_missing_stage_evidence_stops_finitely_before_staging(settings, domain, story, status):
    model = StageRepairModel(status, always_invalid=True)
    worker, progress, task, chapter = pending_workflow(settings, domain, story, model)
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(maintain(worker, progress))
    # Three bounded validation rounds; identical repair calls reuse the completed step.
    assert len(model.calls) == 2
    repairs = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair' ORDER BY id",
        (task["id"],),
    )
    assert [json.loads(row["payload"])["round"] for row in repairs] == [0, 1, 2]
    assert all("stages[0]" in json.loads(row["payload"])["issues"][0] for row in repairs)
    assert all(not output["records"][4]["record"]["stages"][0]["sources"] for output in model.outputs)
    assert domain.db.all("SELECT * FROM artifacts WHERE kind='story_extraction'") == []
    assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.db.all("SELECT * FROM scene_times") == []
    assert domain.chapter(chapter["id"]) == chapter
    assert "artifact_id" not in domain.task(task["id"])["result"]["pending_structure"]


@pytest.mark.parametrize(
    "status,note,valid",
    [
        ("planted", "", False),
        ("reinforced", "", False),
        ("resolved", "", False),
        ("retained", " \n", False),
        ("abandoned", "", False),
        ("retained", "作者决定在下一卷继续处理", True),
        ("abandoned", "作者决定取消此支线", True),
        ("planned", "", True),
    ],
)
def test_stage_early_checks_match_existing_domain_rules(domain, story, status, note, valid):
    chapter = domain.save(story["chapter_id"], 0, "铜牌来历未知。")
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"]),
        "quote": chapter["content"],
    }
    record = RECORD.validate_python(
        {
            "kind": "promise",
            "title": "铜牌来历",
            "sources": [source],
            "stages": [{"status": status, "note": note}],
        }
    ).model_dump()
    issues = promise_stage_issues(record, "records[0](local_id=p).record", {}, "合法source_id：S1P0001。")
    data = SaveRecord(base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=record)
    if valid:
        assert not issues
        assert (
            save_record(domain, story["branch_id"], data)["records"][0]["payload"]["stages"][0]["status"]
            == status
        )
    else:
        assert issues and f"stages[0]({status})" in issues[0]
        with pytest.raises(DomainError):
            save_record(domain, story["branch_id"], data)
        assert domain.db.all("SELECT * FROM story_records") == []


def test_multiple_stages_are_checked_individually_without_borrowing_main_sources():
    record = RECORD.validate_python(
        {
            "kind": "promise",
            "title": "铜牌",
            "sources": [{"chapter_id": "c", "revision": 1, "start": 0, "end": 1, "quote": "字"}],
            "stages": [{"status": "planned"}, {"status": "planted"}, {"status": "resolved"}],
        }
    ).model_dump()
    original = deepcopy(record)
    issues = promise_stage_issues(record, "records[1](local_id=p).record", {}, "合法source_id：S1P0001。")
    assert len(issues) == 2
    assert "stages[1](planted).sources" in issues[0] and "stages[2](resolved).sources" in issues[1]
    assert record == original


def test_same_source_can_be_explicitly_selected_for_multiple_supported_stages():
    source = {"chapter_id": "c", "revision": 1, "start": 0, "end": 1, "quote": "字"}
    record = RECORD.validate_python(
        {
            "kind": "promise",
            "title": "铜牌",
            "sources": [source],
            "stages": [{"status": status, "sources": [source]} for status in ("planted", "resolved")],
        }
    ).model_dump()
    assert not promise_stage_issues(record, "records[0](local_id=p).record", {}, "合法source_id：S1P0001。")


class NotesOnlyPromiseUpdate(ExtractionModel):
    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        target = next(item for item in self.calls[-1]["任务"]["已有记录索引"] if item["kind"] == "promise")
        item = value["records"][4]
        item.update(target_id=target["id"], target_revision=target["revision"], update_fields=["notes"])
        item["record"].update(notes="补充说明", stages=[{"status": "planted"}])
        value["records"] = [item]
        result["text"] = dumps(value)
        return result


def test_ignored_update_stages_retain_formal_evidence_without_false_repair(client, domain, settings, story):
    chapter = domain.save(story["chapter_id"], 0, "铜牌仍在桌上。")
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"]),
        "quote": chapter["content"],
    }
    before = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "promise",
                "title": "铜牌来历",
                "sources": [source],
                "stages": [{"status": "planted", "sources": [source]}],
            },
        ),
    )["records"][0]
    artifact, model = produce(client, domain, settings, story, NotesOnlyPromiseUpdate())
    assert len(model.calls) == 1
    proposal = artifact["payload"]["records"][0]
    assert proposal["record"]["stages"] == before["payload"]["stages"]
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt",
        json=adoption(domain, story, artifact),
    )
    assert response.status_code == 200, response.text
    assert domain.chapter(chapter["id"])["content"] == chapter["content"]
