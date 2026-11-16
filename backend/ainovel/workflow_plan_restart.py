"""Explicit new-generation recovery for a known invalid pre-prose interpretation.

This never resumes an unknown dispatch and never edits an existing plan, failed
step or receipt. All three invalid replies must be complete and settled first.
"""

from copy import deepcopy
import json
from time import time
from typing import Literal

from pydantic import Field, StrictInt

from .conditional_continuation import lineage, receipt_budget
from .db import DomainError, dumps, uid
from .evidence import resolve_field, source_units
from .models import Input
from .plot_audit import ScopedAuthorPlot, OpenPlanInterpretation, author_contract_issues, open_plan_issues
from .workflow_plan_open_details import KEY, VERSION
from .workflow_plan_preparation import sha
from .workflow_plan_source_roles import (
    KEY as ROLE_KEY,
    VERSION as ROLE_VERSION,
    RepairState,
    validate_task as role_task,
    check_row as check_role_row,
)

RESTART_VERSION = "workflow-plan-restart/1"


class PlanRestartInput(Input):
    client_request_id: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    chapter: StrictInt = Field(ge=1, le=10000)
    base_story_revision: StrictInt = Field(ge=0)
    base_progress_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_plan_source_roles_version: Literal["workflow-plan-source-roles/1"] | None = None


def normalized(request):
    # No optional server field is inserted into an old nonce's frozen request.
    return {key: value for key, value in request.items() if key != ROLE_KEY or value is not None}


def previous(domain, parent, request, tx=None):
    request = normalized(request)
    sql = "SELECT t.id,t.result FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?"
    rows = (
        list(tx.execute(sql, (request["client_request_id"],)))
        if tx is not None
        else domain.db.all(sql, (request["client_request_id"],))
    )
    if not rows:
        return None
    row = rows[0]
    receipt = json.loads(row["result"]).get("plan_preparation_restart", {})
    if (
        receipt.get("version") != RESTART_VERSION
        or receipt.get("source_task_id") != parent
        or receipt.get("request") != request
    ):
        raise DomainError("idempotency", "该提交标识已用于不同的写前重做决定。", 409)
    child = domain.task(row["id"])
    if request.get(ROLE_KEY) == ROLE_VERSION:
        role_task(child, domain)
    return child


def checkpoint(domain, current, request):
    progress, payload = current["result"], current["payload"]
    new_roles = request.get(ROLE_KEY) == ROLE_VERSION
    if ROLE_KEY in request and request[ROLE_KEY] not in (None, ROLE_VERSION):
        raise DomainError("plan_source_roles_version", "无法识别本次写前重做解释版本。", 409)
    if new_roles:
        role_task(current, domain)
    if (
        current["kind"] != "workflow"
        or current["status"] not in ("blocked_runtime", "paused")
        or payload.get("mode") != "automatic"
        or payload.get("plot_contract_check") is not True
        or progress.get("plot_contract_version") != "author-plot-audit/2"
        or progress.get("workflow_plan_preparation_version") != "workflow-plan-preparation/1"
        or (progress.get(KEY) != VERSION if new_roles else KEY in progress)
        or any(
            progress.get(k)
            for k in (
                "waiting_artifact",
                "waiting_structure",
                "pending_structure",
                "pause_after_maintenance_requested",
            )
        )
        or not request["reason"].strip()
    ):
        raise DomainError(
            "plan_restart_checkpoint", "仅全自动已知失败的写前解释可显式新建一代；先处理已有正文和维护。", 409
        )
    branch = domain.branch(current["branch_id"], current["novel_id"])
    if (
        branch["story_revision"] != request["base_story_revision"]
        or progress.get("expected_story_revision") != branch["story_revision"]
        or sha(progress) != request["base_progress_sha256"]
    ):
        raise DomainError("story_changed", "故事或原写前检查点已改变，请重新读取；未创建任务。", 409)
    domain.check_genre_snapshot(current["branch_id"], progress.get("genre_profile_snapshot"))
    domain.check_author_contract_snapshot(current["branch_id"], progress.get("author_contract_snapshot"))
    number = request["chapter"]
    plan = progress.get("plan", {}).get("chapters", [])
    if progress.get("next_chapter") != number or [p.get("number") for p in plan] != list(
        range(1, payload["chapters"] + 1)
    ):
        raise DomainError("plan_restart_checkpoint", "须保留完整原规划并只重做当前未写章节。", 409)
    row = progress.get("plot_preparations", {}).get(str(number))
    from types import SimpleNamespace
    from . import workflow_plan_recipient_scope as recipient_scope

    scope_execution = row is not None and recipient_scope.enabled(
        SimpleNamespace(task=current, domain=domain, db=domain.db), row
    )
    allowed_phases = {"recipient_scope:0", "interpret:0"} if scope_execution else {"interpret:0"}
    if scope_execution:
        inventory_slot = row.get("phases", {}).get("recipient_scope:0", {})
        inventory = inventory_slot.get("response")
        if (
            inventory_slot.get("status") != "completed"
            or inventory is None
            or inventory_slot.get("response_sha256") != sha(inventory)
            or not recipient_scope.inventory_valid(inventory, row["original_plot"])
            or inventory["status"] != "supported"
        ):
            raise DomainError("unknown_result", "先行接收范围观察未完成或仍未知，不重发旧步骤。", 409)
    if (
        not row
        or row.get("round") != 0
        or row.get("status") != "interpreting"
        or row.get("interpretations")
        or row.get("choices")
        or row.get("preservation_checks")
        or set(row.get("phases", {})) != allowed_phases
    ):
        raise DomainError(
            "plan_restart_checkpoint", "仅重做尚无有效首轮解释的已知失败步骤；不清除选择或保持核对失败。", 409
        )
    if new_roles:
        check_role_row(current, row, domain)
    plot = next(p for p in plan if p["number"] == number)
    chapter = domain.chapter(row["chapter_id"], current["branch_id"])
    from .workflow_plan_preparation import bindings

    class Basis:
        task = current

        def author_snapshot(self):
            return progress["author_contract_snapshot"]

    if (
        chapter["position"] != number
        or chapter["archived"]
        or chapter["revision"] != 0
        or chapter["content"] != ""
        or any(row.get(k) != v for k, v in bindings(Basis(), plot, chapter).items())
        or row.get("effective_plot") != plot["plot"]
        or row.get("effective_plot_sha256") != sha(plot["plot"])
        or domain.db.all(
            "SELECT id FROM artifacts WHERE chapter_id=? AND kind IN ('chapter_draft','text_patch')",
            (chapter["id"],),
        )
    ):
        raise DomainError("plan_preparation_late", "本章已有改稿、候选或来源改变；不能事后重选章纲。", 409)
    prior = domain.db.all(
        "SELECT position,content,stale FROM chapters WHERE branch_id=? AND archived=0 AND position<? ORDER BY position",
        (current["branch_id"], number),
    )
    if [p["position"] for p in prior] != list(range(1, number)) or any(
        not p["content"].strip() or p["stale"] for p in prior
    ):
        raise DomainError("plan_restart_checkpoint", "前章存在缺稿或失效，需要先核对原检查点。", 409)
    tasks = lineage(domain, current)
    ids = [t["id"] for t in tasks]
    placeholders = ",".join("?" for _ in ids)
    events = domain.db.all(f"SELECT kind,payload FROM task_events WHERE task_id IN ({placeholders})", ids)
    if any(
        e["kind"] == "chapter_started" and json.loads(e["payload"]).get("number") == number for e in events
    ):
        raise DomainError("plan_preparation_late", "本章已进入正文生成，不支持写前新代际重做。", 409)
    if new_roles:
        from .flow_policy_continuation import is_body_action

        if any(not str(key).isdigit() or int(key) > number for key in progress.get("plot_preparations", {})):
            raise DomainError(
                "plan_preparation_late", "后续章节已有写前准备，不能清除或混用其冻结政策。", 409
            )
        future = domain.db.all(
            "SELECT id,revision,content FROM chapters WHERE branch_id=? AND archived=0 AND position>=?",
            (current["branch_id"], number),
        )
        future_ids = {
            c["id"]
            for c in domain.db.all(
                "SELECT id FROM chapters WHERE branch_id=? AND position>=?", (current["branch_id"], number)
            )
        }
        if any(c["revision"] or c["content"] for c in future) or any(
            domain.db.all(
                "SELECT id FROM artifacts WHERE chapter_id=? AND kind IN ('chapter_draft','text_patch')",
                (c["id"],),
            )
            for c in future
        ):
            raise DomainError(
                "plan_preparation_late", "本章或后章已有正式稿、修订或候选，不能跨阶段重做写前解释。", 409
            )
        events = domain.db.all(
            "SELECT e.kind,e.payload FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE t.branch_id=? AND t.kind IN ('workflow','module') "
            "AND e.kind IN ('chapter_started','step_dispatched')",
            (current["branch_id"],),
        )
        for event in events:
            data = json.loads(event["payload"])
            if not isinstance(data, dict):
                continue
            target = data.get("request", {})
            target = target.get("target_version", {}) if isinstance(target, dict) else {}
            if (
                event["kind"] == "chapter_started"
                and type(data.get("number")) is int
                and data["number"] >= number
            ) or (
                event["kind"] == "step_dispatched"
                and is_body_action(data.get("action"))
                and isinstance(target, dict)
                and isinstance(target.get("id"), str)
                and target["id"] in future_ids
            ):
                raise DomainError(
                    "plan_preparation_late", "本章或后章已有正文生成或修订派发，不重做写前解释。", 409
                )
    return branch, row, tasks


def known_invalid_phase(domain, usage, current, row, *, open_phase=False):
    slot = row["phases"]["interpret:0"]
    if slot.get("owner_task_id") != current["id"] or slot.get("status") != "started" or "response" in slot:
        raise DomainError("unknown_result", "原写前步骤身份或回执状态无法确认，未重发。", 409)
    steps = domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (current["id"],))
    old = set(slot.get("before_steps", []))
    if not old <= {s["fingerprint"] for s in steps}:
        raise DomainError("unknown_result", "原步骤缓存不完整，未重发。", 409)
    replies = [s for s in steps if s["fingerprint"] not in old]
    dispatched = [
        json.loads(e["payload"])
        for e in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (current["id"],)
        )
    ]
    new_dispatches = [d for d in dispatched if d["fingerprint"] not in old]
    from types import SimpleNamespace
    from . import workflow_plan_recipient_scope as recipient_scope
    from .plot_audit import ScopeBoundOpenPlanInterpretation, ScopeBoundOpenPlanInterpretationV2

    scope_execution = recipient_scope.enabled(SimpleNamespace(task=current, domain=domain, db=domain.db), row)
    inventory = (
        row.get("phases", {}).get("recipient_scope:0", {}).get("response") if scope_execution else None
    )
    if scope_execution and (
        not open_phase
        or not recipient_scope.inventory_valid(inventory, row["original_plot"])
        or inventory["status"] != "supported"
    ):
        raise DomainError("unknown_result", "先行范围观察不完整或未知，不重发旧解释。", 409)
    schema = (
        ScopeBoundOpenPlanInterpretationV2
        if scope_execution
        and row[recipient_scope.KEY] in {recipient_scope.CURRENT_VERSION, recipient_scope.NEW_VERSION}
        else ScopeBoundOpenPlanInterpretation
        if scope_execution
        else OpenPlanInterpretation
        if open_phase
        else ScopedAuthorPlot
    )
    if {d["fingerprint"] for d in new_dispatches} != {s["fingerprint"] for s in replies} or any(
        d.get("action") != "写前解释AI工作章纲" or d.get("schema") != schema.__name__ for d in new_dispatches
    ):
        raise DomainError("unknown_result", "写前派发与完整解释回执不能一一核对，未重发。", 409)
    if open_phase and (
        len(new_dispatches) != 3
        or any(
            not isinstance(d.get("request"), dict)
            or d["request"].get(ROLE_KEY) != current["result"].get(ROLE_KEY)
            or d["request"].get(KEY) != VERSION
            or (scope_execution and d["request"].get(recipient_scope.KEY) != row[recipient_scope.KEY])
            for d in new_dispatches
        )
    ):
        raise DomainError("unknown_result", "原派发模板版本、次数或开放槽身份无法核实，未重发。", 409)
    calls = [dict(c) for c in usage.execute("SELECT * FROM calls WHERE task_id=?", (current["id"],))]
    if open_phase and not set(slot.get("before_calls", [])) <= {c["id"] for c in calls}:
        raise DomainError("unknown_result", "原阶段调用前像缺失，未重发。", 409)
    new_calls = [c for c in calls if c["id"] not in slot.get("before_calls", [])]
    if (
        len(replies) != 3
        or len(new_calls) != 3
        or any(c["status"] != "settled" or c["charged"] is None for c in new_calls)
    ):
        raise DomainError("unknown_result", "须核实三份完整且已结算的无效解释；未知或缺失回执不重发。", 409)
    source = row["original_plot"]
    sources = {u["source_id"]: u for u in source_units(source, "AUTHOR")}
    receipt_ids = []
    repair_state = (
        RepairState(source, open_plan_issues) if open_phase and role_task(current, domain) else None
    )
    # New guidance's repair anchors depend on dispatch order, not fingerprint order.
    if open_phase:
        by_fingerprint = {s["fingerprint"]: s for s in replies}
        replies = [by_fingerprint[d["fingerprint"]] for d in new_dispatches]
    for step in replies:
        try:
            response = json.loads(step["result"])
            value = schema.model_validate(json.loads(response["text"])).model_dump()
            for item in value["items"]:
                resolve_field(item, "author_evidence", sources)
            invalid = (
                repair_state.validate(recipient_scope.core_value(value) if scope_execution else value)
                if repair_state is not None
                else open_plan_issues(value, source)
                if open_phase
                else author_contract_issues(value, source, scoped=True)
            )
            if scope_execution:
                invalid.extend(recipient_scope.binding_issues(value, inventory))
        except (KeyError, ValueError, TypeError) as error:
            raise DomainError(
                "unknown_result", "旧解释格式或连续来源无法核对，未创建重做任务。", 409
            ) from error
        if not invalid or response.get("call_id") not in {c["id"] for c in new_calls}:
            raise DomainError(
                "plan_restart_checkpoint", "旧步骤不是已证明无效的解释，不擦除有效结果或盲重发。", 409
            )
        receipt_ids.append(response["call_id"])
    if len(set(receipt_ids)) != 3:
        raise DomainError("unknown_result", "旧步骤调用回执不一一对应，未重发。", 409)
    return {"fingerprints": sorted(s["fingerprint"] for s in replies), "call_ids": sorted(receipt_ids)}


def restart(domain, budget, current, request):
    request = normalized(request)
    new_roles = request.get(ROLE_KEY) == ROLE_VERSION
    prior = previous(domain, current["id"], request)
    if prior is not None:
        return prior
    branch, old_row, ancestors = checkpoint(domain, current, request)
    source = budget.settings.providers.get(current["payload"]["account"])
    if source is None:
        raise DomainError("configuration", "原执行配置不存在，未换账户或重置预算。", 409)
    with budget.db(True) as usage:
        costs = receipt_budget(domain, usage, ancestors, current, source)
        proof = known_invalid_phase(domain, usage, current, old_row, open_phase=new_roles)
        payload = {
            **deepcopy(current["payload"]),
            "client_request_id": request["client_request_id"],
            "budget_limit": costs["remaining_run_limit"],
            **({ROLE_KEY: ROLE_VERSION} if new_roles else {}),
        }
        progress = deepcopy(current["result"])
        del progress["plot_preparations"][str(request["chapter"])]
        progress.update(continued_from=current["id"], **{KEY: VERSION})
        if new_roles:
            progress[ROLE_KEY] = ROLE_VERSION
        progress["plan_preparation_restart"] = {
            "version": RESTART_VERSION,
            KEY: VERSION,
            **({ROLE_KEY: ROLE_VERSION} if new_roles else {}),
            "source_task_id": current["id"],
            "request": deepcopy(request),
            "previous_preparation": deepcopy(old_row),
            "previous_preparation_sha256": sha(old_row),
            "known_invalid_steps": proof,
            "budget": costs,
            "policy": "新任务只重做尚未写作的首轮解释；原章纲、旧失败步骤和费用保留，不复用失败缓存或未知重发。",
        }
        with domain.db.connect(True) as tx:
            prior = previous(domain, current["id"], request, tx)
            if prior is not None:
                return prior
            live = tx.execute("SELECT * FROM tasks WHERE id=?", (current["id"],)).fetchone()
            if (
                not live
                or live["updated"] != current["updated"]
                or live["status"] != current["status"]
                or json.loads(live["result"]) != current["result"]
                or json.loads(live["payload"]) != current["payload"]
            ):
                raise DomainError("task_changed", "原任务检查点已改变，未创建重做。", 409)
            if (
                tx.execute(
                    "SELECT story_revision FROM branches WHERE id=?", (current["branch_id"],)
                ).fetchone()[0]
                != branch["story_revision"]
            ):
                raise DomainError("story_changed", "故事在提交期间已改变，未创建重做。", 409)
            if tx.execute(
                "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') AND cancel=0 LIMIT 1",
                (current["branch_id"], current["id"]),
            ).fetchone():
                raise DomainError("active_task", "本工作版本仍有活动任务，未创建重做。", 409)
            # Re-read source and adopted configuration under the manuscript lock.
            checkpoint(domain, current, request)
            if new_roles:
                if known_invalid_phase(domain, usage, current, old_row, open_phase=True) != proof:
                    raise DomainError("task_changed", "原完整回执在提交期间改变，未创建重做。", 409)
            identifier, now = uid(), time()
            tx.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    "workflow",
                    current["novel_id"],
                    current["branch_id"],
                    "queued",
                    dumps(payload),
                    dumps(progress),
                    now,
                    now,
                ),
            )
            tx.execute("INSERT INTO task_requests VALUES(?,?)", (request["client_request_id"], identifier))
            tx.execute(
                "UPDATE tasks SET status='continued',result=?,updated=? WHERE id=?",
                (dumps({**current["result"], "continued_to": identifier}), now, current["id"]),
            )
            event = {
                "source_task_id": current["id"],
                "task_id": identifier,
                "version": RESTART_VERSION,
                "request": request,
            }
            domain.db.event(tx, current["id"], "plan_preparation_restarted", event)
            domain.db.event(tx, identifier, "plan_preparation_restarted_from", event)
    return domain.task(identifier)


def preview(domain, budget, current):
    new_roles = KEY in current["result"]
    basis = {
        "chapter": current["result"].get("next_chapter"),
        "base_story_revision": domain.branch(current["branch_id"])["story_revision"],
        "base_progress_sha256": sha(current["result"]),
        **({ROLE_KEY: ROLE_VERSION} if new_roles else {}),
    }
    branch, row, ancestors = checkpoint(domain, current, {**basis, "reason": "只读核对写前失败检查点"})
    source = budget.settings.providers.get(current["payload"]["account"])
    if source is None:
        raise DomainError("configuration", "原执行配置不存在，未准备重做。", 409)
    with budget.db() as usage:
        costs = receipt_budget(domain, usage, ancestors, current, source)
        proof = known_invalid_phase(domain, usage, current, row, open_phase=new_roles)
    return {
        "version": RESTART_VERSION,
        KEY: VERSION,
        **({ROLE_KEY: ROLE_VERSION} if new_roles else {}),
        "task_id": current["id"],
        "branch_id": branch["id"],
        "chapter_id": row["chapter_id"],
        "request_basis": basis,
        "original_plot": row["original_plot"],
        "original_plot_sha256": row["original_plot_sha256"],
        "old_preparation_sha256": sha(row),
        "known_invalid_steps": proof,
        "budget": costs,
        "policy": "仅准备显式新任务；尚未创建、重发或升级原写前阶段。原AI章纲、旧失败与费用保留。",
    }


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/tasks/{task_id}/reprepare-plan-preview")
    def reprepare_plan_preview(task_id: str):
        return preview(domain, budget, domain.task(task_id))

    @app.get("/api/tasks/{task_id}/plan-restarts/{client_request_id}")
    def plan_restart_receipt(task_id: str, client_request_id: str):
        parent = domain.task(task_id)
        rows = domain.db.all(
            "SELECT t.id,t.result,t.branch_id FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?",
            (client_request_id,),
        )
        task = None
        if rows:
            receipt = json.loads(rows[0]["result"]).get("plan_preparation_restart", {})
            if (
                receipt.get("version") != RESTART_VERSION
                or receipt.get("source_task_id") != task_id
                or receipt.get("request", {}).get("client_request_id") != client_request_id
                or rows[0]["branch_id"] != parent["branch_id"]
            ):
                raise DomainError("idempotency", "该请求标识不属于这个写前重做检查点。", 409)
            task = domain.task(rows[0]["id"])
            if receipt.get("request", {}).get(ROLE_KEY) == ROLE_VERSION:
                role_task(task, domain)
        return {"source_task_id": task_id, "client_request_id": client_request_id, "task": task}

    @app.post("/api/tasks/{task_id}/reprepare-plan", status_code=202)
    def reprepare_plan(task_id: str, data: PlanRestartInput):
        current = domain.task(task_id)
        request = data.model_dump(exclude_none=True)
        found = previous(domain, task_id, request)
        if found is not None:
            return found
        provider_ready(current["payload"]["account"])
        return restart(domain, budget, current, request)
