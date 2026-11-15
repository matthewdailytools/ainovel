"""Explicitly start a new policy batch from an adopted, maintained checkpoint."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from time import time

from pydantic import Field

from .budget import micro
from .conditional_review import KEY, VERSION
from .db import DomainError, dumps, uid
from .models import Input
from .settings import CAPS


class ConditionalContinuationInput(Input):
    client_request_id: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    account: str | None = Field(default=None, min_length=1, max_length=100)


def sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def previous_request(domain, parent_id, request, tx=None):
    sql = "SELECT t.* FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?"
    rows = (
        [dict(row) for row in tx.execute(sql, (request["client_request_id"],))]
        if tx is not None
        else domain.db.all(sql, (request["client_request_id"],))
    )
    if not rows:
        return None
    task = rows[0]
    result = json.loads(task["result"])
    policy = result.get("policy_continuation", {})
    if (
        task["kind"] != "workflow"
        or result.get(KEY) != VERSION
        or result.get("continued_from") != parent_id
        or policy.get("request") != request
        or policy.get("source_task_id") != parent_id
    ):
        raise DomainError("idempotency", "该提交标识已用于不同的政策续跑决定，未新增任务。", 409)
    return domain.task(task["id"])


def checkpoint(domain, current):
    progress, payload = current["result"], current["payload"]
    if current["kind"] != "workflow" or current["status"] != "paused" or KEY in progress:
        raise DomainError(
            "conditional_checkpoint", "只从旧政策已暂停的章节检查点新建批次；不升级原任务。", 409
        )
    if any(progress.get(key) for key in ("waiting_artifact", "waiting_structure", "pending_structure")):
        raise DomainError(
            "review_required", "请先显式采纳待审正文并完成对应结构维护，再在章节检查点暂停。", 409
        )
    if progress.get("pause_after_maintenance_requested"):
        raise DomainError(
            "conditional_checkpoint", "维护后暂停请求尚未完成，请先恢复原维护流程到检查点。", 409
        )
    number = progress.get("next_chapter")
    plan = progress.get("plan", {}).get("chapters", [])
    if (
        type(number) is not int
        or not 2 <= number <= payload["chapters"] + 1
        or [plot.get("number") for plot in plan] != list(range(1, payload["chapters"] + 1))
    ):
        raise DomainError(
            "conditional_checkpoint", "没有完整的采用规划及已完成章节检查点，未创建政策续跑。", 409
        )
    branch = domain.branch(current["branch_id"], current["novel_id"])
    if progress.get("expected_story_revision") != branch["story_revision"]:
        raise DomainError("story_changed", "故事版本已改变，请先在原任务核对影响。", 409)
    domain.check_genre_snapshot(current["branch_id"], progress.get("genre_profile_snapshot"))
    domain.check_author_contract_snapshot(current["branch_id"], progress.get("author_contract_snapshot"))
    if not isinstance(progress.get("genre_profile_snapshot"), dict) or not isinstance(
        progress.get("author_contract_snapshot"), dict
    ):
        raise DomainError("conditional_checkpoint", "创作配置冻结依据缺失，未升级旧任务。", 409)
    chapters = domain.db.all(
        "SELECT id,position,revision,content,stale FROM chapters WHERE branch_id=? AND archived=0 "
        "AND position<? ORDER BY position",
        (current["branch_id"], number),
    )
    if [chapter["position"] for chapter in chapters] != list(range(1, number)) or any(
        not chapter["content"].strip() or chapter["stale"] for chapter in chapters
    ):
        raise DomainError("conditional_checkpoint", "检查点前存在缺章、空稿或失效正文，未继续。", 409)
    latest_id = progress.get("latest_artifact")
    if not latest_id:
        raise DomainError("conditional_checkpoint", "缺少最近一章的明确采纳记录，未继续。", 409)
    latest = domain.artifact(latest_id)
    chapter = chapters[-1]
    receipts = domain.db.all("SELECT result FROM receipts WHERE artifact_id=?", (latest_id,))
    if (
        latest["kind"] != "chapter_draft"
        or latest["branch_id"] != current["branch_id"]
        or latest["chapter_id"] != chapter["id"]
        or latest["status"] != "accepted"
        or latest["payload"].get("text") != chapter["content"]
        or not any(json.loads(row["result"]).get("revision") == chapter["revision"] for row in receipts)
    ):
        raise DomainError("review_required", "最近一章的采纳回执或当前正文无法核对，未创建新批次。", 409)
    if payload.get("structured_maintenance") and not any(
        row.get("chapter_id") == chapter["id"] and row.get("revision") == chapter["revision"]
        for row in progress.get("structure_history", [])
    ):
        raise DomainError("review_required", "最近已采用章节的结构维护尚未完成，未继续。", 409)
    if any(
        "response" not in phase and phase.get("status") != "not_sent"
        for row in progress.get("plot_preparations", {}).values()
        for phase in row.get("phases", {}).values()
    ):
        raise DomainError("unknown_result", "写前准备尚无完整回执，先核对原步骤，不自动重发。", 409)
    return branch


def lineage(domain, current):
    result, seen = [], set()
    while current["id"] not in seen:
        if current["kind"] != "workflow" or (result and current["branch_id"] != result[0]["branch_id"]):
            break
        result.append(current)
        seen.add(current["id"])
        parent = current["result"].get("continued_from")
        if not parent:
            return result
        current = domain.task(parent)
    raise DomainError("conditional_lineage", "原批次的续跑来源不完整或存在循环，未创建新任务。", 409)


def receipt_budget(domain, usage, tasks, current, provider):
    ids = [task["id"] for task in tasks]
    slots = ",".join("?" for _ in ids)
    calls = [dict(row) for row in usage.execute(f"SELECT * FROM calls WHERE task_id IN ({slots})", ids)]
    if any(
        call["status"] != "settled"
        or call["charged"] is None
        or any(type(call[key]) is not int or call[key] < 0 for key in ("input_tokens", "output_tokens"))
        for call in calls
    ):
        raise DomainError("unknown_result", "原批次仍有未知费用或未决调用，预留保留；未开启新政策批次。", 409)
    receipts = {
        (row["task_id"], json.loads(row["result"]).get("call_id"))
        for row in domain.db.all(f"SELECT task_id,result FROM task_steps WHERE task_id IN ({slots})", ids)
    }
    incomplete = [
        {
            "id": call["id"],
            "account": call["account"],
            "amount": call["charged"] / 1_000_000,
            "currency": call["currency"],
            "status": call["status"],
        }
        for call in calls
        if (call["task_id"], call["id"]) not in receipts
    ]
    own = sum(call["charged"] for call in calls if call["task_id"] == current["id"])
    if any(
        call["task_id"] == current["id"]
        and (call["provider"] != provider.kind or call["account"] != provider.id)
        for call in calls
    ):
        raise DomainError("conditional_lineage", "原批次调用账户与冻结配置不一致，未混合预算继续。", 409)
    limit = current["payload"].get("budget_limit")
    remaining = micro(limit) - own if limit is not None else None
    if remaining is not None and remaining <= 0:
        raise DomainError("budget", "原批次剩余预算不足，未通过政策续跑重置额度。", 409)
    return {
        "source_run_limit": limit,
        "source_task_settled": own / 1_000_000,
        "settled_calls_without_step": incomplete,
        "old_call_replay": "禁止重发或重放已结算但没有完整步骤产物的旧调用；仅从已采纳并维护完成的下一章检查点继续。",
        "lineage_task_ids": ids,
        "provider": provider.kind,
        "lineage_settled": sum(call["charged"] for call in calls if call["provider"] == provider.kind)
        / 1_000_000,
        "lineage_by_currency": [
            {
                "provider": kind,
                "currency": currency,
                "settled": sum(
                    call["charged"]
                    for call in calls
                    if call["provider"] == kind and call["currency"] == currency
                )
                / 1_000_000,
            }
            for kind, currency in sorted({(call["provider"], call["currency"]) for call in calls})
        ],
        "remaining_run_limit": float(Decimal(remaining) / 1_000_000) if remaining is not None else None,
        "unknown_reserve": 0,
        "note": "子任务限额只减当前父任务已付费用；祖先仅汇总审计，避免重复扣减已缩减的父任务额度。服务商共享账本保持。",
    }


def continue_policy(domain, budget, current, request):
    """Queue a distinct task; old policy, reports, prose and ledger remain intact."""
    if not request["reason"].strip():
        raise DomainError("conditional_reason", "请说明明确采用新条件性约定政策的原因。", 409)
    previous = previous_request(domain, current["id"], request)
    if previous is not None:
        return previous
    branch = checkpoint(domain, current)
    ancestors = lineage(domain, current)
    original_account = current["payload"]["account"]
    account = request.get("account") or original_account
    source = budget.settings.providers.get(original_account)
    target = budget.settings.providers.get(account)
    if (
        source is None
        or target is None
        or not target.key
        or target.input_rate is None
        or target.output_rate is None
    ):
        raise DomainError("configuration", "原账户或选定模型配置不可用，请先核对配置。", 400)
    if source.kind != target.kind or CAPS[source.kind][0] != CAPS[target.kind][0]:
        raise DomainError("conditional_account", "政策续跑仅可选择同服务商、同币种的已配置模型账户。", 409)
    # Serialize against reservations while reading the immutable, completed old
    # calls. No provider/network work is performed while holding these locks.
    with budget.db(True) as usage:
        costs = receipt_budget(domain, usage, ancestors, current, source)
        payload = {
            **deepcopy(current["payload"]),
            "account": account,
            "client_request_id": request["client_request_id"],
            "budget_limit": costs["remaining_run_limit"],
        }
        progress = deepcopy(current["result"])
        progress.pop("pause_after_maintenance_requested", None)
        progress.update(
            continued_from=current["id"],
            **{KEY: VERSION},
            policy_continuation={
                "version": VERSION,
                "source_task_id": current["id"],
                "source_payload_sha256": sha(current["payload"]),
                "source_progress_sha256": sha(current["result"]),
                "source_policy": "legacy",
                "original_account": original_account,
                "new_account": account,
                "story_revision": branch["story_revision"],
                "next_chapter": progress["next_chapter"],
                "request": deepcopy(request),
                "budget": costs,
            },
        )
        with domain.db.connect(True) as tx:
            previous = previous_request(domain, current["id"], request, tx)
            if previous is not None:
                return previous
            live = tx.execute("SELECT * FROM tasks WHERE id=?", (current["id"],)).fetchone()
            if (
                not live
                or live["status"] != current["status"]
                or live["updated"] != current["updated"]
                or json.loads(live["payload"]) != current["payload"]
                or json.loads(live["result"]) != current["result"]
            ):
                raise DomainError("task_changed", "原批次检查点已改变，请刷新后重新核对。", 409)
            revision = tx.execute(
                "SELECT story_revision FROM branches WHERE id=?", (current["branch_id"],)
            ).fetchone()[0]
            if revision != branch["story_revision"]:
                raise DomainError("story_changed", "创建政策批次期间故事版本改变，未继续。", 409)
            # Recheck adopted configuration under the same manuscript write lock.
            domain.check_genre_snapshot(current["branch_id"], progress["genre_profile_snapshot"])
            domain.check_author_contract_snapshot(current["branch_id"], progress["author_contract_snapshot"])
            if tx.execute(
                "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') "
                "AND cancel=0 LIMIT 1",
                (current["branch_id"], current["id"]),
            ).fetchone():
                raise DomainError(
                    "active_task", "本工作版本还有正在执行或排队的任务，请先到检查点停止。", 409
                )
            identifier, now = uid(), time()
            tx.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
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
            details = {
                "previous_task": current["id"],
                "next_task": identifier,
                **progress["policy_continuation"],
            }
            domain.db.event(tx, current["id"], "continued_conditional_policy", details)
            domain.db.event(tx, identifier, "conditional_policy_continued_from", details)
    return domain.task(identifier)
