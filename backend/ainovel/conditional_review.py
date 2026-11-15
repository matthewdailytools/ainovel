"""Versioned chapter applicability for explicit conditional whole-book checks."""

from typing import Literal

from pydantic import AliasChoices, Field, model_validator

from .db import DomainError
from .models import Review, ReviewCheck

KEY = "conditional_book_policy_version"
VERSION = "conditional-book-check/1"
GUIDANCE_VERSION = "book-condition-context/1"
ADHERENCE_VERSION = "chapter-adherence/1"


class ConditionalReviewCheck(ReviewCheck):
    condition: str = Field(
        description="只填本次固定条件编号表中的FIX编号，按原顺序各一次；不重抄或改写原话。"
    )
    result: Literal["pass", "fail", "unknown", "not_triggered"] = Field(
        validation_alias=AliasChoices("chapter_result", "result"),
        description="只判断本章当前遵守情况。book源不要求提前兑现未来全书结果；chapter源仍须核对本章肯定事项实际完成。证据不足仍unknown。not_triggered仅用于允许清单且满足适用性与本章义务资格，不等于pass或全书履约。",
    )
    evidence: str = Field(
        description="pass必须引用本章合法source_id或连续逐字原文。完全未触发且满足本次新政策时可not_triggered并留空，其余证据不足仍unknown；不得编造引文。"
    )
    scope: Literal["book", "chapter", "prohibition", "unspecified"] = Field(
        default="unspecified",
        description="沿用固定条件来源的scope；来源是全书不表示本章必须兑现，其本章义务另判。",
    )
    applicability: Literal["triggered", "not_triggered", "uncertain", "unconditional"] = "uncertain"
    chapter_obligation: Literal["required", "not_required", "uncertain"] = "uncertain"
    explanation: str = Field(default="", max_length=5000)

    @model_validator(mode="before")
    @classmethod
    def consistent_result_aliases(cls, value):
        if isinstance(value, dict) and "chapter_result" in value and "result" in value:
            if value["chapter_result"] != value["result"]:
                raise ValueError("chapter_result与result不能表示矛盾判断，未选择或改判其中一项。")
            return {key: item for key, item in value.items() if key != "result"}
        return value


class ConditionalReview(Review):
    checks: list[ConditionalReviewCheck]


def enabled(task):
    version = task["result"].get(KEY)
    if version is not None and version != VERSION:
        raise DomainError("conditional_policy", "全书条件适用性政策无法确认，未升级或继续。", 409)
    return version == VERSION


def book_conditions(task):
    if not enabled(task):
        return []
    if task["kind"] == "workflow":
        return task["payload"]["fixed"]
    elif task["kind"] == "candidate_recheck":
        snapshot = task["result"]["candidate_recheck_snapshot"]
        if snapshot.get(KEY) != VERSION:
            raise DomainError("conditional_policy", "复检的全书条件政策与冻结来源不符。", 409)
        return snapshot["book_fixed"]
    return []


def condition_origins(task, fixed, instructions, prohibitions):
    """Classify author sources without inventing when a condition must occur."""
    book = book_conditions(task)
    chapter = (instructions or {}).get("本章", {}).get("fixed", [])
    return [
        {
            "condition": condition,
            "scope": "prohibition"
            if condition in prohibitions
            else "chapter"
            if condition in chapter or condition not in book
            else "book",
        }
        for condition in fixed
    ]


def eligible_conditions(task, fixed, instructions, prohibitions):
    """Eligibility comes from frozen book conditions, never generated prose."""
    return [
        row["condition"]
        for row in condition_origins(task, fixed, instructions, prohibitions)
        if row["scope"] == "book" and "若涉及" in row["condition"]
    ]


def valid_not_triggered(check, eligible):
    return (
        check.get("result") == "not_triggered"
        and check.get("condition") in eligible
        and check.get("scope") == "book"
        and check.get("applicability") == "not_triggered"
        and check.get("chapter_obligation") == "not_required"
        and isinstance(check.get("explanation"), str)
        and bool(check["explanation"].strip())
    )


def check_issues(check, index, eligible, text):
    if check["result"] != "not_triggered":
        return []
    issues = []
    if not valid_not_triggered(check, eligible):
        issues.append(
            f"checks[{index}]不能使用not_triggered；仅清单中的明确‘若涉及’全书约定，"
            "scope=book、applicability=not_triggered、chapter_obligation=not_required，"
            "并逐项说明本章为何完全未触发、没有本章必达事项时可用。"
            "章内必达、禁止项、已触发或适用性不明仍须pass/fail/unknown，不得删改条件。"
        )
    if check["evidence"] and check["evidence"] not in text:
        issues.append(f"checks[{index}].evidence不在本章逐字原文；未涉及可留空，不得补造证据。")
    return issues


def bind_report(review, eligible, origins):
    review.update(
        conditional_policy_version=VERSION,
        conditional_guidance_version=GUIDANCE_VERSION,
        chapter_adherence_version=ADHERENCE_VERSION,
        conditional_book_conditions=eligible,
        condition_origins=origins,
        book_checks_pending=[
            {
                "condition": check["condition"],
                "chapter_result": check["result"],
                "status": "whole_book_pending",
            }
            for check in review["checks"]
            if any(row["condition"] == check["condition"] and row["scope"] == "book" for row in origins)
        ],
    )
    return review
