"""Chapter adherence never fulfills future book promises or skips current results."""

import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from ainovel.conditional_review import ConditionalReviewCheck
from ainovel.db import dumps
from ainovel.models import Completion, Review
from ainovel.evidence import source_schema, source_units
from ainovel.providers import strict_schema
from ainovel.review_condition_ids import condition_table, id_contract
from ainovel.worker import Worker
from test_workflow_scope import SegmentModel, workflow_payload


FINALE = "【原创桥灯约定😀𠮷】第36章公开桥灯记录及责任归属，" + "保留救援工具的来历和修桥代价；" * 6
POSITIVE = "本章必须量完三根桩，并写下三次实际刻线读数。"
BODY = "林舟把灯罩靠在桥栏上，先登记裂痕，再把标记写入本子；本次仍在调查，没有宣称责任已经查清。" * 6


def test_new_wire_chapter_result_maps_to_canonical_result_without_reclassifying_it():
    value = ConditionalReviewCheck.model_validate(
        {"condition": "FIX001", "chapter_result": "unknown", "evidence": ""}
    )
    assert value.model_dump()["result"] == "unknown"
    assert "chapter_result" not in value.model_dump()


@pytest.mark.parametrize("result", ["pass", "fail", "unknown", "not_triggered"])
def test_legacy_result_alias_is_accepted_without_semantic_conversion(result):
    value = ConditionalReviewCheck.model_validate({"condition": "FIX001", "result": result, "evidence": ""})
    assert value.model_dump()["result"] == result


@pytest.mark.parametrize("result", ["pass", "unknown", "fail"])
def test_equal_dual_aliases_remain_the_same_canonical_judgment(result):
    original = {"condition": "FIX001", "chapter_result": result, "result": result, "evidence": ""}
    before = deepcopy(original)
    value = ConditionalReviewCheck.model_validate(original)
    assert value.model_dump()["result"] == result
    assert "chapter_result" not in value.model_dump()
    assert original == before


@pytest.mark.parametrize(
    "chapter_result,result", [("pass", "fail"), ("unknown", "pass"), ("not_triggered", "pass")]
)
def test_conflicting_dual_aliases_are_rejected_instead_of_selecting_a_convenient_result(
    chapter_result, result
):
    with pytest.raises(ValidationError, match="矛盾判断"):
        ConditionalReviewCheck.model_validate(
            {"condition": "FIX001", "chapter_result": chapter_result, "result": result, "evidence": ""}
        )


def test_only_the_conditional_wire_uses_chapter_result_and_preserves_fix_and_source_enums():
    from ainovel.conditional_review import ConditionalReview

    schema = ConditionalReview.model_json_schema()
    check = schema["$defs"]["ConditionalReviewCheck"]
    assert "chapter_result" in check["properties"] and "result" not in check["properties"]
    table = condition_table([FINALE], [{"condition": FINALE, "scope": "book"}], [])
    contract = id_contract(source_schema(schema, source_units(BODY, "C001P")), table)
    wire = strict_schema(contract)
    branch = wire["properties"]["checks"]["items"]["anyOf"][0]["properties"]
    assert branch["chapter_result"]["enum"] == ["pass", "fail", "unknown"]
    assert branch["condition"]["enum"] == ["FIX001"]
    assert branch["evidence"]["enum"] == ["C001P0001", ""]
    for legacy in (Review, Completion):
        fields = legacy.model_json_schema()["$defs"]["ReviewCheck"]["properties"]
        assert "result" in fields and "chapter_result" not in fields


class AdherenceModel(SegmentModel):
    def __init__(self, *, chapter_missing=False):
        super().__init__()
        self.chapter_missing = chapter_missing
        self.reviews, self.book_reports, self.actions = [], [], []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        action = instruction.get("动作")
        self.actions.append(action)
        if action == "按章纲写完整正文":
            output = BODY
        elif action == "审查正文":
            self.reviews.append(deepcopy(instruction))
            first = instruction["可引用原文"][0]["source_id"]
            output = {
                "checks": [
                    {
                        "condition": row["condition_id"],
                        "chapter_result": "unknown"
                        if row["scope"] == "chapter" and self.chapter_missing
                        else "pass",
                        "evidence": "" if row["scope"] == "chapter" and self.chapter_missing else first,
                        "scope": row["scope"],
                        "applicability": "unconditional",
                        "chapter_obligation": "required" if row["scope"] == "chapter" else "not_required",
                        "explanation": "本章仍在调查，未提前宣布第36章结局；全书履行仍待核对。"
                        if row["scope"] == "book"
                        else "本章虽未违背方向，但指定的三次实测未完成，不能仅凭没有冲突通过。",
                    }
                    for row in instruction["固定条件编号表"]
                ],
                "findings": [],
                "summary": "仅核对本章，不认可尚未完成的全书履约。",
            }
        elif action == "从正文提取事实候选与章节摘要":
            output = {"facts": [], "summary": "林舟仍在调查桥灯记录，终局没有发生。"}
        elif action == "整书完结检查":
            self.book_reports.append(deepcopy(instruction))
            output = {
                "closed": False,
                "promises": [
                    {"condition": condition, "result": "unknown", "evidence": ""}
                    for condition in instruction["必须逐项核对的作者硬约定"]
                ],
                "findings": [],
                "summary": "没有全书终局兑现依据，章内遵守不证明整书履约。",
            }
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": f"offline-chapter-adherence-{self.calls}",
        }


def queue(domain, story, *, scope="segment", chapter_missing=False):
    payload = workflow_payload(story, scope=scope)
    payload["fixed"] = [FINALE]
    payload["plots"][0]["fixed"] = [POSITIVE] if chapter_missing else []
    return domain.queue("workflow", payload, story["branch_id"])


def test_chapter_pass_keeps_a_long_future_book_condition_pending(settings, domain, story):
    task = queue(domain, story)
    model = AdherenceModel()
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "segment_complete", completed["error"]
    artifact = domain.artifact(completed["result"]["latest_artifact"])
    report = artifact["payload"]["review"]
    assert report["checks"][0]["condition"] == FINALE
    assert report["checks"][0]["result"] == "pass"
    assert report["book_checks_pending"] == [
        {"condition": FINALE, "chapter_result": "pass", "status": "whole_book_pending"}
    ]
    assert report["chapter_adherence_version"] == "chapter-adherence/1"
    assert model.reviews[0]["章节遵守核对版本"] == "chapter-adherence/1"
    assert "未到本章的整书终局" in model.reviews[0]["章节结果含义"]
    steps = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
    )
    metadata = next(
        json.loads(step["payload"])["request"]
        for step in steps
        if json.loads(step["payload"])["action"] == "审查正文"
    )
    assert metadata["chapter_adherence_version"] == "chapter-adherence/1"
    assert domain.completion(story["branch_id"])["production_complete"] is False


def test_a_current_chapter_positive_result_cannot_pass_just_because_nothing_contradicts_it(
    settings, domain, story
):
    task = queue(domain, story, chapter_missing=True)
    model = AdherenceModel(chapter_missing=True)
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "needs_review", stopped["error"]
    artifact = domain.artifact(stopped["result"]["waiting_artifact"])
    checks = artifact["payload"]["review"]["checks"]
    assert [check["result"] for check in checks] == ["pass", "unknown"]
    assert artifact["payload"]["review_passed"] is False and artifact["payload"]["memory"] == {}
    assert domain.chapter(artifact["chapter_id"])["revision"] == 0
    assert model.actions == ["按章纲写完整正文", "审查正文"]


def test_a_chapter_pass_is_not_reused_as_fulfillment_at_whole_book_completion(settings, domain, story):
    task = queue(domain, story, scope="whole_book")
    model = AdherenceModel()
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_creative", stopped["error"]
    chapter = domain.artifact(stopped["result"]["latest_artifact"])
    assert chapter["payload"]["review"]["checks"][0]["result"] == "pass"
    assert chapter["payload"]["review"]["book_checks_pending"][0]["condition"] == FINALE
    assert model.book_reports[0]["必须逐项核对的作者硬约定"] == [FINALE]
    assert stopped["result"]["completion"]["promises"] == [
        {"condition": FINALE, "result": "unknown", "evidence": ""}
    ]
    assert domain.completion(story["branch_id"])["production_complete"] is False


@pytest.mark.parametrize("model,field", [(Review, "checks"), (Completion, "promises")])
def test_legacy_review_and_completion_do_not_reinterpret_their_result_field(model, field):
    value = {
        field: [{"condition": FINALE, "chapter_result": "pass", "evidence": BODY}],
        "findings": [],
        "summary": "旧契约仍只接受result。",
    }
    if model is Completion:
        value["closed"] = True
    with pytest.raises(ValidationError):
        model.model_validate(value)
