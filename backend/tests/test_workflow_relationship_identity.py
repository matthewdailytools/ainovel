import asyncio
from copy import deepcopy

import pytest

from ainovel.story_extraction import AdoptExtraction, adopt
from ainovel.story_graph import RECORD, SaveRecord, graph, save_record
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain, safe_automatic


@pytest.mark.parametrize("updating", [False, True])
def test_same_character_endpoints_require_author_review_even_for_metadata_extension(updating):
    before = RECORD.validate_python(
        {"kind": "relationship", "title": "自我认知", "from_id": "a", "to_id": "a", "relation": "自省"}
    ).model_dump()
    after = deepcopy(before)
    after["sources"] = [{"chapter_id": "c", "revision": 1, "start": 0, "end": 1, "quote": "问"}]
    item = {"record": after}
    if updating:
        item.update(target_id="relation", previous_record={"record": before})
    assert not safe_automatic(item)
    after["to_id"] = "b"
    if not updating:
        assert safe_automatic(item)


@pytest.mark.parametrize("title", ["宁禾与江舟", "宁禾的自问"])
def test_automatic_maintenance_holds_self_relation_without_changing_manuscript_or_identities(
    settings, domain, story, monkeypatch, title
):
    chapter = domain.save(
        story["chapter_id"], 0, "宁禾问起江舟，江舟说他还不知道。\n宁禾又问自己，该不该相信那句话。"
    )
    created = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={"kind": "character", "title": "宁禾"},
        ),
    )
    character = next(r for r in created["records"] if r["kind"] == "character")
    revision = domain.branch(story["branch_id"])["story_revision"]
    task = domain.queue(
        "workflow",
        {"novel_id": story["id"], "account": "openai_1", "adoption": "automatic"},
        story["branch_id"],
    )
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = {
        "next_chapter": 2,
        "expected_story_revision": revision,
        "pending_structure": {"chapter_id": chapter["id"], "revision": chapter["revision"]},
    }
    worker.checkpoint(result)
    record = RECORD.validate_python(
        {
            "kind": "relationship",
            "title": title,
            "from_id": character["id"],
            "to_id": character["id"],
            "relation": "询问",
            "truth": "actual",
            "sources": [
                {
                    "chapter_id": chapter["id"],
                    "revision": 1,
                    "start": 0,
                    "end": len(chapter["content"].encode("utf-16-le")) // 2,
                    "quote": chapter["content"],
                }
            ],
        }
    ).model_dump()
    item = {"id": "same-endpoint-proposal", "local_id": "r", "record": record, "reasoning": "原文有询问"}
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "story_extraction",
        {"records": [item], "unknowns": [], "coverage": "本章"},
        {
            "story_revision": revision,
            "maintenance_chapter": {"id": chapter["id"], "revision": 1},
            "maintenance_epoch": "initial",
        },
        task["id"],
        worker.attempt,
    )
    original_graph = graph(domain, story["branch_id"])

    async def unexpected_call(*args, **kwargs):
        pytest.fail("恢复已保存结构候选不应新增模型调用")

    monkeypatch.setattr(worker, "call", unexpected_call)
    assert not asyncio.run(maintain(worker, result))
    held = domain.task(task["id"])
    assert held["status"] == "needs_review"
    assert "关系两端相同" in held["error"]
    assert held["result"]["waiting_structure"] == artifact["id"]
    assert domain.artifact(artifact["id"])["status"] == "candidate"
    assert domain.chapter(chapter["id"]) == chapter
    assert graph(domain, story["branch_id"]) == original_graph

    if title == "宁禾与江舟":
        return
    # Intentional self-relations remain an explicit author decision, not a ban.
    answer = adopt(
        domain,
        story["branch_id"],
        artifact["id"],
        AdoptExtraction(
            base_story_revision=revision,
            candidate_revision=0,
            selected_ids=[item["id"]],
            idempotency_key="author-explicit-self-relation",
        ),
    )
    assert answer["record_ids"] == [item["id"]]
    assert domain.artifact(artifact["id"])["status"] == "accepted"
    assert domain.chapter(chapter["id"]) == chapter
