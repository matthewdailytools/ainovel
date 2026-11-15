"""Book source scope does not turn a future trigger into a chapter obligation."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.conditional_review import GUIDANCE_VERSION, KEY, VERSION, condition_origins, eligible_conditions
from ainovel.db import dumps
from ainovel.worker import Worker
from test_workflow_scope import SegmentModel, workflow_payload


NUMERIC = "只在首次有效同刻对照发生时兑现指定数值，未校准的记录不能冒充有效观测。"
MOTHER = "若涉及母亲，其去向仍保持未知。"
TEXT = "沈砚收好借尺，零线尚未核对；没有校准，没有读数，只记下待核的起点。" * 9


def test_origins_are_frozen_source_categories_not_inferred_chapter_obligations():
    task = {
        "kind": "workflow",
        "payload": {"fixed": [NUMERIC, MOTHER, "本章固定", "作者禁止"]},
        "result": {KEY: VERSION},
    }
    before = deepcopy(task)
    instructions = {"本章": {"fixed": ["本章固定", "本章另项"]}}
    fixed = [NUMERIC, MOTHER, "本章固定", "本章另项", "作者禁止"]
    origins = condition_origins(task, fixed, instructions, ["作者禁止"])
    assert [row["scope"] for row in origins] == ["book", "book", "chapter", "chapter", "prohibition"]
    assert all("chapter_obligation" not in row for row in origins)
    assert eligible_conditions(task, fixed, instructions, ["作者禁止"]) == [MOTHER]
    assert task == before


class PreparationReviewModel(SegmentModel):
    def __init__(self, verdict):
        super().__init__()
        self.verdict = verdict
        self.reviews = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        action = instruction.get("动作")
        if action == "按章纲写完整正文":
            output = TEXT
        elif action == "审查正文":
            self.reviews.append(deepcopy(instruction))
            output = {
                "checks": [
                    {
                        "condition": NUMERIC,
                        "result": self.verdict,
                        "scope": "book",
                        "applicability": "not_triggered" if self.verdict == "pass" else "uncertain",
                        "chapter_obligation": "not_required" if self.verdict == "pass" else "uncertain",
                        "explanation": "本稿有未校准、没有读数的代表段，本章保持限制；整书数值未兑现。",
                        "evidence": instruction["可引用原文"][0]["source_id"]
                        if self.verdict == "pass"
                        else "",
                    }
                ],
                "findings": [],
                "summary": "隔离工程替身，只验证来源装配和门控，不代替真实语义评估。",
            }
        elif action == "从正文提取事实候选与章节摘要":
            output = {"facts": [], "summary": "本章尚未校准，没有记录有效数值。"}
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-origin-guidance",
        }


@pytest.mark.parametrize("verdict", ["pass", "unknown"])
def test_representative_preparation_can_support_adherence_but_unknown_is_not_rewritten(
    settings, domain, story, verdict
):
    payload = workflow_payload(story)
    payload["fixed"] = [NUMERIC]
    payload["plots"][0].update(plot="收好借尺，记录待核的起点；本章不校准不读数。", fixed=[])
    task = domain.queue("workflow", payload, story["branch_id"])
    model = PreparationReviewModel(verdict)
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    candidate = domain.artifact(completed["result"]["latest_artifact"])
    review = candidate["payload"]["review"]
    assert len(model.reviews) == 1
    instruction = model.reviews[0]
    assert instruction["固定条件来源"] == [{"condition": NUMERIC, "scope": "book"}]
    assert instruction["允许单列本章未触发的全书约定"] == []
    assert instruction["全书条件上下文提示版本"] == GUIDANCE_VERSION
    assert "本章情节明确要求完成A" in instruction["来源与本章义务"]
    assert review["checks"][0]["result"] == verdict
    assert review["conditional_guidance_version"] == GUIDANCE_VERSION
    assert review["condition_origins"] == instruction["固定条件来源"]
    assert review["book_checks_pending"] == [
        {"condition": NUMERIC, "chapter_result": verdict, "status": "whole_book_pending"}
    ]
    if verdict == "pass":
        assert completed["status"] == "segment_complete"
        assert candidate["status"] == "accepted"
    else:
        assert completed["status"] == "needs_review"
        assert candidate["status"] == "candidate"
        assert candidate["payload"]["memory"] == {}
        assert domain.chapter(candidate["chapter_id"])["revision"] == 0
