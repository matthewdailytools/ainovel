"""Exercise real Worker dispatch and final artifact transactions with offline models."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import dumps
from ainovel.models import ModuleInput
from ainovel.worker import Worker


TEXT = "沈砚看见船门敞开。\n\n他独自离开。\n\n船门随后关上。"


@pytest.fixture
def queued_revision(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    source = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": chapter["revision"],
        },
        "review",
        {
            "checks": [],
            "findings": [
                {
                    "severity": "blocker",
                    "chapter": 1,
                    "quote": "他独自离开。",
                    "reason": "离开前的关门动作缺失。",
                    "repair": "补关门动作，不增加新事件。",
                }
            ],
            "summary": "仅用于工程竞态验证。",
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    request = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=chapter["id"],
        base_revision=chapter["revision"],
        action="revision.targeted",
        instruction="只补关门动作，保留原稿。",
        account="openai_1",
        source_review_id=source["id"],
        finding_indices=[0],
    ).model_dump()
    return domain.queue("module", request), source, chapter


class OfflineRevisionModel:
    def __init__(self, domain, source, reject_on_invalid_patch=False):
        self.domain, self.source = domain, source
        self.reject_on_invalid_patch = reject_on_invalid_patch
        self.calls = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        self.calls.append(deepcopy(instruction))
        action = instruction.get("动作")
        if action == "用局部替换修订正文":
            if self.reject_on_invalid_patch and len(self.calls) == 1:
                with self.domain.db.connect(True) as tx:
                    tx.execute("UPDATE artifacts SET status='rejected' WHERE id=?", (self.source["id"],))
                output = {
                    "edits": [{"original": "冻结原文不存在这句。", "replacement": "无效引用触发修复。"}],
                    "reason": "离线夹具制造一次引用修复。",
                }
            else:
                unit = instruction["可替换原文"][0]
                output = {
                    "edits": [
                        {
                            "original": unit["source_id"],
                            "replacement": unit["text"].replace("他独自离开。", "他把门掩好，独自离开。"),
                        }
                    ],
                    "reason": "补足本次选中的动作缺口。",
                }
        elif action == "审查正文":
            output = {
                "checks": [
                    {
                        "condition": fixed,
                        "result": "pass",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                    }
                    for fixed in instruction["固定条件"]
                ],
                "findings": [],
                "summary": "离线夹具，不是文学质量结论。",
            }
        elif action == "复检选中的原审稿问题":
            output = {
                "results": [
                    {
                        "finding_index": finding["index"],
                        "result": "pass",
                        "reason": "离线夹具确认候选包含本次动作补足。",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                    }
                    for finding in instruction["原问题"]
                ]
            }
        else:
            raise AssertionError(f"Unexpected offline action: {action}")
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": "offline-only"}


def test_real_worker_keeps_a_source_bound_unaccepted_revision_candidate(settings, domain, queued_revision):
    task, source, chapter = queued_revision
    model = OfflineRevisionModel(domain, source)
    asyncio.run(Worker(settings, model).run_once())
    result = domain.task(task["id"])
    assert result["status"] == "succeeded", result["error"]
    candidate = domain.artifact(result["result"]["artifact_id"])
    assert len(model.calls) == 3
    assert candidate["kind"] == "chapter_draft" and candidate["status"] == "candidate"
    assert candidate["basis"]["targeted_revision"] == task["result"]["targeted_revision_snapshot"]
    assert candidate["payload"]["revision_trace"]["selected_problems_resolved"] is True
    assert domain.chapter(chapter["id"]) == chapter


def test_rejected_source_stops_internal_citation_repair_before_a_second_dispatch(
    settings, domain, queued_revision
):
    task, source, chapter = queued_revision
    model = OfflineRevisionModel(domain, source, reject_on_invalid_patch=True)
    asyncio.run(Worker(settings, model).run_once())
    result = domain.task(task["id"])
    assert len(model.calls) == 1
    assert result["status"] == "blocked_runtime"
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert domain.db.all("SELECT result FROM task_steps WHERE task_id=?", (task["id"],))
    assert domain.chapter(chapter["id"]) == chapter


@pytest.mark.parametrize("change", ["reject", "payload"])
def test_source_change_after_the_last_call_cannot_stage_a_current_candidate(
    settings, domain, queued_revision, monkeypatch, change
):
    task, source, chapter = queued_revision
    model = OfflineRevisionModel(domain, source)
    worker = Worker(settings, model)
    stage = worker.domain.stage

    def change_before_staging(*args, **kwargs):
        with domain.db.connect(True) as tx:
            if change == "reject":
                tx.execute("UPDATE artifacts SET status='rejected' WHERE id=?", (source["id"],))
            else:
                updated = deepcopy(source["payload"])
                updated["findings"][0]["reason"] = "来源报告在最后一次检查后改变。"
                tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(updated), source["id"]))
        return stage(*args, **kwargs)

    monkeypatch.setattr(worker.domain, "stage", change_before_staging)
    asyncio.run(worker.run_once())
    result = domain.task(task["id"])
    assert len(model.calls) == 3
    assert result["status"] == "blocked_runtime"
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert len(domain.db.all("SELECT result FROM task_steps WHERE task_id=?", (task["id"],))) == 3
    assert domain.chapter(chapter["id"]) == chapter
