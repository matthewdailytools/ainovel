"""Opt-in source-only object naming links, without rewriting archived objects.

Raw scope observation stays the v1 blind frame. A separate model compares every
eligible observation; only a unique same-transfer/same-instance link can supply
scope fields, and independent full-source preservation must verify it again.
"""

from copy import deepcopy
import json
from time import time
from typing import Literal

from pydantic import Field

from . import prepared_source_scope_correction as old
from .candidate_recheck import freeze as original_freeze, sha
from .db import DomainError, dumps
from .evidence import source_units
from .models import Input
from . import workflow_plan_recipient_scope as scope

VERSION = old.ALIAS_VERSION
RECHECK_VERSION = old.ALIAS_RECHECK_VERSION
LINK_VERSION = "prepared-source-object-alias/1"
ASSOCIATE = "独立核对旧AI章纲物品称谓与同次实例关联"
VERIFY = "独立核对物品关联及范围窄修的全部原义保持"
USABLE = {"recipient_exclusivity", "named_destination", "explicitly_unrestricted"}


class AssociationCheck(Input):
    observation_id: str = Field(max_length=100)
    relation: Literal["same_transfer_same_instance", "different_transfer_or_instance", "unknown"]
    evidence: str = Field(max_length=100)
    reason: str = Field(min_length=1, max_length=1200)


class AssociationItem(Input):
    item_id: str = Field(max_length=100)
    checks: list[AssociationCheck] = Field(min_length=1, max_length=24)
    reason: str = Field(min_length=1, max_length=1200)


class Association(Input):
    input_sha256: str = Field(min_length=64, max_length=64)
    items: list[AssociationItem] = Field(min_length=1, max_length=12)
    note: str = Field(max_length=2000)


class AliasScopeItemCheck(old.ScopeItemCheck):
    object_alias_check: Literal["pass", "fail", "unknown", "not_needed"]


class AliasPreservation(old.Preservation):
    item_checks: list[AliasScopeItemCheck]


def freeze(domain, request, *, verify_basis=True):
    if request.get("verify_object_aliases") is not True:
        raise DomainError("prepared_scope_alias", "称谓关联核对仅限显式新政策。", 409)
    payload = {k: v for k, v in request.items() if k not in {"verify_object_aliases", old.KEY}}
    snapshot = old.freeze(domain, payload, verify_basis=False)
    snapshot.pop("basis_sha256")
    snapshot.update(version=VERSION, verify_object_aliases=True)
    snapshot["basis_sha256"] = sha({k: v for k, v in snapshot.items() if k != "item_ids"})
    if verify_basis and request.get("preview_basis_sha256") != snapshot["basis_sha256"]:
        raise DomainError("prepared_scope_basis", "称谓选项或来源依据改变，请重新读取确认。", 409)
    return snapshot


def validate_task(domain, task):
    snapshot = task["result"].get(old.SNAPSHOT)
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (task["id"], old.EVENT)
    )
    expected = {old.KEY: VERSION, "payload_sha256": sha(task["payload"]), "snapshot_sha256": sha(snapshot)}
    if (
        task["kind"] != old.KIND
        or task["payload"].get(old.KEY) != VERSION
        or task["payload"].get("verify_object_aliases") is not True
        or task["result"].get(old.KEY) != VERSION
        or len(events) != 1
        or json.loads(events[0]["payload"]) != expected
        or snapshot is None
        or freeze(domain, task["payload"]) != snapshot
    ):
        raise DomainError("prepared_scope_version", "新称谓政策及独立创建见证不一致，未升级旧任务。", 409)
    return snapshot


def eligible(item):
    return (
        item["transfer_scope"] in {"attempted", "completed", "planned"}
        and all(item[k] for k in ("supplier", "recipient", "transfer_object"))
        and item["supplier"] != item["recipient"]
    )


def candidates(item, inventory):
    if not eligible(item):
        return []
    return [
        row
        for row in inventory["inventory"]["observations"]
        if row["transfer_object"]
        and all(row[k] == item[k] for k in ("supplier", "recipient", "transfer_scope"))
    ]


def requirements(snapshot, inventory):
    if inventory["status"] != "supported":
        return []
    needed = []
    for n, item in enumerate(snapshot["prepared_contract"]["items"], 1):
        identifier = f"P{n:02}"
        rows = candidates(item, inventory)
        if identifier not in snapshot["item_ids"] or not rows or not any(r["kind"] in USABLE for r in rows):
            continue
        if len(rows) != 1 or rows[0]["transfer_object"] != item["transfer_object"]:
            needed.append(identifier)
    return needed


def association_token(snapshot, inventory):
    return sha(
        {
            "version": LINK_VERSION,
            "source_sha256": snapshot["source_text_sha256"],
            "inventory": inventory,
            "selected_items": [
                {"item_id": f"P{n:02}", **item}
                for n, item in enumerate(snapshot["prepared_contract"]["items"], 1)
                if f"P{n:02}" in snapshot["item_ids"]
            ],
        }
    )


def association_issues(value, snapshot, inventory):
    required = requirements(snapshot, inventory)
    problems = []
    if (
        value["input_sha256"] != association_token(snapshot, inventory)
        or [r["item_id"] for r in value["items"]] != required
    ):
        problems.append("须对应本次原文/观察/旧事项指纹，所需P按序各一次。")
        return problems
    choices = {r["source_id"]: r for r in scope.units(snapshot["plot"]["plot"])}
    for row in value["items"]:
        item = snapshot["prepared_contract"]["items"][int(row["item_id"][1:]) - 1]
        comparable = candidates(item, inventory)
        if [r["observation_id"] for r in row["checks"]] != [r["observation_id"] for r in comparable]:
            problems.append("同双方/阶段的全部原观察必须按序各核一次，不能挑一项而漏掉备选。")
            continue
        for check, observation in zip(row["checks"], comparable, strict=True):
            evidence = check["evidence"]
            if check["relation"] != "unknown" and evidence not in choices:
                problems.append("同指或排除均须本次原文连续编号依据；不能只凭称谓相似。")
            if evidence and evidence not in {observation["evidence"], observation["restriction_evidence"]}:
                problems.append("关联依据须回到该原观察自己的动作/限制原句，不借其他传递句。")
    return problems


def resolve_association(snapshot, inventory, value):
    value = Association.model_validate(value).model_dump()
    if association_issues(value, snapshot, inventory):
        raise DomainError("prepared_scope_alias", "称谓关联覆盖或连续来源不合法。", 409)
    sources = {r["source_id"]: r for r in scope.units(snapshot["plot"]["plot"])}
    results = []
    for row in value["items"]:
        item = snapshot["prepared_contract"]["items"][int(row["item_id"][1:]) - 1]
        indexed = {r["observation_id"]: r for r in candidates(item, inventory)}
        checks = [
            {
                "observation_id": r["observation_id"],
                "relation": r["relation"],
                "evidence_source": deepcopy(sources.get(r["evidence"])),
                "reason": r["reason"],
            }
            for r in row["checks"]
        ]
        same = [r for r in checks if r["relation"] == "same_transfer_same_instance"]
        complete = not any(r["relation"] == "unknown" for r in checks)
        supported = len(same) == 1 and complete and indexed[same[0]["observation_id"]]["kind"] in USABLE
        results.append(
            {
                "item_id": row["item_id"],
                "status": "supported" if supported else "different" if complete and not same else "unknown",
                "observation_id": same[0]["observation_id"] if supported else "",
                "checks": checks,
                "reason": row["reason"],
            }
        )
    return {
        "version": LINK_VERSION,
        "state": "supported" if all(r["status"] == "supported" for r in results) else "unverified",
        "input_sha256": value["input_sha256"],
        "required_item_ids": requirements(snapshot, inventory),
        "items": results,
        "note": value["note"],
    }


def not_needed(snapshot, inventory):
    return {
        "version": LINK_VERSION,
        "state": "not_needed",
        "input_sha256": association_token(snapshot, inventory),
        "required_item_ids": [],
        "items": [],
        "note": "本次无需要或允许追加关联的事项；正文及来源仍另核，不代表全部通过。",
    }


def verify_saved_association(snapshot, inventory, resolution):
    required = requirements(snapshot, inventory)
    if not required:
        if resolution != not_needed(snapshot, inventory):
            raise DomainError("prepared_scope_alias", "不需要的称谓关联回执被改变。", 409)
        return
    raw = {"input_sha256": resolution["input_sha256"], "note": resolution["note"], "items": []}
    for item in resolution["items"]:
        raw["items"].append(
            {
                "item_id": item["item_id"],
                "reason": item["reason"],
                "checks": [
                    {
                        "observation_id": check["observation_id"],
                        "relation": check["relation"],
                        "evidence": check["evidence_source"]["source_id"] if check["evidence_source"] else "",
                        "reason": check["reason"],
                    }
                    for check in item["checks"]
                ],
            }
        )
    if resolve_association(snapshot, inventory, raw) != resolution:
        raise DomainError("prepared_scope_alias", "称谓关联与冻结原文不符，未使用。", 409)


async def associate(worker, snapshot, inventory):
    if not old.inventory_valid(inventory, snapshot):
        raise DomainError("prepared_scope_alias", "称谓核对前的冻结盲观察无效，未派发关联。", 409)
    required = requirements(snapshot, inventory)
    if not required:
        return not_needed(snapshot, inventory)
    selected = []
    for n, item in enumerate(snapshot["prepared_contract"]["items"], 1):
        identifier = f"P{n:02}"
        if identifier in required:
            selected.append(
                {
                    "item_id": identifier,
                    "历史事项身份字段（非真值）": {k: v for k, v in item.items() if k not in old.FIELDS},
                    "须逐一核对的原观察": [r["observation_id"] for r in candidates(item, inventory)],
                }
            )
    token = association_token(snapshot, inventory)
    schema = Association.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [token]
    schema["properties"]["items"].update(minItems=len(required), maxItems=len(required))
    schema["$defs"]["AssociationItem"]["properties"]["item_id"]["enum"] = required
    schema["$defs"]["AssociationCheck"]["properties"]["evidence"]["enum"] = [
        "",
        *[r["source_id"] for r in scope.units(snapshot["plot"]["plot"])],
    ]
    value = await worker.grounded(
        {
            "动作": ASSOCIATE,
            old.KEY: VERSION,
            "物品称谓关联版本": LINK_VERSION,
            "input_sha256": token,
            "有效AI章纲原文": snapshot["plot"]["plot"],
            "可引用原文": scope.units(snapshot["plot"]["plot"]),
            "独立冻结原文观察（AI，非真值）": inventory,
            "所选旧P身份提案": selected,
            "要求": "只核同一次传递的同一实物/信息实例，不改原P字符串、不选择剧情、不判断scope字段。逐P对同双方/阶段全部O逐一判same_transfer_same_instance、different_transfer_or_instance或unknown，并为同指及排除给该O原动作/限制句SCOPE连续依据；不能强挑一个后漏掉其他。原件/抄本、两个副本、两次传递不能因同叫记录而合并；不同实体副本也不自动代表不同信息，原P指内容且无法唯一分清时保持unknown。仅含许可、别次交付、不同角色/阶段不关联为本次实际传递。旧P.object/动作/结果可帮助辨其所指，但旧提案不是原文真值、相似词不是同实例证据；缺原文依据未知，不造角色/物品/时点。没有正文、旧正文检查、自由reason或未来设定。关联只是独立提案，末尾另核全部原义。",
        },
        Association,
        {"scope": "仅冻结AI章纲、盲观察和所选旧事项身份，无正文或旧正文结论"},
        lambda value: association_issues(value, snapshot, inventory),
        contract=schema,
        diagnostic_label="物品称谓同次关联",
        repair_feedback=lambda: {
            "引用修正要求": "本步骤仅有冻结AI章纲、原盲观察及旧事项身份。按原顺序完整核对全部备选O，关联依据只能引用该O自己的原动作/限制SCOPE句；不读或补写正文，不为凑唯一关联把未知改为排除。"
        },
    )
    return resolve_association(snapshot, inventory, value)


def propose(snapshot, inventory, resolution):
    if not old.inventory_valid(inventory, snapshot):
        raise DomainError("prepared_scope_source", "原盲观察不能回到冻结原文。", 409)
    verify_saved_association(snapshot, inventory, resolution)
    contract = deepcopy(snapshot["prepared_contract"])
    proposals = []
    links = {r["item_id"]: r for r in resolution["items"]}
    for n, item in enumerate(contract["items"], 1):
        identifier = f"P{n:02}"
        observation = None
        status, reason = "supported", "未选择事项全部保持。"
        if identifier in snapshot["item_ids"]:
            rows = candidates(item, inventory)
            if inventory["status"] == "supported":
                if identifier in links and links[identifier]["status"] == "supported":
                    observation = next(
                        r for r in rows if r["observation_id"] == links[identifier]["observation_id"]
                    )
                elif (
                    identifier not in links
                    and len(rows) == 1
                    and rows[0]["transfer_object"] == item["transfer_object"]
                ):
                    observation = rows[0]
            if observation is None or observation["kind"] not in USABLE:
                status, reason = "unknown", "没有唯一同双方/阶段/实例来源关联；保留原事项，不放宽字符串匹配。"
                observation = None
            else:
                item["recipient_scope"] = {
                    "recipient_exclusivity": "only",
                    "explicitly_unrestricted": "unrestricted",
                    "named_destination": "unspecified",
                }[observation["kind"]]
                item["scope_condition"] = (
                    observation["condition"] if observation["kind"] != "named_destination" else ""
                )
                reason = "仅据原观察分类与唯一关联窄修两范围字段；原物品称谓不改，仍须独立保持。"
        proposals.append(
            {
                "item_id": identifier,
                "recipient_scope": item["recipient_scope"],
                "scope_condition": item["scope_condition"],
                "observation_ids": [observation["observation_id"]] if observation else [],
                "status": status,
                "reason": reason,
            }
        )
    return {"items": proposals}, contract


def preservation_schema(snapshot):
    schema = AliasPreservation.model_json_schema()
    ids = [f"P{n:02}" for n in range(1, len(snapshot["prepared_contract"]["items"]) + 1)]
    schema["properties"]["item_checks"].update(minItems=len(ids), maxItems=len(ids))
    p = schema["$defs"]["AliasScopeItemCheck"]["properties"]
    p["item_id"]["enum"] = ids
    p["evidence"]["enum"] = [r["source_id"] for r in source_units(snapshot["plot"]["plot"], "AUTHOR")]
    fixed = [f"FIX{n:03}" for n in range(1, len(snapshot["fixed"]) + 1)]
    schema["properties"]["fixed_checks"].update(minItems=len(fixed), maxItems=len(fixed))
    if fixed:
        schema["$defs"]["FixedCheck"]["properties"]["condition_id"]["enum"] = fixed
        schema["$defs"]["FixedCheck"]["properties"]["evidence"]["enum"] = fixed
    return schema


def resolve_preservation(snapshot, value):
    value = AliasPreservation.model_validate(value).model_dump()
    flags = [r.pop("object_alias_check") for r in value["item_checks"]]
    resolved = old.resolve_preservation(snapshot, value)
    for row, flag in zip(resolved["item_checks"], flags, strict=True):
        row["object_alias_check"] = flag
    return resolved


def verify_saved_preservation(snapshot, value):
    raw = deepcopy(value)
    for row in raw["item_checks"]:
        row["evidence"] = row.pop("evidence_source")["source_id"]
    for row in raw["fixed_checks"]:
        row["evidence"] = row["condition_id"]
    if resolve_preservation(snapshot, raw) != value:
        raise DomainError("prepared_scope_alias", "独立保持记录与原文不符。", 409)


def ready(proposal, preservation, resolution):
    return (
        old.ready(proposal, preservation)
        and resolution["state"] in {"not_needed", "supported"}
        and all(
            row["object_alias_check"]
            == ("pass" if row["item_id"] in resolution["required_item_ids"] else "not_needed")
            for row in preservation["item_checks"]
        )
    )


async def run(worker, snapshot):
    inventory = worker.task["result"].get("prepared_scope_inventory")
    if inventory is None:
        inventory = await old.observe(worker, snapshot)
        validate_task(worker.domain, worker.task)
        worker.task["result"] = {**worker.task["result"], "prepared_scope_inventory": inventory}
        worker.checkpoint(worker.task["result"])
    resolution = worker.task["result"].get("prepared_scope_object_aliases")
    if resolution is None:
        resolution = await associate(worker, snapshot, inventory)
        validate_task(worker.domain, worker.task)
        worker.task["result"] = {**worker.task["result"], "prepared_scope_object_aliases": resolution}
        worker.checkpoint(worker.task["result"])
    proposal, contract = propose(snapshot, inventory, resolution)
    preservation = None
    if all(r["status"] == "supported" for r in proposal["items"]):
        value = await worker.call(
            {
                "动作": VERIFY,
                old.KEY: VERSION,
                "来源类型": "workflow_plan（冻结AI章纲，非作者原话）",
                "有效AI章纲原文": snapshot["plot"]["plot"],
                "可引用原文": source_units(snapshot["plot"]["plot"], "AUTHOR"),
                "原全部P提案": [
                    {"item_id": f"P{n:02}", **r}
                    for n, r in enumerate(snapshot["prepared_contract"]["items"], 1)
                ],
                "原AI解释备注（历史提案，非来源/非新锁定）": snapshot["prepared_contract"]["note"],
                "独立原文观察（AI，不是真值）": inventory,
                "物品称谓关联（AI，不是真值）": resolution,
                "范围字段窄修提案": proposal,
                "窄修后全P": [{"item_id": f"P{n:02}", **r} for n, r in enumerate(contract["items"], 1)],
                "原固定条件": [
                    {"condition_id": f"FIX{n:03}", "text": text}
                    for n, text in enumerate(snapshot["fixed"], 1)
                ],
                "要求": "独立回原章纲逐P及全部固定条件核原义，不凭关联模型或旧only就pass。物品关联只供核对，未改原transfer_object；须再核唯一同次同一实物/信息实例，全部可比O是否有依据排除，原件/副本、不同副本同信息和两个传递不能混同。需要关联的事项object_alias_check仅充分可证为pass，缺依据unknown、不同实例fail；其余not_needed。只改选中两scope字段，主体、动作、方向、对象、结果、数值、否定、阶段、时点及未选事项保持。仅命名收件人不排他，许可不是交付，真only/条件仍保留。scope_only_fix_sufficient须说明两字段是否足够；其他核心字段或历史note仍残留无源限制且须改时填no/fail或unknown，note不成为新锁定且不改。逐P和FIX按原序各一次，P evidence仅AUTHOR，FIX同编号；后章未到不要求提前兑现。无正文/候选/旧正文结论/自由reason；就绪不代表正文或文学通过。",
            },
            AliasPreservation,
            {"scope": "仅原AI章纲/原事项/note/原固定条件及独立关联提案，不含正文"},
            contract=preservation_schema(snapshot),
        )
        validate_task(worker.domain, worker.task)
        preservation = resolve_preservation(snapshot, value)
    state = "ready" if ready(proposal, preservation, resolution) else "unverified"
    payload = {
        "version": VERSION,
        "source_kind": "workflow_plan",
        "state": state,
        "snapshot": snapshot,
        "inventory": inventory,
        "object_alias_resolution": resolution,
        "proposal": proposal,
        "corrected_contract": contract,
        "preservation": preservation,
        "note": "只读显式物品称谓关联及范围窄修；原称谓/观察/备注/原稿保持，仍须另行正文复检。",
    }

    def publish(tx, artifact_id):
        validate_task(worker.domain, worker.task)
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
        old.KIND,
        payload,
        {"story_revision": snapshot["story_revision"]},
        worker.task["id"],
        worker.attempt,
        on_stage=publish,
    )


def freeze_recheck(domain, request, *, verify_basis=True, flow_entailment_version=None):
    from .conditional_review import VERSION as COND
    from .book_applicability import VERSION as APP
    from .flow_entailment import VERSION as FLOW

    original = original_freeze(
        domain,
        request,
        conditional_policy=COND,
        applicability_version=APP,
        flow_entailment_version=flow_entailment_version or FLOW,
    )
    receipt = domain.artifact(request["receipt_id"])
    payload = receipt["payload"]
    if receipt["kind"] != old.KIND or receipt["status"] != "candidate" or not receipt.get("task_id"):
        raise DomainError("prepared_scope_receipt", "须选择本候选的显式称谓核对回执。", 409)
    task = domain.task(receipt["task_id"])
    validate_task(domain, task)
    if (
        task["status"] != "succeeded"
        or task["result"].get("artifact_id") != receipt["id"]
        or payload.get("snapshot") != task["result"].get(old.SNAPSHOT)
        or payload.get("version") != VERSION
        or payload.get("state") != "ready"
        or task["result"].get("prepared_scope_state") != "ready"
        or (receipt["novel_id"], receipt["branch_id"], receipt["chapter_id"])
        != (request["novel_id"], request["branch_id"], request["chapter_id"])
    ):
        raise DomainError("prepared_scope_receipt", "称谓关联回执未就绪或目标不符。", 409)
    snapshot = freeze(
        domain,
        {
            **request,
            "verify_object_aliases": True,
            "item_ids": payload["snapshot"]["item_ids"],
            "preview_basis_sha256": payload["snapshot"]["basis_sha256"],
        },
    )
    if snapshot != payload["snapshot"] or not old.inventory_valid(payload["inventory"], snapshot):
        raise DomainError("prepared_scope_receipt", "原来源或候选已改变，不套历史称谓回执。", 409)
    try:
        proposal, contract = propose(snapshot, payload["inventory"], payload["object_alias_resolution"])
        verify_saved_preservation(snapshot, payload["preservation"])
        valid = (
            proposal == payload["proposal"]
            and contract == payload["corrected_contract"]
            and ready(proposal, payload["preservation"], payload["object_alias_resolution"])
        )
    except (ValueError, KeyError, TypeError) as error:
        raise DomainError("prepared_scope_receipt", "称谓或保持回执格式不完整，未核正文。", 409) from error
    if not valid:
        raise DomainError("prepared_scope_receipt", "来源关联及独立保持未通过，未核正文。", 409)
    result = {
        **original,
        "version": RECHECK_VERSION,
        "verify_object_aliases": True,
        "source_kind": "workflow_plan",
        "prepared_contract": contract,
        "receipt_id": receipt["id"],
        "receipt_payload_sha256": sha(payload),
        "source_scope_correction_metadata": {
            "version": VERSION,
            "receipt_id": receipt["id"],
            "items": proposal["items"],
            "object_alias_resolution": deepcopy(payload["object_alias_resolution"]),
        },
    }
    result["basis_sha256"] = sha(result)
    if verify_basis and request.get("preview_basis_sha256") != result["basis_sha256"]:
        raise DomainError("source_correction_preview_changed", "称谓正文复检依据改变，请显式重新读取。", 409)
    return result


def validate_recheck(worker):
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
        raise DomainError("prepared_scope_recheck", "称谓/正文版本或冻结依据不符，未升级旧任务。", 409)
    return expected
