"""Freeze author-source scope checks without granting automatic plot selection."""

from copy import deepcopy
import hashlib
import json

from .db import DomainError, dumps
from . import workflow_plan_recipient_scope as core

KEY = "authored_source_scope_version"
VERSION = "authored-source-scope/1"
CURRENT_VERSION = "authored-source-scope/2"
NEW_TASK_VERSION = CURRENT_VERSION
SUPPORTED = {VERSION, CURRENT_VERSION}
SNAPSHOT = "authored_source_scope_snapshot"
RECEIPTS = "authored_source_scope_receipts"
EVENT = "authored_source_scope_frozen"
ACTION = "独立观察作者情节接收范围来源"
MODULES = {"writing.expand_plot", "review.chapter", "reference.draft", "reference.check"}


def sha(value):
    return hashlib.sha256((value if isinstance(value, str) else dumps(value)).encode()).hexdigest()


def sources(payload, initial):
    if payload.get("mode") == "assisted":
        return [
            {
                "key": f"chapter:{p['number']}",
                "number": p["number"],
                "kind": "author_plot",
                "text": p["plot"],
                "sha256": sha(p["plot"]),
            }
            for p in payload["plots"]
        ]
    if payload.get("action", "").startswith("reference."):
        p = initial["reference_snapshot"]["payload"]
        text, kind = p["plot"], "author_plot"
    else:
        from .plot_review import module_plot_source

        text, kind = module_plot_source(payload), "author_instruction"
    return [{"key": "module", "number": None, "kind": kind, "text": text, "sha256": sha(text)}]


def artifact_audit(artifact):
    payload = artifact["payload"]
    for audit in (
        payload.get("plot_audit"),
        payload.get("previous_plot_audit"),
        payload.get("reference_comparison", {}).get("plot_audit"),
        payload.get("previous_reference_comparison", {}).get("plot_audit"),
    ):
        if isinstance(audit, dict):
            return audit
    return None


def inherited_source(domain, payload, initial):
    from .plot_audit import VERSION as PLOT_VERSION, ScopedAuthorPlot, author_contract_issues

    candidate = payload.get("reference_check_candidate")
    if not candidate:
        return None
    artifact = domain.artifact(candidate["id"])
    producer = domain.task(artifact["task_id"]) if artifact.get("task_id") else None
    old_reference = producer["result"].get("reference_snapshot") if producer else None
    current_reference = initial["reference_snapshot"]
    if not old_reference or (
        old_reference["id"] != current_reference["id"]
        or old_reference["payload"]["plot"] != current_reference["payload"]["plot"]
        or old_reference["payload"].get("fixed") != current_reference["payload"].get("fixed")
        or artifact["branch_id"] != payload["branch_id"]
        or artifact["chapter_id"] != payload["chapter_id"]
    ):
        raise DomainError(
            "reference_source_changed",
            "候选冻结情节与当前参考方案不同；请创建明确的新方案／试写，不从旧稿重定要求。",
            409,
        )
    audit = artifact_audit(artifact)
    text = old_reference["payload"]["plot"]
    if audit is None:
        # A first explicit check may freeze the producer's unchanged author
        # source. There is no prior interpretation to preserve or downgrade to.
        # The candidate body is never used to construct this source contract.
        return None
    if audit.get("version") != PLOT_VERSION:
        # Preserve an existing historical interpretation and its original policy.
        initial["plot_contract_version"] = producer["result"].get(
            "plot_contract_version", "author-plot-audit/1"
        )
        if "flow_source_entailment_version" in producer["result"]:
            initial["flow_source_entailment_version"] = producer["result"]["flow_source_entailment_version"]
        else:
            initial.pop("flow_source_entailment_version", None)
        return False
    try:
        contract = ScopedAuthorPlot.model_validate(
            {
                "items": [{k: v for k, v in item.items() if k != "id"} for item in audit["items"]],
                "note": audit["interpretation_note"],
            }
        ).model_dump()
        if (
            audit.get("source_kind") != "author_plot"
            or audit.get("source", {}).get("text_sha256") != sha(text)
            or author_contract_issues(contract, text, scoped=True)
        ):
            raise ValueError("invalid source")
    except (ValueError, KeyError, TypeError) as exc:
        raise DomainError(
            "reference_source", "旧候选的作者来源解释无法核对，未重新解释或清除限制。", 409
        ) from exc
    validate_task(producer, domain)
    policy = producer["result"].get(KEY)
    prior = None
    if policy is not None:
        validate_task(producer, domain)
        prior = next(
            (r for r in producer["result"].get(RECEIPTS, {}).values() if r.get("source_sha256") == sha(text)),
            None,
        )
        if (
            prior is None
            or prior.get("state") != "ready"
            or not receipt_valid(prior, text, {t["id"] for t in ancestry(producer, domain)})
        ):
            raise DomainError("reference_source", "候选原来源范围回执缺失或未就绪，未重新解释。", 409)

        def immutable(item):
            return {k: v for k, v in item.items() if k not in {"actor", "certainty"}}

        if [immutable(i) for i in contract["items"]] != [immutable(i) for i in prior["contract"]["items"]]:
            raise DomainError("reference_source", "候选解释与原冻结范围不同，未借对照重新定义要求。", 409)
    return {
        "task_id": producer["id"],
        "candidate_id": artifact["id"],
        "candidate_revision": candidate["revision"],
        "contract": contract,
        "source_policy_version": policy,
        "scope_receipt": deepcopy(prior),
    }


def freeze(domain, task_id, kind, payload, branch_id, initial):
    from . import authored_source_execution as execution

    execution.reject_payload(payload)
    if KEY in payload or SNAPSHOT in payload:
        raise DomainError("authored_scope_version", "作者来源执行见证只由服务器冻结。", 409)
    eligible = payload.get("plot_contract_check") is True and (
        (kind == "workflow" and payload.get("mode") == "assisted")
        or (kind == "module" and payload.get("action") in MODULES)
    )
    if not eligible:
        return
    inherited = (
        inherited_source(domain, payload, initial) if payload.get("action") == "reference.check" else None
    )
    if inherited is False:
        return
    version = inherited.get("source_policy_version", VERSION) if inherited else NEW_TASK_VERSION
    if version not in SUPPORTED:
        version = VERSION
    witness = {
        "version": version,
        "task_id": task_id,
        "novel_id": payload["novel_id"],
        "branch_id": branch_id,
        "kind": kind,
        "payload_sha256": sha(payload),
        "sources": sources(payload, initial),
        "inherited": inherited,
    }
    initial.update({KEY: version, SNAPSHOT: witness})
    execution.freeze_execution(domain, task_id, kind, payload, initial)


def event(db, tx, task_id, payload, initial):
    if KEY in initial:
        db.event(
            tx,
            task_id,
            EVENT,
            {KEY: initial[KEY], "snapshot_sha256": sha(initial[SNAPSHOT]), "payload_sha256": sha(payload)},
        )
    from .authored_source_execution import event as execution_event

    execution_event(db, tx, task_id, payload, initial)


def ancestry(task, domain):
    seen = set()
    while task["id"] not in seen:
        seen.add(task["id"])
        yield task
        parent = task["result"].get("continued_from")
        if not parent:
            break
        task = domain.task(parent)


def validate_task(task, domain):
    result = task.get("result", {})
    if KEY not in result and SNAPSHOT not in result and (domain is None or not task.get("id")):
        return False
    if domain is None:
        raise DomainError("authored_scope_version", "新作者来源版本缺少可核对的创建来源。", 409)
    chain = list(ancestry(task, domain))
    events = [
        domain.db.all("SELECT payload FROM task_events WHERE task_id=? AND kind=?", (t["id"], EVENT))
        for t in chain
    ]
    marker, witness = task["result"].get(KEY), task["result"].get(SNAPSHOT)
    if marker is None and witness is None and not any(events):
        return False
    if marker not in SUPPORTED or not isinstance(witness, dict) or witness.get("version") != marker:
        raise DomainError("authored_scope_version", "作者来源执行版本或创建见证缺失，未派发或降级。", 409)
    origin = next((t for t in chain if t["id"] == witness.get("task_id")), None)
    if (
        origin is None
        or origin["result"].get(KEY) != marker
        or origin["result"].get(SNAPSHOT) != witness
        or witness.get("payload_sha256") != sha(origin["payload"])
        or witness.get("novel_id") != task["novel_id"]
        or witness.get("branch_id") != task["branch_id"]
        or witness.get("kind") != task["kind"]
        or sources(task["payload"], task["result"]) != witness.get("sources")
    ):
        raise DomainError("authored_scope_version", "作者原话、目标或冻结来源链已改变，未反向套用候选。", 409)
    rows = domain.db.all("SELECT payload FROM task_events WHERE task_id=? AND kind=?", (origin["id"], EVENT))
    expected = {KEY: marker, "snapshot_sha256": sha(witness), "payload_sha256": sha(origin["payload"])}
    if len(rows) != 1 or json.loads(rows[0]["payload"]) != expected:
        raise DomainError("authored_scope_version", "作者来源独立创建事件无法核对，未派发。", 409)
    from .author_source_clarification import validate_task as validate_clarification

    validate_clarification(task, domain, chain)
    from .author_source_recheck import validate_task as validate_recheck

    validate_recheck(task, domain, chain)
    from .authored_source_execution import validate_execution

    validate_execution(task, domain, chain)
    return True


def select(task, text, kind, number=None):
    from .author_source_clarification import source_entry

    entries = [source_entry(task, e) for e in task["result"][SNAPSHOT]["sources"]]
    if number is None and task["kind"] == "workflow":
        number = task["result"].get("next_chapter")
    found = [
        e
        for e in entries
        if e["kind"] == kind and e["text"] == text and (e["number"] is None or e["number"] == number)
    ]
    if len(found) != 1:
        raise DomainError(
            "authored_scope_source", "本次原话不属于冻结作者来源，未从正文或其他作品补造要求。", 409
        )
    return found[0]


def token(entry, version=VERSION):
    return sha({"version": version, "key": entry["key"], "kind": entry["kind"], "source": entry["text"]})


def inventory_valid(receipt, entry):
    try:
        version = receipt["version"]
        value = core.ScopeInventory.model_validate(receipt["inventory"]).model_dump()
        choices = {u["source_id"]: u for u in core.units(entry["text"])}
        return (
            version in SUPPORTED
            and receipt["input_sha256"] == token(entry, version)
            and receipt["source_sha256"] == entry["sha256"]
            and receipt["sources"] == list(choices.values())
            and not core.inventory_issues(value, token(entry, version), choices)
            and receipt["status"] == ("supported" if core.inventory_supported(value) else "needs_review")
        )
    except ValueError, KeyError, TypeError:
        return False


def core_value(value):
    return {
        "items": [
            {
                k: deepcopy(v)
                for k, v in item.items()
                if k not in {"scope_observation_ids", "scope_binding_status", "scope_binding_reason"}
            }
            for item in value["items"]
        ],
        "note": value["note"],
    }


def binding_data(value, version=VERSION):
    return {
        "version": version,
        "items": [
            {
                "item_id": f"P{i:02}",
                "observation_ids": deepcopy(item["scope_observation_ids"]),
                "status": item["scope_binding_status"],
                "reason": item["scope_binding_reason"],
            }
            for i, item in enumerate(value["items"], 1)
        ],
    }


def binding_issues(value, inventory, *, legacy_diagnostic_labels=False):
    observations = {row["observation_id"]: row for row in inventory["inventory"]["observations"]}
    used, issues = set(), []
    for index, item in enumerate(value["items"], 1):
        ids = item["scope_observation_ids"]
        if len(ids) != len(set(ids)) or any(key not in observations for key in ids):
            issues.append(f"P{index:02}只绑定本次O编号，不重复或补造。")
            continue
        used.update(ids)
        if not item["scope_binding_reason"].strip():
            issues.append(f"P{index:02}须说明来源支持或真实未明；先行观察不是原话真值。")
        if item["scope_binding_status"] == "supported":
            subset = {
                "version": core.CURRENT_VERSION,
                "inventory": {"observations": [observations[key] for key in ids]},
            }
            local_issues = core.binding_issues({"items": [item], "note": value["note"]}, subset)
            issues.extend(
                f"P{index:02}{issue[3:]}"
                if not legacy_diagnostic_labels and issue.startswith("P01")
                else issue
                for issue in local_issues
            )
    if used != set(observations):
        issues.append("所有原文范围观察均须关联并保留，未明可unknown，不能删除真实限制。")
    return issues


def binding_valid(contract, binding, inventory):
    try:
        if binding["version"] not in SUPPORTED or [r["item_id"] for r in binding["items"]] != [
            f"P{i:02}" for i in range(1, len(contract["items"]) + 1)
        ]:
            return False
        value = {
            "items": [
                {
                    **item,
                    "scope_observation_ids": row["observation_ids"],
                    "scope_binding_status": row["status"],
                    "scope_binding_reason": row["reason"],
                }
                for item, row in zip(contract["items"], binding["items"], strict=True)
            ],
            "note": contract["note"],
        }
        return not binding_issues(value, inventory)
    except KeyError, TypeError, ValueError:
        return False


def receipt_valid(record, text, owner_ids=None):
    from .plot_audit import ScopedAuthorPlot, author_contract_issues

    if not isinstance(record, dict):
        return False
    if record.get("version") == CURRENT_VERSION:
        from .authored_source_classification import receipt_valid as classification_valid

        return classification_valid(record, text, owner_ids)
    try:
        entry = record["source"]
        if entry["text"] != text or entry["sha256"] != sha(text) or record["source_sha256"] != sha(text):
            return False
        contract = ScopedAuthorPlot.model_validate(record["contract"]).model_dump()
        if owner_ids is not None and any(
            slot.get("owner_task_id") not in owner_ids for slot in record.get("phases", {}).values()
        ):
            return False
        if record.get("inherited"):
            return record["state"] in {"ready", "legacy_frozen"} and not author_contract_issues(
                contract, text, scoped=True
            )
        inventory = record["phases"]["observe"]["response"]
        interpretation = record["phases"]["interpret"]["response"]
        return (
            inventory_valid(inventory, entry)
            and inventory["status"] == "supported"
            and all(
                s["status"] == "completed" and s["response_sha256"] == sha(s["response"])
                for s in record["phases"].values()
            )
            and interpretation["contract"] == contract
            and interpretation["bindings"] == record["bindings"]
            and all(r["status"] == "supported" for r in record["bindings"]["items"])
            and binding_valid(contract, record["bindings"], inventory)
            and not author_contract_issues(contract, text, scoped=True)
        )
    except KeyError, ValueError, TypeError:
        return False


async def observe(worker, entry):
    units = core.units(entry["text"])
    choices = {u["source_id"]: u for u in units}
    schema = core.ScopeInventory.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [token(entry)]
    schema["properties"]["unit_checks"].update(minItems=len(units), maxItems=len(units))
    schema["$defs"]["UnitCheck"]["properties"]["source_id"]["enum"] = list(choices)
    identifiers = [f"O{i:02}" for i in range(1, 25)]
    schema["$defs"]["UnitCheck"]["properties"]["observation_ids"]["items"]["enum"] = identifiers
    fields = schema["$defs"]["ScopeObservation"]["properties"]
    fields["observation_id"]["enum"] = identifiers
    fields["evidence"]["enum"] = list(choices)
    fields["restriction_evidence"]["enum"] = ["", *choices]
    value = await worker.grounded(
        {
            "动作": ACTION,
            KEY: VERSION,
            "input_sha256": token(entry),
            "作者本次情节原话": entry["text"],
            "可引用原文": units,
            "分类口径": {
                "recipient_exclusivity": "原话明确排除其他全部接收者，须定位本次同一传递及限制原句，保留原件／副本、阶段和条件。",
                "named_destination": "只命名本次收件人不等于排他；一份材料、单次寄往、回执未到均不能证明only。",
                "specific_recipient_exclusion": "只排除特定人或未告知某人，不能扩成排除所有其他人。",
                "action_permission": "许可携带／使用／另抄副本限制动作或物品，不等于许可人交付或实际接收范围。",
                "explicitly_unrestricted": "原话明确不限制接收人，未说明不等于明确不限。",
                "unknown": "限制归属或范围仅由原话无法确认，保留未知，不选择人物或补造。",
            },
            "要求": "逐原句独立观察，尚无P卡、候选、正文、题材或作者全书约定。O01起按序记录范围，全部原句按原序各一次标covered/not_scope_related/unknown；covered关联其动作或限制原句O编号。只填原话已明确的角色、物品及实际／尝试／计划阶段，未明留空；真不明保持unknown，不自行选择作者未定内容。普通目的地与另一句许可或单独排除某人不得串为only。限制的条件须连续原话，没有条件留空；原件、副本和不同次传递不合并。evidence、restriction_evidence只填SCOPE编号，非限制句不能用来证明排他。unknown有效，不能因覆盖表必须填完而编造来源。",
        },
        core.ScopeInventory,
        {"scope": "仅作者本次原话及稳定编号，无P卡、正文、候选、频道题材、全书约定或未来章"},
        lambda v: core.inventory_issues(v, token(entry), choices),
        contract=schema,
        max_output=8192,
        diagnostic_label="作者来源接收范围观察",
    )
    return {
        "version": VERSION,
        "input_sha256": token(entry),
        "source_sha256": entry["sha256"],
        "sources": units,
        "inventory": value,
        "status": "supported" if core.inventory_supported(value) else "needs_review",
    }


def executable(task):
    return task["kind"] == "workflow" or task["payload"].get("action") in {
        "writing.expand_plot",
        "reference.draft",
    }


def continuation(domain, current):
    from .author_source_clarification import continuation as check_clarification_continuation

    check_clarification_continuation(current)
    if validate_task(current, domain) and any(
        s.get("status") not in {"completed", "not_sent"}
        for r in current["result"].get(RECEIPTS, {}).values()
        for s in r.get("phases", {}).values()
    ):
        raise DomainError(
            "authored_source_pending", "作者来源步骤尚无完整回执，先在原账户核对；未换账户重放未知请求。", 409
        )


async def prepare(worker, text, kind, number=None):
    if validate_task(worker.task, worker.domain) and worker.task["result"][KEY] == CURRENT_VERSION:
        entry = select(worker.task, text, kind, number)
        record = worker.task["result"].get(RECEIPTS, {}).get(entry["key"])
        if record is None or record.get("version") == CURRENT_VERSION:
            from .authored_source_classification import prepare as classification_prepare

            return await classification_prepare(worker, entry)
    return await prepare_legacy(worker, text, kind, number)


async def prepare_legacy(worker, text, kind, number=None):
    from .plot_audit import interpret_plot, ScopedAuthorPlot
    from .workflow_plan_preparation import phase

    if not validate_task(worker.task, worker.domain):
        raise DomainError("authored_scope_version", "本任务未冻结作者来源执行版本。", 409)
    entry = select(worker.task, text, kind, number)
    records = worker.task["result"].setdefault(RECEIPTS, {})
    record = records.get(entry["key"])
    inherited = worker.task["result"][SNAPSHOT]["inherited"]
    if record is None:
        record = records[entry["key"]] = {
            "version": VERSION,
            "source": deepcopy(entry),
            "source_sha256": entry["sha256"],
            "phases": {},
            "inherited": deepcopy(inherited),
            "state": "pending",
        }
        worker.checkpoint(worker.task["result"])
    if inherited:
        if record["inherited"] != inherited or record["source"] != entry:
            raise DomainError("authored_scope_changed", "旧候选来源继承回执已改变，未重新解释。", 409)
        record.update(
            contract=deepcopy(inherited["contract"]),
            state="ready" if inherited["source_policy_version"] == VERSION else "legacy_frozen",
        )
        worker.checkpoint(worker.task["result"])
        return deepcopy(record["contract"])
    if not core.units(text):
        record.update(
            state="unverified", contract={"items": [], "note": "作者来源没有可解释的情节原话，未补造条件。"}
        )
        worker.checkpoint(worker.task["result"])
    if record.get("state") in {"ready", "unverified"}:
        if record["state"] == "ready" and not receipt_valid(
            record, text, {t["id"] for t in ancestry(worker.task, worker.domain)}
        ):
            raise DomainError("authored_scope_changed", "作者来源回执无法核对，未重新解释。", 409)
        if record["state"] == "unverified" and executable(worker.task):
            raise DomainError(
                "author_source_unverified", "作者原话仍有待确认，未自动选择内容或生成正文。", 409
            )
        return deepcopy(record["contract"])
    owners = {t["id"] for t in ancestry(worker.task, worker.domain)}
    inventory = await phase(
        worker, record, "observe", "observing", lambda: observe(worker, entry), owner_ids=owners
    )
    if not inventory_valid(inventory, entry):
        raise DomainError("authored_scope_changed", "来源观察与原话不符，未继续解释。", 409)
    if inventory["status"] != "supported":
        record.update(
            state="unverified",
            contract={"items": [], "note": "作者来源范围归属未明，原话保留；没有生成新的执行安排。"},
        )
    else:
        interpreted = await phase(
            worker,
            record,
            "interpret",
            "interpreting",
            lambda: interpret_plot(
                worker, text, source_kind=kind, source_position=number, authored_inventory=inventory
            ),
            owner_ids=owners,
        )
        contract = ScopedAuthorPlot.model_validate(interpreted["contract"]).model_dump()
        record.update(
            contract=contract,
            bindings=deepcopy(interpreted["bindings"]),
            state="ready"
            if contract["items"]
            and all(i["certainty"] == "explicit" for i in contract["items"])
            and all(i["status"] == "supported" for i in interpreted["bindings"]["items"])
            else "unverified",
        )
    worker.checkpoint(worker.task["result"])
    if record["state"] == "unverified" and executable(worker.task):
        raise DomainError("author_source_unverified", "作者原话仍有歧义，未由AI选人、补条件或生成正文。", 409)
    return deepcopy(record["contract"])


def metadata(worker, text, kind, number):
    if not validate_task(worker.task, worker.domain):
        return {}
    entry = select(worker.task, text, kind, number)
    record = worker.task["result"].get(RECEIPTS, {}).get(entry["key"], {})
    inherited = record.get("inherited")
    return {
        "authored_source_scope": {
            "version": record.get("version", VERSION),
            "source_key": entry["key"],
            "source_sha256": entry["sha256"],
            "receipt_sha256": sha(record),
            "state": record.get("state", "unverified"),
            "scope_policy_version": inherited["source_policy_version"]
            if inherited
            else record.get("version", VERSION),
            "author_literary_approval": "unverified",
        }
    }
