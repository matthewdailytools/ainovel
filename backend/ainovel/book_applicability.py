"""Observe conditional book triggers separately from any review judgment."""

import hashlib
from typing import Literal

from pydantic import Field

from .db import DomainError, dumps
from .evidence import resolve_field, source_schema, source_units
from .models import Input

KEY = "book_applicability_version"
VERSION = "book-applicability/1"
ACTION = "独立观察全书条件在本章是否触发"


class ApplicabilityCheck(Input):
    condition_id: str = Field(min_length=1, max_length=20)
    trigger: Literal["triggered", "not_triggered", "uncertain"]
    chapter_obligation: Literal["required", "not_required", "uncertain"]
    trigger_clause: str = Field(
        min_length=1,
        max_length=10_000,
        description="引用对应原条件中触发前提的连续原话；原条件混有本章必达事项时保留完整相关范围，不能借截短前提跳过义务。",
    )
    evidence: str = Field(
        max_length=8000,
        description="triggered必须选本章一个合法SOURCE编号，或连续非空正文原文；not_triggered/uncertain可空，不能把条件原话、计划或无关段落充当发生证据。",
    )
    explanation: str = Field(min_length=1, max_length=5000)


class Applicability(Input):
    checks: list[ApplicabilityCheck] = Field(max_length=60)


def enabled(worker):
    version = (getattr(worker, "task", None) or {}).get("result", {}).get(KEY)
    if version is not None and version != VERSION:
        raise DomainError("book_applicability_version", "全书条件独立观察政策无法确认，未派发旧任务。", 409)
    return version == VERSION


def selected_conditions(fixed, origins, eligible):
    if len(origins) != len(fixed) or any(
        row.get("condition") != condition
        or row.get("scope") not in {"book", "chapter", "prohibition", "unspecified"}
        for condition, row in zip(fixed, origins)
    ):
        raise DomainError("book_applicability_source", "固定条件与来源范围无法逐项对应，未观察或推断。", 409)
    rows = [
        {"condition_id": f"FIX{i:03}", "condition": condition}
        for i, (condition, origin) in enumerate(zip(fixed, origins), 1)
        if condition in eligible and origin["scope"] == "book" and "若涉及" in condition
    ]
    if any(condition not in {row["condition"] for row in rows} for condition in eligible):
        raise DomainError(
            "book_applicability_source", "观察范围只含已明确允许的‘若涉及’全书条件，未扩大或改写条件。", 409
        )
    return rows


async def assess(worker, text, fixed, origins, number, instructions, eligible):
    """Bind independent model observations to exact prose and the full fixed list."""
    bound = {
        "version": VERSION,
        "chapter_number": number,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "conditions_sha256": hashlib.sha256(dumps(fixed).encode()).hexdigest(),
    }
    if not eligible:
        return {**bound, "checks": []}
    if not enabled(worker):
        raise DomainError("book_applicability_version", "原任务未冻结独立适用性观察政策，未新增调用。", 409)
    if type(number) is not int or number < 1:
        raise DomainError("book_applicability_source", "本章编号无效，未推断条件何时必须完成。", 409)
    rows = selected_conditions(fixed, origins, eligible)
    units = source_units(text, "SOURCE")
    if not units:
        raise DomainError("book_applicability_source", "本次正文为空，未将空稿判断为条件未触发。", 409)
    chapter = instructions.get("本章", {}) if isinstance(instructions, dict) else {}
    chapter = chapter if isinstance(chapter, dict) else {}
    # Only the frozen original chapter arrangement is allowed across this
    # boundary. Old reports and candidate judgments may coexist in instructions.
    plot = chapter.get("plot", "")
    chapter_fixed = chapter.get("fixed", [])
    if (
        not isinstance(plot, str)
        or not isinstance(chapter_fixed, list)
        or any(not isinstance(condition, str) for condition in chapter_fixed)
    ):
        raise DomainError("book_applicability_source", "本章原情节或章级条件无法核对，未猜测义务。", 409)
    expected = [row["condition_id"] for row in rows]
    conditions = {row["condition_id"]: row["condition"] for row in rows}
    sources = {unit["source_id"]: unit for unit in units}
    contract = source_schema(Applicability.model_json_schema(), units)
    contract["properties"]["checks"].update(minItems=len(rows), maxItems=len(rows))
    contract["$defs"]["ApplicabilityCheck"]["properties"]["condition_id"]["enum"] = expected

    def validate(value):
        issues = []
        if [check["condition_id"] for check in value["checks"]] != expected:
            issues.append("checks须按本次FIX编号原顺序每项恰好一次，不得重排、新增、遗漏或重复。")
        for index, check in enumerate(value["checks"]):
            condition = conditions.get(check["condition_id"])
            if condition is None:
                continue
            clause = check["trigger_clause"]
            if not clause.strip() or clause not in condition:
                issues.append(
                    f"checks[{index}].trigger_clause须引用本项原条件的连续原话，不能引用正文或别项条件。"
                )
            resolve_field(check, "evidence", sources)
            quote = check["evidence"]
            if quote and (not quote.strip() or quote not in text):
                issues.append(
                    f"checks[{index}].evidence须为本次SOURCE编号或连续非空正文，不能用原条件、旧稿或拼接引文。"
                )
            if check["trigger"] == "triggered" and not quote.strip():
                issues.append(
                    f"checks[{index}]标triggered须有本次正文发生依据；证据不足应uncertain，不能补造触发。"
                )
            if not check["explanation"].strip():
                issues.append(f"checks[{index}].explanation须说明触发判断与本章义务，不能纯空白。")
        return issues

    observed = await worker.grounded(
        {
            "动作": ACTION,
            "全书条件适用观察版本": VERSION,
            "章节号": number,
            "全书条件原话": rows,
            "本章原情节": plot,
            "本章原固定条件": chapter_fixed,
            "本次正文": text,
            "可引用原文": units,
            "来源边界": "只读这些全书条件原话、本章原情节、章级条件与本次正文；没有提供旧审稿、候选结论或预期答案。条件原话只界定前提，不能当作前提已在正文发生的证据；不补造角色、经历或时间。",
            "触发观察": "逐项独立判断triggered/not_triggered/uncertain。triggered必须有本次正文中确实涉及对应前提的SOURCE或连续原文；无关人物或事项不证明该条件前提已发生。全文确实未涉及前提可not_triggered并留空证据；指代不清或范围不足仍uncertain。不能把缺线索、未出现、未来计划判断为已经兑现条件，也不判断审稿是否通过。",
            "本章义务": "chapter_obligation与正文是否出现前提分开判断。原条件或本章原情节明确要求本章必须完成的事项，即使正文完全漏写或前提人物没出现仍required；同一条约定前半句有触发限定、后半句另有无条件的指定章节要求时，后半句不能被前半句跳过。未到指定章节且本章无必达要求可not_required；混合条件、原话或本章安排不明确仍uncertain。缺少本章原情节时不能猜测没有本章义务。",
            "输出边界": "按提供的FIX编号原顺序每项一次，不输出、改写或新增condition，不生成正文，不回改计划。trigger_clause只引用该项条件原话的连续相关前提，evidence只引用本次正文；两种来源不可互换。explanation解释触发与本章义务的最终判断，未知保持，不借措辞制造已兑现或预期结论。",
        },
        Applicability,
        {"scope": "独立观察仅本次正文、指定全书原条件及本章原安排；不含旧审稿、候选判断或预期答案"},
        validate,
        contract=contract,
        diagnostic_label="全书条件独立适用性观察",
    )
    return {**bound, "checks": observed["checks"]}
