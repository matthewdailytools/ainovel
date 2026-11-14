import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from ainovel.ideation import IdeaAdoptInput, adopt_idea
from ainovel.ideation_structure import CompleteIdea, render_idea
from ainovel.models import ModuleInput
from ainovel.worker import Worker
from helpers.ideation_fixture import complete_idea_output


def test_complete_story_has_causal_spine_payoffs_and_an_ending():
    idea = CompleteIdea.model_validate(complete_idea_output())
    text = render_idea(idea)
    for section in (
        "人物目标与动机",
        "背景与关键关系",
        "核心矛盾与风险",
        "开端",
        "发展",
        "转折",
        "高潮",
        "结局与人物变化",
        "主要伏笔与承诺回收",
        "持续推进或收束",
    ):
        assert section in text
    assert "承接节点3" in text
    assert "节点1铺设，节点4回收" in text
    assert len(text) <= 6000


@pytest.mark.parametrize(
    "failure",
    [
        "missing_ending",
        "missing_development",
        "blank_motivation",
        "placeholder",
        "broken_chain",
        "future_cause",
        "duplicate_cause",
        "missing_payoff",
        "future_payoff",
        "backward_payoff",
        "wrong_turn",
        "oversized",
    ],
)
def test_incomplete_structures_do_not_pass(failure):
    value = complete_idea_output()
    if failure == "missing_ending":
        value["beats"][-1]["stage"] = "发展"
    elif failure == "missing_development":
        value["beats"][1]["stage"] = "转折"
    elif failure == "blank_motivation":
        value["protagonist"] = "  "
    elif failure == "placeholder":
        value["ending"] = "待定"
    elif failure == "broken_chain":
        value["beats"][3]["caused_by"] = [1]
    elif failure == "future_cause":
        value["beats"][3]["caused_by"] = [3, 5]
    elif failure == "duplicate_cause":
        value["beats"][3]["caused_by"] = [3, 3]
    elif failure == "missing_payoff":
        value["payoffs"] = []
    elif failure == "future_payoff":
        value["payoffs"][0]["resolved_at"] = 9
    elif failure == "backward_payoff":
        value["payoffs"][0].update(planted_at=4, resolved_at=1)
    elif failure == "wrong_turn":
        value["beats"][2]["stage"], value["beats"][3]["stage"] = "高潮", "转折"
    elif failure == "oversized":
        for beat in value["beats"]:
            for key in ("event", "cause", "choice", "result"):
                beat[key] = ("具体事件与因果" * 50)[:300]
    with pytest.raises(ValidationError):
        CompleteIdea.model_validate(value)


class StructureModel:
    def __init__(self, invalid=0):
        self.invalid = invalid
        self.requests = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        self.requests.append(json.loads(prompt.split("\n只返回")[0]))
        value = complete_idea_output()
        if len(self.requests) <= self.invalid:
            value["beats"][3]["caused_by"] = []
        return {
            "text": json.dumps(value, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-structure",
        }


def queue_structure(domain, story):
    request = ModuleInput(
        action="ideation.structure",
        novel_id=story["id"],
        branch_id=story["branch_id"],
        instruction="构思结构完整的修补旧物故事，无CP、不战斗。",
        account="openai_1",
    ).model_dump()
    return domain.queue("module", request)


@pytest.mark.parametrize("invalid", [0, 1])
def test_worker_publishes_only_valid_full_structure_and_keeps_source(settings, domain, story, invalid):
    model = StructureModel(invalid)
    task = queue_structure(domain, story)
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "succeeded"
    assert len(model.requests) == invalid + 1
    original = model.requests[0]["任务"]
    assert original["action"] == "ideation.structure"
    assert "作者想法" in original
    artifact = domain.artifact(completed["result"]["artifact_id"])
    assert artifact["kind"] == "advice"
    assert artifact["chapter_id"] is None
    assert artifact["payload"]["idea_structure_version"] == "1.0.0"
    assert artifact["payload"]["idea_structure_revision"] == 0
    structure = CompleteIdea.model_validate(artifact["payload"]["idea_structure"])
    assert artifact["payload"]["text"] == render_idea(structure)
    assert not domain.author_contract(story["branch_id"])["configured"]
    assert domain.chapter(story["chapter_id"])["content"] == ""
    before = deepcopy(artifact["payload"])
    adopt_idea(
        domain,
        story["branch_id"],
        IdeaAdoptInput(
            text=artifact["payload"]["text"],
            author_idea="修补旧物",
            source_candidate_id=artifact["id"],
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            idempotency_key="adopt-complete-structure",
        ),
    )
    assert domain.author_contract(story["branch_id"])["intent_contract"]["direction"] == render_idea(
        structure
    )
    assert domain.artifact(artifact["id"])["payload"] == before


def test_repair_limit_stops_incomplete_result_without_adopting(settings, domain, story):
    task = queue_structure(domain, story)
    model = StructureModel(invalid=10)
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "blocked_runtime"
    assert len(model.requests) == 2
    assert domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],)) == []
    assert not domain.author_contract(story["branch_id"])["configured"]
