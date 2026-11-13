import asyncio
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.extraction_scenes import coverage, scene_issues
from ainovel.scene_time import SaveScene, SceneTime, save_scene, scenes
from ainovel.story_extraction import AdoptExtraction, adopt
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain
from ainovel.story_graph import SaveRecord, graph, save_record
from test_story_extraction import ExtractionModel


class SceneModel(ExtractionModel):
    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        instruction = self.calls[-1]["任务"]
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        units = instruction["可引用原文"]
        value["scenes"] = [
            dict(
                title=f"场景{i + 1}",
                source_ids=[u["source_id"]],
                story_order=10 if i == 0 else None,
                story_time="读到暗号时" if i == 0 else "未知",
                audience="character",
                character_id="n",
                note="只核对本段",
            )
            for i, u in enumerate(units)
        ]
        result["text"] = dumps(value)
        return result


@pytest.fixture
def prepared(settings, domain, story, client):
    text = "\n😀宁禾读到暗号。\n\n她收起铜牌。\n"
    chapter = domain.save(story["chapter_id"], 0, text)

    def generate():
        response = client.post(
            f"/api/branches/{story['branch_id']}/story-extractions",
            json={
                "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
                "chapter_ids": [chapter["id"]],
                "account": "openai_1",
                "client_request_id": "scene-extraction-"
                + str(domain.branch(story["branch_id"])["story_revision"]),
                "include_scene_ranges": True,
            },
        )
        assert response.status_code == 202, response.text
        task = response.json()
        asyncio.run(Worker(settings, SceneModel()).run_once())
        finished = domain.task(task["id"])
        assert finished["status"] == "succeeded", finished["error"]
        return domain.artifact(finished["result"]["artifact_id"])

    return chapter, generate


def data_for(domain, story, artifact, **changes):
    return AdoptExtraction(
        base_story_revision=domain.branch(story["branch_id"])["story_revision"],
        candidate_revision=artifact["candidate_revision"],
        selected_ids=[r["id"] for r in artifact["payload"]["records"]],
        selected_scene_ids=[s["id"] for s in artifact["payload"]["scenes"]],
        idempotency_key="accept-scenes-" + artifact["id"],
        **changes,
    )


def test_frozen_units_cover_utf16_newlines_and_save_scenes_with_records(prepared, domain, story):
    chapter, generate = prepared
    artifact = generate()
    proposed = artifact["payload"]["scenes"]
    assert proposed[0]["scene"]["start"] == 0
    assert proposed[0]["scene"]["end"] == proposed[1]["scene"]["start"]
    assert proposed[-1]["scene"]["end"] == len(chapter["content"].encode("utf-16-le")) // 2
    data = data_for(domain, story, artifact)
    receipt = adopt(domain, story["branch_id"], artifact["id"], data)
    saved = scenes(domain, story["branch_id"], chapter["id"])
    assert len(saved["scenes"]) == 2 and all(not r["stale"] for r in saved["scenes"])
    assert len(receipt["scene_revisions"]) == 2
    assert adopt(domain, story["branch_id"], artifact["id"], data) == receipt
    with domain.db.connect() as tx:
        result = coverage(tx, story["branch_id"])
        assert result == {"complete": True, "scenes": 2, "unknown_story_times": 1, "issues": []}
        assert tx.execute("SELECT count(*) FROM scene_time_history").fetchone()[0] == 2
    assert domain.chapter(chapter["id"])["content"] == chapter["content"]


def test_existing_author_binding_needs_explicit_replacement_and_history_is_preserved(prepared, domain, story):
    chapter, generate = prepared
    original = save_scene(
        domain,
        story["branch_id"],
        chapter["id"],
        SaveScene(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            scene=SceneTime(
                title="作者原绑定",
                source_revision=chapter["revision"],
                start=0,
                end=len(chapter["content"].encode("utf-16-le")) // 2,
                audience="author",
            ),
        ),
    )
    old = original["scenes"][0]
    artifact = generate()
    data = data_for(domain, story, artifact)
    with pytest.raises(DomainError, match="明确确认归档"):
        adopt(domain, story["branch_id"], artifact["id"], data)
    assert domain.db.one("SELECT count(*) AS n FROM story_records")["n"] == 0
    assert scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]["archived"] == 0
    receipt = adopt(
        domain,
        story["branch_id"],
        artifact["id"],
        data.model_copy(update={"confirm_scene_replacement": True}),
    )
    assert receipt["archived_scene_ids"] == [old["id"]]
    history = domain.db.all(
        "SELECT * FROM scene_time_history WHERE scene_id=? ORDER BY revision", (old["id"],)
    )
    assert [h["archived"] for h in history] == [0, 1]
    assert json.loads(history[0]["payload"]) == old["payload"]


def test_scene_failure_after_first_insert_rolls_back_all_records_and_ranges(
    prepared, domain, story, monkeypatch
):
    _, generate = prepared
    artifact = generate()
    original = SceneTime.model_validate
    calls = []

    def fail_second(value, **kwargs):
        calls.append(value)
        if len(calls) == 2:
            raise DomainError("injected_scene_failure", "模拟第二场景写入中断", 409)
        return original(value, **kwargs)

    monkeypatch.setattr(SceneTime, "model_validate", staticmethod(fail_second))
    before = domain.branch(story["branch_id"])["story_revision"]
    with pytest.raises(DomainError, match="模拟第二场景"):
        adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    assert domain.branch(story["branch_id"])["story_revision"] == before
    for table in ["story_records", "story_record_history", "scene_times", "scene_time_history", "receipts"]:
        assert domain.db.one("SELECT count(*) AS n FROM " + table)["n"] == 0
    assert domain.artifact(artifact["id"])["status"] == "candidate"


@pytest.mark.parametrize("error", ["missing", "duplicate", "wrong_actor", "unknown_unit", "invented_order"])
def test_scene_partition_and_identity_errors_are_rejected(error):
    units = [
        {"source_id": f"p{i}", "chapter_id": "c", "revision": 1, "start": i * 3, "end": i * 3 + 2}
        for i in range(2)
    ]
    scene = scene_proposal()
    if error == "missing":
        scene["source_ids"] = ["p0"]
    elif error == "duplicate":
        scene["source_ids"] = ["p0", "p0", "p1"]
    elif error == "wrong_actor":
        scene.update(audience="character", character_id="other-branch")
    elif error == "unknown_unit":
        scene["source_ids"] = ["p0", "absent"]
    else:
        scene["story_order"] = 99
    assert scene_issues([scene], units, {}, [])


def scene_proposal():
    return {
        "title": "段落",
        "source_ids": ["p0", "p1"],
        "story_order": None,
        "story_time": "未知",
        "audience": "author",
        "character_id": None,
        "note": "",
    }


class SceneOnlyModel:
    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        units = instruction["可引用原文"]
        return {
            "text": dumps(
                {
                    "records": [],
                    "unknowns": [],
                    "coverage": "本章",
                    "scenes": [{**scene_proposal(), "source_ids": [u["source_id"] for u in units]}],
                }
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-scene",
        }


@pytest.mark.parametrize("author_changed", [False, True])
def test_workflow_replaces_only_its_unchanged_prior_bindings(
    prepared, settings, domain, story, author_changed
):
    chapter, _ = prepared
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
    worker = Worker(settings, SceneOnlyModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    progress = {
        "next_chapter": 2,
        "expected_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "pending_structure": {"chapter_id": chapter["id"], "revision": chapter["revision"]},
    }
    worker.task["result"] = progress
    worker.checkpoint(progress)
    assert asyncio.run(maintain(worker, progress))
    old = scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]
    if author_changed:
        save_scene(
            domain,
            story["branch_id"],
            chapter["id"],
            SaveScene(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                base_revision=old["revision"],
                scene=SceneTime(**{**old["payload"], "note": "作者亲自调整"}),
            ),
            old["id"],
        )
    revised = domain.save(chapter["id"], chapter["revision"], chapter["content"] + "正文新修订。")
    progress.update(
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
        pending_structure={"chapter_id": chapter["id"], "revision": revised["revision"]},
    )
    worker.checkpoint(progress)
    assert asyncio.run(maintain(worker, progress)) is (not author_changed)
    latest = domain.task(task["id"])
    old_saved = next(
        r for r in scenes(domain, story["branch_id"], chapter["id"])["scenes"] if r["id"] == old["id"]
    )
    if author_changed:
        assert latest["status"] == "needs_review"
        assert old_saved["archived"] == 0 and old_saved["payload"]["note"] == "作者亲自调整"
    else:
        assert old_saved["archived"] == 1
        assert len(latest["result"]["structure_history"]) == 2
        assert all(
            not s["stale"]
            for s in scenes(domain, story["branch_id"], chapter["id"])["scenes"]
            if not s["archived"]
        )


def test_bound_time_survives_added_evidence_but_not_a_correction_and_revert(prepared, domain, story):
    chapter, generate = prepared
    artifact = generate()
    adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    binding = next(
        r
        for r in scenes(domain, story["branch_id"], chapter["id"])["scenes"]
        if r["payload"]["story_order"] == 10
    )
    event = next(r for r in graph(domain, story["branch_id"])["records"] if r["kind"] == "event")
    extra = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 12,
        "end": 18,
        "quote": "她收起铜牌。",
    }

    def update(row, payload):
        result = save_record(
            domain,
            story["branch_id"],
            SaveRecord(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                base_revision=row["revision"],
                record=payload,
            ),
            row["id"],
        )
        return next(r for r in result["records"] if r["id"] == row["id"])

    event = update(event, {**event["payload"], "sources": [*event["payload"]["sources"], extra]})
    assert not next(
        r for r in scenes(domain, story["branch_id"], chapter["id"])["scenes"] if r["id"] == binding["id"]
    )["stale"]
    old_payload = dict(event["payload"])
    event = update(event, {**event["payload"], "story_order": 11})
    event = update(event, old_payload)
    assert next(
        r for r in scenes(domain, story["branch_id"], chapter["id"])["scenes"] if r["id"] == binding["id"]
    )["stale"]
    with pytest.raises(DomainError, match="事件依据已改变"):
        save_scene(
            domain,
            story["branch_id"],
            chapter["id"],
            SaveScene(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                base_revision=binding["revision"],
                scene=SceneTime(**{**binding["payload"], "event_revision": event["revision"] + 1}),
            ),
            binding["id"],
        )


@pytest.mark.parametrize("timing", ["before_checkpoint", "after_checkpoint"])
def test_author_adoption_racing_workflow_publication_does_not_lose_progress(
    prepared, settings, domain, story, monkeypatch, timing
):
    chapter, _ = prepared
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
    worker = Worker(settings, SceneOnlyModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    progress = worker.task["result"]
    progress.update(
        next_chapter=2,
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
        pending_structure={"chapter_id": chapter["id"], "revision": chapter["revision"]},
    )
    original = worker.checkpoint
    raced = []

    def checkpoint(value, *args, **kwargs):
        identifier = value.get("pending_structure", {}).get("artifact_id")
        if identifier and not raced:
            raced.append(identifier)
            candidate = domain.artifact(identifier)
            data = data_for(domain, story, candidate)
            if timing == "before_checkpoint":
                with pytest.raises(DomainError, match="检查点尚未发布"):
                    adopt(domain, story["branch_id"], identifier, data)
                return original(value, *args, **kwargs)
            original(value, *args, **kwargs)
            adopt(domain, story["branch_id"], identifier, data)
            return
        return original(value, *args, **kwargs)

    original(progress)
    monkeypatch.setattr(worker, "checkpoint", checkpoint)
    assert asyncio.run(maintain(worker, progress))
    saved = domain.task(task["id"])
    assert saved["status"] == "running" and "pending_structure" not in saved["result"]
    assert len(saved["result"]["structure_history"]) == 1
    assert domain.db.one("SELECT count(*) AS n FROM scene_times")["n"] == 1
