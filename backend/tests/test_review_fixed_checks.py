"""Fixed-check repair diagnoses shape errors without changing review judgments."""

import asyncio
import json

import pytest

from ainovel.db import dumps
from ainovel.plot_review import review_passed
from ainovel.worker import Worker


class FixedReviewModel:
    def __init__(self, invalid_rounds=3, defect="extra", judgment="unknown"):
        self.calls = []
        self.invalid_rounds = invalid_rounds
        self.defect = defect
        self.judgment = judgment

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        self.calls.append(instruction)
        fixed = instruction["固定条件"]
        source = instruction["可引用原文"][0]["source_id"]
        checks = [{"condition": condition, "result": "pass", "evidence": source} for condition in fixed]
        if len(checks) > 1:
            checks[1].update(result=self.judgment, evidence=source if self.judgment == "fail" else "")
        if len(self.calls) <= self.invalid_rounds:
            if self.defect == "extra":
                checks.append({"condition": "其他情节覆盖", "result": "pass", "evidence": source})
            elif self.defect == "missing":
                checks.pop()
            elif self.defect == "reordered":
                checks.reverse()
            elif self.defect == "duplicate":
                checks[-1]["condition"] = fixed[0]
            elif self.defect == "paraphrased":
                checks[-1]["condition"] += "模型补充"
        return {
            "text": dumps(
                {
                    "checks": checks,
                    "findings": [],
                    "summary": f"第{len(self.calls)}轮：条件是否触发仍待核对。",
                }
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-fixed-check-review",
        }


def queue_review(client, domain, story, *, action="review.chapter", fixed=None):
    original = domain.save(story["chapter_id"], 0, "沈砚只留下复测标记，尚未校准或测量。😀𠮷")
    if fixed is None:
        fixed = ["沈砚留下标记。", "只有完成有效同刻观测时才使用指定测量数值。"] + [
            f"固定约定{i}。" for i in range(9)
        ]
    response = client.post(
        "/api/modules",
        json={
            "action": action,
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": original["revision"],
            "account": "deepseek",
            "instruction": "逐条核对本章，不能提前发生未来观测。",
            "fixed": fixed,
        },
    )
    assert response.status_code == 202, response.text
    return response.json(), original, fixed


def test_extra_fixed_check_stops_with_concrete_diagnostics_after_two_repairs(client, domain, settings, story):
    task, original, fixed = queue_review(client, domain, story)
    model = FixedReviewModel()
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime"
    assert len(model.calls) == 3
    assert "checks须严格11项，实际12项" in stopped["error"]
    assert "checks[11]" in stopped["error"] and "findings" in stopped["error"]
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.db.all("SELECT * FROM facts") == []
    assert domain.chapter(story["chapter_id"]) == original
    assert all(call["固定条件"] == fixed for call in model.calls)


@pytest.mark.parametrize("defect", ["missing", "reordered", "duplicate", "paraphrased"])
def test_other_fixed_list_errors_are_not_silently_reordered_or_dropped(
    client, domain, settings, story, defect
):
    task, original, _ = queue_review(client, domain, story)
    model = FixedReviewModel(defect=defect)
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime" and len(model.calls) == 3
    assert "checks须严格11项" in stopped["error"]
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.chapter(story["chapter_id"]) == original


@pytest.mark.parametrize("judgment", ["unknown", "fail"])
def test_repaired_shape_preserves_unknown_or_fail_and_original_conditions(
    client, domain, settings, story, judgment
):
    task, original, fixed = queue_review(client, domain, story)
    model = FixedReviewModel(invalid_rounds=1, judgment=judgment)
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "succeeded" and len(model.calls) == 2
    report = domain.artifact(completed["result"]["artifact_id"])["payload"]
    assert [check["condition"] for check in report["checks"]] == fixed
    assert report["checks"][1]["result"] == judgment and not review_passed(report)
    assert model.calls[1]["前次产物"]["checks"][1]["result"] == judgment
    assert model.calls[0]["固定条件项数"] == 11
    assert "不能" in model.calls[0]["条件性约束核对"]
    assert domain.chapter(story["chapter_id"]) == original
    assert domain.db.all("SELECT * FROM facts") == []


def test_pass_without_source_evidence_still_stops(client, domain, settings, story):
    task, original, _ = queue_review(client, domain, story)
    model = FixedReviewModel(invalid_rounds=0, judgment="pass")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime" and len(model.calls) == 3
    assert "checks[1].evidence" in stopped["error"]
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.chapter(story["chapter_id"]) == original


def test_no_fixed_conditions_does_not_allow_invented_checks(client, domain, settings, story):
    task, original, _ = queue_review(client, domain, story, fixed=[])
    model = FixedReviewModel()
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime" and len(model.calls) == 3
    assert "checks须严格0项，实际1项" in stopped["error"]
    assert "checks[0]" in stopped["error"]
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.chapter(story["chapter_id"]) == original


def test_review_instruction_update_reuses_completed_prose(client, domain, settings, story, monkeypatch):
    class WritingModel(FixedReviewModel):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.writer_calls = 0

        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            if instruction.get("action") == "writing.expand_plot":
                self.writer_calls += 1
                return {
                    "text": "沈砚只留下复测标记，尚未校准或测量。😀𠮷",
                    "usage": {"input": 1, "output": 1},
                    "call_id": "offline-paid-prose",
                }
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    task, original, fixed = queue_review(client, domain, story, action="writing.expand_plot")
    first = WritingModel()
    asyncio.run(Worker(settings, first).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert first.writer_calls == 1 and len(first.calls) == 3
    monkeypatch.setattr("ainovel.worker.REVIEW_CHECK_VERSION", "fixed-check-shape/test-update")
    resumed = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True})
    assert resumed.status_code == 200, resumed.text
    second = WritingModel(invalid_rounds=0)
    asyncio.run(Worker(settings, second).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "succeeded"
    assert second.writer_calls == 0 and len(second.calls) == 1
    candidate = domain.artifact(completed["result"]["artifact_id"])["payload"]
    assert candidate["text"] == original["content"]
    assert candidate["review"]["checks"][1]["result"] == "unknown"
    assert candidate["fixed"] == fixed and candidate["review_passed"] is False
    assert domain.chapter(story["chapter_id"]) == original
