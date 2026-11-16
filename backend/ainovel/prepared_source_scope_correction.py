"""Explicit scope-only correction of a legacy frozen AI chapter interpretation.

The raw observer sees no P items, expected scope or prose. The server only maps
same-transfer observations to two scope fields. Independent preservation must
then verify all original meanings before the read-only receipt is ready.
"""

from copy import deepcopy
import json
from time import time
from typing import Literal

from .db import DomainError, dumps
from .candidate_recheck import freeze as old_freeze, sha
from . import source_interpretation_correction as actor
from . import workflow_plan_recipient_scope as scope
from .evidence import source_units

VERSION = "prepared-source-scope-correction/1"
ALIAS_VERSION = "prepared-source-scope-correction/2"
KIND = "prepared_source_scope_correction"
KEY = "prepared_source_scope_version"
SNAPSHOT = "prepared_source_scope_snapshot"
EVENT = "prepared_source_scope_frozen"
RECHECK_VERSION = "scope-corrected-candidate-recheck/1"
ALIAS_RECHECK_VERSION = "scope-corrected-candidate-recheck/2"
RECHECK_VERSIONS = {RECHECK_VERSION, ALIAS_RECHECK_VERSION}
OBSERVE = "独立观察旧有效AI章纲的接收范围"
VERIFY = "独立核对旧AI章纲范围纠偏与全部事项保持"
FIELDS = {"recipient_scope", "scope_condition"}


class ScopeCorrectionInput(actor.CorrectionInput):
    verify_object_aliases: bool = False


class ScopeItemCheck(actor.ItemCheck):
    scope_only_fix_sufficient: Literal["yes", "no", "unknown"]


class Preservation(actor.Preservation):
    item_checks: list[ScopeItemCheck]


def versioned_payload(payload):
    aliases = payload.get("verify_object_aliases", False)
    if type(aliases) is not bool:
        raise DomainError("prepared_scope_version", "物品称谓核对须显式布尔选项。", 409)
    version = ALIAS_VERSION if aliases else VERSION
    if KEY in payload and payload[KEY] != version:
        raise DomainError("prepared_scope_version", "范围纠偏执行版本未知，未派发。", 409)
    original = {k: v for k, v in payload.items() if k != "verify_object_aliases" or aliases}
    return {**original, KEY: version}


def freeze(domain, request, *, verify_basis=True):
    if request.get("verify_object_aliases") is True:
        from .prepared_source_object_aliases import freeze as freeze_aliases

        return freeze_aliases(domain, request, verify_basis=verify_basis)
    snapshot = old_freeze(domain, request)
    parent = domain.task(snapshot["source_workflow_id"])
    if parent["payload"].get("mode") != "automatic":
        raise DomainError(
            "prepared_scope_mode", "此入口仅核对全自动旧有效AI章纲的范围解释；作者情节纠偏沿原入口。", 409
        )
    ids = [f"P{i:02}" for i in range(1, len(snapshot["prepared_contract"]["items"]) + 1)]
    selected = request["item_ids"]
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(ids):
        raise DomainError("prepared_scope_items", "须主动选择既有P事项，不能新增或重复事项。", 409)
    snapshot = {
        **snapshot,
        "version": VERSION,
        "source_kind": "workflow_plan",
        "item_ids": selected,
        "original_contract_sha256": sha(snapshot["prepared_contract"]),
        "source_text_sha256": sha(snapshot["plot"]["plot"]),
    }
    snapshot["basis_sha256"] = sha({k: v for k, v in snapshot.items() if k != "item_ids"})
    if verify_basis and request.get("preview_basis_sha256") != snapshot["basis_sha256"]:
        raise DomainError(
            "prepared_scope_basis", "原候选、有效AI章纲或冻结来源改变，请保留输入并重新读取依据。", 409
        )
    return snapshot


def validate_task(domain, task):
    if task["payload"].get(KEY) == ALIAS_VERSION:
        from .prepared_source_object_aliases import validate_task as validate_aliases

        return validate_aliases(domain, task)
    snapshot = task["result"].get(SNAPSHOT)
    events = domain.db.all("SELECT payload FROM task_events WHERE task_id=? AND kind=?", (task["id"], EVENT))
    expected = {KEY: VERSION, "payload_sha256": sha(task["payload"]), "snapshot_sha256": sha(snapshot)}
    if task["kind"] != KIND or len(events) != 1 or json.loads(events[0]["payload"]) != expected:
        raise DomainError("prepared_scope_version", "范围纠偏的独立创建见证不符，未降级或发送。", 409)
    if (
        task["payload"].get(KEY) != VERSION
        or task["result"].get(KEY) != VERSION
        or snapshot is None
        or freeze(domain, task["payload"]) != snapshot
    ):
        raise DomainError("prepared_scope_version", "范围纠偏任务与冻结来源不符，未升级或派发旧任务。", 409)
    return snapshot


def validate(worker):
    return validate_task(worker.domain, worker.task)


def inventory_valid(receipt, snapshot):
    try:
        value = scope.ScopeInventory.model_validate(receipt["inventory"]).model_dump()
        units = scope.units(snapshot["plot"]["plot"])
        token = sha({"version": VERSION, "source": snapshot["plot"]["plot"]})
        return (
            receipt["version"] == VERSION
            and receipt["input_sha256"] == token
            and receipt["sources"] == units
            and not scope.inventory_issues(value, token, {u["source_id"]: u for u in units})
            and receipt["status"] == ("supported" if scope.inventory_supported(value) else "needs_review")
        )
    except ValueError, KeyError, TypeError:
        return False


async def observe(worker, snapshot):
    units = scope.units(snapshot["plot"]["plot"])
    choices = {u["source_id"]: u for u in units}
    token = sha({"version": VERSION, "source": snapshot["plot"]["plot"]})
    schema = scope.ScopeInventory.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [token]
    schema["properties"]["unit_checks"].update(minItems=len(units), maxItems=len(units))
    schema["$defs"]["UnitCheck"]["properties"]["source_id"]["enum"] = list(choices)
    obs = schema["$defs"]["ScopeObservation"]["properties"]
    obs["evidence"]["enum"] = list(choices)
    obs["restriction_evidence"]["enum"] = ["", *choices]
    value = await worker.grounded(
        {
            "动作": OBSERVE,
            KEY: VERSION,
            "input_sha256": token,
            "有效AI章纲原文": snapshot["plot"]["plot"],
            "可引用原文": units,
            "分类口径": {
                "recipient_exclusivity": "原句明确排除全部其他接收者，须同一次实际/尝试/计划传递及连续限制证据；命名目的地不是排他。",
                "named_destination": "仅命名收件人、一份抄本或回执未到，不表示only；回执未到不撤销已寄出。",
                "action_permission": "另抄/携带/使用的许可限制动作或物品，不是许可人实际交付、更不是其他邮寄的排他接收范围；没有实际传递则双方/物品空、阶段none。",
                "specific_recipient_exclusion": "单独禁止某人或未告知某人，不扩大为排除全部其他人。",
                "explicitly_unrestricted": "原话明确不限接收者，未声明不等于明确不限。",
                "unknown": "仅原文无法判断时保留未知，不选择人物/物品或补造条件。",
            },
            "要求": "只读有效AI工作章纲原句及SCOPE编号；这是AI规划来源，不伪称作者原话或已发生事实。阶段按章纲要求本章呈现的故事动作判断：completed指本章要求完成交付或寄出，planned仅指本章表达未来安排；不能因章纲是AI规划就把全部事件标planned。这不把规划变成正式已发生事实。没有旧P、预期only、固定条件、候选、正文、旧检查、未来规划或自由reason。逐句观察、全部单元按序核对一次；只填原文明示的双方、物品/原件/副本及completed/attempted/planned/none阶段，不合并独立传递。真正排他、许可、单独排除分别保持；evidence/restriction_evidence只填本次编号，条件为限制原句连续原话，无条件空。unknown有效，不能编造观察覆盖。",
        },
        scope.ScopeInventory,
        {"scope": "仅冻结有效AI章纲原文，无P、期望范围或正文"},
        lambda v: scope.inventory_issues(v, token, choices),
        contract=schema,
        diagnostic_label="旧AI章纲范围来源",
    )
    return {
        "version": VERSION,
        "input_sha256": token,
        "sources": units,
        "inventory": value,
        "status": "supported" if scope.inventory_supported(value) else "needs_review",
    }


def propose(snapshot, inventory):
    if not inventory_valid(inventory, snapshot):
        raise DomainError("prepared_scope_source", "原始范围观察无法回到冻结AI章纲。", 409)
    contract = deepcopy(snapshot["prepared_contract"])
    items = []
    keys = ("supplier", "recipient", "transfer_object", "transfer_scope")
    observations = inventory["inventory"]["observations"]
    for index, item in enumerate(contract["items"], 1):
        identifier = f"P{index:02}"
        ids = []
        status = "supported"
        reason = "未选择事项所有字段保持。"
        if identifier in snapshot["item_ids"]:
            eligible = (
                item["transfer_scope"] in {"attempted", "completed", "planned"}
                and all(item[k] for k in keys[:3])
                and item["supplier"] != item["recipient"]
            )
            matching = [o for o in observations if eligible and all(o[k] == item[k] for k in keys)]
            relevant = [
                o
                for o in matching
                if o["kind"]
                in {
                    "recipient_exclusivity",
                    "explicitly_unrestricted",
                    "named_destination",
                    "action_permission",
                }
            ]
            exclusive = [o for o in relevant if o["kind"] == "recipient_exclusivity"]
            unrestricted = [o for o in relevant if o["kind"] == "explicitly_unrestricted"]
            selected = exclusive or unrestricted or relevant
            conditions = {o["condition"] for o in selected}
            if (
                inventory["status"] != "supported"
                or not relevant
                or (exclusive and unrestricted)
                or len(conditions) != 1
            ):
                status = "unknown"
                reason = "原双方/物品/阶段或限制条件不能可靠唯一绑定，保留旧提案，不编角色或清除限制。"
            else:
                ids = [o["observation_id"] for o in selected]
                item["recipient_scope"] = (
                    "only" if exclusive else "unrestricted" if unrestricted else "unspecified"
                )
                item["scope_condition"] = next(iter(conditions)) if exclusive or unrestricted else ""
                reason = "仅按原文观察绑定同一次传递的范围字段；命名目的地/纯许可不升级排他，真实only仍保持；另须独立核全P。"
        items.append(
            {
                "item_id": identifier,
                "recipient_scope": item["recipient_scope"],
                "scope_condition": item["scope_condition"],
                "observation_ids": ids,
                "status": status,
                "reason": reason,
            }
        )
    return {"items": items}, contract


def preservation_schema(snapshot):
    schema = Preservation.model_json_schema()
    ids = [f"P{i:02}" for i in range(1, len(snapshot["prepared_contract"]["items"]) + 1)]
    authors = [u["source_id"] for u in source_units(snapshot["plot"]["plot"], "AUTHOR")]
    fixed = [f"FIX{i:03}" for i in range(1, len(snapshot["fixed"]) + 1)]
    schema["properties"]["item_checks"].update(minItems=len(ids), maxItems=len(ids))
    p = schema["$defs"]["ScopeItemCheck"]["properties"]
    p["item_id"]["enum"] = ids
    p["evidence"]["enum"] = authors
    p["scope_only_fix_sufficient"]["enum"] = ["yes", "no", "unknown"]
    schema["properties"]["fixed_checks"].update(minItems=len(fixed), maxItems=len(fixed))
    if fixed:
        p = schema["$defs"]["FixedCheck"]["properties"]
        p["condition_id"]["enum"] = fixed
        p["evidence"]["enum"] = fixed
    return schema


def resolve_preservation(snapshot, value):
    checked = Preservation.model_validate(value).model_dump()
    flags = [r.pop("scope_only_fix_sufficient") for r in checked["item_checks"]]
    result = actor.resolve_preservation(checked, snapshot)
    for row, flag in zip(result["item_checks"], flags, strict=True):
        row["scope_only_fix_sufficient"] = flag
    return result


def verify_saved_preservation(snapshot, value):
    """Re-resolve the saved source evidence, never trust a ready label alone."""
    raw = deepcopy(value)
    for row in raw["item_checks"]:
        evidence = row.pop("evidence_source")
        row["evidence"] = evidence["source_id"]
    for row in raw["fixed_checks"]:
        row["evidence"] = row["condition_id"]
    if resolve_preservation(snapshot, raw) != value:
        raise DomainError("prepared_scope_receipt", "来源保持证据与冻结连续原文不符。", 409)


def ready(proposal, preservation):
    return (
        preservation is not None
        and all(i["status"] == "supported" for i in proposal["items"])
        and all(
            r["result"] == "pass" for group in ("item_checks", "fixed_checks") for r in preservation[group]
        )
        and all(r["scope_only_fix_sufficient"] == "yes" for r in preservation["item_checks"])
    )


async def run(worker):
    snapshot = validate(worker)
    if snapshot["version"] == ALIAS_VERSION:
        from .prepared_source_object_aliases import run as run_aliases

        return await run_aliases(worker, snapshot)
    inventory = worker.task["result"].get("prepared_scope_inventory")
    if inventory is None:
        inventory = await observe(worker, snapshot)
        validate(worker)
        worker.task["result"] = {**worker.task["result"], "prepared_scope_inventory": inventory}
        worker.checkpoint(worker.task["result"])
    proposal, contract = propose(snapshot, inventory)
    preservation = None
    if all(i["status"] == "supported" for i in proposal["items"]):
        value = await worker.call(
            {
                "动作": VERIFY,
                KEY: VERSION,
                "来源类型": "workflow_plan（冻结有效AI章纲，不是作者原话）",
                "有效AI章纲原文": snapshot["plot"]["plot"],
                "可引用原文": source_units(snapshot["plot"]["plot"], "AUTHOR"),
                "原全部P提案": [
                    {"item_id": f"P{i:02}", **x}
                    for i, x in enumerate(snapshot["prepared_contract"]["items"], 1)
                ],
                "原AI解释备注（历史提案，非来源/非新锁定）": snapshot["prepared_contract"]["note"],
                "范围字段窄修提案": proposal,
                "窄修后全P": [{"item_id": f"P{i:02}", **x} for i, x in enumerate(contract["items"], 1)],
                "独立原文观察（AI，不是真值）": inventory,
                "原固定条件": [
                    {"condition_id": f"FIX{i:03}", "text": v} for i, v in enumerate(snapshot["fixed"], 1)
                ],
                "要求": "独立逐P回到有效AI章纲判断全义，不能凭旧only、第一模型或提案自称保持就pass。只允许选定项recipient_scope/scope_condition纠偏；角色、动作、对象/原件副本、否定、许可/实际交付/尝试/计划阶段与所有其他字段不能改。命名收件人/一份材料不证明排他；许可另抄携带或禁止某人不得串为别的实际邮寄only。原话真实排他/条件仍必须保持。scope_only_fix_sufficient明确判断仅两字段是否足够：若action/object/direction/result等尚含无源only或需改任何其他含义，填no并fail，不能删完整条件或伪造窄修成功；无法判断unknown。所有原P/固定条件按原序各一次；item evidence只填AUTHOR编号，fixed证据只填同FIX编号。固定条件只核解释保留，后章未到不要求提前兑现。原AI解释备注仅是历史提案，不能证明原文排他或成为新锁定；备注原样保留，也须按原AI章纲独立核对。若无源only仍留在备注且须改备注才足够，scope_only_fix_sufficient填no或unknown并停止，不能把未核备注混成就绪核心。无候选/正文/旧正文检查/自由reason；来源就绪不是正文通过或作者文学批准。",
            },
            Preservation,
            {"scope": "仅冻结有效AI章纲、原解释、范围提案与固定条件的独立保持，无正文"},
            contract=preservation_schema(snapshot),
        )
        validate(worker)
        preservation = resolve_preservation(snapshot, value)
    state = "ready" if ready(proposal, preservation) else "unverified"
    payload = {
        "version": VERSION,
        "source_kind": "workflow_plan",
        "state": state,
        "snapshot": snapshot,
        "inventory": inventory,
        "proposal": proposal,
        "corrected_contract": contract,
        "preservation": preservation,
        "note": "只读AI章纲范围来源纠偏；不改旧prepared/正文/原报告，不表示正文或文学通过。",
    }

    def publish(tx, artifact_id):
        validate(worker)
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        result = {**worker.task["result"], "artifact_id": artifact_id, "prepared_scope_state": state}
        tx.execute(
            "UPDATE tasks SET status='succeeded',result=?,updated=? WHERE id=? AND attempt=?",
            (dumps(result), time(), worker.task["id"], worker.attempt),
        )
        worker.db.event(
            tx, worker.task["id"], "prepared_scope_receipt", {"artifact_id": artifact_id, "state": state}
        )

    worker.domain.stage(
        worker.task["payload"],
        KIND,
        payload,
        {"story_revision": snapshot["story_revision"]},
        worker.task["id"],
        worker.attempt,
        on_stage=publish,
    )


def freeze_recheck(domain, request, *, verify_basis=True, flow_entailment_version=None):
    if domain.artifact(request["receipt_id"])["payload"].get("version") == ALIAS_VERSION:
        from .prepared_source_object_aliases import freeze_recheck as freeze_alias_recheck

        return freeze_alias_recheck(
            domain, request, verify_basis=verify_basis, flow_entailment_version=flow_entailment_version
        )
    from .conditional_review import VERSION as COND
    from .book_applicability import VERSION as APP
    from .flow_entailment import VERSION as FLOW

    chosen = flow_entailment_version or FLOW
    original = old_freeze(
        domain, request, conditional_policy=COND, applicability_version=APP, flow_entailment_version=chosen
    )
    receipt = domain.artifact(request["receipt_id"])
    payload = receipt["payload"]
    if receipt["kind"] != KIND or receipt["status"] != "candidate" or not receipt.get("task_id"):
        raise DomainError("prepared_scope_receipt", "请选择本候选只读来源范围回执。", 409)
    task = domain.task(receipt["task_id"])
    validate_task(domain, task)
    if (
        task["kind"] != KIND
        or task["status"] != "succeeded"
        or task["result"].get(KEY) != VERSION
        or task["result"].get("artifact_id") != receipt["id"]
        or payload.get("snapshot") != task["result"].get(SNAPSHOT)
        or payload.get("version") != VERSION
        or payload.get("state") != "ready"
        or task["result"].get("prepared_scope_state") != "ready"
        or (receipt["novel_id"], receipt["branch_id"], receipt["chapter_id"])
        != (request["novel_id"], request["branch_id"], request["chapter_id"])
    ):
        raise DomainError("prepared_scope_receipt", "范围回执未就绪或创建来源不符。", 409)
    snapshot = freeze(
        domain,
        {
            **request,
            "item_ids": payload["snapshot"]["item_ids"],
            "preview_basis_sha256": payload["snapshot"]["basis_sha256"],
        },
    )
    if snapshot != payload["snapshot"] or not inventory_valid(payload["inventory"], snapshot):
        raise DomainError("prepared_scope_receipt", "旧来源或候选已改变，不套用历史回执。", 409)
    proposed, contract = propose(snapshot, payload["inventory"])
    try:
        verify_saved_preservation(snapshot, payload["preservation"])
    except (ValueError, KeyError, TypeError) as error:
        raise DomainError("prepared_scope_receipt", "来源保持回执不完整或字段不合法。", 409) from error
    if (
        proposed != payload["proposal"]
        or contract != payload["corrected_contract"]
        or not ready(proposed, payload["preservation"])
    ):
        raise DomainError("prepared_scope_receipt", "原义保持未通过或超出scope两字段，未核正文。", 409)
    result = {
        **original,
        "version": RECHECK_VERSION,
        "source_kind": "workflow_plan",
        "prepared_contract": contract,
        "receipt_id": receipt["id"],
        "receipt_payload_sha256": sha(payload),
        "source_scope_correction_metadata": {
            "version": VERSION,
            "receipt_id": receipt["id"],
            "items": proposed["items"],
        },
    }
    result["basis_sha256"] = sha(result)
    if verify_basis and request.get("preview_basis_sha256") != result["basis_sha256"]:
        raise DomainError(
            "source_correction_preview_changed", "正文复检依据改变，请显式重新读取；未重发。", 409
        )
    return result


def validate_recheck(worker):
    if worker.task["result"].get("source_corrected_recheck_version") == ALIAS_RECHECK_VERSION:
        from .prepared_source_object_aliases import validate_recheck as validate_alias_recheck

        return validate_alias_recheck(worker)
    from .flow_entailment import KEY as FLOW_KEY

    expected = worker.task["result"].get("source_corrected_recheck_snapshot")
    if (
        expected is None
        or worker.task["result"].get("source_corrected_recheck_version") != RECHECK_VERSION
        or expected.get(FLOW_KEY) != worker.task["result"].get(FLOW_KEY)
        or any(
            expected.get(k) != worker.task["result"].get(k)
            for k in ("conditional_book_policy_version", "book_applicability_version")
        )
        or freeze_recheck(
            worker.domain, worker.task["payload"], flow_entailment_version=expected.get(FLOW_KEY)
        )
        != expected
    ):
        raise DomainError("prepared_scope_recheck", "范围回执及正文目标/新FLOW冻结不一致，未继续。", 409)
    return expected


def verified_contract(worker, text, number, supplied):
    snapshot = validate_recheck(worker)
    if (
        text != snapshot["plot"]["plot"]
        or number != snapshot["plot"]["number"]
        or supplied != snapshot["prepared_contract"]
    ):
        raise DomainError("prepared_scope_recheck", "不能借正文复检重解释旧章纲或替换scope回执。", 409)
    return deepcopy(snapshot["prepared_contract"])


def preview(domain, artifact_id, *, verify_object_aliases=False):
    candidate = domain.artifact(artifact_id)
    payload = {
        "source_candidate_id": artifact_id,
        "candidate_revision": candidate["candidate_revision"],
        "novel_id": candidate["novel_id"],
        "branch_id": candidate["branch_id"],
        "chapter_id": candidate["chapter_id"],
        "base_revision": candidate["base_revision"],
        "item_ids": ["P01"],
        **({"verify_object_aliases": True} if verify_object_aliases else {}),
    }
    snapshot = freeze(domain, payload, verify_basis=False)
    return {
        "version": snapshot["version"],
        **({"verify_object_aliases": True} if verify_object_aliases else {}),
        "target": artifact_id,
        "candidate_revision": candidate["candidate_revision"],
        "source_kind": "workflow_plan",
        "source_text": snapshot["plot"]["plot"],
        "items": [{"id": f"P{i:02}", **v} for i, v in enumerate(snapshot["prepared_contract"]["items"], 1)],
        "source_text_sha256": snapshot["source_text_sha256"],
        "original_contract_sha256": snapshot["original_contract_sha256"],
        "basis_sha256": snapshot["basis_sha256"],
        "story_revision": snapshot["story_revision"],
        "source_workflow_id": snapshot["source_workflow_id"],
        "allowed_fields": sorted(FIELDS),
        "notice": (
            "显式核对物品不同称谓：精确关联不足时增加独立来源关联及费用，再独立保持核对；不改原物品或正文，不自动采纳。"
            if verify_object_aliases
            else "仅核冻结有效AI章纲的来源范围；不是作者输入或正文通过。两个独立来源步骤，额外费用沿原家族，不修改或采纳候选。"
        ),
    }


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/artifacts/{artifact_id}/prepared-source-scope-preview")
    def scope_preview(artifact_id: str, verify_object_aliases: bool = False):
        return preview(domain, artifact_id, verify_object_aliases=verify_object_aliases)

    @app.post("/api/artifacts/{artifact_id}/prepared-source-scope-corrections", status_code=202)
    def create_scope(artifact_id: str, data: ScopeCorrectionInput):
        payload = versioned_payload(actor.request_payload(domain, artifact_id, data))
        previous = domain.find_request(KIND, payload)
        if previous:
            return previous
        provider_ready(data.account)
        return domain.queue(KIND, payload)

    @app.get("/api/artifacts/{artifact_id}/prepared-source-scope-corrections")
    def scope_request(artifact_id: str, client_request_id: str):
        task = actor.query_request(domain, artifact_id, client_request_id, KIND)
        return {"source_candidate_id": artifact_id, "client_request_id": client_request_id, "task": task}
