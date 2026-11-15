"""Chapter non-applicability is explicit, versioned and never a book fulfillment."""

import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from ainovel.candidate_recheck import freeze
from ainovel.conditional_review import KEY, VERSION
from ainovel.book_applicability import KEY as APPLICABILITY_KEY, ACTION as APPLICABILITY_ACTION
from ainovel.db import dumps
from ainovel.models import Completion, Review
from ainovel.plot_review import review_passed, unresolved_without_blocker
from ainovel.worker import Worker
from test_author_contract import FIELDS, adopt_intent
from test_candidate_recheck import (
    RecheckModel,
    assert_parent_not_resumed,
    queue_recheck,
    stopped_candidate as stopped_candidate_fixture,
)
from test_workflow_scope import SegmentModel, workflow_payload
from test_workflow_plan_preparation import PreparationModel


CONDITION = "若涉及母亲病情，须保持她尚未康复。"
stopped_candidate = stopped_candidate_fixture


class ConditionalModel(SegmentModel):
    """Offline responses exercise orchestration, not semantic detection quality."""

    def __init__(self, *, result="not_triggered", target=CONDITION, check_changes=None, completion=None):
        super().__init__()
        self.result = result
        self.target = target
        self.check_changes = check_changes or {}
        self.completion = completion
        self.actions = []
        self.chapter_reviews = []
        self.book_reviews = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("动作")
        self.actions.append(action)
        if action == "审查正文":
            self.calls += 1
            self.chapter_reviews.append(deepcopy(instruction))
            source = instruction["可引用原文"][0]["source_id"]
            checks = []
            for condition in instruction["固定条件"]:
                check = {"condition": condition, "result": "pass", "evidence": source}
                if condition == self.target:
                    check.update(
                        result=self.result,
                        evidence="" if self.result != "pass" else source,
                        scope="book",
                        chapter_obligation="not_required",
                        applicability="not_triggered" if self.result == "not_triggered" else "uncertain",
                        explanation="本章只测潮记下读数，完全未涉及母亲病情；整书仍待核对。",
                    )
                    check.update(self.check_changes)
                checks.append(check)
            return {
                "text": dumps(
                    {"checks": checks, "findings": [], "summary": f"第{self.calls}轮隔离适用性工程样例。"}
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": f"offline-conditional-{self.calls}",
            }
        if action == "整书完结检查" and self.completion is not None:
            self.calls += 1
            self.book_reviews.append(deepcopy(instruction))
            output = {
                "closed": self.completion == "omitted",
                "promises": []
                if self.completion == "omitted"
                else [
                    {
                        "condition": condition,
                        "result": "unknown",
                        "evidence": "",
                    }
                    for condition in instruction["必须逐项核对的作者硬约定"]
                ],
                "findings": [],
                "summary": f"第{self.calls}轮：章内未触发不等于全书满足，缺少本书证据仍待核对。",
            }
            return {
                "text": dumps(output),
                "usage": {"input": 1, "output": 1},
                "call_id": f"offline-conditional-book-{self.calls}",
            }
        return await super().generate(account, task_id, system, prompt, max_output, **kwargs)


def queue(domain, story, *, fixed=None, chapter_fixed=None, legacy=False, scope="segment"):
    payload = workflow_payload(story, scope=scope)
    payload["fixed"] = fixed if fixed is not None else [CONDITION]
    if chapter_fixed is not None:
        payload["plots"][0]["fixed"] = chapter_fixed
    task = domain.queue("workflow", payload, story["branch_id"])
    if legacy:
        result = {k: v for k, v in task["result"].items() if k not in {KEY, APPLICABILITY_KEY}}
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
        task = domain.task(task["id"])
    return task


def draft(domain, task):
    return domain.artifact(task["result"]["latest_artifact"])


def test_new_workflow_can_adopt_a_not_triggered_chapter_and_continue_without_faking_pass(
    settings, domain, story
):
    task = queue(domain, story)
    assert task["result"][KEY] == VERSION
    model = ConditionalModel()
    assert asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert completed["status"] == "segment_complete", completed["error"]
    candidate = draft(domain, completed)
    report = candidate["payload"]["review"]
    assert candidate["status"] == "accepted"
    assert candidate["payload"]["review_passed"] is True
    assert candidate["payload"]["memory"]["facts"]
    assert report["conditional_policy_version"] == VERSION
    assert report["conditional_book_conditions"] == [CONDITION]
    assert report["checks"][0]["result"] == "not_triggered"
    assert report["checks"][0]["evidence"] == ""
    assert report["book_checks_pending"] == [
        {"condition": CONDITION, "chapter_result": "not_triggered", "status": "whole_book_pending"}
    ]
    assert model.chapter_reviews[0]["允许单列本章未触发的全书约定"] == [CONDITION]
    assert model.chapter_reviews[0]["作者写作要求"]["本章"]["fixed"] == ["记录读数"]
    assert completed["result"]["next_chapter"] == 2
    assert domain.chapter(candidate["chapter_id"])["revision"] == 1
    assert len(domain.db.all("SELECT * FROM receipts")) == 1


def test_uncertain_applicability_stops_before_prose_repair_or_memory(settings, domain, story):
    task = queue(domain, story)
    model = ConditionalModel(result="unknown")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "needs_review", stopped["error"]
    candidate = draft(domain, stopped)
    assert candidate["status"] == "candidate" and candidate["payload"]["review_passed"] is False
    assert candidate["payload"]["review"]["checks"][0]["result"] == "unknown"
    assert candidate["payload"]["memory"] == {}
    assert len(model.chapter_reviews) == 1
    assert model.actions == ["按章纲写完整正文", APPLICABILITY_ACTION, "审查正文"]
    assert "未自动修订" in stopped["error"]
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT * FROM receipts")


@pytest.mark.parametrize("obligation", ["not_required", "required", "uncertain"])
def test_labeled_mixed_book_condition_requires_an_explicit_chapter_obligation_decision(
    settings, domain, story, obligation
):
    mixed = "【母亲病情】第1—25章若涉及诊治，须保持尚未康复；第26章须写明治疗结果。"
    task = queue(domain, story, fixed=[mixed])
    model = ConditionalModel(target=mixed, check_changes={"chapter_obligation": obligation})
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(task["id"])
    assert model.chapter_reviews[0]["允许单列本章未触发的全书约定"] == [mixed]
    if obligation == "not_required":
        assert completed["status"] == "segment_complete", completed["error"]
        review = draft(domain, completed)["payload"]["review"]
        assert review["checks"][0]["condition"] == mixed
        assert review["checks"][0]["result"] == "not_triggered"
        assert review["book_checks_pending"][0]["condition"] == mixed
    else:
        assert completed["status"] == "blocked_runtime", completed["error"]
        assert domain.chapter(story["chapter_id"])["revision"] == 0
        assert not domain.db.all("SELECT * FROM receipts")


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "chapter"},
        {"scope": "prohibition"},
        {"applicability": "uncertain"},
        {"applicability": "triggered"},
        {"chapter_obligation": "required"},
        {"chapter_obligation": "uncertain"},
        {"explanation": ""},
        {"explanation": " \n "},
        {"evidence": "母亲已经痊愈，来源根本不存在。"},
    ],
)
def test_invalid_non_applicability_never_adopts_or_silently_repairs_conditions(
    settings, domain, story, changes
):
    task = queue(domain, story)
    model = ConditionalModel(check_changes=changes)
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    assert len(model.chapter_reviews) == 3
    assert all(item["固定条件"][0] == CONDITION for item in model.chapter_reviews)
    assert not domain.db.all("SELECT * FROM artifacts")
    assert not domain.db.all("SELECT * FROM receipts")
    assert not domain.db.all("SELECT * FROM facts")
    assert domain.chapter(story["chapter_id"])["content"] == ""


@pytest.mark.parametrize("scope", ["chapter", "unconditional", "prohibition"])
def test_chapter_mandatory_and_unconditional_or_prohibited_requirements_cannot_be_skipped(
    settings, domain, story, scope
):
    condition = CONDITION if scope != "unconditional" else "本章完成母亲诊治。"
    if scope == "prohibition":
        adopt_intent(domain, story, fields={**FIELDS, "prohibitions": [CONDITION]})
    receipts_before = domain.db.all("SELECT * FROM receipts")
    task = queue(
        domain,
        story,
        fixed=[condition],
        chapter_fixed=[condition] if scope == "chapter" else ["记录读数"],
    )
    model = ConditionalModel(target=condition)
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    assert "not_triggered" in stopped["error"]
    assert all(item["允许单列本章未触发的全书约定"] == [] for item in model.chapter_reviews)
    assert domain.chapter(story["chapter_id"])["revision"] == 0
    assert domain.db.all("SELECT * FROM receipts") == receipts_before


def test_legacy_workflow_rejects_new_result_instead_of_getting_silently_upgraded(settings, domain, story):
    task = queue(domain, story, legacy=True)
    model = ConditionalModel()
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    assert KEY not in stopped["result"]
    assert all("全书条件适用性政策" not in item for item in model.chapter_reviews)
    assert domain.chapter(story["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT * FROM artifacts")


def test_legacy_unknown_remains_unknown_without_new_policy_metadata(settings, domain, story):
    task = queue(domain, story, legacy=True)
    model = ConditionalModel(result="unknown")
    # Legacy Review intentionally does not accept the new applicability fields.
    # An adapter strips only those new fields, retaining its actual unknown verdict.
    original_generate = model.generate

    async def legacy_generate(*args, **kwargs):
        response = await original_generate(*args, **kwargs)
        try:
            result = json.loads(response["text"])
        except json.JSONDecodeError:
            return response
        if isinstance(result, dict) and "checks" in result:
            for check in result["checks"]:
                for key in ("scope", "applicability", "chapter_obligation", "explanation"):
                    check.pop(key, None)
            response["text"] = dumps(result)
        return response

    # Stop at the first legacy review, before its separate finite-revision policy.
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    model.generate = legacy_generate
    review = asyncio.run(
        worker.review(
            "沈砚用旧尺测量潮水，留下可以回查的记录。",
            [CONDITION],
            worker.context(story["branch_id"], story["chapter_id"]),
            1,
            {"本章": task["payload"]["plots"][0]},
        )
    )
    assert review["checks"][0]["result"] == "unknown"
    assert not review_passed(review)
    assert "conditional_policy_version" not in review
    assert KEY not in domain.task(task["id"])["result"]


@pytest.mark.parametrize("defect", ["blocker", "plot_failed", "plot_unknown", "unknown", "fail", "version"])
def test_non_applicability_does_not_bypass_other_gates(defect):
    review = {
        "conditional_policy_version": VERSION,
        "conditional_book_conditions": [CONDITION],
        "checks": [
            {
                "condition": CONDITION,
                "result": "not_triggered",
                "evidence": "",
                "scope": "book",
                "chapter_obligation": "not_required",
                "applicability": "not_triggered",
                "explanation": "本章未涉及，整书待核对。",
            }
        ],
        "findings": [],
    }
    assert review_passed(review)
    if defect == "blocker":
        review["findings"].append({"severity": "blocker"})
    elif defect.startswith("plot_"):
        review["plot_audit"] = {"status": "failed" if defect == "plot_failed" else "unverified"}
    elif defect == "version":
        review["conditional_policy_version"] = "conditional-book-check/unknown"
    else:
        review["checks"].append({"condition": "本章须完成测量", "result": defect, "evidence": ""})
    assert not review_passed(review)
    if defect == "unknown":
        assert unresolved_without_blocker(review)
    if defect == "blocker":
        assert not unresolved_without_blocker(review)


def test_new_recheck_freezes_policy_but_preserves_legacy_parent_and_old_report(
    settings, domain, client, stopped_candidate
):
    parent, candidate = stopped_candidate
    old_progress = {
        key: value for key, value in parent["result"].items() if key not in {KEY, APPLICABILITY_KEY}
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(old_progress), parent["id"]))
    parent = domain.task(parent["id"])
    before = deepcopy(domain.artifact(candidate["id"]))
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    queued = response.json()
    snapshot = queued["result"]["candidate_recheck_snapshot"]
    assert queued["result"][KEY] == snapshot[KEY] == VERSION
    assert snapshot["book_fixed"] == parent["payload"]["fixed"]
    assert KEY not in parent["result"]
    asyncio.run(Worker(settings, RecheckModel()).run_once())
    done = domain.task(queued["id"])
    assert done["status"] == "succeeded", done["error"]
    after = domain.artifact(candidate["id"])
    assert after["payload"]["previous_review"] == before["payload"].get(
        "review", before["payload"]["previous_review"]
    )
    assert after["payload"]["review"]["conditional_policy_version"] == VERSION
    report = domain.artifact(done["result"]["artifact_id"])
    assert report["payload"]["previous_candidate"] == before["payload"]
    assert after["payload"]["text"] == before["payload"]["text"]
    assert after["candidate_revision"] == before["candidate_revision"]
    assert_parent_not_resumed(domain, parent)
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0


def test_explicit_new_recheck_can_classify_a_legacy_candidates_untouched_book_condition(
    settings, domain, story, client
):
    payload = workflow_payload(story, mode="automatic")
    payload.update(fixed=[CONDITION], plot_contract_check=True)
    task = domain.queue("workflow", payload, story["branch_id"])
    legacy_result = {
        key: value for key, value in task["result"].items() if key not in {KEY, APPLICABILITY_KEY}
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy_result), task["id"]))
    initial_model = PreparationModel()
    initial_model.verdict = "unknown"
    asyncio.run(Worker(settings, initial_model).run_once())
    parent = domain.task(task["id"])
    assert parent["status"] == "needs_review", parent["error"]
    candidate = domain.artifact(parent["result"]["waiting_artifact"])
    assert "conditional_policy_version" not in candidate["payload"]["review"]
    old_report = deepcopy(candidate["payload"]["review"])

    class CurrentConditionalRecheck(RecheckModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            instruction = frame["任务"]
            if instruction.get("动作") != "审查正文":
                return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
            self.calls += 1
            self.actions.append("审查正文")
            self.frames.append(deepcopy(frame))
            assert instruction["允许单列本章未触发的全书约定"] == [CONDITION]
            source = instruction["可引用原文"][0]["source_id"]
            return {
                "text": dumps(
                    {
                        "checks": [
                            {
                                "condition": condition,
                                "result": "not_triggered" if condition == CONDITION else "pass",
                                "evidence": "" if condition == CONDITION else source,
                                "scope": "book",
                                "applicability": "not_triggered"
                                if condition == CONDITION
                                else "unconditional",
                                "chapter_obligation": "not_required",
                                "explanation": "本章只测量和留复测记录，未出现母亲病情；整书仍待核对。",
                            }
                            for condition in instruction["固定条件"]
                        ],
                        "findings": [],
                        "summary": "独立新版复检，无正文写作或采用。",
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-new-policy-recheck",
            }

    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    recheck = response.json()
    model = CurrentConditionalRecheck()
    asyncio.run(Worker(settings, model).run_once())
    done = domain.task(recheck["id"])
    assert done["status"] == "succeeded", done["error"]
    assert model.actions == [
        APPLICABILITY_ACTION,
        "审查正文",
        "逐项对照作者情节解释与本次正文",
        "从正文提取事实候选与章节摘要",
    ]
    updated = domain.artifact(candidate["id"])
    assert updated["payload"]["review_passed"] is True
    assert updated["payload"]["memory"]["facts"]
    assert updated["payload"]["review"]["checks"][0]["result"] == "not_triggered"
    assert updated["payload"]["previous_review"] == old_report
    assert updated["payload"]["text"] == candidate["payload"]["text"]
    assert updated["candidate_revision"] == candidate["candidate_revision"] == 0
    assert updated["status"] == "candidate"
    report = domain.artifact(done["result"]["artifact_id"])
    assert report["payload"]["previous_candidate"] == candidate["payload"]
    assert report["payload"]["review"]["book_checks_pending"][0]["condition"] == CONDITION
    assert_parent_not_resumed(domain, parent)
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT * FROM receipts")


def test_already_queued_legacy_recheck_keeps_legacy_snapshot_and_report_shape(
    settings, domain, client, stopped_candidate
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202
    task = response.json()
    original = deepcopy(task["result"])
    original.pop(KEY)
    original.pop(APPLICABILITY_KEY)
    original["candidate_recheck_snapshot"] = freeze(domain, task["payload"])
    assert KEY not in original["candidate_recheck_snapshot"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(original), task["id"]))
    asyncio.run(Worker(settings, RecheckModel()).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    assert KEY not in done["result"]
    assert done["result"]["candidate_recheck_snapshot"] == original["candidate_recheck_snapshot"]
    report = domain.artifact(done["result"]["artifact_id"])
    assert "conditional_policy_version" not in report["payload"]["review"]
    assert_parent_not_resumed(domain, parent)


def test_recheck_policy_and_frozen_source_version_mismatch_stops_before_any_call(
    settings, domain, client, stopped_candidate
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202
    task = response.json()
    result = deepcopy(task["result"])
    result.pop(KEY)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    model = RecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    assert model.actions == []
    assert domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)


def test_chapter_not_triggered_does_not_fulfill_the_same_whole_book_condition(settings, domain, story):
    task = queue(domain, story, scope="whole_book")
    model = ConditionalModel(completion="unknown")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_creative", stopped["error"]
    candidate = draft(domain, stopped)
    assert candidate["status"] == "accepted"
    assert candidate["payload"]["review"]["checks"][0]["result"] == "not_triggered"
    assert model.book_reviews[0]["必须逐项核对的作者硬约定"] == [CONDITION]
    assert stopped["result"]["completion"]["promises"] == [
        {"condition": CONDITION, "result": "unknown", "evidence": ""}
    ]
    assert domain.completion(story["branch_id"])["production_complete"] is False


def test_omitting_a_deferred_book_condition_cannot_close_the_book(settings, domain, story):
    task = queue(domain, story, scope="whole_book")
    model = ConditionalModel(completion="omitted")
    asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime", stopped["error"]
    assert len(model.book_reviews) == 3
    repairs = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair'", (task["id"],)
    )
    assert any(CONDITION in issue for row in repairs for issue in json.loads(row["payload"])["issues"])
    assert domain.completion(story["branch_id"])["production_complete"] is False


def test_legacy_review_and_completion_schemas_do_not_gain_non_applicability():
    check = {"condition": CONDITION, "result": "not_triggered", "evidence": ""}
    with pytest.raises(ValidationError):
        Review.model_validate({"checks": [check], "findings": [], "summary": "仍待核对"})
    with pytest.raises(ValidationError):
        Completion.model_validate(
            {"closed": True, "promises": [check], "findings": [], "summary": "不能绕过整书检查"}
        )
