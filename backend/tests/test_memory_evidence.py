"""Single-source memory errors remain precise, finite and non-destructive."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.evidence import source_units
from ainovel.memory_evidence import MEMORY_EVIDENCE_VERSION, memory_evidence_issues
from ainovel.models import ModuleInput
from ainovel.worker import Worker

TEXT = "甲记下到岸时刻。😀𠮷\n\n乙收回旧钥匙。\n\n两人约定明日再核对。"


class MemoryModel:
    def __init__(self, evidence="MEM0001,MEM0002", *, repair=True, over_limit=False):
        self.evidence, self.repair, self.over_limit = evidence, repair, over_limit
        self.requests, self.outputs = [], []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.requests.append(deepcopy(frame))
        instruction = frame["任务"]
        while isinstance(instruction.get("原始任务"), dict):
            instruction = instruction["原始任务"]
        value = self.evidence if len(self.requests) == 1 or not self.repair else "MEM0002"
        facts = [
            {
                "subject": "甲",
                "kind": "event",
                "statement": "记下时刻",
                "truth": "fact",
                "evidence": "MEM0001",
            },
            {
                "subject": "乙",
                "kind": "resource",
                "statement": "收回旧钥匙",
                "truth": "fact",
                "evidence": value,
            },
        ]
        if self.over_limit:
            facts = [deepcopy(facts[i % 2]) for i in range(7)]
        result = {"facts": facts, "summary": "记录时刻并取回钥匙，后续核对仍是计划。"}
        self.outputs.append(deepcopy(result))
        return {"text": dumps(result), "usage": {"input": 1, "output": 1}, "call_id": "offline-memory"}


def prepared(settings, domain, story, model):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    request = ModuleInput(
        action="memory.extract",
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=chapter["id"],
        base_revision=chapter["revision"],
        instruction="只从本章提取有依据的事实与摘要",
        account="deepseek",
    ).model_dump()
    task = domain.queue("module", request)
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    return worker, worker.context(story["branch_id"], chapter["id"]), chapter, task


def test_multiple_ids_receive_precise_feedback_then_model_selects_supported_single_source(
    settings, domain, story
):
    model = MemoryModel()
    worker, context, chapter, task = prepared(settings, domain, story, model)
    result = asyncio.run(worker.memory(TEXT, context))
    assert len(model.requests) == 2
    instruction = model.requests[0]["任务"]
    assert instruction["事实证据契约版本"] == MEMORY_EVIDENCE_VERSION
    assert "不能盲选第一编号" in instruction["证据字段规则"]
    feedback = model.requests[1]["任务"]["定位错误"]
    assert len(feedback) == 1 and "facts[1].evidence" in feedback[0]
    assert "多个编号" in feedback[0] and "MEM0001" in feedback[0] and "MEM0003" in feedback[0]
    previous = model.requests[1]["任务"]["前次产物"]["facts"]
    assert len(previous) == 2 and previous[1]["evidence"] == "MEM0001,MEM0002"
    assert (
        result["facts"][1]["evidence"] == "乙收回旧钥匙。"
    )  # The model chose the second ID, not a fallback first.
    assert result["facts"][1]["statement"] == model.outputs[0]["facts"][1]["statement"]
    assert asyncio.run(worker.memory(TEXT, context)) == result
    assert len(model.requests) == 2  # Both completed calls are reused, with no repeated model request.
    asyncio.run(worker.module())
    assert len(model.requests) == 2
    stored = domain.task(task["id"])
    assert stored["status"] == "succeeded"
    artifact = domain.artifact(stored["result"]["artifact_id"])
    assert artifact["kind"] == "memory" and artifact["status"] == "candidate"
    assert len(artifact["payload"]["facts"]) == 2
    dispatches = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
    )
    assert all(
        json.loads(row["payload"])["request"]["memory_evidence_version"] == MEMORY_EVIDENCE_VERSION
        for row in dispatches
    )
    assert domain.chapter(chapter["id"]) == chapter and domain.db.all("SELECT * FROM facts") == []


@pytest.mark.parametrize("value", ["MEM0001,MEM0002", "MEM0001, MEM0002", "MEM0001—MEM0003"])
def test_persistent_multiple_ids_stop_after_two_repairs_without_deleting_facts(
    settings, domain, story, value
):
    model = MemoryModel(value, repair=False)
    worker, _, chapter, task = prepared(settings, domain, story, model)
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(worker.module())
    assert len(model.requests) == 2  # The identical final repair reuses its saved call.
    repairs = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair' ORDER BY id",
        (task["id"],),
    )
    assert [json.loads(row["payload"])["round"] for row in repairs] == [0, 1, 2]
    assert all(
        len(output["facts"]) == 2 and output["facts"][1]["evidence"] == value for output in model.outputs
    )
    assert len(model.requests[1]["任务"]["前次产物"]["facts"]) == 2
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.db.all("SELECT * FROM facts") == [] and domain.db.all("SELECT * FROM artifacts") == []


@pytest.mark.parametrize("value", ["MEM9999", "MEM0000", "乙已经退还了钥匙。", "前一章独有的结论"])
def test_invalid_id_paraphrase_or_prior_context_is_not_accepted(settings, domain, story, value):
    model = MemoryModel(value, repair=False)
    worker, context, chapter, task = prepared(settings, domain, story, model)
    context["recent"] = [{"id": "old", "revision": 1, "content": "前一章独有的结论"}]
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(worker.memory(TEXT, context))
    assert len(model.requests) == 2
    feedback = model.requests[1]["任务"]["定位错误"]
    assert len(feedback) == 1 and "facts[1].evidence" in feedback[0]
    assert "MEM0001" in feedback[0] and "MEM0003" in feedback[0]
    assert value not in feedback[0]  # Diagnostics describe the rule, never echo untrusted quotations.
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.db.all("SELECT * FROM artifacts") == []


def test_contiguous_verbatim_fallback_remains_compatible(settings, domain, story):
    model = MemoryModel("乙收回旧钥匙。", repair=False)
    worker, context, chapter, _ = prepared(settings, domain, story, model)
    result = asyncio.run(worker.memory(TEXT, context))
    assert len(model.requests) == 1 and result["facts"][1]["evidence"] == "乙收回旧钥匙。"
    assert domain.chapter(chapter["id"]) == chapter


def test_fact_limit_is_not_relaxed_to_repair_multiple_sources(settings, domain, story):
    model = MemoryModel(over_limit=True)
    worker, context, chapter, _ = prepared(settings, domain, story, model)
    with pytest.raises(DomainError, match="结构化产物未通过契约"):
        asyncio.run(worker.memory(TEXT, context))
    assert len(model.requests) == 3 and all(len(output["facts"]) == 7 for output in model.outputs)
    assert domain.chapter(chapter["id"]) == chapter and domain.db.all("SELECT * FROM facts") == []


def test_diagnostics_do_not_copy_original_text_or_mutate_invalid_evidence():
    private = "原稿假样本不应进入diagnostic"
    text = private + "。\n\n她收回钥匙。"
    sources = {u["source_id"]: u for u in source_units(text, "MEM")}
    result = {"facts": [{"evidence": "MEM0001,MEM0002"}, {"evidence": private + "被改写"}]}
    before = deepcopy(result)
    issues = memory_evidence_issues(result, text, sources)
    assert result == before and len(issues) == 2
    assert private not in dumps(issues)


def test_literal_text_containing_id_list_is_not_silently_interpreted_as_references():
    text = "纸上原样写着MEM0001,MEM0002两个编号。"
    sources = {u["source_id"]: u for u in source_units(text, "MEM")}
    result = {"facts": [{"evidence": "MEM0001,MEM0002"}]}
    assert memory_evidence_issues(result, text, sources) == []
    assert result["facts"][0]["evidence"] == "MEM0001,MEM0002"
