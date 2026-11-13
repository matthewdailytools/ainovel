"""Separate author-only interpretation from evidence-grounded prose comparison."""

from typing import Literal

from pydantic import Field

from .evidence import resolve_field, source_schema, source_units
from .models import Input as StrictModel


class PlotItem(StrictModel):
    author_evidence: str = Field(max_length=800)
    actor: str = Field(max_length=200)
    action: str = Field(max_length=200)
    object: str = Field(max_length=200)
    direction: str = Field(max_length=400)
    result: str = Field(max_length=400)
    certainty: Literal["explicit", "ambiguous"]


class AuthorPlot(StrictModel):
    items: list[PlotItem] = Field(max_length=12)
    note: str = Field(max_length=2000)


class PlotCheck(StrictModel):
    item_id: str = Field(min_length=1, max_length=20)
    result: Literal["pass", "fail", "unknown"]
    observed: str = Field(max_length=800)
    evidence: str = Field(max_length=800)
    reason: str = Field(max_length=1000)
    repair: str = Field(max_length=1000)


class PlotAudit(StrictModel):
    checks: list[PlotCheck] = Field(max_length=12)
    summary: str = Field(max_length=2000)


async def audit_plot(worker, author_text, text, number):
    if not author_text.strip():
        return {
            "items": [],
            "checks": [],
            "findings": [],
            "status": "unverified",
            "summary": "缺少作者情节，未调用模型核对。",
            "interpretation_note": "不能从候选反推作者要求。",
            "policy": "作者情节可为空，但无法据此判断忠实性；正文未改变。",
        }
    author_units = source_units(author_text, "AUTHOR")
    author_sources = {unit["source_id"]: unit for unit in author_units}
    author_contract = AuthorPlot.model_json_schema()
    author_contract["$defs"]["PlotItem"]["properties"]["author_evidence"]["enum"] = list(author_sources)

    def validate_author(value):
        issues = []
        for item in value["items"]:
            resolve_field(item, "author_evidence", author_sources)
            if not item["author_evidence"] or item["author_evidence"] not in author_text:
                issues.append("每项解释必须引用作者原话，不能用候选正文或参考材料佐证。")
            if not any(item[field].strip() for field in ("action", "object", "direction", "result")):
                issues.append("解释须包含原话支持的事件内容，不能仅凭空字段宣称覆盖情节。")
        return issues

    # No candidate, reference excerpt, application plan or old review reaches this call.
    contract = await worker.grounded(
        {
            "动作": "仅依据作者原话整理本场景情节",
            "作者原话": author_text,
            "可引用原文": author_units,
            "要求": "只整理原话明确给出的事件、行动者、动作、对象、给出与取得方向和结果。每项引用AUTHOR编号；未给出的字段留空。方向可写成谁向谁给出或取得什么。不能把愿望改成已发生结果；有多种解读标ambiguous并保留歧义。这里只是AI解释提案，不是作者新增锁定项或本书事实。",
        },
        AuthorPlot,
        {"scope": "仅作者原话，尚未读取候选"},
        validate_author,
        contract=author_contract,
    )
    items = [{"id": f"P{index:02}", **item} for index, item in enumerate(contract["items"], 1)]
    if not items:
        return {
            "items": [],
            "checks": [],
            "findings": [],
            "status": "unverified",
            "summary": "未整理出可逐项核对的情节，不能视为通过。",
            "interpretation_note": contract["note"],
            "policy": "AI解释未形成作者新条件，正文未改变。",
        }
    units = source_units(text, f"PLOT{number:03}P")
    sources = {unit["source_id"]: unit for unit in units}
    comparison_contract = source_schema(PlotAudit.model_json_schema(), units)
    comparison_contract["properties"]["checks"].update(minItems=len(items), maxItems=len(items))
    comparison_contract["$defs"]["PlotCheck"]["properties"]["item_id"]["enum"] = [
        item["id"] for item in items
    ]

    def validate_checks(value):
        issues = []
        if sorted(row["item_id"] for row in value["checks"]) != sorted(item["id"] for item in items):
            issues.append("每项情节解释必须恰好核对一次，不得漏项或重复。")
        for row in value["checks"]:
            resolve_field(row, "evidence", sources)
            if row["evidence"] and row["evidence"] not in text:
                issues.append("正文证据必须来自本次候选连续原文。")
            if row["result"] != "unknown" and not row["evidence"]:
                issues.append("通过或不符均须有正文依据，不能定位时标unknown。")
            if not row["reason"].strip() or (row["result"] != "unknown" and not row["observed"].strip()):
                issues.append("须说明理由；通过或不符还须写出本稿实际发生了什么。")
        return issues

    compared = await worker.grounded(
        {
            "动作": "逐项对照作者情节解释与本次正文",
            "作者原话": author_text,
            "独立整理的解释提案": items,
            "本次候选": text,
            "可引用原文": units,
            "要求": "先写本稿实际发生了什么，再逐项比较行动者、对象、流向及结果；不要用候选重新解释作者原话来制造一致。即使交易成败相同，对象或流向反转也应指出。歧义或证据不足标unknown，不能把AI解释升级成作者新条件；偏好不判fail。只能引用PLOT原文编号，不能拿AUTHOR证据作正文证据。",
        },
        PlotAudit,
        {"scope": "情节对照，未修改或采用正文"},
        validate_checks,
        contract=comparison_contract,
    )
    indexed = {item["id"]: item for item in items}
    findings = []
    for row in compared["checks"]:
        item = indexed[row["item_id"]]
        if row["result"] == "fail" and row["evidence"]:
            findings.append(
                {
                    "severity": "blocker" if item["certainty"] == "explicit" else "suggestion",
                    "chapter": number,
                    "quote": row["evidence"],
                    "reason": f"作者原话：{item['author_evidence']}；AI解释（非新增条件）：{item['actor']}／{item['action']}／{item['object']}／{item['direction']}／{item['result']}。本稿观察：{row['observed']}。{row['reason']}",
                    "repair": row["repair"],
                }
            )
    return {
        "items": items,
        **compared,
        "interpretation_note": contract["note"],
        "status": "failed"
        if any(finding["severity"] == "blocker" for finding in findings)
        else "unverified"
        if not items
        or any(item["certainty"] == "ambiguous" for item in items)
        or any(row["result"] == "unknown" for row in compared["checks"])
        else "model_pass",
        "findings": findings,
        "policy": "解释来自作者原话，仍须作者核对；不写入故事事实，不自动采用候选。",
    }
