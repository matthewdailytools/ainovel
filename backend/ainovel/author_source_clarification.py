"""Explicit author clarification child; original plot, source and paid history stay frozen."""

from copy import deepcopy
import json
from time import time
from typing import Literal

from pydantic import Field

from .db import DomainError, dumps, uid
from .models import Input
from . import authored_source_scope as authored
from .conditional_continuation import lineage, unresolved_call
from .flow_policy_continuation import auxiliary_family, family_budget, is_body_action, provider
from .workflow_plan_preparation import phase

VERSION = "author-source-clarification/1"
KEY = "author_source_clarification_version"
RECEIPT = "author_source_clarification"
CHECKS = "author_source_clarification_checks"
EVENT = "author_source_clarified_from"
ACTION = "独立核对作者补充澄清与原来源保持"
sha = authored.sha


class ClarificationInput(Input):
    version: Literal["author-source-clarification/1"]
    client_request_id: str = Field(min_length=8, max_length=128)
    preview_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clarification: str = Field(min_length=1, max_length=8000)
    actor: Literal["author", "authorized_operator"]
    reason: str = Field(max_length=2000, default="")


class SourceCheck(Input):
    source_id: str
    result: Literal["pass", "fail", "unknown"]
    evidence_ids: list[str] = Field(max_length=6)
    reason: str = Field(min_length=1, max_length=1200)


class AdditionCheck(Input):
    source_id: str
    classification: Literal["clarification", "direction_change", "unknown"]
    evidence_ids: list[str] = Field(max_length=6)
    reason: str = Field(min_length=1, max_length=1200)


class ConstraintCheck(Input):
    result: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1, max_length=1600)


class Preservation(Input):
    source_checks: list[SourceCheck]
    addition_checks: list[AdditionCheck]
    fixed_checks: list[SourceCheck]
    author_contract_check: ConstraintCheck
    summary: str = Field(max_length=2000)


def combine(entry, request):
    label = "作者" if request["actor"] == "author" else "授权操作者"
    text = entry["text"] + f"\n\n【{label}本章补充澄清；与原话分开保存】\n" + request["clarification"]
    return {**deepcopy(entry), "text": text, "sha256": sha(text)}


def validate_task(task, domain, chain=None):
    """Called after the original authored scope creation witness was validated."""
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
        raise DomainError("author_clarification_version", "补充澄清版本或创建来源不完整，未降级或派发。", 409)
    origin = next((t for t in chain if t["id"] == receipt.get("task_id")), None)
    parent = next((t for t in chain if t["id"] == receipt.get("source_task_id")), None)
    if origin is None or parent is None:
        raise DomainError("author_clarification_version", "补充来源链无法核对，未替换原话。", 409)
    rows = domain.db.all("SELECT payload FROM task_events WHERE task_id=? AND kind=?", (origin["id"], EVENT))
    key = f"chapter:{receipt.get('chapter')}"
    original = next((e for e in parent["result"][authored.SNAPSHOT]["sources"] if e["key"] == key), None)
    prior = parent["result"].get(authored.RECEIPTS, {}).get(key)
    clean_parent = {k: v for k, v in parent["result"].items() if k != "continued_to"}
    expected_event = {
        "version": VERSION,
        "source_task_id": parent["id"],
        "task_id": origin["id"],
        "receipt_sha256": sha(receipt),
    }
    request = receipt.get("request", {})
    try:
        request = ClarificationInput.model_validate(request).model_dump()
    except ValueError as error:
        raise DomainError(
            "author_clarification_version", "冻结补充输入不符合原契约，未派发。", 409
        ) from error
    if (
        origin["result"].get(KEY) != VERSION
        or origin["result"].get(RECEIPT) != receipt
        or origin["result"].get("continued_from") != parent["id"]
        or receipt.get("branch_id") != task["branch_id"]
        or receipt.get("novel_id") != task["novel_id"]
        or any(t["kind"] != "workflow" or t["branch_id"] != task["branch_id"] for t in (origin, parent))
        or original != receipt.get("original_source_entry")
        or prior != receipt.get("previous_source_receipt")
        or prior is None
        or prior.get("source") != receipt.get("previous_source_entry")
        or receipt.get("source_payload_sha256") != sha(parent["payload"])
        or receipt.get("source_progress_sha256") != sha(clean_parent)
        or receipt.get("plan_sha256") != sha(origin["result"].get("plan"))
        or any(origin["result"].get(k) != v for k, v in receipt.get("policies", {}).items())
        or receipt.get("source_entry") != combine(receipt["previous_source_entry"], request)
        or {k: v for k, v in origin["payload"].items() if k not in {"client_request_id", "budget_limit"}}
        != {k: v for k, v in parent["payload"].items() if k not in {"client_request_id", "budget_limit"}}
        or len(rows) != 1
        or json.loads(rows[0]["payload"]) != expected_event
    ):
        raise DomainError(
            "author_clarification_version", "原来源、补充、规划或独立创建回执不一致，未派发。", 409
        )
    return receipt


def authorized_account_descendant(domain, chain, origin):
    """Recognize existing explicit continuations; do not turn a historical
    creation account into a new restriction on every future generation."""
    changed = False
    for child, parent in zip(chain, chain[1:]):
        if child["id"] == origin["id"]:
            break
        if child["payload"]["account"] == parent["payload"]["account"]:
            continue
        request = child["result"].get("continuation_request")
        kind = "continued_from"
        if (
            isinstance(request, dict)
            and request.get("account") == child["payload"]["account"]
            and request.get("client_request_id") == child["payload"].get("client_request_id")
        ):
            event = {"previous_task": parent["id"], "next_task": child["id"], **request}
        else:
            receipt = child["result"].get("policy_continuation", {})
            request = receipt.get("request")
            kind = "conditional_policy_continued_from"
            if (
                not isinstance(request, dict)
                or receipt.get("source_task_id") != parent["id"]
                or receipt.get("new_account") != child["payload"]["account"]
                or receipt.get("original_account") != parent["payload"]["account"]
            ):
                raise DomainError(
                    "author_clarification_account",
                    "换账户来源缺少既有显式续跑决定，未静默换配置。",
                    409,
                )
            event = {"previous_task": parent["id"], "next_task": child["id"], **receipt}
        nonce = child["payload"].get("client_request_id")
        rows = domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (child["id"], kind)
        )
        if (
            request.get("client_request_id") != nonce
            or parent["result"].get("continued_to") != child["id"]
            or not domain.db.all(
                "SELECT key FROM task_requests WHERE key=? AND task_id=?", (nonce, child["id"])
            )
            or len(rows) != 1
            or json.loads(rows[0]["payload"]) != event
        ):
            raise DomainError(
                "author_clarification_account",
                "账户续跑的原提交与创建事件无法核对，未自动发送。",
                409,
            )
        changed = True
    return changed


def continuation(task):
    if KEY in task["result"] and any(
        p.get("status") not in {"completed", "not_sent"}
        for p in task["result"].get(CHECKS, {}).get("phases", {}).values()
    ):
        raise DomainError(
            "unknown_result", "补充保持步骤尚无完整回执，保留原账户预留，未借换账户重做未知。", 409
        )


def ensure_runtime(task, domain, settings):
    receipt = validate_task(task, domain)
    if receipt is None:
        return
    chain = list(authored.ancestry(task, domain))
    origin = next(t for t in chain if t["id"] == receipt["task_id"])
    changed = authorized_account_descendant(domain, chain, origin)
    chosen = settings.providers.get(task["payload"]["account"])
    if chosen is None or (not changed and chosen.public() != receipt.get("account")):
        raise DomainError(
            "configuration_changed",
            "原账户模型配置已变化，请核对冻结来源与费用，未自动换配置继续。",
            409,
        )
    # A queued child must not replay body work dispatched later by another
    # producer while it waited. Its own generation and legitimate descendants
    # are permitted; original parents and unrelated tasks are not promoted.
    allowed = {t["id"] for t in chain[: chain.index(origin) + 1]}
    targets = {
        c["id"]
        for c in domain.db.all(
            "SELECT id FROM chapters WHERE branch_id=? AND position>=?",
            (task["branch_id"], receipt["chapter"]),
        )
    }
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
                "author_clarification_late",
                "子任务等待期间已有其他正文派发，保留现场，未跨任务重发。",
                409,
            )


def source_entry(task, entry):
    receipt = task["result"].get(RECEIPT)
    return (
        deepcopy(receipt["source_entry"])
        if receipt and entry["key"] == receipt["source_entry"]["key"]
        else deepcopy(entry)
    )


def effective_plot(task, plot):
    receipt = task["result"].get(RECEIPT)
    return (
        {**deepcopy(plot), "plot": receipt["source_entry"]["text"]}
        if receipt and plot["number"] == receipt["chapter"]
        else deepcopy(plot)
    )


def completed_unverified_record(domain, current, entry):
    from .plot_audit import ScopedAuthorPlot, author_contract_issues

    record = current["result"].get(authored.RECEIPTS, {}).get(entry["key"])
    if record and record.get("version") == authored.CURRENT_VERSION:
        from .authored_source_classification import completed_unverified_record

        return completed_unverified_record(domain, current, entry, record)
    owners = {t["id"] for t in lineage(domain, current)}
    try:
        if (
            record["state"] != "unverified"
            or record["source"] != entry
            or record["source_sha256"] != entry["sha256"]
            or record.get("inherited")
        ):
            raise ValueError("not a known author-source stop")
        phases = record["phases"]
        if (
            not phases
            or not set(phases) <= {"observe", "interpret"}
            or any(
                s["status"] != "completed"
                or s["owner_task_id"] not in owners
                or s["response_sha256"] != sha(s["response"])
                for s in phases.values()
            )
        ):
            raise ValueError("unknown phase")
        inventory = phases["observe"]["response"]
        if not authored.inventory_valid(inventory, entry):
            raise ValueError("invalid inventory")
        if "interpret" in phases:
            interpreted = phases["interpret"]["response"]
            contract = ScopedAuthorPlot.model_validate(record["contract"]).model_dump()
            if (
                inventory["status"] != "supported"
                or interpreted["contract"] != contract
                or interpreted["bindings"] != record["bindings"]
                or not authored.binding_valid(contract, record["bindings"], inventory)
                or author_contract_issues(contract, entry["text"], scoped=True)
            ):
                raise ValueError("unbound interpretation")
            if (
                contract["items"]
                and all(i["certainty"] == "explicit" for i in contract["items"])
                and all(i["status"] == "supported" for i in record["bindings"]["items"])
            ):
                raise ValueError("already ready")
        elif inventory["status"] != "needs_review":
            raise ValueError("missing interpretation")
        return record
    except (KeyError, ValueError, TypeError) as error:
        raise DomainError(
            "author_clarification_checkpoint",
            "仅已完整返回的来源待核停点可补充；未知派发、格式失败或已就绪结果不重做。",
            409,
        ) from error


def checkpoint(domain, current, *, record_check=None, allow_running=False):
    state, payload = current["result"], current["payload"]
    if (
        current["kind"] != "workflow"
        or payload.get("mode") != "assisted"
        or payload.get("plot_contract_check") is not True
        or current["status"]
        not in ({"running"} if allow_running else {"blocked_runtime", "needs_review", "paused", "cancelled"})
        or any(
            state.get(k)
            for k in (
                "continued_to",
                "waiting_artifact",
                "waiting_structure",
                "pending_structure",
                "pause_after_maintenance_requested",
            )
        )
        or not authored.validate_task(current, domain)
    ):
        raise DomainError(
            "author_clarification_checkpoint",
            "仅半自动无候选的已知来源待核停点可显式补充；先处理正文、维护或活动任务。",
            409,
        )
    branch = domain.branch(current["branch_id"], current["novel_id"])
    if state.get("expected_story_revision") != branch["story_revision"]:
        raise DomainError("story_changed", "前文或故事已变化，请先核对影响，不沿旧依据补充。", 409)
    domain.check_genre_snapshot(branch["id"], state["genre_profile_snapshot"])
    domain.check_author_contract_snapshot(branch["id"], state["author_contract_snapshot"])
    number = state.get("next_chapter")
    plan = state.get("plan", {}).get("chapters")
    if (
        type(number) is not int
        or not 1 <= number <= payload["chapters"]
        or plan != payload.get("plots")
        or [p["number"] for p in plan] != list(range(1, payload["chapters"] + 1))
    ):
        raise DomainError(
            "author_clarification_checkpoint", "完整原计划或当前章无法核对，未清空轮次重建。", 409
        )
    original = next(e for e in state[authored.SNAPSHOT]["sources"] if e["number"] == number)
    entry = source_entry(current, original)
    record = (record_check or completed_unverified_record)(domain, current, entry)
    chapters = domain.db.all(
        "SELECT id,position,revision,title,content,stale,archived FROM chapters WHERE branch_id=? ORDER BY position,id",
        (branch["id"],),
    )
    target = [c for c in chapters if c["position"] == number and not c["archived"]]
    prior = [c for c in chapters if c["position"] < number and not c["archived"]]
    if (
        len(target) != 1
        or target[0]["revision"] != 0
        or target[0]["content"] != ""
        or [c["position"] for c in prior] != list(range(1, number))
        or any(not c["content"].strip() or c["stale"] for c in prior)
    ):
        raise DomainError(
            "author_clarification_late", "当前章已有正文或前章缺稿/失效，不能写前澄清续接。", 409
        )
    future = [c for c in chapters if c["position"] >= number and not c["archived"]]
    ids = {c["id"] for c in chapters if c["position"] >= number}
    if any(c["revision"] or c["content"] for c in future) or any(
        domain.db.all(
            "SELECT id FROM artifacts WHERE chapter_id=? AND kind IN ('chapter_draft','text_patch')",
            (c["id"],),
        )
        for c in future
    ):
        raise DomainError(
            "author_clarification_late",
            "当前或后续章已有正文、修订或候选，不借澄清改变原要求。",
            409,
        )
    tasks = lineage(domain, current)
    owners = {t["id"] for t in tasks}
    for other in domain.db.all(
        "SELECT id,status,result FROM tasks WHERE branch_id=? AND kind='workflow'", (branch["id"],)
    ):
        result = json.loads(other["result"])
        prepared = result.get("plot_preparations", {})
        if any(not str(k).isdigit() or int(k) >= number for k in prepared):
            raise DomainError("author_clarification_late", "本章或后章已有其他写前准备，保留其原记录。", 409)
        if any(
            type(r.get("source", {}).get("number")) is int
            and r["source"]["number"] >= number
            and (other["id"] not in owners or r["source"]["number"] > number)
            for r in result.get(authored.RECEIPTS, {}).values()
        ):
            raise DomainError(
                "author_clarification_late", "本章或后章已有独立批次来源准备，未混用或重发。", 409
            )
    events = domain.db.all(
        "SELECT e.kind,e.payload FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE t.branch_id=? AND t.kind IN ('workflow','module') AND e.kind IN ('chapter_started','step_dispatched')",
        (branch["id"],),
    )
    for event in events:
        data = json.loads(event["payload"])
        if not isinstance(data, dict):
            continue
        request = data.get("request", {})
        target_version = request.get("target_version", {}) if isinstance(request, dict) else {}
        if (
            event["kind"] == "chapter_started"
            and type(data.get("number")) is int
            and data["number"] >= number
        ) or (
            event["kind"] == "step_dispatched"
            and is_body_action(data.get("action"))
            and isinstance(target_version, dict)
            and target_version.get("id") in ids
        ):
            raise DomainError(
                "author_clarification_late",
                "当前或后章已派发正文，未知结果也不得通过新子任务重发。",
                409,
            )
    if domain.db.all(
        "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') AND cancel=0 LIMIT 1",
        (branch["id"], current["id"]),
    ):
        raise DomainError("active_task", "同工作版本仍有活动任务，未创建子批次。", 409)
    return branch, target[0], original, entry, record, tasks, chapters


def known_source_proof(domain, usage, record):
    owners = sorted({p["owner_task_id"] for p in record["phases"].values()})
    calls, steps, events = [], [], []
    for owner in owners:
        own_calls = [
            dict(r) for r in usage.execute("SELECT * FROM calls WHERE task_id=? ORDER BY id", (owner,))
        ]
        own_steps = domain.db.all(
            "SELECT task_id,fingerprint,result FROM task_steps WHERE task_id=? ORDER BY fingerprint",
            (owner,),
        )
        complete = {
            json.loads(r["result"]).get("call_id")
            for r in own_steps
            if isinstance(json.loads(r["result"]).get("text"), str)
        }
        if any(
            unresolved_call(c)
            or (c["id"] not in complete and any(c[k] for k in ("charged", "input_tokens", "output_tokens")))
            for c in own_calls
        ):
            raise DomainError(
                "unknown_result", "原来源调用或完整步骤回执尚未核实，预留保留，未借补充重发。", 409
            )
        if not complete or not any(c["id"] in complete for c in own_calls):
            raise DomainError(
                "unknown_result", "原来源缺少可对应的完整模型步骤与结算回执，未开始新工作。", 409
            )
        calls.extend(own_calls)
        steps.extend(own_steps)
        events.extend(
            domain.db.all(
                "SELECT id,kind,payload FROM task_events WHERE task_id=? AND kind IN ('step_dispatched','step_not_sent') ORDER BY id",
                (owner,),
            )
        )
    return {
        "owner_task_ids": owners,
        "call_ids": sorted(c["id"] for c in calls),
        "step_fingerprints": sorted(r["fingerprint"] for r in steps),
        "sha256": sha({"calls": calls, "steps": steps, "events": events}),
    }


def frozen_basis(current, checkpoint_value, costs, shared, account):
    branch, chapter, original, entry, record, tasks, chapters = checkpoint_value
    manuscripts = [{**c, "content": sha(c["content"])} for c in chapters]
    frozen = {
        "version": VERSION,
        "source_task_id": current["id"],
        "status": current["status"],
        "updated": current["updated"],
        "payload": current["payload"],
        "progress": current["result"],
        "story_revision": branch["story_revision"],
        "manuscripts": manuscripts,
        "budget": costs,
        "shared_budget": shared,
        "account": account,
    }
    return {
        "preview_basis_sha256": sha(frozen),
        "story_revision": branch["story_revision"],
        "chapter_revision": chapter["revision"],
        "chapter_content_sha256": sha(chapter["content"]),
        "manuscript_versions": manuscripts,
        "manuscript_sha256": sha(manuscripts),
        "plan_sha256": sha(current["result"]["plan"]),
        "source_receipt_sha256": sha(record),
        "payload_sha256": sha(current["payload"]),
        "progress_sha256": sha(current["result"]),
    }


def preview(domain, budget, current, *, usage=None):
    gate = checkpoint(domain, current)
    branch, chapter, original, entry, record, tasks, chapters = gate
    auxiliary = auxiliary_family(domain, tasks)
    if usage is None:
        with budget.db() as handle:
            costs = family_budget(domain, handle, current, tasks, provider(budget, current), auxiliary)
            source_proof = known_source_proof(domain, handle, record)
    else:
        costs = family_budget(domain, usage, current, tasks, provider(budget, current), auxiliary)
        source_proof = known_source_proof(domain, usage, record)
    costs = {**costs, "source_steps": source_proof}
    shared = budget.report()
    basis = frozen_basis(current, gate, costs, shared, provider(budget, current).public())
    return {
        "version": VERSION,
        "source_task_id": current["id"],
        "novel_id": current["novel_id"],
        "branch_id": branch["id"],
        "chapter_id": chapter["id"],
        "chapter": chapter["position"],
        "request_basis": {"preview_basis_sha256": basis["preview_basis_sha256"]},
        "baseline": basis,
        "original_source": deepcopy(original),
        "current_source": deepcopy(entry),
        "source_review": {
            "state": record["state"],
            "contract": deepcopy(record.get("contract")),
            "bindings": deepcopy(record.get("bindings")),
            "inventory": deepcopy(record["phases"]["observe"]["response"]),
        },
        "plan": deepcopy(current["result"]["plan"]),
        "policies": {k: deepcopy(v) for k, v in current["result"].items() if k.endswith("_version")},
        "adoption": current["payload"]["adoption"],
        "repair_round": current["result"].get("repair_round", 0),
        "account": provider(budget, current).public(),
        "budget": costs,
        "shared_budget": shared,
        "notice": "原话来源仍待核对，不代表作者写错。只补充原意兼容的角色/回指或安排说明，不强求匿名角色补姓名。提交会创建并开始同工作版本子批次，后续模型调用沿原账户和剩余额度收费；原情节、旧失败与费用保留。真正改向请另建独立批次。",
        "author_literary_approval": "unverified",
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
        raise DomainError("idempotency", "该提交标识已用于不同的补充或来源，保留原请求。", 409)
    child = domain.task(row["id"])
    authored.validate_task(child, domain)
    return child


def clarify(domain, budget, current, request):
    found = previous(domain, current, request)
    if found is not None:
        return found
    if not request["clarification"].strip():
        raise DomainError(
            "author_clarification_empty", "请填写本章补充说明；无需为明确的角色称谓另造姓名。", 409
        )
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
            raise DomainError("task_changed", "来源检查点已变化，补充保留，请显式重新读取依据。", 409)
        observed = preview(domain, budget, current, usage=usage)
        if observed["request_basis"]["preview_basis_sha256"] != request["preview_basis_sha256"]:
            raise DomainError(
                "author_clarification_basis",
                "全文、来源、策略或费用依据已变化，补充保留，请显式刷新确认。",
                409,
            )
        identifier, now = uid(), time()
        payload = {
            **deepcopy(current["payload"]),
            "client_request_id": request["client_request_id"],
            "budget_limit": observed["budget"]["remaining_run_limit"],
        }
        progress = deepcopy(current["result"])
        entry = observed["current_source"]
        receipt = {
            "version": VERSION,
            "task_id": identifier,
            "source_task_id": current["id"],
            "novel_id": current["novel_id"],
            "branch_id": current["branch_id"],
            "chapter": observed["chapter"],
            "chapter_id": observed["chapter_id"],
            "source_payload_sha256": sha(current["payload"]),
            "source_progress_sha256": sha(current["result"]),
            "previous_source_receipt": deepcopy(progress[authored.RECEIPTS][entry["key"]]),
            "original_source_entry": observed["original_source"],
            "previous_source_entry": entry,
            "source_entry": combine(entry, request),
            "plan_sha256": observed["baseline"]["plan_sha256"],
            "baseline": observed["baseline"],
            "account": observed["account"],
            "request": deepcopy(request),
            "budget": observed["budget"],
            "policies": observed["policies"],
            "policy": "仅追加显式补充；独立保持通过后才继续正文，原情节与历史不改，非文学批准。",
            "author_literary_approval": "unverified",
        }
        progress.update(continued_from=current["id"], **{KEY: VERSION, RECEIPT: receipt})
        progress[authored.RECEIPTS].pop(entry["key"])
        progress.pop(CHECKS, None)
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
        domain.db.event(tx, current["id"], "author_source_clarified", event)
        domain.db.event(tx, identifier, EVENT, event)
    return domain.task(identifier)


def units(text, prefix):
    return [{**u, "source_id": f"{prefix}{i:04}"} for i, u in enumerate(authored.core.units(text), 1)]


def check_issues(value, original, addition, fixed):
    issues = []
    if not value["author_contract_check"]["reason"].strip():
        issues.append("采用约定核对须说明依据；不能凭空白理由判通过。")
    choices = {u["source_id"] for u in [*original, *addition, *fixed]}
    for name, expected in (
        ("source_checks", original),
        ("addition_checks", addition),
        ("fixed_checks", fixed),
    ):
        rows = value[name]
        if [r["source_id"] for r in rows] != [u["source_id"] for u in expected]:
            issues.append(f"{name}须按全部原编号顺序各核一次，不删除或合并未知事项。")
        for row in rows:
            ids = row["evidence_ids"]
            if len(ids) != len(set(ids)) or any(i not in choices for i in ids) or not row["reason"].strip():
                issues.append("只引用本次连续原话编号并说明理由，不能从候选或推测补造证据。")
            positive = row.get("result") == "pass" or row.get("classification") == "clarification"
            if positive and not ids:
                issues.append("通过或兼容澄清须有本次原话依据，不确定可unknown。")
    return issues


async def before_prose(worker, plot, contract):
    receipt = validate_task(worker.task, worker.domain)
    if receipt is None or receipt["chapter"] != plot["number"]:
        return
    original = units(receipt["previous_source_entry"]["text"], "ORIGINAL")
    addition = units(receipt["request"]["clarification"], "ADDITION")
    fixed_text = worker.task["payload"]["fixed"] + [
        v for v in plot["fixed"] if v not in worker.task["payload"]["fixed"]
    ]
    fixed = [{"source_id": f"FIX{i:03}", "text": v} for i, v in enumerate(fixed_text, 1)]
    record = worker.task["result"].setdefault(
        CHECKS,
        {
            "version": VERSION,
            "receipt_sha256": sha(receipt),
            "contract_sha256": sha(contract),
            "phases": {},
            "status": "pending",
        },
    )
    if (
        record["version"] != VERSION
        or record["receipt_sha256"] != sha(receipt)
        or record["contract_sha256"] != sha(contract)
    ):
        raise DomainError("author_clarification_changed", "补充保持核对依据改变，未复用或重发。", 409)
    schema = Preservation.model_json_schema()
    choices = [u["source_id"] for u in [*original, *addition, *fixed]]
    for name in ("SourceCheck", "AdditionCheck"):
        schema["$defs"][name]["properties"]["evidence_ids"]["items"]["enum"] = choices
    for field, definition, source in (
        ("source_checks", "SourceCheck", original),
        ("addition_checks", "AdditionCheck", addition),
        ("fixed_checks", "SourceCheck", fixed),
    ):
        schema["properties"][field].update(minItems=len(source), maxItems=len(source))
        schema["properties"][field]["items"] = deepcopy(schema["$defs"][definition])
        if source:
            schema["properties"][field]["items"]["properties"]["source_id"]["enum"] = [
                u["source_id"] for u in source
            ]
    result = await phase(
        worker,
        record,
        "preserve",
        "checking",
        lambda: worker.grounded(
            {
                "动作": ACTION,
                KEY: VERSION,
                "原话与既有补充": receipt["previous_source_entry"]["text"],
                "本次显式补充": receipt["request"]["clarification"],
                "补充归属": receipt["request"]["actor"],
                "原话连续单元": original,
                "本次补充单元": addition,
                "本章及全书固定条件": fixed,
                "采用的全书创作约定": worker.author_snapshot(),
                "新来源解释（AI提案，非作者新增条件）": contract,
                "要求": "独立逐原句、逐补充与全部固定条件核对原意兼容；不能因新解释自称保持就通过。原事件、结果、否定、许可、仅向范围、原件/副本、阶段和保留未知不可改；明确角色回指/分工可澄清，但未命名且范围清楚不要求补姓名。补充若取消原结果、增加独立事件、改动原剧情方向或无法确认兼容，分别direction_change/unknown并停止；真正改向应另建批次。原AI未明不是原话错误或作者义务，原提案/先行观察不是事实。全书尚未到的后续结果只检查解释仍保留，不要求本章提前兑现。仅给定原话、显式补充、采用约定及新解释，无正文、候选、未来章或自由reason；不采纳、不写事实、不作文学批准。source_checks/addition_checks/fixed_checks按各自完整编号顺序各一次；evidence_ids只填本次连续单元编号，未知可空。",
            },
            Preservation,
            {"scope": "显式作者补充的独立来源保持；无正文或候选，原话为裁决依据"},
            lambda v: check_issues(v, original, addition, fixed),
            contract=schema,
            diagnostic_label="作者补充原意保持",
        ),
        owner_ids={t["id"] for t in lineage(worker.domain, worker.task)},
    )
    passed = (
        all(r["result"] == "pass" for name in ("source_checks", "fixed_checks") for r in result[name])
        and all(r["classification"] == "clarification" for r in result["addition_checks"])
        and result["author_contract_check"]["result"] == "pass"
    )
    record.update(
        status="ready" if passed else "unverified",
        result=deepcopy(result),
        author_literary_approval="unverified",
    )
    worker.checkpoint(worker.task["result"])
    if not passed:
        raise DomainError(
            "author_clarification_unverified",
            "补充与原意或创作约定尚未核实，原话及报告保留；未开始正文，真正改向请另建批次。",
            409,
        )


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/tasks/{task_id}/author-source-clarification-preview")
    def clarification_preview(task_id: str):
        return preview(domain, budget, domain.task(task_id))

    # Opaque POST nonces may contain slash, LF or literal percent escapes.
    # Query parameters preserve them across URL/ASGI path decoding. Keep the
    # original segment route for existing clients and receipts.
    @app.get("/api/tasks/{task_id}/author-source-clarifications")
    @app.get("/api/tasks/{task_id}/author-source-clarifications/{client_request_id}")
    def clarification_receipt(task_id: str, client_request_id: str):
        parent = domain.task(task_id)
        rows = domain.db.all(
            "SELECT result FROM tasks WHERE id IN (SELECT task_id FROM task_requests WHERE key=?)",
            (client_request_id,),
        )
        task = None
        if rows:
            request = json.loads(rows[0]["result"]).get(RECEIPT, {}).get("request")
            if not isinstance(request, dict):
                raise DomainError("idempotency", "该标识不属于此补充提交，保留原标识。", 409)
            task = previous(domain, parent, request)
        return {"source_task_id": task_id, "client_request_id": client_request_id, "task": task}

    @app.post("/api/tasks/{task_id}/author-source-clarifications", status_code=202)
    def author_source_clarification(task_id: str, data: ClarificationInput):
        current = domain.task(task_id)
        request = data.model_dump()
        found = previous(domain, current, request)
        if found is not None:
            return found
        provider_ready(current["payload"]["account"])
        return clarify(domain, budget, current, request)
