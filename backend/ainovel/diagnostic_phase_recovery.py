"""One explicit resume may repair this proved diagnostic-index migration only.

No new source policy, model task, epoch or quota is created. Original failures
remain in task_steps/calls; the existing phase still owns its replay boundary.
"""

from copy import deepcopy
import json
from types import SimpleNamespace

from . import authored_source_scope as authored
from . import authored_source_execution as execution
from . import author_source_clarification as clarification
from .author_source_observation_retry import known_returned_proof, request_identity
from .db import DomainError, dumps

MARK = "diagnostic_feedback_recovery"
FIX = "scope_binding_item_indices"
PHASE = "interpret"
LIMIT = 2
sha = authored.sha


def require(value, message):
    if not value:
        raise DomainError("plan_preparation_unknown", message, 409)


def plain_slot(slot):
    return {k: deepcopy(v) for k, v in slot.items() if k != MARK}


def record_check(domain, current, entry):
    record = current["result"].get(authored.RECEIPTS, {}).get(entry["key"])
    require(
        isinstance(record, dict) and record.get("state") == "pending",
        "仅原来源未完成的解释阶段可核对诊断恢复。",
    )
    phases = record.get("phases", {})
    observe, slot = phases.get("observe", {}), phases.get(PHASE, {})
    require(
        set(phases) == {"observe", PHASE}
        and observe.get("status") == "completed"
        and observe.get("response_sha256") == sha(observe.get("response"))
        and authored.inventory_valid(observe.get("response"), entry)
        and observe["response"]["status"] == "supported"
        and slot.get("owner_task_id") == current["id"]
        and slot.get("status") == "started"
        and "response" not in slot,
        "观察须完整且解释仅有原started现场，未绕过未知或已有结果。",
    )
    return record


def eligible(current):
    result, payload = current["result"], current["payload"]
    return (
        current["kind"] == "workflow"
        and payload.get("mode") == "assisted"
        and result.get(authored.KEY) == authored.CURRENT_VERSION
        and result.get(execution.KEY) == execution.VERSION
    )


def checkpoint(domain, current, settings, *, running=False):
    execution.ensure_runtime(current, domain, settings)
    return clarification.checkpoint(domain, current, record_check=record_check, allow_running=running)


def witness(domain, usage, task_id):
    steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (task_id,))
    calls = [dict(r) for r in usage.execute("SELECT * FROM calls WHERE task_id=? ORDER BY id", (task_id,))]
    meta = [
        dict(r)
        for r in usage.execute(
            "SELECT m.* FROM call_metadata m JOIN calls c ON c.id=m.call_id WHERE c.task_id=? ORDER BY m.call_id",
            (task_id,),
        )
    ]
    return {
        "steps": {r["fingerprint"]: sha(r) for r in steps},
        "calls": {r["id"]: sha(r) for r in calls},
        "metadata": {r["call_id"]: sha(r) for r in meta},
    }


def ensure_witness(domain, usage, task_id, old):
    now = witness(domain, usage, task_id)
    require(
        all(all(now[k].get(key) == value for key, value in rows.items()) for k, rows in old.items()),
        "原始返回、结算或元数据改变，未派发诊断修复。",
    )


def known_currency(usage, provider):
    require(
        not usage.execute(
            "SELECT id FROM calls WHERE provider=? AND (status!='settled' OR charged IS NULL) LIMIT 1",
            (provider,),
        ).fetchone(),
        "所用服务商有未核实调用，含零预留未知，未新增调用。",
    )


def frozen(domain, current, value):
    branch, chapter, original, entry, record, tasks, chapters = value
    clean = deepcopy(record)
    clean["phases"][PHASE] = plain_slot(clean["phases"][PHASE])
    return {
        "payload": sha(current["payload"]),
        "plan": sha(current["result"].get("plan")),
        "source_snapshot": sha(current["result"][authored.SNAPSHOT]),
        "execution_snapshot": sha(current["result"][execution.SNAPSHOT]),
        "genre": sha(current["result"]["genre_profile_snapshot"]),
        "author": sha(current["result"]["author_contract_snapshot"]),
        "repair_round": current["result"].get("repair_round"),
        "epoch": current["result"].get("generation_epoch"),
        "story_revision": branch["story_revision"],
        "record": sha(clean),
        "chapters": [{**c, "content": sha(c["content"])} for c in chapters],
    }


def resume_marker(domain, budget, settings, current, data):
    if (
        not eligible(current)
        or current["status"] != "blocked_runtime"
        or not data.acknowledge_unknown
        or data.regenerate_pending
        or data.acknowledge_changes
        or data.pause_after_maintenance
    ):
        return None
    try:
        value = checkpoint(domain, current, settings)
        entry, record = value[3:5]
        slot = record["phases"][PHASE]
        if MARK in slot:
            return None
        events = domain.db.all(
            "SELECT id,kind,payload FROM task_events WHERE task_id=? ORDER BY id", (current["id"],)
        )
        if any(e["kind"] == "resume_requested" and MARK in json.loads(e["payload"]) for e in events):
            return None
        running = [
            e
            for e in events
            if e["kind"] == "running" and json.loads(e["payload"]) == {"attempt": slot["attempt"]}
        ]
        require(len(running) == 1, "原解释attempt无法核对。")
        checks = [
            e
            for e in events
            if e["id"] > running[0]["id"]
            and e["kind"] == "checkpoint"
            and json.loads(e["payload"])
            .get(authored.RECEIPTS, {})
            .get(entry["key"], {})
            .get("phases", {})
            .get(PHASE)
            == slot
        ]
        require(checks, "原解释派发前检查点缺失。")
        after = [e for e in events if e["id"] > checks[0]["id"]]
        require(
            not any(
                e["kind"] in {"running", "resume_requested", "interrupted", "step_not_sent"} for e in after
            ),
            "原尝试有其他恢复或未知过程。",
        )
        relevant = [
            e
            for e in after
            if e["kind"] in {"step_dispatched", "step_completed", "citation_repair", "stopped"}
        ]
        require(
            [e["kind"] for e in relevant]
            == [k for _ in range(3) for k in ("step_dispatched", "step_completed", "citation_repair")]
            + ["stopped"],
            "须恰三份完整引用修正返回及原终态。",
        )
        stop = json.loads(relevant[-1]["payload"])
        require(
            stop.get("code") == "evidence" and stop.get("message") == current["error"],
            "原终态不是已核对引用失败。",
        )
        chosen = clarification.provider(SimpleNamespace(settings=settings), current)
        with budget.db() as usage:
            known_currency(usage, chosen.kind)
            basic = clarification.known_source_proof(domain, usage, record)
            rows = witness(domain, usage, current["id"])
        require(
            len(set(rows["steps"]) - set(slot["before_steps"]))
            == len(set(rows["calls"]) - set(slot["before_calls"]))
            == 3,
            "原phase返回或结算计数不完整。",
        )
        return {
            "fix": FIX,
            "source_key": entry["key"],
            "phase": PHASE,
            "old_attempt": slot["attempt"],
            "old_slot_sha256": sha(slot),
            "stopped_event_id": relevant[-1]["id"],
            "original_error": current["error"],
            "frozen": frozen(domain, current, value),
            "known_source_sha256": basic["sha256"],
            "original_rows": rows,
            "account": chosen.public(),
            "max_new_wire_calls": LIMIT,
        }
    except DomainError, KeyError, TypeError, ValueError:
        return None


def set_frame(worker, instruction, schema, context, maximum, contract):
    ctx = worker.__dict__.get("_diagnostic_phase")
    if ctx is not None:
        ctx["current_frame"] = {
            "instruction": deepcopy(instruction),
            "schema": schema,
            "context": deepcopy(context),
            "max_output": maximum,
            "contract": contract,
        }


def revalidate(worker, marker):
    current = worker.domain.task(worker.task["id"])
    value = checkpoint(worker.domain, current, worker.settings, running=True)
    require(
        frozen(worker.domain, current, value) == marker["frozen"],
        "恢复依据在派发许可核对期间改变。",
    )
    chosen = clarification.provider(SimpleNamespace(settings=worker.settings), current)
    require(chosen.public() == marker["account"], "派发许可所用原账户配置改变。")
    with worker.budget.db() as usage:
        known_currency(usage, chosen.kind)
        ensure_witness(worker.domain, usage, current["id"], marker["original_rows"])


def guard_pending_recovery(worker):
    # A valid interpretation still needs the ordinary independent next phase.
    # Its permit is not an exemption from unresolved selected-provider charges.
    for record in worker.task["result"].get(authored.RECEIPTS, {}).values():
        grant = record.get("phases", {}).get(PHASE, {}).get(MARK)
        if record.get("state") != "pending" or not isinstance(grant, dict):
            continue
        chosen = clarification.provider(SimpleNamespace(settings=worker.settings), worker.task)
        require(chosen.public() == grant["marker"]["account"], "来源保持核对前原账户配置改变。")
        with worker.budget.db() as usage:
            known_currency(usage, chosen.kind)
            ensure_witness(worker.domain, usage, worker.task["id"], grant["marker"]["original_rows"])


def claim(
    worker,
    instruction,
    schema,
    context,
    maximum,
    contract,
    validator,
    legacy_validator,
    repair_feedback,
):
    ctx = worker.__dict__.get("_diagnostic_phase")
    if not ctx or ctx["key"] != PHASE or not eligible(worker.task):
        return
    slot = ctx["slot"]
    if MARK in slot:
        return
    events = worker.db.all(
        "SELECT id,kind,payload FROM task_events WHERE task_id=? AND kind IN ('resume_requested','running') ORDER BY id",
        (worker.task["id"],),
    )
    resumes = [e for e in events if e["kind"] == "resume_requested"]
    if not resumes:
        return
    resume = resumes[-1]
    marker = json.loads(resume["payload"]).get(MARK)
    if not isinstance(marker, dict) or marker.get("fix") != FIX:
        return
    require(
        [e for e in events if e["id"] > resume["id"] and e["kind"] == "running"]
        == [
            next(
                e
                for e in events
                if e["kind"] == "running" and json.loads(e["payload"]) == {"attempt": worker.attempt}
            )
        ],
        "新attempt缺少唯一显式resume见证。",
    )
    require(
        marker["old_attempt"] != worker.attempt and marker["old_slot_sha256"] == sha(plain_slot(slot)),
        "不能复用原attempt或已变化phase。",
    )
    from .worker import citation_repair_request, parse_json

    frame = {
        "instruction": instruction,
        "schema": schema,
        "context": context,
        "max_output": maximum,
        "contract": contract,
        "validator": legacy_validator,
        "repair_request": lambda value, issues: citation_repair_request(
            instruction, value, issues, repair_feedback
        ),
    }
    current = worker.domain.task(worker.task["id"])
    value = checkpoint(worker.domain, current, worker.settings, running=True)
    require(
        frozen(worker.domain, current, value) == marker["frozen"],
        "源、规划、轮次、故事或稿件在恢复后变化。",
    )
    chosen = clarification.provider(SimpleNamespace(settings=worker.settings), current)
    require(chosen.public() == marker["account"], "原账户配置改变。")
    with worker.budget.db() as usage:
        known_currency(usage, chosen.kind)
        ensure_witness(worker.domain, usage, current["id"], marker["original_rows"])
        proof = known_returned_proof(
            worker.domain,
            usage,
            current,
            value[3],
            value[4],
            worker.settings,
            phase_key=PHASE,
            frame=frame,
            stop_event_id=marker["stopped_event_id"],
            terminal_error=marker["original_error"],
        )
    diagnostics = []
    for old in proof["steps"]:
        raw = json.loads(
            worker.db.one(
                "SELECT result FROM task_steps WHERE task_id=? AND fingerprint=?",
                (current["id"], old["fingerprint"]),
            )["result"]
        )
        legacy, corrected = (
            schema.model_validate(parse_json(raw["text"])).model_dump(),
            schema.model_validate(parse_json(raw["text"])).model_dump(),
        )
        before, after = legacy_validator(legacy), validator(corrected)
        require(
            before and after and legacy == corrected and len(before) == len(after),
            "旧返回非同一invalid语义，不能给予诊断重发许可。",
        )
        diagnostics.append({"old": before, "new": after})
    if diagnostics[0]["old"] == diagnostics[0]["new"]:
        # Preserve ordinary exact-cache resume behavior when this migration did
        # not change the first feedback. A later cache miss stays blocked.
        return
    first_value = schema.model_validate(
        parse_json(
            json.loads(
                worker.db.one(
                    "SELECT result FROM task_steps WHERE task_id=? AND fingerprint=?",
                    (current["id"], proof["steps"][0]["fingerprint"]),
                )["result"]
            )["text"]
        )
    ).model_dump()
    issues = validator(first_value)
    corrected_request = citation_repair_request(instruction, first_value, issues, repair_feedback)
    fingerprint, _, _ = request_identity(current, chosen, worker.settings, frame, corrected_request)
    require(
        not worker.db.all(
            "SELECT fingerprint FROM task_steps WHERE task_id=? AND fingerprint=?",
            (current["id"], fingerprint),
        ),
        "已有正确反馈缓存，不新增诊断许可。",
    )
    grant = {
        "resume_event_id": resume["id"],
        "attempt": worker.attempt,
        "original_slot": plain_slot(slot),
        "marker": marker,
        "proof_sha256": proof["sha256"],
        "diagnostics_sha256": sha(diagnostics),
        "first_feedback_fingerprint": fingerprint,
        "max_new_wire_calls": LIMIT,
        "dispatches": [],
    }
    with worker.db.connect(True) as tx:
        worker.db.check_attempt(tx, current["id"], worker.attempt)
        revalidate(worker, marker)
        latest = json.loads(tx.execute("SELECT result FROM tasks WHERE id=?", (current["id"],)).fetchone()[0])
        target = latest[authored.RECEIPTS][marker["source_key"]]["phases"][PHASE]
        require(
            MARK not in target and sha(target) == marker["old_slot_sha256"],
            "原phase或许可已被并发更新。",
        )
        target[MARK] = grant
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(latest), current["id"]))
        worker.db.event(tx, current["id"], "checkpoint", {"status": "running", **latest})
    slot[MARK] = deepcopy(grant)


def permit_dispatch(worker, fingerprint, instruction):
    ctx = worker.__dict__.get("_diagnostic_phase")
    if not ctx or ctx["key"] != PHASE or MARK not in ctx["slot"]:
        return False
    grant = ctx["slot"][MARK]
    if grant["attempt"] != worker.attempt:
        return False
    frame = ctx.get("current_frame")
    root = instruction
    while root.get("动作") == "仅修正结构化产物格式" and isinstance(root.get("原始任务"), dict):
        root = root["原始任务"]
    require(
        frame is not None and root == frame["instruction"],
        "新请求不是当前正确反馈或其既有JSON修正。",
    )
    marker = grant["marker"]
    current = worker.domain.task(worker.task["id"])
    value = checkpoint(worker.domain, current, worker.settings, running=True)
    require(frozen(worker.domain, current, value) == marker["frozen"], "派发前源或正文依据改变。")
    chosen = clarification.provider(SimpleNamespace(settings=worker.settings), current)
    require(chosen.public() == marker["account"], "派发前账户配置改变。")
    with worker.budget.db() as usage:
        known_currency(usage, chosen.kind)
        ensure_witness(worker.domain, usage, current["id"], marker["original_rows"])
    if not grant["dispatches"]:
        require(fingerprint == grant["first_feedback_fingerprint"], "首次修复不是已验证的新正确反馈。")
    with worker.db.connect(True) as tx:
        worker.db.check_attempt(tx, current["id"], worker.attempt)
        revalidate(worker, marker)
        latest = json.loads(tx.execute("SELECT result FROM tasks WHERE id=?", (current["id"],)).fetchone()[0])
        slot = latest[authored.RECEIPTS][marker["source_key"]]["phases"][PHASE]
        active = slot.get(MARK)
        require(
            active == grant and plain_slot(slot) == grant["original_slot"],
            "派发许可、旧slot或已占用额度改变。",
        )
        require(
            len(active["dispatches"]) < LIMIT
            and all(d["fingerprint"] != fingerprint for d in active["dispatches"]),
            "本phase正确反馈最多两次，不重发原标识。",
        )
        active["dispatches"].append({"fingerprint": fingerprint, "attempt": worker.attempt})
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(latest), current["id"]))
        worker.db.event(tx, current["id"], "checkpoint", {"status": "running", **latest})
    ctx["slot"][MARK] = deepcopy(active)
    return True
