"""Freeze executable AI work plans before prose, never adapting them to a draft."""

from copy import deepcopy
import hashlib
from typing import Literal

from pydantic import Field

from .db import DomainError, dumps
from .models import Input
from .plot_audit import VERSION as PLOT_VERSION, ScopedAuthorPlot, interpret_plot

VERSION = "workflow-plan-preparation/1"
MAX_ROUNDS = 2
CHOICE_ACTION = "写前明确AI工作章纲开放细节"
CHECK_ACTION = "独立核对AI工作章纲保持"
# These local adapter/budget errors occur before reservation or HTTP dispatch.
# The additional unchanged-ledger/steps proof excludes a later repair failing
# after an earlier response in the same grounded phase was already charged.
NOT_SENT_CODES = {"budget", "unpriced", "configuration", "context_limit", "schema_unsupported"}


def sha(value):
    return hashlib.sha256((value if isinstance(value, str) else dumps(value)).encode()).hexdigest()


class PlanChoice(Input):
    addendum: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=2000)


class ItemPreservation(Input):
    item_id: str = Field(min_length=1, max_length=20)
    prepared_item_id: str = Field(min_length=1, max_length=20)
    result: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1, max_length=2000)


class ConstraintPreservation(Input):
    result: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1, max_length=2000)


class PlanPreservation(Input):
    item_checks: list[ItemPreservation] = Field(min_length=1, max_length=12)
    constraints_check: ConstraintPreservation
    summary: str = Field(min_length=1, max_length=2000)


def enabled(worker):
    if "workflow_plan_preparation_version" not in worker.task["result"]:
        return False
    marker = worker.task["result"]["workflow_plan_preparation_version"]
    if (
        marker != VERSION
        or worker.task["kind"] != "workflow"
        or worker.task["payload"].get("mode") != "automatic"
        or worker.task["payload"].get("plot_contract_check") is not True
        or worker.task["result"].get("plot_contract_version") != PLOT_VERSION
    ):
        raise DomainError("plan_preparation_version", "AI工作章纲准备政策无法确认，未派发或升级旧任务。", 409)
    return True


def indexed(contract):
    return [{"id": f"P{i:02}", **item} for i, item in enumerate(contract["items"], 1)]


def preservation_issues(value, initial, prepared):
    """No lost, merged, duplicated or newly substituted baseline item can pass."""
    expected = [item["id"] for item in indexed(initial)]
    actual = [item["id"] for item in indexed(prepared)]
    rows = value["item_checks"]
    if (
        not expected
        or actual != expected
        or [row["item_id"] for row in rows] != expected
        or [row["prepared_item_id"] for row in rows] != expected
    ):
        return ["初轮每个P事项及本轮对应事项须按原顺序各核对恰好一次，不能删项、合并或新增替代项。"]
    if any(not row["reason"].strip() for row in rows) or not value["constraints_check"]["reason"].strip():
        return ["逐项保持及作者条件核对均须说明来源和理由，无法确认标unknown。"]
    return []


def preserved(value):
    return value["constraints_check"]["result"] == "pass" and all(
        row["result"] == "pass" for row in value["item_checks"]
    )


def bindings(worker, plot, chapter):
    return {
        "version": VERSION,
        "chapter": plot["number"],
        "chapter_id": chapter["id"],
        "original_plot": plot["plot"],
        "original_plot_sha256": sha(plot["plot"]),
        "original_chapter_sha256": sha(plot),
        "plan_sha256": sha(worker.task["result"]["plan"]),
        "constraints_sha256": sha(constraints(worker, plot)),
    }


def constraints(worker, plot):
    payload = worker.task["payload"]
    return {
        "作者简报": payload["brief"],
        "作者固定方向": payload["fixed"],
        "本章原固定条件": plot["fixed"],
        "采用创作约定": deepcopy(worker.author_snapshot()),
        "原始全批规划": deepcopy(worker.task["result"]["plan"]),
        "运行范围": payload.get("completion_scope", "whole_book"),
    }


def check_receipt(worker, row, plot, chapter):
    expected = bindings(worker, plot, chapter)
    if (
        chapter["position"] != plot["number"]
        or chapter["archived"]
        or any(row.get(key) != value for key, value in expected.items())
    ):
        raise DomainError(
            "plan_preparation_changed", "写前准备的原章纲、固定条件或目标已改变，未重选细节。", 409
        )
    if type(row.get("round")) is not int or not 0 <= row["round"] <= MAX_ROUNDS:
        raise DomainError("plan_preparation_changed", "章纲准备轮次无法核实，未重置。", 409)
    text = plot["plot"]
    for i, choice in enumerate(row["choices"], 1):
        if choice["round"] != i or choice.get("source_sha256") != sha(text):
            raise DomainError("plan_preparation_changed", "章纲准备轮次记录不连续。", 409)
        text += f"\n\n【AI开放细节附注·第{i}轮；非作者新增锁定】\n{choice['addendum']}"
        if choice.get("effective_plot_sha256") != sha(text):
            raise DomainError("plan_preparation_changed", "章纲附注版本无法核实。", 409)
    if row.get("effective_plot") != text or row.get("effective_plot_sha256") != sha(text):
        raise DomainError("plan_preparation_changed", "有效章纲与保留附注不一致，未反向适配正文。", 409)


def lineage(worker):
    ids, current = set(), worker.task
    while current["id"] not in ids:
        if current["branch_id"] != worker.task["branch_id"] or current["kind"] != "workflow":
            break
        ids.add(current["id"])
        previous = current["result"].get("continued_from")
        if not previous:
            return ids
        current = worker.domain.task(previous)
    raise DomainError("workflow_progress", "写前准备的续跑来源无法确认。", 409)


async def phase(worker, row, key, state, invoke):
    """A resumed dispatch can read exact paid cache entries, never issue a new call."""
    active(worker)
    slots = row.setdefault("phases", {})
    slot = slots.get(key)
    history = []
    if slot is not None:
        if slot["owner_task_id"] not in lineage(worker):
            raise DomainError("workflow_progress", "写前准备步骤不属于本次续跑来源。", 409)
        if "response" in slot:
            if slot.get("response_sha256") != sha(slot["response"]):
                raise DomainError("plan_preparation_changed", "写前准备步骤结果已改变，未复用。", 409)
            return deepcopy(slot["response"])
        if slot["status"] == "not_sent" and (
            slot["owner_task_id"] != worker.task["id"] or slot["attempt"] != worker.attempt
        ):
            history = [
                *slot.get("not_sent_history", []),
                {
                    "owner_task_id": slot["owner_task_id"],
                    "attempt": slot["attempt"],
                    "code": slot["not_sent_code"],
                },
            ]
            slot = None
        else:
            worker._workflow_plan_replay = slot["owner_task_id"]
    if slot is None:
        slots[key] = slot = {
            "owner_task_id": worker.task["id"],
            "attempt": worker.attempt,
            "status": "started",
            "not_sent_history": history,
            "before_steps": steps(worker),
            "before_calls": calls(worker),
        }
        row["status"] = state
        worker.checkpoint(worker.task["result"])
    try:
        response = await invoke()
    except DomainError as error:
        if (
            error.code in NOT_SENT_CODES
            and slot["owner_task_id"] == worker.task["id"]
            and slot["before_steps"] == steps(worker)
            and slot["before_calls"] == calls(worker)
        ):
            slot.update(status="not_sent", not_sent_code=error.code)
            worker.checkpoint(worker.task["result"])
        raise
    finally:
        worker.__dict__.pop("_workflow_plan_replay", None)
    slot.update(status="completed", response=deepcopy(response), response_sha256=sha(response))
    worker.checkpoint(worker.task["result"])
    return response


def steps(worker):
    return sorted(
        r["fingerprint"]
        for r in worker.db.all("SELECT fingerprint FROM task_steps WHERE task_id=?", (worker.task["id"],))
    )


def calls(worker):
    with worker.budget.db() as db:
        return sorted(
            r["id"] for r in db.execute("SELECT id FROM calls WHERE task_id=?", (worker.task["id"],))
        )


def active(worker):
    if worker.cancelled():
        raise DomainError("cancelled", "写前准备任务已停止，未派发后续调用。", 409)
    if worker.domain.task(worker.task["id"])["pause"]:
        raise DomainError("plan_preparation_paused", "写前准备已在步骤间暂停。", 409)
    expected = worker.task["result"].get("expected_story_revision")
    if expected is not None and worker.domain.branch(worker.task["branch_id"])["story_revision"] != expected:
        raise DomainError("story_changed", "前文在写前准备期间改变，未继续生成。", 409)


def stop(worker, row, reason):
    row.update(status="needs_review", reason=reason)
    worker.checkpoint(worker.task["result"], "needs_review", reason)
    return None


def frozen_contract(worker, author_text, number, supplied):
    """Only the server's pre-prose receipt may skip reinterpreting a work plan."""
    if not enabled(worker):
        raise DomainError("plan_preparation_version", "旧任务未冻结写前解释，不能补盖准备记录。", 409)
    row = worker.task["result"].get("plot_preparations", {}).get(str(number))
    plot = next((p for p in worker.task["result"]["plan"]["chapters"] if p["number"] == number), None)
    if row is None or plot is None:
        raise DomainError("plan_preparation_changed", "本章写前准备记录缺失。", 409)
    chapter = worker.domain.chapter(row["chapter_id"], worker.task["branch_id"])
    check_receipt(worker, row, plot, chapter)
    contract = ScopedAuthorPlot.model_validate(supplied).model_dump()
    if (
        row["status"] != "ready"
        or row.get("prepared_contract") != contract
        or row.get("prepared_contract_sha256") != sha(contract)
        or author_text != row["effective_plot"]
        or not contract["items"]
        or any(item["certainty"] != "explicit" for item in contract["items"])
        or not row["preservation_checks"]
        or row["interpretations"][-1]["round"] != row["round"]
        or row["interpretations"][-1]["source_sha256"] != row["effective_plot_sha256"]
        or row["interpretations"][-1]["contract"] != contract
        or row["preservation_checks"][-1]["round"] != row["round"]
        or row["preservation_checks"][-1]["source_sha256"] != row["effective_plot_sha256"]
        or not preserved(row["preservation_checks"][-1]["result"])
        or preservation_issues(
            row["preservation_checks"][-1]["result"], row["interpretations"][0]["contract"], contract
        )
    ):
        raise DomainError("plan_preparation_changed", "写前冻结解释、全项保持核对或有效章纲无法确认。", 409)
    return deepcopy(contract)


async def prepare(worker, plot, chapter):
    if not enabled(worker):
        return plot, None
    result = worker.task["result"]
    rows = result.setdefault("plot_preparations", {})
    row = rows.get(str(plot["number"]))
    if row is None:
        if chapter["content"].strip() or worker.db.all(
            "SELECT id FROM artifacts WHERE chapter_id=? AND kind='chapter_draft' LIMIT 1", (chapter["id"],)
        ):
            raise DomainError(
                "plan_preparation_late", "本章已有正文或候选，不能事后选择章纲细节制造一致。", 409
            )
        row = rows[str(plot["number"])] = {
            **bindings(worker, plot, chapter),
            "status": "interpreting",
            "round": 0,
            "effective_plot": plot["plot"],
            "effective_plot_sha256": sha(plot["plot"]),
            "interpretations": [],
            "choices": [],
            "preservation_checks": [],
            "reason": "",
        }
        worker.checkpoint(result)
    check_receipt(worker, row, plot, chapter)
    if row["status"] == "ready":
        contract = frozen_contract(worker, row["effective_plot"], plot["number"], row["prepared_contract"])
        return {**plot, "plot": row["effective_plot"]}, contract
    if row["status"] == "needs_review":
        return stop(worker, row, row["reason"])
    fixed = constraints(worker, plot)
    try:
        while True:
            round_number = row["round"]
            initial = row["interpretations"][0]["contract"] if row["interpretations"] else None
            contract = await phase(
                worker,
                row,
                f"interpret:{round_number}",
                "interpreting",
                lambda: interpret_plot(
                    worker,
                    row["effective_plot"],
                    source_kind="workflow_plan",
                    preparation={
                        "version": VERSION,
                        "round": round_number,
                        **(
                            {"item_count": len(initial["items"]), "原始事项": indexed(initial)}
                            if initial is not None
                            else {}
                        ),
                    },
                ),
            )
            if not any(r["round"] == round_number for r in row["interpretations"]):
                row["interpretations"].append(
                    {
                        "round": round_number,
                        "source_sha256": row["effective_plot_sha256"],
                        "contract": deepcopy(contract),
                    }
                )
                worker.checkpoint(result)
            initial = row["interpretations"][0]["contract"]
            if not initial["items"] or len(contract["items"]) != len(initial["items"]):
                return stop(worker, row, "写前解释未完整覆盖原始事项，未生成正文；请核对保留记录。")
            check_schema = PlanPreservation.model_json_schema()
            ids = [p["id"] for p in indexed(initial)]
            check_schema["properties"]["item_checks"].update(minItems=len(ids), maxItems=len(ids))
            for key in ("item_id", "prepared_item_id"):
                check_schema["$defs"]["ItemPreservation"]["properties"][key]["enum"] = ids
            check = await phase(
                worker,
                row,
                f"check:{round_number}",
                "checking",
                lambda: worker.grounded(
                    {
                        "动作": CHECK_ACTION,
                        "写前准备契约版本": VERSION,
                        "原始本章": plot,
                        "有效章纲": row["effective_plot"],
                        "初轮全部事项": indexed(initial),
                        "本轮全部事项": indexed(contract),
                        "不可更改的来源": fixed,
                        "要求": "独立核对每个初轮事项与同编号本轮事项的语义保持，含原文中尚未展开的事项，不得靠删项、合并、换义消除歧义。原明确事件、否定、尝试/完成/未来阶段、只/仅接收范围和时点限制必须保持。剧情明确保留未知时不得提前解谜；附注只可细化本事项开放细节，不新增脱离原计划事件。逐项按原P编号顺序各一次，不符fail、无法判断unknown；constraints_check独立核对作者简报/固定条件/采用约定和完整原规划前后衔接，不能因附注自称保持就pass。这里没有正文，不得从候选反推章纲。",
                    },
                    PlanPreservation,
                    {"scope": "写前独立保持核对，仅原规划、固定条件和解释，无正文或候选"},
                    lambda value: preservation_issues(value, initial, contract),
                    contract=check_schema,
                ),
            )
            if not any(r["round"] == round_number for r in row["preservation_checks"]):
                row["preservation_checks"].append(
                    {
                        "round": round_number,
                        "source_sha256": row["effective_plot_sha256"],
                        "result": deepcopy(check),
                    }
                )
                worker.checkpoint(result)
            if not preserved(check):
                return stop(worker, row, "写前保持核对存在不符或未知，未生成正文；原规划与每轮记录保留。")
            active(worker)
            if all(item["certainty"] == "explicit" for item in contract["items"]):
                row.update(
                    status="ready",
                    prepared_contract=deepcopy(contract),
                    prepared_contract_sha256=sha(contract),
                    reason="写前执行安排与全项保持核对已冻结；不表示正文或文学质量通过。",
                )
                worker.checkpoint(result)
                return {**plot, "plot": row["effective_plot"]}, deepcopy(contract)
            if round_number >= MAX_ROUNDS:
                return stop(worker, row, "两轮开放细节选择后仍有执行歧义，未生成正文；请核对保留记录。")
            next_round = round_number + 1
            choice = await phase(
                worker,
                row,
                f"choice:{next_round}",
                "clarifying",
                lambda: worker.call(
                    {
                        "动作": CHOICE_ACTION,
                        "写前准备契约版本": VERSION,
                        "原始本章": plot,
                        "当前有效章纲": row["effective_plot"],
                        "执行解释": indexed(contract),
                        "不可更改的来源": fixed,
                        "要求": "只为本轮ambiguous事项选择尚未限定的可执行细节，输出追加附注，不重写原文。原明确事件、阶段、否定、只/仅范围和全部作者条件不得改变；明确保留的剧情未知不能解谜，未来章安排不能提前兑现。不新增脱离原事项的事件。附注是AI工作安排，非作者新锁定或事实；不读取正文或候选。",
                    },
                    PlanChoice,
                    {"scope": "只为未写正文的本章AI开放细节选择，未读取正文或候选"},
                    4096,
                ),
            )
            choice_source = row["effective_plot_sha256"]
            row["effective_plot"] += (
                f"\n\n【AI开放细节附注·第{next_round}轮；非作者新增锁定】\n{choice['addendum']}"
            )
            row.update(round=next_round, effective_plot_sha256=sha(row["effective_plot"]))
            row["choices"].append(
                {
                    "round": next_round,
                    "source_sha256": choice_source,
                    "effective_plot_sha256": row["effective_plot_sha256"],
                    **choice,
                }
            )
            worker.checkpoint(result)
    except DomainError as error:
        if error.code == "plan_preparation_unknown":
            return stop(worker, row, str(error))
        if error.code == "plan_preparation_paused":
            worker.checkpoint(result, "paused", error.message)
            return None
        raise
