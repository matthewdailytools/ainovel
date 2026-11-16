"""New-row-only AI plan classification and independent full meaning preservation."""

from copy import deepcopy

from pydantic import Field

from .db import DomainError
from . import workflow_plan_recipient_scope as scope
from .authored_source_classification import (
    GUIDANCE as SHARED_GUIDANCE,
    ClassificationCheck as SourceCheck,
    ItemCheck,
    SourceCheck as FixedCheck,
    ConstraintCheck,
)
from .models import Input

VERSION = scope.NEW_VERSION
ACTION = "独立核对AI章纲分类与完整规划原意保持"
RECORDS = "scope_classification_preservations"

GUIDANCE = SHARED_GUIDANCE + (
    "本章纲是AI规划，不是作者新增原话或正式已发生事实；completed按本章要求完成的动作判断，"
    "planned只指本章表达未来安排，不因源于AI规划就将全部动作写成未来。"
    "普通执行参与者或物品尚未命名、但原话没有范围限制，不等于真正范围归属歧义；保留原描述或空槽，"
    "不因仅未命名就造范围unknown或要求作者补剧情。观察阶段不选择，后续仍由既有已授权的开放细节流程有限选择，"
    "真实排他/许可/禁止附着哪次传递仍不明时继续unknown，不借开放选择清掉原限制。"
)


class Preservation(Input):
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_checks: list[SourceCheck]
    effective_checks: list[SourceCheck]
    item_checks: list[ItemCheck]
    fixed_checks: list[FixedCheck]
    constraints_check: ConstraintCheck
    summary: str = Field(max_length=2000)


async def observe(worker, source):
    class SourceOnly:
        async def grounded(self, instruction, schema, context, *args, **kwargs):
            instruction = {**instruction, "完整范围分类边界": GUIDANCE}
            kwargs["repair_feedback"] = lambda: {
                "引用修正要求": "本阶段只有本章AI工作章纲及SCOPE编号，无P卡、正文、候选或旧观察。按完整原句核分类；非范围事实和剧情保留未知不造范围unknown，真正范围歧义仍unknown，不删限制或补剧情。"
            }
            return await worker.grounded(instruction, schema, context, *args, **kwargs)

    return await scope.observe_legacy(SourceOnly(), source, version=VERSION)


def basis(worker, row, plot, contract):
    from .workflow_plan_preparation import constraints, sha

    inventory = row["phases"][f"recipient_scope:{row['round']}"]["response"]
    fixed = list(worker.task["payload"]["fixed"])
    fixed.extend(value for value in plot["fixed"] if value not in fixed)
    return {
        "version": VERSION,
        "round": row["round"],
        "original_source": [
            {**unit, "source_id": f"ORIGINAL{i:04}"}
            for i, unit in enumerate(scope.units(row["original_plot"]), 1)
        ],
        "effective_source": [
            {**unit, "source_id": f"EFFECTIVE{i:04}"}
            for i, unit in enumerate(scope.units(row["effective_plot"]), 1)
        ],
        "inventory_sha256": sha(inventory),
        "contract": deepcopy(contract),
        "fixed": [{"source_id": f"FIX{i:03}", "text": value} for i, value in enumerate(fixed, 1)],
        "constraints": constraints(worker, plot),
    }


def issues(value, current):
    from .workflow_plan_preparation import sha

    errors = []
    if value["input_sha256"] != sha(current):
        errors.append("独立保持须绑定完整原始/有效章纲、原观察、全部P、固定条件与规划约定。")
    sources = {
        "original_checks": [u["source_id"] for u in current["original_source"]],
        "effective_checks": [u["source_id"] for u in current["effective_source"]],
        "fixed_checks": [u["source_id"] for u in current["fixed"]],
    }
    for key, expected in sources.items():
        if [r["source_id"] for r in value[key]] != expected:
            errors.append(f"{key}须按全部连续原句/固定项编号原序各一次，not_scope_related也不得省略。")
        for item in value[key]:
            ids = item["evidence_ids"]
            if len(ids) != len(set(ids)) or any(i not in expected for i in ids) or not item["reason"].strip():
                errors.append("各类来源检查只引用本类连续原文编号，不重复、不借其他条件。")
            if (item["result"] == "pass" or item.get("classification_result") == "pass") and item[
                "source_id"
            ] not in ids:
                errors.append("分类/原意或固定项通过须引用本项自身来源，真正未知可留空。")
    expected = [f"P{i:02}" for i in range(1, len(current["contract"]["items"]) + 1)]
    if [item["item_id"] for item in value["item_checks"]] != expected:
        errors.append("全部P必须按原序各核一次，不只核带范围O的事项。")
    choices = set(sources["original_checks"] + sources["effective_checks"])
    for item in value["item_checks"]:
        ids = item["evidence_ids"]
        if (
            len(ids) != len(set(ids))
            or any(i not in choices for i in ids)
            or not item["reason"].strip()
            or (item["result"] == "pass" and not ids)
        ):
            errors.append("P原意通过须回到本次原始/有效章纲，不能用固定项或AI观察当来源真值。")
    if not value["constraints_check"]["reason"].strip():
        errors.append("全规划与采用约定保持须说明依据，不能空白通过。")
    return errors


def passed(value):
    return (
        all(
            item["result"] == "pass"
            for key in ("original_checks", "effective_checks", "item_checks", "fixed_checks")
            for item in value[key]
        )
        and all(
            item["classification_result"] == "pass"
            for key in ("original_checks", "effective_checks")
            for item in value[key]
        )
        and value["constraints_check"]["result"] == "pass"
    )


def validate(worker, row, plot, contract):
    from .workflow_plan_preparation import lineage, sha

    key = f"scope_preserve:{row['round']}"
    records = row.get(RECORDS, [])
    slot = row.get("phases", {}).get(key, {})
    try:
        latest = records[-1]
        current = basis(worker, row, plot, contract)
        result = Preservation.model_validate(slot["response"]).model_dump()
        valid = (
            latest["version"] == VERSION
            and latest["round"] == row["round"]
            and latest["basis"] == current
            and latest["input_sha256"] == sha(current)
            and latest["result"] == result
            and slot["status"] == "completed"
            and slot["owner_task_id"] in lineage(worker)
            and slot["response_sha256"] == sha(result)
            and not issues(result, current)
            and passed(result)
        )
    except KeyError, IndexError, TypeError, ValueError:
        valid = False
    if not valid:
        raise DomainError("plan_preparation_changed", "新版AI章纲分类与独立完整原意保持未就绪或已改变。", 409)


async def check(worker, row, plot, contract):
    from .workflow_plan_preparation import phase, sha

    current = basis(worker, row, plot, contract)
    value = await phase(
        worker,
        row,
        f"scope_preserve:{row['round']}",
        "checking",
        lambda: worker.grounded(
            {
                "动作": ACTION,
                "接收范围核对执行版本": VERSION,
                "input_sha256": sha(current),
                "原始AI本章": row["original_plot"],
                "有效AI本章": row["effective_plot"],
                "全部原始连续单元": current["original_source"],
                "全部有效连续单元": current["effective_source"],
                "先行分类（AI提案非真值）": row["phases"][f"recipient_scope:{row['round']}"]["response"],
                "全部本轮P": [{"item_id": f"P{i:02}", **item} for i, item in enumerate(contract["items"], 1)],
                "全部固定项": current["fixed"],
                "完整原规划与采用约定": current["constraints"],
                "分类边界": GUIDANCE,
                "要求": "独立回到原始AI本章、全部原规划和有效章纲，逐原句核分类与完整原意，全部P和固定项各核一次，不因not_scope_related略去剧情。source检查给classification_result及result；引用自己的原句编号，P只引原始/有效章纲，FIX只引自身编号。原交易失败/未交付、认知未知、请求与许可/内容、归还条件、角色/物品/实例/原件副本、否定、阶段时点、真限制必须保留；不得凭先行分类/P或附注自称保持就pass。无源改变fail、无法确认unknown；未来终局未到只核解释仍保留，不提前兑现。附注仅原事项开放细节，不改变作者约定或原明确事项。没有本章正文、候选、旧正文结论或自由原因；AI章纲不是作者原话或已发生事实，不是文学批准。",
            },
            Preservation,
            {"scope": "仅原始/有效AI章纲、全规划、P及固定/采用约定，无本章正文或候选"},
            lambda result: issues(result, current),
            repair_feedback=lambda: {
                "引用修正要求": "本步骤无本章正文或候选，只核全部ORIGINAL/EFFECTIVE/P/FIX和原规划；按原顺序完整覆盖，真实unknown/fail不得为引用修复改pass。"
            },
        ),
    )
    saved = {
        "version": VERSION,
        "round": row["round"],
        "input_sha256": sha(current),
        "basis": current,
        "result": deepcopy(value),
    }
    records = row.setdefault(RECORDS, [])
    previous = next((r for r in records if r["round"] == row["round"]), None)
    if previous is not None and previous != saved:
        raise DomainError("plan_preparation_changed", "新版完整保持回执已改变，不覆盖旧结果。", 409)
    if previous is None:
        records.append(saved)
        worker.checkpoint(worker.task["result"])
    return passed(value)
