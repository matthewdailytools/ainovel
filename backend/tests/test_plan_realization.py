import asyncio
import json
from copy import deepcopy

import pytest

from ainovel.db import DomainError, dumps
from ainovel.story_graph import RECORD, SaveRecord, graph, query_context, save_record
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain, safe_automatic


@pytest.mark.parametrize("kind", ["event", "state", "relationship"])
def test_only_source_free_unchanged_plans_can_be_automatically_realized(kind):
    source = {"chapter_id": "current", "revision": 1, "start": 0, "end": 2, "quote": "兑现"}
    values = {
        "event": {"kind": "event", "title": "读懂暗号", "status": "planned"},
        "state": {
            "kind": "state",
            "title": "已知暗号",
            "character_id": "actor",
            "event_id": "event",
            "category": "knowledge",
            "key": "暗号",
            "value": "已知",
            "audience": "character",
            "truth": "planned",
        },
        "relationship": {
            "kind": "relationship",
            "title": "结识",
            "from_id": "a",
            "to_id": "b",
            "relation": "相识",
            "truth": "planned",
        },
    }
    before = RECORD.validate_python(values[kind]).model_dump()
    after = deepcopy(before)
    marker = "status" if kind == "event" else "truth"
    after[marker] = "actual"
    after["sources"] = [source]
    proposal = {"target_id": "target", "previous_record": {"record": before}, "record": after}
    assert safe_automatic(proposal)
    before["sources"] = [source]
    assert not safe_automatic(proposal)
    before["sources"] = []
    after["title"] = "同时改写断言"
    assert not safe_automatic(proposal)
    after["title"] = before["title"]
    after["sources"] = []
    assert not safe_automatic(proposal)
    before[marker] = "actual"
    before["sources"] = [source]
    after["sources"] = [source]
    after["title"] = "改写已经发生的断言"
    assert not safe_automatic(proposal)


def test_realized_plan_uses_current_evidence_without_granting_knowledge_to_prior_narration(
    settings, domain, story
):
    earlier = domain.save(story["chapter_id"], 0, "宁禾尚未读懂暗号。")
    later = domain.create_chapter(story["branch_id"], "读到线索")
    later = domain.save(later["id"], 0, "宁禾读懂了暗号。")

    def add(payload):
        old = {r["id"] for r in graph(domain, story["branch_id"])["records"]}
        rows = save_record(
            domain,
            story["branch_id"],
            SaveRecord(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=payload
            ),
        )["records"]
        return next(r for r in rows if r["id"] not in old)

    actor = add({"kind": "character", "title": "宁禾"})
    event = add(
        {
            "kind": "event",
            "title": "读懂暗号",
            "status": "planned",
            "story_order": 10,
            "participants": [actor["id"]],
        }
    )
    state = add(
        {
            "kind": "state",
            "title": "暗号认知",
            "character_id": actor["id"],
            "event_id": event["id"],
            "category": "knowledge",
            "key": "暗号",
            "value": "已知",
            "audience": "character",
            "truth": "planned",
        }
    )

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            value = json.loads(prompt.split("\n只返回")[0])
            instruction = value["任务"]
            assert instruction["动作"] == "按保存稿反向整理长篇结构候选"
            unit = instruction["可引用原文"][0]
            source = {k: unit[k] for k in ("chapter_id", "revision", "start", "end")}
            source["quote"] = unit["source_id"]
            index = {r["id"]: r for r in instruction["已有记录索引"]}
            assert index[state["id"]]["source_free_plan"]
            assert index[state["id"]]["planned_value"] == "已知"
            records = []
            for old, marker in [(event, "status"), (state, "truth")]:
                records.append(
                    {
                        "local_id": "realized-" + old["id"],
                        "target_id": old["id"],
                        "target_revision": old["revision"],
                        "update_fields": [marker],
                        "record": {**old["payload"], marker: "actual", "sources": [source]},
                        "reasoning": "本批原文明确兑现同一断言，不引用早先计划",
                    }
                )
            return {
                "text": dumps({"records": records, "unknowns": [], "coverage": "本章原文"}),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-realization",
            }

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
    worker = Worker(settings, Model())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = {
        "next_chapter": 3,
        "expected_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "pending_structure": {"chapter_id": later["id"], "revision": later["revision"]},
    }
    worker.checkpoint(result)
    assert asyncio.run(maintain(worker, result))
    rows = {r["id"]: r for r in graph(domain, story["branch_id"])["records"]}
    assert rows[event["id"]]["payload"]["status"] == "actual"
    assert rows[state["id"]]["payload"]["truth"] == "actual"
    assert rows[state["id"]]["revision"] == 1
    assert not rows[state["id"]]["invalid_reasons"]
    assert (
        query_context(
            domain, story["branch_id"], earlier["id"], 20, "character", text_position=len(earlier["content"])
        )["states"]
        == []
    )
    assert (
        query_context(domain, story["branch_id"], later["id"], 20, "character", text_position=0)["states"]
        == []
    )
    context = query_context(
        domain, story["branch_id"], later["id"], 20, "character", text_position=len(later["content"])
    )
    assert [r["id"] for r in context["states"]] == [state["id"]]
    assert all(r["id"] not in {event["id"], state["id"]} for r in context["plans"])
    assert domain.chapter(earlier["id"])["content"] == earlier["content"]
    assert domain.task(task["id"])["result"]["structure_history"][0]["policy"] == "worker_automatic"


def test_actual_relationship_cannot_reference_a_future_planned_event(domain, story):
    def add(payload):
        old = {r["id"] for r in graph(domain, story["branch_id"])["records"]}
        rows = save_record(
            domain,
            story["branch_id"],
            SaveRecord(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=payload
            ),
        )["records"]
        return next(r for r in rows if r["id"] not in old)

    a = add({"kind": "character", "title": "宁禾"})
    b = add({"kind": "character", "title": "钱叔"})
    event = add({"kind": "event", "title": "将来相识", "status": "planned"})
    chapter = domain.save(story["chapter_id"], 0, "他们谈到将来相识的可能。")
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"]),
        "quote": chapter["content"],
    }
    with pytest.raises(DomainError, match="事件须已经发生"):
        add(
            {
                "kind": "relationship",
                "title": "相识",
                "from_id": a["id"],
                "to_id": b["id"],
                "relation": "相识",
                "truth": "actual",
                "event_id": event["id"],
                "sources": [source],
            }
        )
