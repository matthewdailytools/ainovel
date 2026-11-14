"""A source match made only of whitespace cannot justify a review judgment."""

import asyncio
import json

import pytest

from ainovel.db import dumps
from ainovel.plot_review import review_passed
from ainovel.worker import REVIEW_CHECK_VERSION, Worker
from test_review_fixed_checks import FixedReviewModel


class EvidenceModel(FixedReviewModel):
    def __init__(self, evidence, *, finding=False, recover=False):
        super().__init__(invalid_rounds=0, judgment="unknown" if finding else "pass")
        self.evidence, self.finding, self.recover = evidence, finding, recover

    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        output = json.loads(response["text"])
        if self.finding:
            output["findings"] = [
                {
                    "severity": "blocker",
                    "chapter": 1,
                    "quote": self.evidence,
                    "reason": "审稿意见必须有实际原文依据。",
                    "repair": "核对原文后修订。",
                }
            ]
        elif self.recover and len(self.calls) > 1:
            output["checks"][1].update(result="unknown", evidence="")
            output["summary"] = "第2项为条件性约束，本章尚无足够可引用依据，保留未知，未判通过或违规。"
        else:
            output["checks"][1]["evidence"] = self.evidence
        return {**response, "text": dumps(output)}


def queue(client, domain, story):
    # Every whitespace-only false quote occurs in the source, so substring
    # membership alone cannot distinguish it from actual evidence.
    original = domain.save(story["chapter_id"], 0, "沈砚 留下标记。\n\t\u3000")
    fixed = ["留下标记。", "只有实际观测时才使用规定数值。"]
    response = client.post(
        "/api/modules",
        json={
            "action": "review.chapter",
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": original["revision"],
            "account": "deepseek",
            "instruction": "按本章原文逐项核对。",
            "fixed": fixed,
        },
    )
    assert response.status_code == 202, response.text
    return response.json(), original, fixed


@pytest.mark.parametrize("evidence", [" ", "\n", "\t", "\u3000"])
@pytest.mark.parametrize("finding", [False, True])
def test_whitespace_match_is_not_review_evidence(client, domain, settings, story, evidence, finding):
    task, original, _ = queue(client, domain, story)
    model = EvidenceModel(evidence, finding=finding)
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime" and len(model.calls) == 3
    assert "findings[0]" in stopped["error"] if finding else "checks[1].evidence" in stopped["error"]
    assert domain.chapter(story["chapter_id"]) == original
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.db.all("SELECT * FROM facts") == []


@pytest.mark.parametrize("evidence", ["", "\u3000"])
def test_model_can_return_unknown_after_empty_pass_without_code_upgrading_it(
    client, domain, settings, story, evidence
):
    task, original, fixed = queue(client, domain, story)
    model = EvidenceModel(evidence, recover=True)
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "succeeded" and len(model.calls) == 2
    report = domain.artifact(completed["result"]["artifact_id"])["payload"]
    assert [x["condition"] for x in report["checks"]] == fixed
    assert report["checks"][1]["result"] == "unknown" and not review_passed(report)
    assert model.calls[1]["前次产物"]["checks"][1]["result"] == "pass"
    assert "evidence" in " ".join(model.calls[1]["定位错误"])
    assert "unknown" in " ".join(model.calls[1]["定位错误"])
    assert "条件未触发" in " ".join(model.calls[1]["定位错误"])
    assert model.calls[0]["固定条件检查格式版本"] == REVIEW_CHECK_VERSION
    traces = [
        json.loads(row["payload"])
        for row in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id",
            (task["id"],),
        )
    ]
    assert len(traces) == 2 and all(
        t["request"]["review_check_version"] == REVIEW_CHECK_VERSION for t in traces
    )
    assert domain.chapter(story["chapter_id"]) == original
    assert domain.db.all("SELECT * FROM facts") == []


def test_nonempty_foreign_quote_does_not_become_valid_evidence(client, domain, settings, story):
    task, original, _ = queue(client, domain, story)
    model = EvidenceModel("旧稿中才有的测量结果。")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime" and len(model.calls) == 3
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.chapter(story["chapter_id"]) == original
