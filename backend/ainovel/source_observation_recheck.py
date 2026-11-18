"""One explicit source-only recheck in the original workflow and budget ledger."""

import json
from copy import deepcopy
from types import SimpleNamespace

from .db import DomainError, dumps
from .diagnostic_phase_recovery import ensure_witness, known_currency, witness
from .flow_policy_continuation import is_body_action
from .story_graph import graph
from .workflow_plan_preparation import sha

VERSION = "source-observation-recheck/1"
OUTPUT_VERSION = "source-scope-observation/1"
KEY = "source_observation_recheck"
EVENT = "source_observation_recheck_authorized"
MAX_CALLS = 3
CTX = "_source_observation_recheck"


def require(value, message):
    if not value:
        raise DomainError("source_observation_recheck", message, 409)


def basis(current):
    return sha({"id": current["id"], "payload": current["payload"], "result": current["result"]})


def account(budget, current):
    chosen = budget.settings.providers.get(current["payload"]["account"])
    require(chosen is not None, "原账户不可用，未换账户核对来源。")
    return chosen, {**chosen.public(), "base_url": chosen.base_url, "limits": chosen.limits()}


def selected(current, result=None):
    from . import authored_source_scope as authored

    result = current["result"] if result is None else result
    number = result.get("next_chapter")
    require(type(number) is int and 0 < number <= current["payload"]["chapters"], "当前章不可核对。")
    mode = current["payload"].get("mode")
    if mode == "assisted":
        key = f"chapter:{number}"
        row = result.get(authored.RECEIPTS, {}).get(key)
        require(isinstance(row, dict), "当前章没有原作者来源阶段。")
        source = row.get("source", {})
        require(source.get("number") == number and source.get("key") == key, "原作者来源身份不符。")
        return number, key, "observe", row, source.get("text")
    require(mode == "automatic", "仅当前半自动或全自动来源阶段可核对。")
    row = result.get("plot_preparations", {}).get(str(number))
    require(isinstance(row, dict), "当前章没有原AI来源阶段。")
    round_number = row.get("round")
    require(type(round_number) is int and 0 <= round_number <= 2, "仅既有有限开放轮次的来源阶段可核对。")
    return number, f"chapter:{number}", f"recipient_scope:{round_number}", row, row.get("effective_plot")


def current_guard(domain, current, number):
    result, branch_id = current["result"], current["branch_id"]
    require(
        current["kind"] == "workflow"
        and not any(
            result.get(k)
            for k in (
                "continued_to",
                "waiting_artifact",
                "waiting_structure",
                "pending_structure",
                "pause_after_maintenance_requested",
            )
        ),
        "先处理原候选、维护或续接任务，未重做来源。",
    )
    require(result.get("repair_round", 0) == 0, "已有正文返修轮次，不能写前重核。")
    branch = domain.branch(branch_id, current["novel_id"])
    require(
        branch["story_revision"] == result.get("expected_story_revision"),
        "故事已变化，先核对前章及写前资料。",
    )
    domain.check_genre_snapshot(branch_id, result.get("genre_profile_snapshot"))
    domain.check_author_contract_snapshot(branch_id, result.get("author_contract_snapshot"))
    require(
        not domain.db.all(
            "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') AND cancel=0 LIMIT 1",
            (branch_id, current["id"]),
        ),
        "同工作版本有其他活动任务。",
    )
    chapters = domain.db.all("SELECT * FROM chapters WHERE branch_id=? ORDER BY position,id", (branch_id,))
    target = [c for c in chapters if c["position"] == number and not c["archived"]]
    require(
        len(target) == 1 and target[0]["revision"] == 0 and target[0]["content"] == "",
        "当前章已有正文或目标不唯一。",
    )
    prior = [c for c in chapters if c["position"] < number and not c["archived"]]
    require(
        [c["position"] for c in prior] == list(range(1, number))
        and all(c["content"].strip() and not c["stale"] for c in prior),
        "前章缺稿或失效。",
    )
    future = {c["id"] for c in chapters if c["position"] >= number}
    require(
        not any(c["revision"] or c["content"] for c in chapters if c["id"] in future),
        "当前章或后章已有稿件修订。",
    )
    require(
        not any(
            domain.db.all(
                "SELECT id FROM artifacts WHERE chapter_id=? AND kind IN ('chapter_draft','text_patch')",
                (key,),
            )
            for key in future
        ),
        "当前章或后章已有正文候选。",
    )
    require(
        not any(not str(k).isdigit() or int(k) > number for k in result.get("plot_preparations", {})),
        "后章已有写前准备。",
    )
    from . import authored_source_scope as authored

    require(
        not any(
            type(r.get("source", {}).get("number")) is int and r["source"]["number"] > number
            for r in result.get(authored.RECEIPTS, {}).values()
        ),
        "后章已有作者来源准备。",
    )
    for event in domain.db.all(
        "SELECT e.kind,e.payload FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE t.branch_id=? AND e.kind IN ('chapter_started','step_dispatched')",
        (branch_id,),
    ):
        value = json.loads(event["payload"])
        require(
            not (
                event["kind"] == "chapter_started"
                and value.get("number", 0) >= number
                or event["kind"] == "step_dispatched"
                and is_body_action(value.get("action"))
                and value.get("request", {}).get("target_version", {}).get("id") in future
            ),
            "当前章或后章已派发正文。",
        )
    state = graph(domain, branch_id)
    require(
        not any(r["invalid_reasons"] for r in state["records"] if not r["archived"]), "原故事记录仍失效。"
    )
    if current["payload"].get("scene_maintenance"):
        from .scene_time import chapter_context

        for chapter in prior:
            coverage = chapter_context(domain, branch_id, chapter["id"])
            require(coverage and not coverage["unbound_ranges"], "前章场景尚未完整维护。")
    return target[0], chapters, state


def frozen(domain, current, number, row, source):
    target, chapters, state = current_guard(domain, current, number)
    scenes = domain.db.all("SELECT * FROM scene_times WHERE branch_id=? ORDER BY id", (current["branch_id"],))
    return {
        "task_id": current["id"],
        "payload": deepcopy(current["payload"]),
        "plan": deepcopy(current["result"]["plan"]),
        "epoch": current["result"].get("generation_epoch"),
        "genre": deepcopy(current["result"]["genre_profile_snapshot"]),
        "author": deepcopy(current["result"]["author_contract_snapshot"]),
        "source": source,
        "chapter_id": target["id"],
        "chapters": chapters,
        "graph": state,
        "scenes": scenes,
        "binding": {
            k: deepcopy(v) for k, v in row.items() if k not in {"phases", "status", "reason", "state"}
        },
    }


def auto_proof(domain, usage, current, row, source, chosen):
    from . import workflow_plan_recipient_scope as scope
    from .worker import parse_json
    from .workflow_plan_preparation import check_receipt

    worker = SimpleNamespace(task=current, domain=domain, db=domain.db)
    worker.genre_snapshot = lambda: current["result"]["genre_profile_snapshot"]
    worker.author_snapshot = lambda: current["result"]["author_contract_snapshot"]
    chapter = domain.chapter(row["chapter_id"], current["branch_id"])
    plot = next((p for p in current["result"]["plan"]["chapters"] if p["number"] == row["chapter"]), None)
    require(plot is not None, "原规划缺少当前章。")
    check_receipt(worker, row, plot, chapter)
    slot = row["phases"]["recipient_scope:0"]
    response = slot.get("response")
    require(
        slot.get("status") == "completed"
        and slot.get("owner_task_id") == current["id"]
        and slot.get("response_sha256") == sha(response)
        and scope.inventory_valid(response, source)
        and response["status"] == "needs_review",
        "原来源必须是完整有效、仍未知的独立观察。",
    )
    steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (current["id"],))
    calls = [dict(r) for r in usage.execute("SELECT * FROM calls WHERE task_id=?", (current["id"],))]
    require(
        set(slot["before_steps"]) <= {r["fingerprint"] for r in steps}
        and set(slot["before_calls"]) <= {r["id"] for r in calls},
        "原阶段前像缺失。",
    )
    steps = [r for r in steps if r["fingerprint"] not in slot["before_steps"]]
    calls = [r for r in calls if r["id"] not in slot["before_calls"]]
    require(len(steps) == len(calls) == 1, "原未知观察须有恰一份完整返回和结算。")
    raw, call = json.loads(steps[0]["result"]), calls[0]
    meta = usage.execute("SELECT * FROM call_metadata WHERE call_id=?", (call["id"],)).fetchone()
    require(
        raw.get("call_id") == call["id"]
        and isinstance(raw.get("text"), str)
        and parse_json(raw["text"]) == response["inventory"]
        and call["account"] == chosen.id
        and call["provider"] == chosen.kind
        and call["model"] == chosen.model
        and call["status"] == "settled"
        and type(call["charged"]) is int
        and meta is not None
        and meta["request_id"] == raw.get("request_id")
        and (meta["actual_model"] or chosen.model) == raw.get("model")
        and raw.get("usage") == {"input": call["input_tokens"], "output": call["output_tokens"]},
        "原未知返回、元数据与结算无法一一核实。",
    )
    checkpoints = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='checkpoint'", (current["id"],)
    )
    require(
        any(
            json.loads(e["payload"])
            .get("plot_preparations", {})
            .get(str(row["chapter"]), {})
            .get("phases", {})
            .get("recipient_scope:0")
            == slot
            for e in checkpoints
        ),
        "原观察缺少独立检查点。",
    )
    return {"call_ids": [call["id"]], "phase_sha256": sha(slot), "returned_sha256": sha(steps[0])}


def authorize(domain, budget, current, result, usage, request):
    """request is dict(ResumeInput.model_dump()); caller appends EVENT in its tx."""
    require(
        isinstance(request, dict)
        and request.get("source_observation_recheck_version") == VERSION
        and request.get("source_observation_basis_sha256") == basis(current)
        and request.get("acknowledge_unknown") is True
        and str(request.get("reason", "")).strip()
        and not any(
            request.get(k) for k in ("acknowledge_changes", "regenerate_pending", "pause_after_maintenance")
        ),
        "须明确确认当前来源、原因及独立复核版本，不与其他恢复操作混用。",
    )
    require(
        KEY not in result
        and not domain.db.all(
            "SELECT id FROM task_events WHERE task_id=? AND kind=?", (current["id"], EVENT)
        ),
        "本任务已明确授权过来源复核，不通过重复恢复重置额度。",
    )
    number, source_key, phase, row, source = selected(current, result)
    require(isinstance(source, str) and source.strip(), "原来源正文缺失。")
    chosen, config = account(budget, current)
    known_currency(usage, chosen.kind)
    current_guard(domain, current, number)
    if current["payload"]["mode"] == "assisted":
        from . import author_source_clarification as clarification
        from . import authored_source_execution as execution
        from . import authored_source_scope as authored
        from .author_source_observation_retry import pending_record

        require(
            current["status"] == "blocked_runtime" and set(row.get("phases", {})) == {"observe"},
            "仅当前作者来源首次观察三份格式失败可重核。",
        )
        require(authored.validate_task(current, domain), "作者来源创建见证无法核对。")
        entry = authored.select(current, source, row["source"]["kind"], number)
        clarification_receipt = clarification.validate_task(current, domain)
        if clarification_receipt is not None:
            require(
                clarification_receipt["task_id"] == current["id"]
                and clarification_receipt["chapter"] == number
                and clarification_receipt.get("observation_failure") is not None,
                "仅原明确格式失败澄清子任务的当前结合来源可重核，不另作澄清。",
            )
            require(
                pending_record(domain, current, entry) == row
                and execution.record_valid(row, result.get(execution.SNAPSHOT)),
                "当前结合来源或原执行见证已改变。",
            )
        else:
            require(
                clarification.failed_observation_record(domain, current, entry) == row,
                "原作者来源停点已改变。",
            )
        proof = clarification.failed_observation_proof(domain, budget, usage, current, entry, row)
    else:
        from .workflow_plan_recipient_scope import KEY as SCOPE_KEY
        from .workflow_plan_recipient_scope import NEW_VERSION

        require(
            current["status"] in {"needs_review", "paused"}
            and row.get(SCOPE_KEY) == NEW_VERSION
            and row.get("round") == 0
            and row.get("status") == "needs_review"
            and set(row.get("phases", {})) == {"recipient_scope:0"}
            and not any(row.get(k) for k in ("interpretations", "choices", "preservation_checks")),
            "仅当前全自动首轮来源未知可明确重核。",
        )
        proof = auto_proof(domain, usage, current, row, source, chosen)
    receipt = {
        "version": VERSION,
        "output_version": OUTPUT_VERSION,
        "task_id": current["id"],
        "mode": current["payload"]["mode"],
        "from_chapter": number,
        "source_key": source_key,
        "phase": phase,
        "basis_sha256": basis(current),
        "request": deepcopy(request),
        "original_row": deepcopy(row),
        "original_slot": deepcopy(row["phases"][phase]),
        "original_status": current["status"],
        "original_error": current["error"],
        "account": config,
        "plan_sha256": sha(result["plan"]),
        "payload_sha256": sha(current["payload"]),
        "original_rows": witness(domain, usage, current["id"]),
        "proof": proof,
        "phases": {},
    }
    result[KEY] = receipt
    if receipt["mode"] == "automatic":
        row["status"] = "interpreting"
    return receipt


def checked_receipt(domain, current):
    receipt = current["result"].get(KEY)
    if receipt is None:
        return None
    require(
        receipt.get("version") == VERSION
        and receipt.get("output_version") == OUTPUT_VERSION
        and receipt.get("task_id") == current["id"]
        and receipt.get("mode") == current["payload"].get("mode")
        and receipt.get("payload_sha256") == sha(current["payload"])
        and receipt.get("plan_sha256") == sha(current["result"].get("plan")),
        "来源复核许可不属于原任务、规划或执行版本。",
    )
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (current["id"], EVENT)
    )

    def static(value):
        return {k: v for k, v in value.items() if k != "phases"}

    require(
        len(events) == 1 and static(json.loads(events[0]["payload"])) == static(receipt),
        "来源复核缺少唯一显式授权前像。",
    )
    return receipt


def prepare_phase(worker, row, key):
    phase_end(worker)
    if KEY not in worker.task["result"] or worker.task["kind"] != "workflow":
        return
    if key != "observe" and not key.startswith("recipient_scope:"):
        return
    number, source_key, expected, target, source = selected(worker.task)
    if key != expected:
        return
    receipt = checked_receipt(worker.domain, worker.task)
    if receipt is None or number < receipt["from_chapter"]:
        return
    require(target is row, "来源阶段不是当前任务行。")
    chosen, config = account(worker.budget, worker.task)
    require(config == receipt["account"], "原来源账户配置改变。")
    grant_key = f"{source_key}/{key}"
    grant = receipt["phases"].get(grant_key)
    if grant is not None:
        slot = row.get("phases", {}).get(key, {})
        require(
            slot.get(KEY) == {"version": VERSION, "source_key": source_key},
            "当前观察slot与已领取许可不一致。",
        )
        if "response" in slot:
            return
        # A complete paid return may be replayed after restart, never replenished.
        worker.__dict__[CTX] = {
            "task_id": worker.task["id"],
            "row": row,
            "key": key,
            "slot": slot,
            "source_key": source_key,
            "grant_key": grant_key,
            "grant": grant,
        }
        return
    old = row.get("phases", {}).get(key)
    require(
        number == receipt["from_chapter"]
        and key == receipt["phase"]
        and old == receipt["original_slot"]
        or (number > receipt["from_chapter"] or key != receipt["phase"])
        and old is None,
        "已完成或已开始的其他旧来源不升级。",
    )
    if key in {"observe", "recipient_scope:0"}:
        require(
            not any(row.get(k) for k in ("interpretations", "choices", "preservation_checks"))
            and set(row.get("phases", {})) <= {key},
            "来源后已有解释、选择或保持，未重新观察。",
        )
    else:
        # Only the original, bounded choice rounds may introduce a new source.
        from .workflow_plan_preparation import check_receipt

        round_number = row["round"]
        require(
            [r.get("round") for r in row.get("choices", [])] == list(range(1, round_number + 1)),
            "没有原有限开放选择回执，不新增来源轮次。",
        )
        adapter = SimpleNamespace(task=worker.task, domain=worker.domain, db=worker.db)
        adapter.genre_snapshot = lambda: worker.task["result"]["genre_profile_snapshot"]
        adapter.author_snapshot = lambda: worker.task["result"]["author_contract_snapshot"]
        plot = next(p for p in worker.task["result"]["plan"]["chapters"] if p["number"] == number)
        check_receipt(adapter, row, plot, worker.domain.chapter(row["chapter_id"], worker.task["branch_id"]))
    with worker.budget.db(True) as usage, worker.db.connect(True) as tx:
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        live = worker.domain.task(worker.task["id"])
        require(live["result"] == worker.task["result"], "来源许可或进度被并发改变。")
        known_currency(usage, chosen.kind)
        ensure_witness(worker.domain, usage, worker.task["id"], receipt["original_rows"])
        frozen_value = frozen(worker.domain, worker.task, number, row, source)
        grant = {
            "attempt": worker.attempt,
            "phase": key,
            "original_row_sha256": sha(row),
            "original_slot_sha256": sha(old),
            "frozen_sha256": sha(frozen_value),
            "dispatches": [],
        }
        slot = {
            "owner_task_id": worker.task["id"],
            "attempt": worker.attempt,
            "status": "started",
            "not_sent_history": [],
            "before_steps": sorted(
                r["fingerprint"]
                for r in worker.db.all(
                    "SELECT fingerprint FROM task_steps WHERE task_id=?", (worker.task["id"],)
                )
            ),
            "before_calls": sorted(
                r["id"] for r in usage.execute("SELECT id FROM calls WHERE task_id=?", (worker.task["id"],))
            ),
            KEY: {"version": VERSION, "source_key": source_key},
        }
        receipt["phases"][grant_key] = grant
        row.setdefault("phases", {})[key] = slot
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(worker.task["result"]), worker.task["id"]))
        worker.db.event(tx, worker.task["id"], "checkpoint", {"status": "running", **worker.task["result"]})
    worker.__dict__[CTX] = {
        "task_id": worker.task["id"],
        "row": row,
        "key": key,
        "slot": slot,
        "source_key": source_key,
        "grant_key": grant_key,
        "grant": grant,
    }


def output_version(worker):
    ctx = worker.__dict__.get(CTX)
    if ctx is not None and ctx.get("task_id") == worker.task["id"]:
        return OUTPUT_VERSION
    phase_end(worker)
    return None


def phase_end(worker):
    worker.__dict__.pop(CTX, None)


def before_dispatch(worker, fingerprint, instruction):
    ctx = worker.__dict__.get(CTX)
    if ctx is None or ctx.get("task_id") != worker.task["id"]:
        phase_end(worker)
        return False
    root = instruction
    while root.get("动作") in {"仅修正结构化产物格式", "修正已知达到输出上限的结构化产物"} and isinstance(
        root.get("原始任务"), dict
    ):
        root = root["原始任务"]
    require(
        root.get("source_observation_output_version", root.get("来源观察输出契约版本")) == OUTPUT_VERSION,
        "新来源派发没有本次明确输出契约。",
    )
    require(
        root.get("动作")
        in {
            "独立观察作者情节接收范围来源",
            "独立观察AI章纲接收范围来源",
        },
        "来源许可不能用于其他动作。",
    )
    receipt = checked_receipt(worker.domain, worker.task)
    grant = ctx["grant"]
    require(
        grant["attempt"] == worker.attempt and receipt["phases"].get(ctx["grant_key"]) is grant,
        "旧attempt不能领取来源复核额度。",
    )
    number, source_key, phase, row, source = selected(worker.task)
    require(source_key == ctx["source_key"] and phase == ctx["key"] and row is ctx["row"], "来源目标已切换。")
    from . import authored_source_scope as authored
    from . import source_scope_observation as output
    from . import workflow_plan_recipient_scope as scope

    is_plan = worker.task["payload"]["mode"] == "automatic"
    token = (
        scope.sha({"version": row[scope.KEY], "source": source})
        if is_plan
        else authored.token(row["source"], authored.CURRENT_VERSION)
    )
    expected_instruction = output.observation_request(
        source, token=token, source_kind="workflow_plan" if is_plan else row["source"]["kind"]
    )["instruction"]
    normalized = {k: v for k, v in root.items() if k not in {"前次产物", "定位错误", "引用修正要求"}}
    require(normalized == expected_instruction, "许可只可派发冻结来源的独立观察及其格式纠正。")
    chosen, config = account(worker.budget, worker.task)
    require(config == receipt["account"], "原账户配置改变。")
    with worker.budget.db(True) as usage, worker.db.connect(True) as tx:
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        current = worker.domain.task(worker.task["id"])
        require(current["result"] == worker.task["result"], "来源派发计数或进度被并发改变。")
        known_currency(usage, chosen.kind)
        ensure_witness(worker.domain, usage, worker.task["id"], receipt["original_rows"])
        require(
            sha(frozen(worker.domain, current, number, row, source)) == grant["frozen_sha256"],
            "来源、原规划、稿件或维护在派发前改变。",
        )
        require(
            len(grant["dispatches"]) < MAX_CALLS
            and not any(r["fingerprint"] == fingerprint for r in grant["dispatches"]),
            "本次来源观察及格式纠正最多三次派发，不重复原标识。",
        )
        require(
            not grant.get("instruction_sha256") or grant["instruction_sha256"] == sha(normalized),
            "格式纠正改变了独立来源请求。",
        )
        new = {
            **grant,
            "instruction_sha256": sha(normalized),
            "dispatches": [*grant["dispatches"], {"fingerprint": fingerprint, "attempt": worker.attempt}],
        }
        current["result"][KEY]["phases"][ctx["grant_key"]] = new
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(current["result"]), current["id"]))
        worker.db.event(tx, current["id"], "checkpoint", {"status": "running", **current["result"]})
    grant.clear()
    grant.update(new)
    return True
