import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.models import Memory
from ainovel.worker import STRUCTURE_REPAIR_VERSION, Worker, structure_issues


TEXT = "她猜老周知道红线的来历，尚无证据。😀𠮷"


class RepairModel:
    def __init__(self, truth, remain_invalid=False):
        self.truth, self.remain_invalid = truth, remain_invalid
        self.requests = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        request = json.loads(prompt.split("\n只返回")[0])
        self.requests.append(deepcopy(request))
        fact = {
            "subject": "她",
            "kind": self.truth if len(self.requests) < 3 or self.remain_invalid else "knowledge",
            "statement": "她猜老周知道来历，尚无证据。",
            "truth": self.truth,
            "evidence": "MEM0001",
        }
        value = {"facts": [fact], "summary": "她保持未证实的判断。"}
        text = dumps(value)
        if len(self.requests) == 1:
            # Reproduce the observed extra root brace, then a distinct enum error.
            text = text.replace(',"summary"', '},"summary"')
        return {"text": text, "usage": {"input": 1, "output": 1}, "call_id": "offline-only"}


def setup_worker(settings, domain, story, model):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": 1,
            "account": "deepseek",
            "action": "memory.extract",
        },
    )
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    return worker, worker.context(story["branch_id"], chapter["id"])


@pytest.mark.parametrize("truth", ["belief", "uncertain", "plan"])
def test_two_format_repairs_explain_enum_without_turning_belief_into_fact(settings, domain, story, truth):
    model = RepairModel(truth)
    worker, context = setup_worker(settings, domain, story, model)
    result = asyncio.run(worker.memory(TEXT, context))
    assert result["facts"][0]["kind"] == "knowledge"
    assert result["facts"][0]["truth"] == truth
    assert result["facts"][0]["evidence"] == TEXT
    assert len(model.requests) == 3
    first = model.requests[1]["任务"]
    assert first["修复契约版本"] == STRUCTURE_REPAIR_VERSION
    assert first["错误字段"] == [{"type": "invalid_json"}]
    assert "不是kind" in first["事实字段说明"]
    issue = model.requests[2]["任务"]["错误字段"][0]
    assert issue["field"] == ["facts", 0, "kind"]
    assert issue["actual"] == truth and "knowledge" in issue["allowed"]
    assert asyncio.run(worker.memory(TEXT, context)) == result
    assert len(model.requests) == 3  # Completed original and repair steps are reused.
    assert domain.chapter(story["chapter_id"])["content"] == TEXT
    assert not domain.db.all("SELECT * FROM facts")
    assert not domain.db.all("SELECT * FROM artifacts")


def test_repeated_wrong_kind_still_stops_after_two_repairs_without_adoption(settings, domain, story):
    model = RepairModel("belief", remain_invalid=True)
    worker, context = setup_worker(settings, domain, story, model)
    with pytest.raises(DomainError, match="两轮格式修正"):
        asyncio.run(worker.memory(TEXT, context))
    assert len(model.requests) == 3
    assert domain.chapter(story["chapter_id"])["revision"] == 1
    assert not domain.db.all("SELECT * FROM facts")
    assert not domain.db.all("SELECT * FROM artifacts")


def test_enum_diagnostic_is_bounded_and_does_not_copy_other_invalid_payloads():
    with pytest.raises(ValueError) as caught:
        Memory.model_validate(
            {"facts": [{"kind": "belief" * 100}], "summary": {"private": "whole model object"}}
        )
    issues = structure_issues(caught.value)
    enum = next(item for item in issues if item["type"] == "literal_error")
    assert len(enum["actual"]) == 200 and "knowledge" in enum["allowed"]
    assert "whole model object" not in dumps(issues)
