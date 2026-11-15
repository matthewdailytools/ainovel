"""The independent trigger observation constrains real workflow/recheck gates."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from ainovel.book_applicability import ACTION, KEY, VERSION
from ainovel.db import dumps
from ainovel.worker import Worker
from test_workflow_scope import SegmentModel, workflow_payload
from test_candidate_recheck import stopped_candidate as stopped_candidate, queue_recheck


CONDITION = "若涉及母亲，其去向须保持未知；后卷再核对完整去向。"


class ObservedReviewModel(SegmentModel):
    def __init__(self, result="not_triggered", trigger="not_triggered"):
        super().__init__()
        self.result, self.trigger = result, trigger
        self.actions, self.frames = [], []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("动作")
        self.actions.append(action)
        self.frames.append(deepcopy(frame))
        if action == ACTION:
            output = {
                "checks": [
                    {
                        "condition_id": row["condition_id"],
                        "trigger": self.trigger,
                        "chapter_obligation": "not_required",
                        "trigger_clause": "若涉及母亲",
                        "evidence": "",
                        "explanation": "只观察本章，工程样例没有母亲信息；未知仍须保持。",
                    }
                    for row in instruction["全书条件原话"]
                ]
            }
        elif action == "审查正文":
            quote = instruction["可引用原文"][0]["source_id"]
            output = {
                "checks": [
                    {
                        "condition": row["condition_id"],
                        "result": self.result if row["condition"] == CONDITION else "pass",
                        "scope": row["scope"],
                        "applicability": "not_triggered" if self.result == "not_triggered" else "triggered",
                        "chapter_obligation": "not_required",
                        "evidence": ""
                        if row["condition"] == CONDITION and self.result in {"not_triggered", "unknown"}
                        else quote,
                        "explanation": f"隔离主审稿响应{self.actions.count('审查正文')}，不按理由改判。",
                    }
                    for row in instruction["固定条件编号表"]
                ],
                "findings": [],
                "summary": f"隔离审稿{self.actions.count('审查正文')}，用于验证门控而非文学质量。",
            }
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        return {
            "text": dumps(output),
            "usage": {"input": 1, "output": 1},
            "call_id": f"offline-observed-{len(self.actions)}",
        }


def queue(domain, story):
    payload = workflow_payload(story)
    payload["fixed"] = [CONDITION]
    return domain.queue("workflow", payload, story["branch_id"])


def test_default_policy_observes_before_review_and_keeps_absence_distinct_from_fulfillment(
    settings, domain, story
):
    task = queue(domain, story)
    assert task["result"][KEY] == VERSION
    model = ObservedReviewModel()
    asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "segment_complete", done["error"]
    candidate = domain.artifact(done["result"]["latest_artifact"])
    review = candidate["payload"]["review"]
    assert model.actions.index(ACTION) < model.actions.index("审查正文")
    assert review["checks"][0]["condition"] == CONDITION
    assert review["checks"][0]["result"] == "not_triggered"
    assert (
        review["book_applicability"]["content_sha256"]
        == hashlib.sha256(candidate["payload"]["text"].encode()).hexdigest()
    )
    assert review["book_applicability"]["checks"][0]["trigger"] == "not_triggered"
    assert "pass" not in review["condition_id_map"][0]["allowed_results"]
    assert review["book_checks_pending"][0]["status"] == "whole_book_pending"
    observed = next(frame for frame in model.frames if frame["任务"].get("动作") == ACTION)
    assert set(observed["上下文"]) == {"scope"}
    assert "作者写作要求" not in observed["任务"]


@pytest.mark.parametrize("trigger", ["not_triggered", "uncertain"])
def test_independent_absence_or_uncertainty_cannot_be_labelled_pass_using_an_unrelated_quote(
    settings, domain, story, trigger
):
    task = queue(domain, story)
    original = domain.chapter(story["chapter_id"])
    model = ObservedReviewModel(result="pass", trigger=trigger)
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    assert model.actions.count(ACTION) == 1
    assert model.actions.count("审查正文") == 3
    assert "允许范围" in stopped["error"]
    assert "从正文提取事实候选与章节摘要" not in model.actions
    assert domain.chapter(story["chapter_id"]) == original
    assert not domain.db.all("SELECT id FROM artifacts WHERE kind='chapter_draft'")
    assert not domain.db.all("SELECT * FROM receipts")


def test_uncertain_observation_and_unknown_review_stop_without_revising_the_story(settings, domain, story):
    task = queue(domain, story)
    model = ObservedReviewModel(result="unknown", trigger="uncertain")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "needs_review", stopped["error"]
    candidate = domain.artifact(stopped["result"]["waiting_artifact"])
    assert candidate["payload"]["review"]["checks"][0]["result"] == "unknown"
    assert candidate["payload"]["memory"] == {}
    assert model.actions.count(ACTION) == model.actions.count("审查正文") == 1
    assert "用局部替换修订正文" not in model.actions
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0


def test_old_frozen_conditional_task_does_not_gain_an_extra_observer_call(settings, domain, story):
    task = queue(domain, story)
    result = deepcopy(task["result"])
    result.pop(KEY)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    model = ObservedReviewModel(result="unknown")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "needs_review", stopped["error"]
    assert ACTION not in model.actions
    report = domain.artifact(stopped["result"]["waiting_artifact"])["payload"]["review"]
    assert "book_applicability" not in report


@pytest.mark.parametrize("key", [KEY, "conditional_book_policy_version"])
def test_unknown_frozen_policy_stops_before_writing_or_any_model_call(settings, domain, story, key):
    task = queue(domain, story)
    result = {**task["result"], key: "unsupported-future-version"}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    model = ObservedReviewModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.actions == []
    assert domain.chapter(story["chapter_id"])["revision"] == 0


def test_recheck_freezes_the_new_observer_and_rejects_source_version_mismatch_before_sending(
    client, domain, settings, stopped_candidate
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202
    task = response.json()
    assert task["result"][KEY] == task["result"]["candidate_recheck_snapshot"][KEY] == VERSION
    broken = {**task["result"], KEY: "unsupported-future-version"}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(broken), task["id"]))
    model = ObservedReviewModel()
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime"
    assert model.actions == []
    assert domain.artifact(candidate["id"]) == candidate
    assert domain.task(parent["id"])["result"] == parent["result"]
