import asyncio
from copy import deepcopy

import pytest

from ainovel.db import DomainError
from ainovel.domain import char_count, utf16_slice
from ainovel.targeted_revision import run_targeted_revision


@pytest.fixture
def reviewed(domain, story):
    chapter = domain.save(story["chapter_id"], 0, "沈砚😀看见船门敞开。\n\n他独自离开。\n\n船门随后关上。")
    branch = domain.branch(story["branch_id"])
    report = domain.stage(
        {"novel_id": story["id"], "branch_id": branch["id"], "chapter_id": chapter["id"], "base_revision": 1},
        "review",
        {
            "checks": [],
            "summary": "工程夹具，仅用于验证问题到修订链路。",
            "fixed": ["不新增同伴"],
            "findings": [
                {
                    "chapter": 1,
                    "severity": "blocker",
                    "quote": "他独自离开。",
                    "reason": "离开前动作缺失。",
                    "repair": "补足关门动作。",
                },
                {
                    "chapter": 1,
                    "severity": "suggestion",
                    "quote": "船门随后关上。",
                    "reason": "语气偏平。",
                    "repair": "可放慢叙述。",
                },
            ],
        },
        {"story_revision": branch["story_revision"]},
    )
    request = {
        "novel_id": story["id"],
        "branch_id": branch["id"],
        "chapter_id": chapter["id"],
        "base_revision": 1,
        "source_review_id": report["id"],
        "finding_indices": [0],
        "fixed": ["不战斗"],
        "instruction": "只修我选中的动作缺口。",
    }
    return request, report, chapter


class RevisionWorker:
    def __init__(self, domain, *, results=("pass",), other_blocker=False, recheck_evidence=None):
        self.domain, self.results = domain, list(results)
        self.other_blocker = other_blocker
        self.recheck_evidence = recheck_evidence
        self.repairs, self.reviews, self.rechecks = [], [], []
        self.repair_units, self.recheck_contexts = [], []

    async def revise_chapter(self, text, instructions, review, context, minimum, maximum, *, repair_units):
        assert (minimum + maximum) // 2 == char_count(text)
        assert repair_units
        for unit in repair_units:
            assert unit["source_id"].startswith("EDIT")
            assert utf16_slice(text, unit["start"], unit["end"])[1] == unit["text"]
            assert text.find(unit["text"]) == text.rfind(unit["text"]) >= 0
            assert unit["text"].strip() != text.strip()
        self.repair_units.append(deepcopy(repair_units))
        self.repairs.append(deepcopy(review))
        return text.replace("他独自离开。", "他把门掩好，独自离开。")

    async def review(self, text, fixed, context, number, instructions):
        self.reviews.append({"text": text, "fixed": fixed, "instructions": instructions})
        findings = []
        if self.other_blocker:
            findings.append(
                {
                    "chapter": 1,
                    "severity": "blocker",
                    "quote": "船门随后关上。",
                    "reason": "另有未选问题。",
                    "repair": "交作者另行决定。",
                }
            )
        return {
            "checks": [{"condition": c, "result": "pass", "evidence": "他把门掩好"} for c in fixed],
            "findings": findings,
            "summary": "独立复检夹具。",
        }

    async def grounded(self, instruction, schema, context, validator, max_output):
        self.rechecks.append(deepcopy(instruction))
        self.recheck_contexts.append(deepcopy(context))
        result = self.results[min(len(self.rechecks) - 1, len(self.results) - 1)]
        evidence = next(
            unit["source_id"]
            for unit in instruction["可引用原文"]
            if "他把门掩好，独自离开。" in unit["text"]
        )
        if self.recheck_evidence is not None:
            evidence = self.recheck_evidence
        report = {
            "results": [
                {
                    "finding_index": f["index"],
                    "result": result,
                    "reason": "工程夹具复检结果，不是文学评分。",
                    "evidence": evidence,
                }
                for f in instruction["原问题"]
            ]
        }
        assert not validator(report)
        return report


def test_selected_problem_revision_recheck_retains_source_and_never_adopts(domain, reviewed):
    request, source, chapter = reviewed
    before = domain.db.all("SELECT * FROM revisions WHERE chapter_id=?", (chapter["id"],))
    context = domain.context(request["branch_id"], chapter["id"])
    worker = RevisionWorker(domain)
    kind, payload = asyncio.run(run_targeted_revision(worker, request, context))
    assert kind == "chapter_draft" and payload["review_passed"] is True
    assert "他把门掩好，独自离开。" in payload["text"]
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.artifact(source["id"]) == source
    assert domain.db.all("SELECT * FROM revisions WHERE chapter_id=?", (chapter["id"],)) == before
    trace = payload["revision_trace"]
    assert trace["source_review_id"] == source["id"] and trace["base_revision"] == 1
    assert trace["selected_findings"][0]["index"] == 0
    quote = trace["selected_findings"][0]
    assert utf16_slice(chapter["content"], quote["start"], quote["end"])[1] == quote["quote"]
    assert [f["quote"] for f in worker.repairs[0]["findings"]] == ["他独自离开。"]
    assert [u["text"] for u in worker.repair_units[0]] == ["他独自离开。"]
    assert trace["rounds"][0]["repair_units"] == worker.repair_units[0]
    assert worker.reviews[0]["fixed"] == ["不新增同伴", "不战斗"]
    assert trace["original_problem_recheck"][0]["result"] == "pass"
    assert trace["adopted"] is False and trace["formal_creative_acceptance"] == "unverified"
    assert len(worker.repairs) == len(worker.reviews) == len(worker.rechecks) == 1
    candidate_context = worker.recheck_contexts[0]
    assert candidate_context["chapter"]["content"] == payload["text"]
    assert candidate_context["chapter"]["base_revision"] == chapter["revision"]
    assert "revision" not in candidate_context["chapter"]
    assert candidate_context["text_target"]["purpose"] == "selected_problem_recheck"
    assert context["chapter"]["content"] == chapter["content"]


@pytest.mark.parametrize("results,passed", [(("fail", "pass"), True), (("unknown", "unknown"), False)])
def test_revision_is_bounded_to_two_rounds_and_keeps_unresolved_outcome(domain, reviewed, results, passed):
    request, _, chapter = reviewed
    worker = RevisionWorker(domain, results=results)
    _, payload = asyncio.run(
        run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
    )
    assert len(worker.repairs) == 2 and payload["review_passed"] is passed
    assert payload["revision_trace"]["rounds_used"] == 2
    assert [u["text"] for u in worker.repair_units[1]] == ["他把门掩好，独自离开。"]
    if not passed:
        assert payload["revision_trace"]["stop_reason"] == "revision_limit"
    assert domain.chapter(chapter["id"]) == chapter


def test_unselected_problem_is_not_automatically_repaired_to_manufacture_pass(domain, reviewed):
    request, _, chapter = reviewed
    worker = RevisionWorker(domain, other_blocker=True)
    _, payload = asyncio.run(
        run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
    )
    assert len(worker.repairs) == 1
    assert payload["review_passed"] is False
    assert payload["revision_trace"]["selected_problems_resolved"] is True
    assert payload["revision_trace"]["stop_reason"] == "selected_resolved_other_checks_pending"


def test_author_can_select_preference_without_reclassifying_it_as_fact_error(domain, reviewed):
    request, _, chapter = reviewed
    request["finding_indices"] = [1]
    worker = RevisionWorker(domain)
    _, payload = asyncio.run(
        run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
    )
    finding = payload["revision_trace"]["selected_findings"][0]
    assert finding["severity"] == "suggestion" and finding["issue_kind"] == "author_selected_preference"
    assert finding["index"] == 1


@pytest.mark.parametrize("indices", [[], [-1], [99], [0, 0], list(range(21))])
def test_invalid_selections_stop_before_any_model_operation(domain, reviewed, indices):
    request, _, chapter = reviewed
    request["finding_indices"] = indices
    worker = RevisionWorker(domain)
    with pytest.raises(DomainError):
        asyncio.run(
            run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
        )
    assert worker.repairs == worker.reviews == worker.rechecks == []


@pytest.mark.parametrize(
    "change", ["body", "story", "quote", "whole", "ambiguous", "nested", "foreign", "rejected"]
)
def test_expired_wrong_or_unlocatable_report_is_blocked_before_spending(domain, reviewed, change):
    request, source, chapter = reviewed
    if change == "body":
        domain.save(chapter["id"], 1, chapter["content"] + "新句。")
    elif change == "story":
        domain.create_chapter(request["branch_id"], "后来章节")
    elif change in {"quote", "whole", "nested", "foreign"}:
        payload = deepcopy(source["payload"])
        if change == "quote":
            payload["findings"][0]["quote"] = "没有出现的引文。"
        elif change == "whole":
            payload["findings"][0]["quote"] = chapter["content"]
        target = {
            "novel_id": source["novel_id"],
            "branch_id": source["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": 1,
        }
        if change == "foreign":
            other = domain.create_novel("别的作品", "工程测试")
            other_chapter = domain.create_chapter(other["branch_id"], "一")
            target.update(novel_id=other["id"], branch_id=other["branch_id"], chapter_id=other_chapter["id"])
        a = domain.stage(
            target, "chapter_draft" if change == "nested" else "review", payload, source["basis"]
        )
        request["source_review_id"] = a["id"]
    elif change == "ambiguous":
        changed = domain.save(chapter["id"], 1, chapter["content"] + "\n\n他独自离开。")
        a = domain.stage(
            {
                "novel_id": source["novel_id"],
                "branch_id": source["branch_id"],
                "chapter_id": chapter["id"],
                "base_revision": changed["revision"],
            },
            "review",
            source["payload"],
            {"story_revision": domain.branch(request["branch_id"])["story_revision"]},
        )
        request.update(source_review_id=a["id"], base_revision=changed["revision"])
    else:
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE artifacts SET status='rejected' WHERE id=?", (source["id"],))
    worker = RevisionWorker(domain)
    with pytest.raises(DomainError):
        asyncio.run(
            run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
        )
    assert not worker.repairs and not worker.rechecks


def test_concurrent_edit_after_patch_stops_review_and_prevents_candidate_result(domain, reviewed):
    request, _, chapter = reviewed
    worker = RevisionWorker(domain)
    revise = worker.revise_chapter

    async def edit_after_patch(*args, **kwargs):
        text = await revise(*args, **kwargs)
        domain.save(chapter["id"], 1, chapter["content"] + "作者并行手写。")
        return text

    worker.revise_chapter = edit_after_patch
    with pytest.raises(DomainError, match="正文已改变"):
        asyncio.run(
            run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
        )
    assert not worker.reviews and not worker.rechecks
    assert domain.chapter(chapter["id"])["content"].endswith("作者并行手写。")


def test_editing_candidate_keeps_recheck_as_expired_history(client, domain, reviewed):
    request, _, chapter = reviewed
    worker = RevisionWorker(domain)
    _, payload = asyncio.run(
        run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
    )
    candidate = domain.stage(
        request,
        "chapter_draft",
        payload,
        {"story_revision": domain.branch(request["branch_id"])["story_revision"]},
    )
    response = client.patch(
        f"/api/artifacts/{candidate['id']}",
        json={"text": payload["text"] + "作者编辑候选。", "candidate_revision": 0},
    )
    assert response.status_code == 200
    edited = response.json()["payload"]
    assert "revision_trace" not in edited and "review" not in edited
    assert edited["previous_revision_trace"] == payload["revision_trace"]
    assert edited["review_passed"] is False
    assert domain.chapter(chapter["id"]) == chapter


@pytest.mark.parametrize("evidence", ["", "船门"])
def test_unlocatable_second_round_preserves_first_candidate_and_trace(domain, reviewed, evidence):
    request, source, chapter = reviewed
    worker = RevisionWorker(domain, results=("unknown",), recheck_evidence=evidence)
    _, payload = asyncio.run(
        run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
    )
    assert len(worker.repairs) == len(worker.rechecks) == len(worker.reviews) == 1
    assert payload["text"] == chapter["content"].replace("他独自离开。", "他把门掩好，独自离开。")
    assert payload["review_passed"] is False
    trace = payload["revision_trace"]
    assert trace["stop_reason"] == "repair_scope_unavailable" and trace["rounds_used"] == 1
    assert trace["original_problem_recheck"][0]["result"] == "unknown"
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.artifact(source["id"]) == source


@pytest.mark.parametrize(
    "text,quote", [("哈哈哈哈\n\n另一句。", "哈哈哈"), ("甲" * 801 + "\n\n保留段落。", "甲" * 801)]
)
def test_overlapping_matches_and_oversized_ranges_stop_before_model(domain, reviewed, text, quote):
    request, source, chapter = reviewed
    changed = domain.save(chapter["id"], chapter["revision"], text)
    report = deepcopy(source["payload"])
    report["findings"][0]["quote"] = quote
    staged = domain.stage(
        {**request, "base_revision": changed["revision"]},
        "review",
        report,
        {"story_revision": domain.branch(request["branch_id"])["story_revision"]},
    )
    request.update(source_review_id=staged["id"], base_revision=changed["revision"])
    worker = RevisionWorker(domain)
    with pytest.raises(DomainError):
        asyncio.run(
            run_targeted_revision(worker, request, domain.context(request["branch_id"], chapter["id"]))
        )
    assert worker.repairs == worker.reviews == worker.rechecks == []
