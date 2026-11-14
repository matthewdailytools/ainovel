"""Turn selected, version-bound review findings into unaccepted revision candidates."""

from collections.abc import Mapping
from copy import deepcopy
import difflib
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .db import DomainError
from .domain import char_count
from .evidence import resolve_field, source_units
from .text_context import text_context


def _repair_units(text, findings):
    """Whitelist only uniquely located issue quotations; never widen to the chapter."""
    units, seen = [], set()
    for finding in findings:
        quote = finding.get("quote")
        if (
            not isinstance(quote, str)
            or not quote.strip()
            or len(quote) > 800
            or quote.strip() == text.strip()
            or text.find(quote) < 0
            or text.find(quote) != text.rfind(quote)
        ):
            raise DomainError(
                "revision_scope",
                "问题引文须唯一定位到不超过800字的局部片段，不能覆盖整章；请重新审稿并缩小引文范围。",
                409,
            )
        if quote in seen:
            continue
        seen.add(quote)
        start = len(text[: text.index(quote)].encode("utf-16-le")) // 2
        units.append(
            {
                "source_id": f"EDIT{len(units) + 1:04}",
                "text": quote,
                "start": start,
                "end": start + len(quote.encode("utf-16-le")) // 2,
            }
        )
    if not units:
        raise DomainError("revision_scope", "缺少可定位的局部问题片段，未回退到整章。", 409)
    return units


class TargetedRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_review_id: str = Field(min_length=1, max_length=128)
    finding_indices: list[Annotated[int, Field(ge=0)]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_findings(self):
        if len(set(self.finding_indices)) != len(self.finding_indices):
            raise ValueError("同一问题不能重复选择。")
        return self


class RecheckedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_index: int = Field(ge=0)
    result: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1, max_length=500)
    evidence: str = Field(description="优先填本次原文source_id，后端回填；未知可为空。")


class TargetedRecheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[RecheckedFinding] = Field(min_length=1, max_length=20)


def validate_targeted_revision(domain, request: Mapping[str, Any], context: Mapping[str, Any]):
    """Validate before queuing and again before every execution stage; no model call."""
    try:
        selection = TargetedRevisionInput.model_validate(
            {
                "source_review_id": request.get("source_review_id"),
                "finding_indices": request.get("finding_indices"),
            }
        )
    except ValidationError as error:
        raise DomainError("revision_selection", "请选择1—20项不重复的审稿问题及其来源报告。") from error
    if not request.get("chapter_id") or request.get("base_revision") is None:
        raise DomainError("chapter_required", "定向修订需要已保存的目标章节及基线修订。")
    chapter = domain.chapter(request["chapter_id"], request["branch_id"])
    branch = domain.branch(request["branch_id"], request["novel_id"])
    source = domain.artifact(selection.source_review_id)
    if source["kind"] != "review":
        raise DomainError("revision_source", "当前只支持已保存正文的独立审稿报告；请先对当前正文重新审稿。")
    if source["status"] == "rejected":
        raise DomainError("revision_source", "该审稿报告已拒绝，请重新审阅当前正文后再选择问题。", 409)
    if (source["novel_id"], source["branch_id"], source["chapter_id"]) != (
        request["novel_id"],
        request["branch_id"],
        chapter["id"],
    ):
        raise DomainError("ownership", "审稿报告不属于当前作品、工作版本或目标章节。", 403)
    if chapter["revision"] != request["base_revision"] or source["base_revision"] != chapter["revision"]:
        raise DomainError("review_stale", "正文已改变，来源审稿报告已过期；请对当前保存稿重新审稿。", 409)
    if source["basis"].get("story_revision") != branch["story_revision"]:
        raise DomainError("review_stale", "审稿所依据的故事版本已改变或无法核实，请重新审稿。", 409)
    if context.get("branch", {}).get("id") != branch["id"]:
        raise DomainError("ownership", "本次上下文与目标工作版本不一致。", 403)
    if not chapter["content"].strip():
        raise DomainError("empty_revision", "空白正文不能执行问题定向修订。")
    findings = source["payload"].get("findings")
    if not isinstance(findings, list):
        raise DomainError("revision_source", "来源报告没有可定位的问题列表，请重新审稿。")
    selected = []
    for index in selection.finding_indices:
        if index >= len(findings) or not isinstance(findings[index], dict):
            raise DomainError("revision_selection", "选中的问题编号不存在，请重新读取来源报告。", 409)
        finding = findings[index]
        if (
            finding.get("chapter") != chapter["position"]
            or finding.get("severity") not in {"blocker", "suggestion"}
            or not all(
                isinstance(finding.get(key), str) and finding[key].strip() for key in ("quote", "reason")
            )
            or not isinstance(finding.get("repair"), str)
        ):
            raise DomainError("revision_source", "选中问题缺少有效原文、理由、修法或章节定位，请重新审稿。")
        quote = finding["quote"]
        if chapter["content"].find(quote) < 0 or chapter["content"].find(quote) != chapter["content"].rfind(
            quote
        ):
            raise DomainError(
                "revision_evidence", "问题引文未唯一匹配当前原文，请重新审稿并提供明确位置。", 409
            )
        start = len(chapter["content"][: chapter["content"].index(quote)].encode("utf-16-le")) // 2
        selected.append(
            {
                **deepcopy(finding),
                "index": index,
                "start": start,
                "end": start + len(quote.encode("utf-16-le")) // 2,
                "issue_kind": "author_selected_preference"
                if finding["severity"] == "suggestion"
                else "reported_blocker",
            }
        )
    _repair_units(chapter["content"], selected)
    return {
        "source_review_id": source["id"],
        "base_revision": chapter["revision"],
        "story_revision": branch["story_revision"],
        "selected_findings": selected,
        "source_review_sha256": hashlib.sha256(
            json.dumps(source["payload"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "source_fixed": [v for v in source["payload"].get("fixed", []) if isinstance(v, str)],
    }


def _changes(before: str, after: str):
    original, updated = before.splitlines(keepends=True), after.splitlines(keepends=True)
    changes = []
    for operation, start, end, new_start, new_end in difflib.SequenceMatcher(
        a=original, b=updated, autojunk=False
    ).get_opcodes():
        if operation == "equal":
            continue
        prefix, quote = "".join(original[:start]), "".join(original[start:end])
        start16 = len(prefix.encode("utf-16-le")) // 2
        changes.append(
            {
                "start": start16,
                "end": start16 + len(quote.encode("utf-16-le")) // 2,
                "original": quote,
                "replacement": "".join(updated[new_start:new_end]),
            }
        )
    return changes


async def _recheck(worker, text, selection, context):
    units = source_units(text, "RECHECK")
    sources = {unit["source_id"]: unit for unit in units}
    expected = {finding["index"] for finding in selection["selected_findings"]}

    def validate(report):
        indices = [row["finding_index"] for row in report["results"]]
        issues = []
        if len(indices) != len(expected) or set(indices) != expected:
            issues.append("results必须对每个原问题index恰好复检一次，不能漏项、重复或追加。")
        for row in report["results"]:
            resolve_field(row, "evidence", sources)
            if row["evidence"] and (not row["evidence"].strip() or row["evidence"] not in text):
                issues.append("复检证据必须来自本次候选正文。")
            if row["result"] == "pass" and not row["evidence"].strip():
                issues.append("通过结论需要本次候选的原文依据；缺证据标unknown。")
        return issues

    return await worker.grounded(
        {
            "动作": "复检选中的原审稿问题",
            "action": "review.chapter",
            "原问题": selection["selected_findings"],
            "候选正文": text,
            "可引用原文": units,
            "要求": "只以本轮候选正文判断，原报告和旧引文只是待验证意见，不代表候选仍有该问题。按原问题index逐项输出pass/fail/unknown及理由与候选原文证据。删除旧引文或新审稿未再提及不自动等于解决。未通过时给出本轮候选中需要继续修订的唯一局部引文；不能定位则留空并标unknown，不复制整章。suggestion是作者本次选定的偏好目标，不变成客观事实错误；不得替作者宣布文学质量通过。不追加新问题，不要求未选建议全部实现。evidence优先只填source_id。",
        },
        TargetedRecheck,
        text_context(context, text, "selected_problem_recheck"),
        validate,
        4096,
    )


async def run_targeted_revision(worker, request: Mapping[str, Any], context: Mapping[str, Any]):
    selection = validate_targeted_revision(worker.domain, request, context)
    chapter = worker.domain.chapter(request["chapter_id"], request["branch_id"])
    original, text = chapter["content"], chapter["content"]
    fixed = list(dict.fromkeys([*selection["source_fixed"], *request.get("fixed", [])]))
    instructions = {
        "作者补充要求": request["instruction"],
        "固定条件": fixed,
        "表达边界": "除非选中问题或本次作者要求明确改变，沿用原稿的视角、人物声音与语气，不因默认写法预设改写其他内容。",
        "选中的原审稿问题": selection["selected_findings"],
        "范围": "只对选中问题作必要的局部修改，不追改未选偏好，不重写整章、不为凑字增加剧情；新增关键证据或能力只能作为待采纳提案。保留原定事件、结果、禁止项与人物认知边界。",
    }
    pending = selection["selected_findings"]
    rounds, review, results = [], None, []
    scope_unavailable = False
    for number in range(1, 3):
        current = validate_targeted_revision(worker.domain, request, context)
        if current != selection:
            raise DomainError("review_stale", "来源审稿报告已改变，停止派发后续修订。", 409)
        try:
            units = _repair_units(text, pending)
        except DomainError:
            if number == 1:
                raise
            scope_unavailable = True
            break
        margin = min(max(200, char_count(text) // 5), max(0, char_count(text) - 1))
        repair_review = {
            "checks": [],
            "findings": pending,
            "summary": "仅处理本次选中的问题；未选偏好不需要执行。",
        }
        before = text
        text = await worker.revise_chapter(
            text,
            instructions,
            repair_review,
            context,
            max(1, char_count(text) - margin),
            char_count(text) + margin,
            repair_units=units,
        )
        if not text.strip() or len(text) > 250_000:
            raise DomainError("revision_length", "局部修订结果为空或超出单章大小限制，未采纳。")
        validate_targeted_revision(worker.domain, request, context)
        review = await worker.review(text, fixed, context, chapter["position"], instructions)
        validate_targeted_revision(worker.domain, request, context)
        results = (await _recheck(worker, text, selection, context))["results"]
        rounds.append(
            {
                "number": number,
                "input_sha256": hashlib.sha256(before.encode()).hexdigest(),
                "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "changes": _changes(before, text),
                "repair_units": units,
                "review": review,
                "original_problem_recheck": results,
            }
        )
        if all(row["result"] == "pass" for row in results):
            break
        status_by_index = {row["finding_index"]: row for row in results}
        pending = [
            {
                **finding,
                "quote": status_by_index[finding["index"]]["evidence"],
                "reason": finding["reason"] + "；本轮复检：" + status_by_index[finding["index"]]["reason"],
            }
            for finding in selection["selected_findings"]
            if status_by_index[finding["index"]]["result"] != "pass"
        ]
    validate_targeted_revision(worker.domain, request, context)
    resolved = all(row["result"] == "pass" for row in results)
    passed = (
        resolved
        and review is not None
        and all(c["result"] == "pass" for c in review["checks"])
        and not any(f["severity"] == "blocker" for f in review["findings"])
    )
    return "chapter_draft", {
        "text": text,
        "fixed": fixed,
        "review": review,
        "review_passed": passed,
        "memory": {},
        "revision_trace": {
            "schema": "targeted-revision/1",
            **selection,
            "rounds": rounds,
            "rounds_used": len(rounds),
            "max_rounds": 2,
            "original_problem_recheck": results,
            "selected_problems_resolved": resolved,
            "stop_reason": "selected_resolved"
            if passed
            else "selected_resolved_other_checks_pending"
            if resolved
            else "repair_scope_unavailable"
            if scope_unavailable
            else "revision_limit",
            "source_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "candidate_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "adopted": False,
            "formal_creative_acceptance": "unverified",
            "note": "原问题逐项复检与本章审稿分别保存；偏好是否更好仍由作者决定，候选生成不改变正文或整书验收。",
        },
    }
