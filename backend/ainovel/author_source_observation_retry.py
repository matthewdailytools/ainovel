"""One explicit child for three known-returned, invalid legacy observations."""

from copy import deepcopy
import hashlib
import json
from time import time
from types import SimpleNamespace
from typing import Literal

from pydantic import Field

from . import authored_source_scope as authored
from . import authored_source_classification as classification
from . import author_source_clarification as clarification
from .budget import Budget
from .conditional_continuation import call_audit, unresolved_call
from .db import DomainError, dumps, uid
from .models import Input

VERSION = "author-source-observation-retry/1"
KEY = "author_source_observation_retry_version"
RECEIPT = "author_source_observation_retry"
EVENT = "author_source_observation_retried_from"
sha = authored.sha


class ObservationRetryInput(Input):
    version: Literal["author-source-observation-retry/1"]
    client_request_id: str = Field(min_length=8, max_length=128)
    preview_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


def reject(message, code="author_observation_retry_checkpoint"):
    raise DomainError(code, message, 409)


def pending_record(domain, current, entry):
    record = current["result"].get(authored.RECEIPTS, {}).get(entry["key"])
    try:
        slot = record["phases"]["observe"]
        if (
            record["version"] != classification.VERSION
            or record["source"] != entry
            or record["source_sha256"] != entry["sha256"]
            or record["state"] != "pending"
            or record.get("inherited") is not None
            or set(record["phases"]) != {"observe"}
            or slot["owner_task_id"] != current["id"]
            or slot["status"] != "started"
            or not isinstance(slot["attempt"], str)
            or not slot["attempt"]
            or current["attempt"] != slot["attempt"]
            or "response" in slot
            or "response_sha256" in slot
            or slot.get("not_sent_history") != []
            or not isinstance(slot["before_steps"], list)
            or not isinstance(slot["before_calls"], list)
            or len(set(slot["before_steps"])) != len(slot["before_steps"])
            or len(set(slot["before_calls"])) != len(slot["before_calls"])
            or any(k in record for k in ("contract", "bindings", "preservation_input"))
        ):
            raise ValueError("not an untouched observation")
    except KeyError, TypeError, ValueError:
        reject("仅原账户未完成的旧观察及完整原始现场可核对；未清空或改造来源回执。")
    return record


def checkpoint(domain, current):
    from . import authored_source_execution as execution

    chain = list(authored.ancestry(current, domain))
    if (
        current["status"] != "blocked_runtime"
        or current["result"].get(authored.KEY) != classification.VERSION
        or any(KEY in t["result"] or RECEIPT in t["result"] for t in chain)
        or any(
            domain.db.all("SELECT id FROM task_events WHERE task_id=? AND kind=?", (t["id"], EVENT))
            for t in chain
        )
        or any(
            execution.KEY in t["result"]
            or execution.SNAPSHOT in t["result"]
            or execution.KEY in t["result"].get(authored.SNAPSHOT, {})
            for t in chain
        )
    ):
        reject("仅旧观察执行模板的一次已返回引用失败可显式恢复；新版或已恢复来源不重复收费。")
    gate = clarification.checkpoint(domain, current, record_check=pending_record)
    for owner in reversed(gate[5]):
        for event in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='checkpoint' ORDER BY id",
            (owner["id"],),
        ):
            first_plan = json.loads(event["payload"]).get("plan")
            if first_plan is not None:
                if first_plan != current["result"]["plan"]:
                    reject("完整原规划含结局方向已变化，未沿旧引用失败建立新批次。")
                return gate
    reject("完整原规划缺少独立检查点，未补造结局方向或重建计划。")


def legacy_frame(domain, current, entry):
    """Read the untouched /1 pure builder, without a provider or database write."""
    return classification.observation_request(entry)


def request_identity(current, provider, settings, frame, instruction):
    """Match Worker.call's exact legacy semantic cache and sent wire metadata."""
    from .worker import SYSTEM
    from .prompt_encoding import encode_prompt
    from .scene_unknown_reason_correction import model_context

    context = model_context(deepcopy(frame["context"]))
    schema, contract = frame["schema"], frame["contract"]
    maximum = provider.output_limit(frame.get("max_output", 4096), structured=True)
    semantic = dumps({"任务": instruction, "上下文": context})
    prompt = dumps(encode_prompt(instruction, context))
    wire = {}
    if provider.kind != "openai":
        suffix = "\n只返回符合以下 JSON Schema 的对象：\n" + dumps(contract)
        prompt += suffix
        semantic += suffix
    else:
        from .providers import WIRE_SCHEMA_VERSION, strict_schema

        wire = {
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "wire_schema_sha256": hashlib.sha256(
                json.dumps(
                    strict_schema(contract),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest(),
        }
    basis = [
        provider.id,
        provider.kind,
        provider.model,
        provider.base_url,
        provider.reasoning,
        SYSTEM,
        semantic,
        maximum,
    ]
    if provider.kind == "openai":
        basis.append(contract)
    if epoch := current["result"].get("generation_epoch"):
        basis.append(epoch)
    profile, author = (
        current["result"]["genre_profile_snapshot"],
        current["result"]["author_contract_snapshot"],
    )
    request = {
        **wire,
        **(
            {"plot_contract_version": instruction["情节核对契约版本"]}
            if "情节核对契约版本" in instruction
            else {}
        ),
        "system_sha256": sha(SYSTEM),
        "prompt_sha256": sha(prompt),
        "semantic_prompt_sha256": sha(semantic),
        "prompt_utf8_bytes": len(prompt.encode()),
        "max_output_tokens": maximum,
        authored.KEY: classification.VERSION,
        "account": provider.public(),
        "reasoning_effort": provider.reasoning,
        "service_tier": "default" if provider.kind == "openai" else None,
        "input_limits": provider.limits(),
        "timeout_seconds": settings.timeout,
        "idle_timeout_seconds": settings.idle_timeout,
        "story_revision": None,
        "genre_profile": {
            "catalog_version": profile.get("catalog_version"),
            "source_artifact_id": profile.get("source_artifact_id"),
            "source_story_revision": profile.get("source_story_revision"),
            "snapshot_sha256": sha(profile),
            "legacy_unconfigured": profile.get("legacy_unconfigured", False),
        },
        "author_contract": {
            "contract_version": author.get("contract_version"),
            "source_artifact_id": author.get("source_artifact_id"),
            "source_story_revision": author.get("source_story_revision"),
            "snapshot_sha256": sha(author),
            "legacy_unconfigured": author.get("legacy_unconfigured", False),
        },
        "target_version": {},
        "source_version_basis": None,
        "source_versions": {
            k: [] for k in ("recent", "facts", "definitions", "selected_external_references")
        },
        "quoted_units": [
            {k: row[k] for k in ("source_id", "chapter_id", "revision", "start", "end") if k in row}
            for row in instruction["可引用原文"]
        ],
    }
    return sha(basis), request, schema.__name__


def known_returned_proof(
    domain,
    usage,
    current,
    entry,
    record,
    settings,
    *,
    phase_key="observe",
    frame=None,
    stop_event_id=None,
    terminal_error=None,
):
    from .worker import PROMPT_VERSION, parse_json

    chosen = clarification.provider(SimpleNamespace(settings=settings), current)
    slot = record["phases"][phase_key]
    all_steps = domain.db.all(
        "SELECT task_id,fingerprint,result FROM task_steps WHERE task_id=? ORDER BY fingerprint",
        (current["id"],),
    )
    own_calls = [
        dict(r) for r in usage.execute("SELECT * FROM calls WHERE task_id=? ORDER BY id", (current["id"],))
    ]
    before_steps, before_calls = set(slot["before_steps"]), set(slot["before_calls"])
    if not before_steps <= {s["fingerprint"] for s in all_steps} or not before_calls <= {
        c["id"] for c in own_calls
    }:
        reject("旧阶段前像缺失，原始返回及费用不能核实。", "unknown_result")
    steps = [s for s in all_steps if s["fingerprint"] not in before_steps]
    calls = [c for c in own_calls if c["id"] not in before_calls]
    events = domain.db.all(
        "SELECT id,kind,payload FROM task_events WHERE task_id=? ORDER BY id", (current["id"],)
    )
    if stop_event_id is not None:
        events = [event for event in events if event["id"] <= stop_event_id]
    running = [
        e
        for e in events
        if e["kind"] == "running" and json.loads(e["payload"]) == {"attempt": slot["attempt"]}
    ]
    if len(running) != 1:
        reject("原观察尝试的独立运行见证缺失，未重发。", "unknown_result")
    interval = [e for e in events if e["id"] > running[0]["id"]]
    if any(e["kind"] in {"running", "interrupted", "resume_requested", "step_not_sent"} for e in interval):
        reject("旧观察存在其他尝试或未知过程，未混用返回重发。", "unknown_result")
    witnessed = [
        e
        for e in interval
        if e["kind"] == "checkpoint"
        and json.loads(e["payload"])
        .get(authored.RECEIPTS, {})
        .get(entry["key"], {})
        .get("phases", {})
        .get(phase_key)
        == slot
    ]
    if not witnessed:
        reject("旧观察派发前检查点不完整，未重发。", "unknown_result")
    after = [e for e in interval if e["id"] > witnessed[0]["id"]]
    relevant = [
        e for e in after if e["kind"] in {"step_dispatched", "step_completed", "citation_repair", "stopped"}
    ]
    expected_kinds = [
        kind for _ in range(3) for kind in ("step_dispatched", "step_completed", "citation_repair")
    ] + ["stopped"]
    if (
        len(steps) != 3
        or len(calls) != 3
        or [e["kind"] for e in relevant] != expected_kinds
        or any(unresolved_call(c) for c in calls)
    ):
        reject(
            "须核实同一次观察三份完整返回、两轮引用修正及已结算调用；未知或缺失回执不重发。",
            "unknown_result",
        )
    frame = frame or legacy_frame(domain, current, entry)
    instruction = deepcopy(frame["instruction"])
    by_step, by_call = {s["fingerprint"]: s for s in steps}, {c["id"]: c for c in calls}
    proof_steps, seen_calls, errors = [], set(), []
    metadata = []
    for index in range(3):
        sent, completed, repaired = [json.loads(e["payload"]) for e in relevant[index * 3 : index * 3 + 3]]
        fingerprint, request, schema_name = request_identity(current, chosen, settings, frame, instruction)
        if (
            sent
            != {
                "fingerprint": fingerprint,
                "action": frame["instruction"].get("动作", authored.ACTION),
                "schema": schema_name,
                "template_version": PROMPT_VERSION,
                "request": request,
            }
            or fingerprint not in by_step
        ):
            reject("旧派发语义、线缆契约、配置或修正次序不符，未重发。", "unknown_result")
        step = by_step[fingerprint]
        try:
            raw = json.loads(step["result"])
            if not isinstance(raw["text"], str) or not raw["text"] or raw["call_id"] not in by_call:
                raise ValueError("incomplete raw result")
            value = frame["schema"].model_validate(parse_json(raw["text"])).model_dump()
            issues = frame["validator"](value)
        except KeyError, TypeError, ValueError, DomainError:
            reject("旧返回不是可完整解析的原结构产物，不能证明仅引用失败。", "unknown_result")
        if not issues:
            reject("原缓存已有可诚实通过的观察，请保留原任务复验恢复，不另开收费观察。")
        call = by_call[raw["call_id"]]
        meta = usage.execute(
            "SELECT actual_model,request_id FROM call_metadata WHERE call_id=?", (call["id"],)
        ).fetchone()
        if (
            call["id"] in seen_calls
            or call["account"] != chosen.id
            or call["provider"] != chosen.kind
            or call["model"] != chosen.model
            or raw.get("requested_model") != chosen.model
            or type(call["charged"]) is not int
            or call["charged"] < 0
            or type(call["reserved"]) is not int
            or call["reserved"] < 0
            or call["currency"] != chosen.public()["currency"]
            or call["input_rate"] != str(chosen.input_rate)
            or call["output_rate"] != str(chosen.output_rate)
            or meta is None
            or (meta["actual_model"] or chosen.model) != raw.get("model")
            or meta["request_id"] != raw.get("request_id")
            or raw.get("usage") != {"input": call["input_tokens"], "output": call["output_tokens"]}
            or completed
            != {
                "fingerprint": fingerprint,
                "call_id": call["id"],
                "model": raw.get("model"),
                "usage": raw.get("usage"),
            }
            or repaired != {"round": index, "issues": issues}
        ):
            reject("旧返回、用量、引用错误与结算不一一对应，未重发。", "unknown_result")
        seen_calls.add(call["id"])
        metadata.append({"call_id": call["id"], **dict(meta)})
        errors.append(issues)
        proof_steps.append(
            {
                "fingerprint": fingerprint,
                "call_id": call["id"],
                "raw_sha256": sha(step["result"]),
                "dispatch_event_id": relevant[index * 3]["id"],
                "issues": issues,
            }
        )
        if "repair_request" in frame:
            instruction = frame["repair_request"](value, issues)
        else:
            feedback = frame["repair_feedback"]()
            instruction = {
                **frame["instruction"],
                "前次产物": value,
                "定位错误": issues,
                **feedback,
            }
    stopped = json.loads(relevant[-1]["payload"])
    message = "两轮引用修正后仍无法回到原文，未采纳或自动推进。"
    if frame.get("diagnostic_label"):
        message += f" {frame['diagnostic_label']}（{len(errors[-1])}项）：" + "；".join(
            i[:350] for i in errors[-1][:6]
        )
        if len(errors[-1]) > 6:
            message += f"；另{len(errors[-1]) - 6}项详见任务引用修正记录"
    if (
        stopped != {"code": "evidence", "message": message, "provider_details": None}
        or (current["error"] if terminal_error is None else terminal_error) != message
    ):
        reject("原终态不是同次已知引用失败，不能据错误字符串单独重发。", "unknown_result")
    return {
        "version": VERSION,
        "owner_task_id": current["id"],
        "attempt": slot["attempt"],
        "steps": proof_steps,
        "calls": [
            call_audit(
                by_call[s["call_id"]],
                complete_step=True,
                scope="source_observation" if phase_key == "observe" else "source_interpretation",
            )
            for s in proof_steps
        ],
        "call_ids": sorted(seen_calls),
        "errors": errors,
        "stopped_event_id": relevant[-1]["id"],
        "sha256": sha(
            {
                "steps": all_steps,
                "calls": own_calls,
                "events": [e for e in events if e["id"] <= relevant[-1]["id"]],
                "call_metadata": metadata,
            }
        ),
    }


def settled_provider(usage, provider):
    if any(
        unresolved_call(dict(c))
        for c in usage.execute("SELECT * FROM calls WHERE provider=?", (provider.kind,))
    ):
        reject("同服务商仍有未决调用，保留全部预留，未开启新观察。", "unknown_result")


def preview(domain, budget, current, *, usage=None):
    from . import authored_source_execution as execution
    from .author_source_recheck import ensure_runtime as ensure_recheck

    gate = checkpoint(domain, current)
    ensure_recheck(current, domain, budget.settings)
    branch, chapter, original, entry, record, tasks, chapters = gate
    chosen = clarification.provider(budget, current)
    auxiliary = clarification.auxiliary_family(domain, tasks)

    def costs_and_proof(handle):
        settled_provider(handle, chosen)
        costs = clarification.family_budget(domain, handle, current, tasks, chosen, auxiliary)
        proof = known_returned_proof(domain, handle, current, entry, record, budget.settings)
        return costs, proof

    if usage is None:
        with budget.db() as handle:
            costs, proof = costs_and_proof(handle)
    else:
        costs, proof = costs_and_proof(usage)
    shared = budget.report()
    basis = clarification.frozen_basis(
        current, gate, {**costs, "known_returned_proof": proof}, shared, chosen.public()
    )
    basis["preview_basis_sha256"] = sha(
        {
            "version": VERSION,
            "basis": basis["preview_basis_sha256"],
            "old_execution_version": execution.LEGACY_VERSION,
            "new_execution_version": execution.VERSION,
        }
    )
    return {
        "version": VERSION,
        "source_policy": classification.VERSION,
        "old_execution_version": execution.LEGACY_VERSION,
        "new_execution_version": execution.VERSION,
        "source_task_id": current["id"],
        "novel_id": current["novel_id"],
        "branch_id": branch["id"],
        "chapter_id": chapter["id"],
        "chapter": chapter["position"],
        "original_source": deepcopy(original),
        "current_source": deepcopy(entry),
        "plan": deepcopy(current["result"]["plan"]),
        "policies": {k: deepcopy(v) for k, v in current["result"].items() if k.endswith("_version")},
        "adoption": current["payload"]["adoption"],
        "repair_round": current["result"].get("repair_round", 0),
        "account": chosen.public(),
        "budget": costs,
        "shared_budget": shared,
        "known_returned_proof": proof,
        "request_basis": {"preview_basis_sha256": basis["preview_basis_sha256"]},
        "baseline": basis,
        "eligible": True,
        "starts_child": True,
        "source_unchanged": True,
        "author_literary_approval": "unverified",
        "notice": "原观察三份完整返回均未通过原引用校验。显式创建一次新执行模板子批次，保留同一作者原话、完整规划、旧失败及累计费用；不代表作者写错或文学质量通过。创建后将按原采纳方式继续写作并沿原账户和剩余额度收费；未知仍停止，不重复同版恢复。原因仅作审计，不加入剧情。",
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
        reject("该提交标识已用于其他来源或恢复决定，请保留原请求。", "idempotency")
    child = domain.task(row["id"])
    authored.validate_task(child, domain)
    return child


def retry(domain, budget, current, request):
    from . import authored_source_execution as execution

    if found := previous(domain, current, request):
        return found
    if not request["reason"].strip():
        reject("请填写显式恢复原因，原因仅作审计，不作为剧情来源。", "author_observation_retry_reason")
    with budget.db(True) as usage, domain.db.connect(True) as tx:
        if found := previous(domain, current, request, tx):
            return found
        latest = tx.execute("SELECT * FROM tasks WHERE id=?", (current["id"],)).fetchone()
        if (
            not latest
            or latest["updated"] != current["updated"]
            or latest["status"] != current["status"]
            or json.loads(latest["payload"]) != current["payload"]
            or json.loads(latest["result"]) != current["result"]
        ):
            reject("来源检查点已改变，请保留请求并重新读取依据。", "task_changed")
        value = preview(domain, budget, current, usage=usage)
        if request["preview_basis_sha256"] != value["request_basis"]["preview_basis_sha256"]:
            reject(
                "来源、完整规划、原账户或费用依据改变，请显式重读。",
                "author_observation_retry_basis",
            )
        identifier, now = uid(), time()
        payload = {
            **deepcopy(current["payload"]),
            "client_request_id": request["client_request_id"],
            "budget_limit": value["budget"]["remaining_run_limit"],
        }
        progress = deepcopy(current["result"])
        entry = value["current_source"]
        receipt = {
            "version": VERSION,
            "task_id": identifier,
            "source_task_id": current["id"],
            "novel_id": current["novel_id"],
            "branch_id": current["branch_id"],
            "chapter_id": value["chapter_id"],
            "chapter": value["chapter"],
            "source_policy": classification.VERSION,
            "old_execution_version": execution.LEGACY_VERSION,
            "new_execution_version": execution.VERSION,
            "source_unchanged": True,
            "starts_child": True,
            "source_payload_sha256": sha(current["payload"]),
            "source_progress_sha256": sha(current["result"]),
            "previous_source_receipt": deepcopy(progress[authored.RECEIPTS][entry["key"]]),
            "original_source_entry": value["original_source"],
            "source_entry": deepcopy(entry),
            "plan_sha256": sha(progress["plan"]),
            "repair_round": value["repair_round"],
            "adoption": value["adoption"],
            "previous_policies": value["policies"],
            "policies": {**value["policies"], execution.KEY: execution.VERSION, KEY: VERSION},
            "baseline": value["baseline"],
            "request": deepcopy(request),
            "account": value["account"],
            "budget": value["budget"],
            "known_returned_proof": value["known_returned_proof"],
            "author_literary_approval": "unverified",
        }
        progress.update(continued_from=current["id"], **{KEY: VERSION, RECEIPT: receipt})
        progress[authored.RECEIPTS].pop(entry["key"])
        execution.clone_source_witness(current, identifier, payload, progress)
        execution.freeze_execution(
            domain, identifier, "workflow", payload, progress, version=execution.VERSION
        )
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
        domain.db.event(tx, current["id"], "author_source_observation_retried", event)
        domain.db.event(tx, identifier, EVENT, event)
        authored.event(domain.db, tx, identifier, payload, progress)
    return domain.task(identifier)


def validate_task(task, domain, chain=None):
    from . import authored_source_execution as execution

    chain = list(chain or authored.ancestry(task, domain))
    events = [
        e
        for t in chain
        for e in domain.db.all("SELECT payload FROM task_events WHERE task_id=? AND kind=?", (t["id"], EVENT))
    ]
    marker, receipt = task["result"].get(KEY), task["result"].get(RECEIPT)
    if marker is None and receipt is None and not events:
        return None
    try:
        if marker != VERSION or receipt["version"] != VERSION:
            raise ValueError("missing marker")
        request = ObservationRetryInput.model_validate(receipt["request"]).model_dump()
        origin = next(t for t in chain if t["id"] == receipt["task_id"])
        parent = next(t for t in chain if t["id"] == receipt["source_task_id"])
        clean_parent = {k: v for k, v in parent["result"].items() if k != "continued_to"}
        entry = receipt["source_entry"]
        old_source, new_source = (
            parent["result"][authored.SNAPSHOT],
            origin["result"][authored.SNAPSHOT],
        )
        strip = {"task_id", "payload_sha256", execution.KEY}
        expected = {
            "version": VERSION,
            "source_task_id": parent["id"],
            "task_id": origin["id"],
            "receipt_sha256": sha(receipt),
        }
        rows = domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (origin["id"], EVENT)
        )
        if (
            origin["result"].get(RECEIPT) != receipt
            or origin["result"].get(KEY) != VERSION
            or parent["result"].get("continued_to") != origin["id"]
            or origin["result"].get("continued_from") != parent["id"]
            or parent["kind"] != origin["kind"]
            or origin["kind"] != task["kind"]
            or task["kind"] != "workflow"
            or parent["branch_id"] != origin["branch_id"]
            or origin["branch_id"] != task["branch_id"]
            or receipt["branch_id"] != task["branch_id"]
            or receipt["novel_id"] != parent["novel_id"]
            or parent["novel_id"] != origin["novel_id"]
            or task["novel_id"] != origin["novel_id"]
            or parent["result"].get(authored.KEY) != classification.VERSION
            or task["result"].get(authored.KEY) != classification.VERSION
            or execution.KEY in parent["result"]
            or execution.SNAPSHOT in parent["result"]
            or origin["result"].get(execution.KEY) != execution.VERSION
            or receipt["source_policy"] != classification.VERSION
            or receipt["old_execution_version"] != execution.LEGACY_VERSION
            or receipt["new_execution_version"] != execution.VERSION
            or receipt["source_unchanged"] is not True
            or receipt["starts_child"] is not True
            or receipt["source_payload_sha256"] != sha(parent["payload"])
            or receipt["source_progress_sha256"] != sha(clean_parent)
            or receipt["previous_source_receipt"] != parent["result"][authored.RECEIPTS][entry["key"]]
            or receipt["previous_source_receipt"]["source"] != entry
            or receipt["plan_sha256"] != sha(origin["result"]["plan"])
            or origin["result"]["plan"] != parent["result"]["plan"]
            or task["result"]["plan"] != origin["result"]["plan"]
            or receipt["repair_round"] != parent["result"].get("repair_round", 0)
            or receipt["adoption"] != origin["payload"]["adoption"]
            or origin["payload"]["account"] != parent["payload"]["account"]
            or {k: v for k, v in origin["payload"].items() if k not in {"client_request_id", "budget_limit"}}
            != {k: v for k, v in parent["payload"].items() if k not in {"client_request_id", "budget_limit"}}
            or origin["payload"]["client_request_id"] != request["client_request_id"]
            or origin["payload"]["budget_limit"] != receipt["budget"]["remaining_run_limit"]
            or {k: v for k, v in new_source.items() if k not in strip}
            != {k: v for k, v in old_source.items() if k not in strip}
            or any(origin["result"].get(k) != v for k, v in receipt["policies"].items())
            or len(rows) != 1
            or json.loads(rows[0]["payload"]) != expected
            or not domain.db.all(
                "SELECT key FROM task_requests WHERE key=? AND task_id=?",
                (request["client_request_id"], origin["id"]),
            )
        ):
            raise ValueError("source or receipt changed")
    except KeyError, TypeError, ValueError, StopIteration:
        reject(
            "来源、完整计划、新执行模板或独立恢复见证不符，未派发。",
            "author_observation_retry_version",
        )
    return receipt


def ensure_runtime(task, domain, settings):
    receipt = validate_task(task, domain)
    if receipt is None:
        return
    chain = list(authored.ancestry(task, domain))
    origin = next(t for t in chain if t["id"] == receipt["task_id"])
    parent = next(t for t in chain if t["id"] == receipt["source_task_id"])
    chosen = settings.providers.get(task["payload"]["account"])
    changed = clarification.authorized_account_descendant(domain, chain, origin)
    if chosen is None or (not changed and chosen.public() != receipt["account"]):
        reject("恢复所冻结原账户配置已改变，未静默改模型派发。", "configuration_changed")
    with Budget(settings).db() as usage:
        settled_provider(usage, chosen)
        proof = known_returned_proof(
            domain,
            usage,
            parent,
            receipt["source_entry"],
            receipt["previous_source_receipt"],
            settings,
        )
    if proof != receipt["known_returned_proof"]:
        reject("原三份已付返回或结算证明已变化，未派发新调用。", "unknown_result")
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
        request = data.get("request", {})
        target = request.get("target_version", {}) if isinstance(request, dict) else {}
        if (
            event["kind"] == "chapter_started"
            and type(data.get("number")) is int
            and data["number"] >= receipt["chapter"]
        ) or (
            event["kind"] == "step_dispatched"
            and clarification.is_body_action(data.get("action"))
            and isinstance(target, dict)
            and target.get("id") in targets
        ):
            reject(
                "恢复等待期间当前或后章已有其他正文派发，保留现场，未跨任务重发。",
                "author_observation_retry_late",
            )


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/tasks/{task_id}/author-source-observation-retry-preview")
    def read_preview(task_id: str):
        return preview(domain, budget, domain.task(task_id))

    @app.get("/api/tasks/{task_id}/author-source-observation-retries")
    @app.get("/api/tasks/{task_id}/author-source-observation-retries/{client_request_id}")
    def read_receipt(task_id: str, client_request_id: str):
        parent = domain.task(task_id)
        rows = domain.db.all(
            "SELECT result FROM tasks WHERE id IN (SELECT task_id FROM task_requests WHERE key=?)",
            (client_request_id,),
        )
        task = None
        if rows:
            request = json.loads(rows[0]["result"]).get(RECEIPT, {}).get("request")
            if not isinstance(request, dict):
                reject("该标识不属于本次观察恢复，保留原标识。", "idempotency")
            task = previous(domain, parent, request)
        return {"source_task_id": task_id, "client_request_id": client_request_id, "task": task}

    @app.post("/api/tasks/{task_id}/author-source-observation-retries", status_code=202)
    def create_retry(task_id: str, data: ObservationRetryInput):
        current = domain.task(task_id)
        request = data.model_dump()
        if found := previous(domain, current, request):
            return found
        provider_ready(current["payload"]["account"])
        return retry(domain, budget, current, request)
