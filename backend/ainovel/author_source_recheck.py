"""Explicit same-source policy upgrade; no clarification, replay or silent resumption."""

from copy import deepcopy
import json
from time import time
from typing import Literal

from pydantic import Field

from . import authored_source_scope as authored
from . import author_source_clarification as clarification
from .budget import Budget
from .db import DomainError, dumps, uid
from .models import Input

VERSION = "author-source-recheck/1"
KEY = "author_source_recheck_version"
RECEIPT = "author_source_recheck"
EVENT = "author_source_rechecked_from"
sha = authored.sha


class RecheckInput(Input):
    version: Literal["author-source-recheck/1"]
    client_request_id: str = Field(min_length=8, max_length=128)
    preview_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


def validate_task(task, domain, chain=None):
    chain = list(chain or authored.ancestry(task, domain))
    events = [
        e
        for t in chain
        for e in domain.db.all("SELECT payload FROM task_events WHERE task_id=? AND kind=?", (t["id"], EVENT))
    ]
    marker, receipt = task["result"].get(KEY), task["result"].get(RECEIPT)
    if marker is None and receipt is None and not events:
        return None
    if marker != VERSION or not isinstance(receipt, dict) or receipt.get("version") != VERSION:
        raise DomainError("author_recheck_version", "同源核对版本或独立创建见证缺失，未降级或派发。", 409)
    origin = next((t for t in chain if t["id"] == receipt.get("task_id")), None)
    parent = next((t for t in chain if t["id"] == receipt.get("source_task_id")), None)
    if origin is None or parent is None:
        raise DomainError("author_recheck_version", "同源核对来源链不完整。", 409)
    try:
        request = RecheckInput.model_validate(receipt["request"]).model_dump()
        key = f"chapter:{receipt['chapter']}"
        original = next(e for e in parent["result"][authored.SNAPSHOT]["sources"] if e["key"] == key)
        entry = clarification.source_entry(parent, original)
        clean_parent = {k: v for k, v in parent["result"].items() if k != "continued_to"}
        prior = parent["result"][authored.RECEIPTS][key]
        rows = domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (origin["id"], EVENT)
        )
        event = {
            "version": VERSION,
            "source_task_id": parent["id"],
            "task_id": origin["id"],
            "receipt_sha256": sha(receipt),
        }
        expected_policies = {**receipt["previous_policies"], authored.KEY: authored.CURRENT_VERSION}
        valid = (
            parent["result"].get(authored.KEY) == authored.VERSION
            and origin["result"].get(authored.KEY) == authored.CURRENT_VERSION
            and origin["result"].get(KEY) == VERSION
            and origin["result"].get(RECEIPT) == receipt
            and origin["result"].get("continued_from") == parent["id"]
            and parent["result"].get("continued_to") == origin["id"]
            and origin["result"].get(authored.SNAPSHOT, {}).get("task_id") == origin["id"]
            and receipt["old_policy"] == authored.VERSION
            and receipt["new_policy"] == authored.CURRENT_VERSION
            and receipt["source_unchanged"] is True
            and receipt["starts_child"] is True
            and receipt["branch_id"] == task["branch_id"] == parent["branch_id"] == origin["branch_id"]
            and receipt["novel_id"] == task["novel_id"] == parent["novel_id"] == origin["novel_id"]
            and parent["kind"] == origin["kind"] == "workflow"
            and original == receipt["original_source_entry"]
            and entry == receipt["previous_source_entry"] == receipt["source_entry"]
            and prior == receipt["previous_source_receipt"]
            and prior["source"] == entry
            and receipt["source_payload_sha256"] == sha(parent["payload"])
            and receipt["source_progress_sha256"] == sha(clean_parent)
            and receipt["plan_sha256"] == sha(origin["result"].get("plan"))
            and receipt["policies"] == expected_policies
            and all(origin["result"].get(k) == v for k, v in expected_policies.items())
            and {k: v for k, v in origin["payload"].items() if k not in {"client_request_id", "budget_limit"}}
            == {k: v for k, v in parent["payload"].items() if k not in {"client_request_id", "budget_limit"}}
            and origin["payload"]["budget_limit"] == receipt["budget"]["remaining_run_limit"]
            and origin["payload"]["client_request_id"] == request["client_request_id"]
            and bool(
                domain.db.all(
                    "SELECT key FROM task_requests WHERE key=? AND task_id=?",
                    (request["client_request_id"], origin["id"]),
                )
            )
            and len(rows) == 1
            and json.loads(rows[0]["payload"]) == event
        )
    except KeyError, TypeError, ValueError, StopIteration:
        valid = False
    if not valid:
        raise DomainError("author_recheck_version", "同原话、旧来源、新政策或独立创建回执不符，未派发。", 409)
    return receipt


def checkpoint(domain, current):
    gate = clarification.checkpoint(domain, current)
    if (
        current["result"].get(authored.KEY) != authored.VERSION
        or KEY in current["result"]
        or RECEIPT in current["result"]
    ):
        raise DomainError(
            "author_recheck_policy",
            "仅旧版完整已知待核停点可显式升级，同版不重复付费核对；真正补充请使用原澄清入口。",
            409,
        )
    return gate


def preview(domain, budget, current, *, usage=None):
    checkpoint(domain, current)
    value = clarification.preview(domain, budget, current, usage=usage)
    basis = sha(
        {
            "version": VERSION,
            "old_policy": authored.VERSION,
            "new_policy": authored.CURRENT_VERSION,
            "original_basis": value["request_basis"]["preview_basis_sha256"],
        }
    )
    return {
        **value,
        "version": VERSION,
        "old_policy": authored.VERSION,
        "new_policy": authored.CURRENT_VERSION,
        "source_unchanged": True,
        "starts_child": True,
        "eligible": True,
        "request_basis": {"preview_basis_sha256": basis},
        "baseline": {**value["baseline"], "preview_basis_sha256": basis},
        "notice": "保留同一原话，用新版分类和独立完整原意保持创建并开始子批次；不是认可旧unknown，也不证明作者原话有误。新增模型调用沿原账户及累计剩余额度收费，旧调用、旧解释和费用保留，不补剧情、不自动恢复旧任务。新核对仍未知会停止；真正需补充内容请使用原澄清入口。",
    }


def previous(domain, parent, request, tx=None):
    sql = "SELECT t.id,t.branch_id,t.result FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.key=?"
    rows = (
        list(tx.execute(sql, (request["client_request_id"],)))
        if tx
        else domain.db.all(sql, (request["client_request_id"],))
    )
    if not rows:
        return None
    row = rows[0]
    receipt = json.loads(row["result"]).get(RECEIPT, {})
    if (
        row["branch_id"] != parent["branch_id"]
        or receipt.get("version") != VERSION
        or receipt.get("source_task_id") != parent["id"]
        or receipt.get("request") != request
    ):
        raise DomainError("idempotency", "该提交标识已用于不同来源或新版核对决定，请保留原请求。", 409)
    child = domain.task(row["id"])
    authored.validate_task(child, domain)
    return child


def recheck(domain, budget, current, request):
    found = previous(domain, current, request)
    if found is not None:
        return found
    if not request["reason"].strip():
        raise DomainError("author_recheck_reason", "请填写本次显式同源核对原因；原因不会当作剧情来源。", 409)
    with budget.db(True) as usage, domain.db.connect(True) as tx:
        found = previous(domain, current, request, tx)
        if found is not None:
            return found
        live = tx.execute("SELECT * FROM tasks WHERE id=?", (current["id"],)).fetchone()
        if (
            not live
            or live["updated"] != current["updated"]
            or live["status"] != current["status"]
            or json.loads(live["payload"]) != current["payload"]
            or json.loads(live["result"]) != current["result"]
        ):
            raise DomainError("task_changed", "来源检查点已变化，请保留输入并重新读取。", 409)
        observed = preview(domain, budget, current, usage=usage)
        if observed["request_basis"]["preview_basis_sha256"] != request["preview_basis_sha256"]:
            raise DomainError(
                "author_recheck_basis", "同原话、规划、版本或费用依据改变，请显式重读确认。", 409
            )
        identifier, now = uid(), time()
        payload = {
            **deepcopy(current["payload"]),
            "client_request_id": request["client_request_id"],
            "budget_limit": observed["budget"]["remaining_run_limit"],
        }
        progress = deepcopy(current["result"])
        entry = observed["current_source"]
        policies = {**observed["policies"], authored.KEY: authored.CURRENT_VERSION}
        receipt = {
            "version": VERSION,
            "task_id": identifier,
            "source_task_id": current["id"],
            "novel_id": current["novel_id"],
            "branch_id": current["branch_id"],
            "chapter": observed["chapter"],
            "chapter_id": observed["chapter_id"],
            "old_policy": authored.VERSION,
            "new_policy": authored.CURRENT_VERSION,
            "source_unchanged": True,
            "starts_child": True,
            "source_payload_sha256": sha(current["payload"]),
            "source_progress_sha256": sha(current["result"]),
            "previous_source_receipt": deepcopy(progress[authored.RECEIPTS][entry["key"]]),
            "original_source_entry": observed["original_source"],
            "previous_source_entry": entry,
            "source_entry": deepcopy(entry),
            "plan_sha256": observed["baseline"]["plan_sha256"],
            "baseline": observed["baseline"],
            "account": observed["account"],
            "request": deepcopy(request),
            "budget": observed["budget"],
            "previous_policies": observed["policies"],
            "policies": policies,
            "policy": "只用同一原话新版观察、解释与独立保持，未知仍停止，旧报告和累计费用不改，非文学认可。",
            "author_literary_approval": "unverified",
        }
        progress.update(
            continued_from=current["id"],
            **{KEY: VERSION, RECEIPT: receipt, authored.KEY: authored.CURRENT_VERSION},
        )
        progress[authored.RECEIPTS].pop(entry["key"])
        progress[authored.SNAPSHOT] = {
            "version": authored.CURRENT_VERSION,
            "task_id": identifier,
            "novel_id": payload["novel_id"],
            "branch_id": current["branch_id"],
            "kind": "workflow",
            "payload_sha256": sha(payload),
            "sources": authored.sources(payload, progress),
            "inherited": None,
        }
        tx.execute(
            "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,'workflow',?,?,'queued',?,?,?,?)",
            (
                identifier,
                current["novel_id"],
                current["branch_id"],
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
            "version": VERSION,
            "source_task_id": current["id"],
            "task_id": identifier,
            "receipt_sha256": sha(receipt),
        }
        domain.db.event(tx, current["id"], "author_source_rechecked", event)
        domain.db.event(tx, identifier, EVENT, event)
        authored.event(domain.db, tx, identifier, payload, progress)
    return domain.task(identifier)


def ensure_runtime(task, domain, settings):
    receipt = validate_task(task, domain)
    if receipt is None:
        return
    chain = list(authored.ancestry(task, domain))
    origin = next(t for t in chain if t["id"] == receipt["task_id"])
    changed = clarification.authorized_account_descendant(domain, chain, origin)
    chosen = settings.providers.get(task["payload"]["account"])
    if chosen is None or (not changed and chosen.public() != receipt["account"]):
        raise DomainError("configuration_changed", "本次新版同源核对冻结账户已变化，未静默换配置派发。", 409)
    with Budget(settings).db() as usage:
        proof = clarification.known_source_proof(domain, usage, receipt["previous_source_receipt"])
    if proof != receipt["budget"]["source_steps"]:
        raise DomainError("unknown_result", "旧已付来源的完整步骤或费用证明已变化，未派发新调用。", 409)
    allowed = {t["id"] for t in chain[: chain.index(origin) + 1]}
    targets = {
        c["id"]
        for c in domain.db.all(
            "SELECT id FROM chapters WHERE branch_id=? AND position>=?",
            (task["branch_id"], receipt["chapter"]),
        )
    }
    from .flow_policy_continuation import is_body_action

    for event in domain.db.all(
        "SELECT e.task_id,e.kind,e.payload FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE t.branch_id=? AND t.kind IN ('workflow','module') AND e.kind IN ('chapter_started','step_dispatched')",
        (task["branch_id"],),
    ):
        if event["task_id"] in allowed:
            continue
        data = json.loads(event["payload"])
        if not isinstance(data, dict):
            continue
        request = data.get("request", {})
        target = request.get("target_version", {}) if isinstance(request, dict) else {}
        if (
            event["kind"] == "chapter_started"
            and type(data.get("number")) is int
            and data["number"] >= receipt["chapter"]
        ) or (
            event["kind"] == "step_dispatched"
            and is_body_action(data.get("action"))
            and isinstance(target, dict)
            and target.get("id") in targets
        ):
            raise DomainError(
                "author_recheck_late",
                "新子任务等待期间本章或后章已有其他正文派发，保留原现场，未跨任务重发。",
                409,
            )


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/tasks/{task_id}/author-source-recheck-preview")
    def source_recheck_preview(task_id: str):
        return preview(domain, budget, domain.task(task_id))

    @app.get("/api/tasks/{task_id}/author-source-rechecks")
    @app.get("/api/tasks/{task_id}/author-source-rechecks/{client_request_id}")
    def source_recheck_receipt(task_id: str, client_request_id: str):
        parent = domain.task(task_id)
        rows = domain.db.all(
            "SELECT result FROM tasks WHERE id IN (SELECT task_id FROM task_requests WHERE key=?)",
            (client_request_id,),
        )
        task = None
        if rows:
            request = json.loads(rows[0]["result"]).get(RECEIPT, {}).get("request")
            if not isinstance(request, dict):
                raise DomainError("idempotency", "该标识不属于本次同源核对提交，保留原标识。", 409)
            task = previous(domain, parent, request)
        return {"source_task_id": task_id, "client_request_id": client_request_id, "task": task}

    @app.post("/api/tasks/{task_id}/author-source-rechecks", status_code=202)
    def source_recheck(task_id: str, data: RecheckInput):
        current = domain.task(task_id)
        request = data.model_dump()
        found = previous(domain, current, request)
        if found is not None:
            return found
        provider_ready(current["payload"]["account"])
        return recheck(domain, budget, current, request)
