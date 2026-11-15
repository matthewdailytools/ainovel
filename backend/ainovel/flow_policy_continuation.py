"""Explicit future-work policy child from an adopted, maintained checkpoint."""

from copy import deepcopy
import json
from time import time
from typing import Literal

from pydantic import Field

from .budget import micro
from .conditional_continuation import (
    adopted_checkpoint,
    call_audit,
    lineage,
    receipt_budget,
    sha,
    unresolved_call,
)
from .db import DomainError, dumps, uid
from .flow_entailment import KEY, VERSION
from .models import Input
from .modules import MODULES

CONTINUATION_VERSION = "workflow-flow-policy-continuation/1"
BODY_DISPATCH_ACTIONS = {
    "根据情节补写正文",
    "按章纲写完整正文",
    "用局部替换修订正文",
    "按确认参考应用方案原创试写",
}


def is_body_action(action):
    if not isinstance(action, str):
        return False
    spec = MODULES.get(action)
    return action in BODY_DISPATCH_ACTIONS or (
        spec is not None and spec.artifact_kind in {"chapter_draft", "text_patch"}
    )


class FlowContinuationInput(Input):
    client_request_id: str = Field(min_length=8, max_length=128)
    preview_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flow_source_entailment_version: Literal["source-flow-entailment/1"]
    reason: str = Field(min_length=1, max_length=2000)


def previous(domain, parent, request, tx=None):
    sql = "SELECT t.* FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?"
    rows = (
        list(tx.execute(sql, (request["client_request_id"],)))
        if tx is not None
        else domain.db.all(sql, (request["client_request_id"],))
    )
    if not rows:
        return None
    row = rows[0]
    result = json.loads(row["result"])
    receipt = result.get("flow_policy_continuation", {})
    if (
        row["kind"] != "workflow"
        or row["branch_id"] != parent["branch_id"]
        or result.get(KEY) != VERSION
        or result.get("continued_from") != parent["id"]
        or receipt.get("version") != CONTINUATION_VERSION
        or receipt.get("source_task_id") != parent["id"]
        or receipt.get("request") != request
    ):
        raise DomainError("idempotency", "该提交标识不属于同一流向政策决定，未新增任务。", 409)
    return domain.task(row["id"])


def checkpoint(domain, current):
    state, payload = current["result"], current["payload"]
    if (
        current["kind"] != "workflow"
        or payload.get("plot_contract_check") is not True
        or state.get("plot_contract_version") != "author-plot-audit/2"
        or KEY in state
    ):
        raise DomainError("flow_checkpoint", "仅未启用新流向政策的v2整书检查点可显式新建批次。", 409)
    branch = adopted_checkpoint(domain, current)
    number = state["next_chapter"]
    if number > payload["chapters"]:
        raise DomainError("flow_checkpoint", "本批没有尚未开始的后续章节，无需新建政策任务。", 409)
    tasks = lineage(domain, current)
    future = domain.db.all(
        "SELECT id,revision,content FROM chapters WHERE branch_id=? AND archived=0 AND position>=?",
        (current["branch_id"], number),
    )
    if any(row["revision"] or row["content"] for row in future) or any(
        domain.db.all(
            "SELECT id FROM artifacts WHERE chapter_id=? AND kind IN ('chapter_draft','text_patch')",
            (row["id"],),
        )
        for row in future
    ):
        raise DomainError("flow_checkpoint", "下一章或后章已有正文、修订或候选，不能跨阶段换政策。", 409)
    if any(not str(key).isdigit() or int(key) >= number for key in state.get("plot_preparations", {})):
        raise DomainError("flow_checkpoint", "后续章已有写前准备，须保留原政策处理，不重置准备。", 409)
    # Only these registered task kinds produce manuscript candidates. Read-only
    # correction/recheck tasks cannot become body producers from a raw event label.
    events = domain.db.all(
        "SELECT e.kind,e.payload FROM task_events e JOIN tasks t ON t.id=e.task_id "
        "WHERE t.branch_id=? AND t.kind IN ('workflow','module') "
        "AND e.kind IN ('chapter_started','step_dispatched')",
        (current["branch_id"],),
    )
    # The child owns the entire remaining plan, as does the existing text/candidate
    # gate above; a later dispatched body cannot be restarted under its new policy.
    future_chapter_ids = {
        row["id"]
        for row in domain.db.all(
            "SELECT id FROM chapters WHERE branch_id=? AND position>=?", (current["branch_id"], number)
        )
    }

    def dispatched_future(row):
        data = json.loads(row["payload"])
        if not isinstance(data, dict):
            return False
        if row["kind"] == "chapter_started":
            return type(data.get("number")) is int and data["number"] >= number
        if not is_body_action(data.get("action")):
            return False
        request = data.get("request", {})
        if not isinstance(request, dict):
            return False
        target = request.get("target_version", {})
        return (
            isinstance(target, dict)
            and isinstance(target.get("id"), str)
            and target["id"] in future_chapter_ids
        )

    if any(dispatched_future(row) for row in events):
        raise DomainError("flow_checkpoint", "后续章节已进入正文派发，不能混用或重放旧步骤。", 409)
    if domain.db.all(
        "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') AND cancel=0 LIMIT 1",
        (current["branch_id"], current["id"]),
    ):
        raise DomainError("active_task", "本工作版本仍有其他活动任务，未开启新批次。", 409)
    return branch, tasks


def auxiliary_family(domain, tasks):
    ids = {row["id"] for row in tasks}
    rows = domain.db.all(
        "SELECT * FROM tasks WHERE branch_id=? AND kind IN (?,?) ORDER BY id",
        (tasks[0]["branch_id"], "source_interpretation_correction", "source_corrected_candidate_recheck"),
    )
    result = []
    for row in rows:
        payload, state = json.loads(row["payload"]), json.loads(row["result"])
        bound = state.get("source_correction_snapshot", state.get("source_corrected_recheck_snapshot", {}))
        if isinstance(bound, dict) and bound.get("source_workflow_id") in ids:
            if row["status"] in ("queued", "running"):
                raise DomainError("active_task", "相关来源纠偏或正文复检尚在执行，未开启新批次。", 409)
            result.append({**row, "payload": payload, "result": state})
    return result


def family_budget(domain, usage, current, tasks, provider, auxiliary):
    costs = receipt_budget(domain, usage, tasks, current, provider, allow_foreign_history=True)
    calls, foreign_history = [], []
    for task in auxiliary:
        own = [dict(row) for row in usage.execute("SELECT * FROM calls WHERE task_id=?", (task["id"],))]
        relevant = [row for row in own if row["provider"] == provider.kind]
        if any(unresolved_call(row) for row in relevant):
            raise DomainError("unknown_result", "相关来源任务仍有未决调用或费用，保留预留并停止。", 409)
        steps = domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task["id"],))
        steps = [row for row in steps if isinstance(json.loads(row["result"]).get("text"), str)]
        complete_calls = {json.loads(row["result"]).get("call_id") for row in steps}
        foreign_history.extend(
            call_audit(row, complete_step=row["id"] in complete_calls, scope="auxiliary")
            for row in own
            if row["provider"] != provider.kind
        )
        calls.extend(own)
        # This new child cannot replay old auxiliary steps. A known foreign
        # dispatch keeps its own currency reserve instead of gating new CNY/USD.
        if own and not relevant:
            continue
        if any(row["id"] not in complete_calls for row in relevant):
            raise DomainError("unknown_result", "当前服务商来源调用缺少完整步骤产物，未开启新政策。", 409)
        completed = {row["fingerprint"] for row in steps}
        events = domain.db.all(
            "SELECT id,kind,payload FROM task_events WHERE task_id=? "
            "AND kind IN ('step_dispatched','step_not_sent') ORDER BY id",
            (task["id"],),
        )
        not_sent = {
            (
                json.loads(row["payload"]).get("dispatch_event_id"),
                json.loads(row["payload"]).get("fingerprint"),
            )
            for row in events
            if row["kind"] == "step_not_sent"
        }
        if any(
            row["kind"] == "step_dispatched"
            and json.loads(row["payload"]).get("fingerprint") not in completed
            and (row["id"], json.loads(row["payload"]).get("fingerprint")) not in not_sent
            for row in events
        ):
            raise DomainError("unknown_result", "相关来源步骤已派发但没有完整回执，未借新任务重发。", 409)
    spent = sum(row["charged"] for row in calls if row["provider"] == provider.kind)
    raw = costs["remaining_run_limit"]
    available = (micro(raw) - spent) / 1_000_000 if raw is not None else None
    if available is not None and available <= 0:
        raise DomainError("budget", "原任务及来源复检剩余额度不足，未重置预算。", 409)
    return {
        **costs,
        "auxiliary_task_ids": [row["id"] for row in auxiliary],
        "auxiliary_settled": spent / 1_000_000,
        "auxiliary_by_currency": [
            {
                "provider": kind,
                "currency": currency,
                "settled": sum(
                    row["charged"] or 0
                    for row in calls
                    if row["provider"] == kind and row["currency"] == currency
                )
                / 1_000_000,
            }
            for kind, currency in sorted({(row["provider"], row["currency"]) for row in calls})
        ],
        "effective_remaining_run_limit": available,
        "foreign_unknown_calls": [
            *costs["foreign_unknown_calls"],
            *[
                item
                for item in foreign_history
                if item["id"] in {row["id"] for row in calls if unresolved_call(row)}
            ],
        ],
        "foreign_history_calls": [*costs["foreign_history_calls"], *foreign_history],
        "auxiliary_policy": "child保存名义剩余；Worker再扣同一家族辅助费用一次，不把可用余额重复写入限额。",
    }


def basis(domain, current, branch, auxiliary):
    chapters = domain.db.all(
        "SELECT id,position,revision,content,stale FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position",
        (current["branch_id"],),
    )
    return sha(
        {
            "version": CONTINUATION_VERSION,
            KEY: VERSION,
            "source_task_id": current["id"],
            "payload": current["payload"],
            "progress": current["result"],
            "story_revision": branch["story_revision"],
            "chapters": [{**row, "content": sha(row["content"])} for row in chapters],
            "auxiliary": [
                {
                    "id": row["id"],
                    "status": row["status"],
                    "payload": sha(row["payload"]),
                    "result": sha(row["result"]),
                }
                for row in auxiliary
            ],
        }
    )


def provider(budget, current):
    chosen = budget.settings.providers.get(current["payload"]["account"])
    if chosen is None:
        raise DomainError("configuration", "原账户配置不存在，未换账户或重置预算。", 409)
    return chosen


def preview(domain, budget, current):
    branch, tasks = checkpoint(domain, current)
    auxiliary = auxiliary_family(domain, tasks)
    with budget.db() as usage:
        costs = family_budget(domain, usage, current, tasks, provider(budget, current), auxiliary)
    return {
        "version": CONTINUATION_VERSION,
        KEY: VERSION,
        "source_task_id": current["id"],
        "branch_id": current["branch_id"],
        "story_revision": branch["story_revision"],
        "source_flow_policy": "legacy",
        "source_policies": {
            KEY: None,
            **{key: value for key, value in current["result"].items() if key.endswith("_version")},
        },
        "next_chapter": current["result"]["next_chapter"],
        "plan_sha256": sha(current["result"]["plan"]),
        "preview_basis_sha256": basis(domain, current, branch, auxiliary),
        "account": current["payload"]["account"],
        "budget": costs,
        "notice": "仅从本检查点为后续新审查创建独立政策任务；旧稿和旧报告不追认、不改写。包括后续整书返修产生的新审查，额外步骤及失败仍收费。",
    }


def continue_policy(domain, budget, current, request):
    found = previous(domain, current, request)
    if found is not None:
        return found
    if not request["reason"].strip():
        raise DomainError("flow_reason", "请记录本次显式政策决定的原因。", 409)
    if request.get(KEY) != VERSION:
        raise DomainError("flow_entailment_version", "新任务须显式冻结当前受支持的流向版本。", 409)
    with budget.db(True) as usage, domain.db.connect(True) as tx:
        found = previous(domain, current, request, tx)
        if found is not None:
            return found
        row = tx.execute("SELECT * FROM tasks WHERE id=?", (current["id"],)).fetchone()
        if (
            not row
            or row["status"] != current["status"]
            or row["updated"] != current["updated"]
            or json.loads(row["payload"]) != current["payload"]
            or json.loads(row["result"]) != current["result"]
        ):
            raise DomainError("task_changed", "原检查点已变化，请显式刷新依据，未创建子任务。", 409)
        branch, tasks = checkpoint(domain, current)
        auxiliary = auxiliary_family(domain, tasks)
        if basis(domain, current, branch, auxiliary) != request["preview_basis_sha256"]:
            raise DomainError("flow_basis_changed", "来源依据已变化，请保留输入并显式重新读取确认。", 409)
        if tx.execute(
            "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') AND cancel=0 LIMIT 1",
            (current["branch_id"], current["id"]),
        ).fetchone():
            raise DomainError("active_task", "本工作版本仍有其他活动任务，未开启新批次。", 409)
        costs = family_budget(domain, usage, current, tasks, provider(budget, current), auxiliary)
        payload = {
            **deepcopy(current["payload"]),
            "client_request_id": request["client_request_id"],
            "budget_limit": costs["remaining_run_limit"],
        }
        progress = deepcopy(current["result"])
        identifier, now = uid(), time()
        progress.update(continued_from=current["id"], **{KEY: VERSION})
        progress["flow_policy_continuation"] = {
            "version": CONTINUATION_VERSION,
            "source_task_id": current["id"],
            "source_flow_policy": "legacy",
            "source_payload_sha256": sha(current["payload"]),
            "source_progress_sha256": sha(current["result"]),
            "plan_sha256": sha(progress["plan"]),
            "next_chapter": progress["next_chapter"],
            "request": deepcopy(request),
            "budget": costs,
            "policy": "旧政策保持历史；仅本child的后续新审查使用新FLOW，原轮次、规划、缓存及累计费用不重置。",
        }
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
            "version": CONTINUATION_VERSION,
            "request": request,
        }
        domain.db.event(tx, current["id"], "flow_policy_continued", event)
        domain.db.event(tx, identifier, "flow_policy_continued_from", event)
    return domain.task(identifier)


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/tasks/{task_id}/continue-flow-policy-preview")
    def flow_policy_preview(task_id: str):
        return preview(domain, budget, domain.task(task_id))

    @app.get("/api/tasks/{task_id}/flow-policy-continuations/{client_request_id}")
    def flow_policy_receipt(task_id: str, client_request_id: str):
        parent = domain.task(task_id)
        rows = domain.db.all(
            "SELECT t.* FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?",
            (client_request_id,),
        )
        if not rows:
            raise DomainError("not_found", "暂未查到原政策提交；保留标识继续查询，不新建请求。", 404)
        receipt = json.loads(rows[0]["result"]).get("flow_policy_continuation", {})
        request = receipt.get("request", {})
        if not isinstance(request, dict) or request.get("client_request_id") != client_request_id:
            raise DomainError("idempotency", "该标识不属于此流向政策提交，未改写请求。", 409)
        return previous(domain, parent, request)

    @app.post("/api/tasks/{task_id}/continue-flow-policy", status_code=202)
    def continue_flow_policy(task_id: str, data: FlowContinuationInput):
        current, request = domain.task(task_id), data.model_dump()
        found = previous(domain, current, request)
        if found is not None:
            return found
        provider_ready(current["payload"]["account"])
        return continue_policy(domain, budget, current, request)
