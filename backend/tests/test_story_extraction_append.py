"""Explicit promise append preserves history without asking the model to reconstruct it."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.story_graph import SaveRecord, graph, save_record
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain, safe_automatic
from test_story_extraction import adoption, produce


class AppendModel:
    def __init__(self, target, status="reinforced", defect=None, old_source=None):
        self.target, self.status, self.defect, self.old_source = target, status, defect, old_source
        self.calls = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.calls.append(frame)
        instruction = frame["任务"]
        index = next(item for item in instruction["已有记录索引"] if item["id"] == self.target)
        unit = instruction["可引用原文"][0]
        source = {k: unit[k] for k in ("chapter_id", "revision", "start", "end")}
        source["quote"] = unit["source_id"]
        proposal = {
            "local_id": "next_stage",
            "record": {
                "kind": "promise",
                "title": "铜牌来历",
                "sources": [source],
                "stages": [{"status": self.status, "sources": [deepcopy(source)], "note": "本批有新证据"}],
            },
            "reasoning": "只提案本批新增阶段，不知道未发送的旧历史",
            "target_id": index["id"],
            "target_revision": index["revision"],
            "update_fields": ["stages"],
            "append_stages": True,
        }
        if self.defect == "new_target":
            proposal.update(target_id=None, target_revision=None, update_fields=[])
        elif self.defect == "missing_field":
            proposal["update_fields"] = ["notes"]
        elif self.defect == "old_stage_source":
            proposal["record"]["stages"][0]["sources"] = [deepcopy(self.old_source)]
        elif self.defect == "replace":
            proposal.pop("append_stages")  # Existing callers retain full-field replacement.
        elif self.defect == "replace_sources":
            proposal["update_fields"].append("sources")
        result = {"records": [proposal], "unknowns": [], "coverage": "仅本批保存稿"}
        return {"text": dumps(result), "usage": {"input": 1, "output": 1}, "call_id": "offline-append"}


def prepare(domain, story, *, count=1, fault=None):
    original = domain.save(story["chapter_id"], 0, "历史独有：铜牌第一次出现，来历未明。")
    source = {
        "chapter_id": original["id"],
        "revision": original["revision"],
        "start": 0,
        "end": len(original["content"]),
        "quote": original["content"],
    }
    promise = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "promise",
                "title": "铜牌来历",
                "sources": [source],
                "stages": [
                    {"status": "planted", "sources": [source], "note": f"原阶段{i}"} for i in range(count)
                ],
            },
        ),
    )["records"][0]
    if fault == "quote":
        payload = deepcopy(promise["payload"])
        payload["stages"][0]["sources"][0]["quote"] = "伪造的旧引文"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE story_records SET payload=? WHERE id=?", (dumps(payload), promise["id"]))
    elif fault == "revision":
        domain.save(original["id"], original["revision"], "作者后来改了第一章。")
    current = domain.create_chapter(story["branch_id"], "当前章")
    current = domain.save(current["id"], 0, "铜牌的另一半印记出现在账页上。")
    return original, current, promise, source


def workflow(settings, domain, story, chapter, model):
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
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    progress = {
        "next_chapter": chapter["position"] + 1,
        "expected_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "pending_structure": {"chapter_id": chapter["id"], "revision": chapter["revision"]},
    }
    worker.task["result"] = progress
    worker.checkpoint(progress)
    return worker, progress, task


def test_real_worker_appends_reinforcement_and_resolution_with_same_identity(settings, domain, story):
    original, chapter, promise, source = prepare(domain, story)
    model = AppendModel(promise["id"])
    worker, progress, task = workflow(settings, domain, story, chapter, model)
    assert asyncio.run(maintain(worker, progress))
    first = next(r for r in graph(domain, story["branch_id"])["records"] if r["id"] == promise["id"])
    assert first["revision"] == 1 and first["payload"]["stages"][:1] == promise["payload"]["stages"]
    assert first["payload"]["sources"][0] == source
    assert first["payload"]["stages"][1]["sources"][0]["chapter_id"] == chapter["id"]

    third = domain.create_chapter(story["branch_id"], "后续章")
    third = domain.save(third["id"], 0, "印记与失踪账本相合，铜牌来历得到解释。")
    progress.update(
        next_chapter=4,
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
        pending_structure={"chapter_id": third["id"], "revision": third["revision"]},
    )
    worker.checkpoint(progress)
    model.status = "resolved"
    assert asyncio.run(maintain(worker, progress))
    rows = graph(domain, story["branch_id"])["records"]
    assert len(rows) == 1 and rows[0]["id"] == promise["id"] and rows[0]["revision"] == 2
    assert rows[0]["payload"]["stages"][:2] == first["payload"]["stages"]
    assert [s["status"] for s in rows[0]["payload"]["stages"]] == ["planted", "reinforced", "resolved"]
    assert rows[0]["payload"]["stages"][0]["sources"] == [source]
    history = domain.db.all(
        "SELECT revision,payload FROM story_record_history WHERE record_id=? ORDER BY revision",
        (promise["id"],),
    )
    assert [h["revision"] for h in history] == [0, 1, 2]
    assert json.loads(history[0]["payload"]) == promise["payload"]
    assert domain.chapter(original["id"]) == original and domain.chapter(chapter["id"]) == chapter
    assert len(domain.task(task["id"])["result"]["structure_history"]) == 2
    assert len(model.calls) == 2
    for i, frame in enumerate(model.calls):
        index = next(r for r in frame["任务"]["已有记录索引"] if r["id"] == promise["id"])
        assert index["promise_stage_count"] == i + 1
        assert index["promise_current_status"] == ("planted" if i == 0 else "reinforced")
        assert "stages" not in index and "sources" not in index
        assert original["content"] not in dumps(frame)


@pytest.mark.parametrize(
    "defect",
    [
        "new_target",
        "wrong_kind",
        "missing_field",
        "old_stage_source",
        "limit",
        "retained",
        "abandoned",
        "planned",
    ],
)
def test_invalid_append_stops_without_staging_or_discarding_history(settings, domain, story, defect):
    original, chapter, promise, source = prepare(domain, story, count=100 if defect == "limit" else 1)
    target = promise["id"]
    if defect == "wrong_kind":
        target = save_record(
            domain,
            story["branch_id"],
            SaveRecord(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                record={"kind": "character", "title": "旁观者"},
            ),
        )["records"][-1]["id"]
    model = AppendModel(
        target,
        status=defect if defect in {"retained", "abandoned", "planned"} else "reinforced",
        defect=defect,
        old_source=source,
    )
    before = deepcopy(graph(domain, story["branch_id"]))
    worker, progress, task = workflow(settings, domain, story, chapter, model)
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(maintain(worker, progress))
    assert 1 <= len(model.calls) <= 3
    assert domain.db.all("SELECT * FROM artifacts WHERE kind='story_extraction'") == []
    assert graph(domain, story["branch_id"]) == before
    assert domain.chapter(original["id"]) == original and domain.chapter(chapter["id"]) == chapter
    feedback = dumps(model.calls[-1]["任务"]["定位错误"])
    assert ("101" in feedback and "100" in feedback) if defect == "limit" else feedback
    if defect == "old_stage_source":
        assert "stages[0]" in feedback and "S1P0001" in feedback


@pytest.mark.parametrize("fault", ["quote", "revision"])
def test_appended_old_sources_still_receive_strict_domain_validation(settings, domain, story, fault):
    _, chapter, promise, _ = prepare(domain, story, fault=fault)
    before = deepcopy(graph(domain, story["branch_id"]))
    histories = domain.db.all("SELECT * FROM story_record_history")
    model = AppendModel(promise["id"])
    worker, progress, task = workflow(settings, domain, story, chapter, model)
    if fault == "revision":
        with pytest.raises(DomainError) as error:
            asyncio.run(maintain(worker, progress))
        assert error.value.code == "revision_conflict"
    else:
        assert not asyncio.run(maintain(worker, progress))
    saved = domain.task(task["id"])
    if fault == "quote":
        assert saved["status"] == "needs_review" and "来源" in saved["error"]
    assert graph(domain, story["branch_id"]) == before
    assert domain.db.all("SELECT * FROM story_record_history") == histories
    identifier = (
        saved["result"].get("waiting_structure") or saved["result"]["pending_structure"]["artifact_id"]
    )
    artifact = domain.artifact(identifier)
    assert artifact["status"] == "candidate"
    assert (
        artifact["payload"]["records"][0]["record"]["stages"][0]
        == before["records"][0]["payload"]["stages"][0]
    )
    assert domain.chapter(chapter["id"]) == chapter


def test_default_replacement_and_explicit_sources_field_keep_existing_semantics(
    client, settings, domain, story
):
    _, chapter, promise, source = prepare(domain, story)
    current_story = {**story, "chapter_id": chapter["id"]}
    artifact, _ = produce(
        client, domain, settings, current_story, AppendModel(promise["id"], "resolved", "replace")
    )
    item = artifact["payload"]["records"][0]
    assert item["append_stages"] is False
    assert len(item["record"]["stages"]) == 1 and item["record"]["stages"][0]["status"] == "resolved"
    assert item["record"]["sources"][0] == source and len(item["record"]["sources"]) == 2
    assert not safe_automatic(item)
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt",
        json=adoption(domain, current_story, artifact),
    )
    assert response.status_code == 200, response.text

    appended, _ = produce(
        client, domain, settings, current_story, AppendModel(promise["id"], "reinforced", "replace_sources")
    )
    item = appended["payload"]["records"][0]
    assert item["append_stages"] is True and len(item["record"]["stages"]) == 2
    assert len(item["record"]["sources"]) == 1
    assert item["record"]["sources"][0]["chapter_id"] == chapter["id"]
    assert not safe_automatic(item)  # Explicit evidence replacement never silently drops old main evidence.
