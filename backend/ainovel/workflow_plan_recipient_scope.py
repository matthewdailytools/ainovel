"""Execution-only source checks for recipient restrictions before any prose exists."""

from copy import deepcopy
import hashlib
import json
import re
from typing import Literal

from pydantic import Field

from .db import DomainError, dumps
from .models import Input

KEY = "workflow_plan_recipient_scope_version"
VERSION = "workflow-plan-recipient-scope/1"
ACTION = "独立观察AI章纲接收范围来源"
GUIDANCE = (
    "命名一个收件人、只写一次寄送或一份抄件，不表示排除其他收件人。"
    "recipient_scope=only仅用于原话明确限制本项实际／尝试／计划传递的接收范围；"
    "普通寄往、交给、请求校核保留原收件方而scope为unspecified，不新增排他条件。"
    "另项只准携带副本属于许可动作／物品范围；单独禁止某人知情只排该人，均不能串成本项排除所有他人。"
    "真实仅向某方、禁止转给任何他人、限制条件、否定、原件与副本及传递阶段仍须保持；不为过关清掉原限制。"
    "没有排他原话不能凭命名目的地填only，真未明保留歧义。only／unrestricted的scope_condition严格等于该观察condition，无条件必须留空；不能从别项借时点或添加条件。独立来源范围观察另行核对，不代表作者新增锁定。"
)


class ScopeObservation(Input):
    observation_id: str
    kind: Literal[
        "recipient_exclusivity",
        "named_destination",
        "specific_recipient_exclusion",
        "action_permission",
        "explicitly_unrestricted",
        "unknown",
    ]
    supplier: str = Field(max_length=200)
    recipient: str = Field(max_length=200)
    transfer_object: str = Field(max_length=200)
    transfer_scope: Literal["attempted", "completed", "planned", "none"]
    evidence: str = Field(max_length=800)
    restriction_evidence: str = Field(max_length=800)
    condition: str = Field(max_length=800)
    reason: str = Field(min_length=1, max_length=1200)


class UnitCheck(Input):
    source_id: str
    status: Literal["covered", "not_scope_related", "unknown"]
    observation_ids: list[str] = Field(max_length=24)
    reason: str = Field(min_length=1, max_length=800)


class ScopeInventory(Input):
    input_sha256: str = Field(min_length=64, max_length=64)
    observations: list[ScopeObservation] = Field(max_length=24)
    unit_checks: list[UnitCheck] = Field(min_length=1)
    note: str = Field(max_length=2000)


def sha(value):
    return hashlib.sha256((value if isinstance(value, str) else dumps(value)).encode()).hexdigest()


def units(text):
    """Smaller citation choices retain exact source bytes and UTF-16 positions."""
    result = []
    for match in re.finditer(r"[^。！？；\n]+[。！？；]?", text):
        for start in range(match.start(), match.end(), 800):
            end = min(start + 800, match.end())
            quote = text[start:end]
            if quote.strip():
                result.append(
                    {
                        "source_id": f"SCOPE{len(result) + 1:04}",
                        "text": quote,
                        "start": len(text[:start].encode("utf-16-le")) // 2,
                        "end": len(text[:end].encode("utf-16-le")) // 2,
                    }
                )
    return result


def prior_rows(worker, row):
    from .workflow_plan_source_roles import ancestors

    ids = []
    for task in ancestors(worker.domain, worker.task):
        ids.append(task["id"])
        restart = task["result"].get("plan_preparation_restart", {})
        if (
            restart.get("source_task_id") == task["result"].get("continued_from")
            and restart.get("request", {}).get("chapter") == row["chapter"]
        ):
            break
    placeholders = ",".join("?" for _ in ids)
    events = worker.domain.db.all(
        f"SELECT payload FROM task_events WHERE task_id IN ({placeholders}) AND kind='checkpoint' ORDER BY id",
        tuple(ids),
    )
    found = []
    for event in events:
        snapshot = json.loads(event["payload"])
        candidate = snapshot.get("plot_preparations", {}).get(str(row["chapter"]))
        if candidate is not None:
            found.append(candidate)
    return found


def freeze(worker, row):
    """Only a genuinely new row receives this execution version."""
    if prior_rows(worker, row):
        raise DomainError("plan_scope_version", "该章已有冻结准备历史，不能以新行覆盖或升级。", 409)
    row[KEY] = VERSION


def enabled(worker, row):
    history = prior_rows(worker, row)
    first = history[0] if history else None
    marker = row.get(KEY)
    witnessed = first.get(KEY) if isinstance(first, dict) else None
    if marker is None and witnessed is None:
        return False
    if (
        marker != VERSION
        or witnessed != VERSION
        or first.get("chapter_id") != row.get("chapter_id")
        or first.get("original_plot_sha256") != row.get("original_plot_sha256")
        or row.get("workflow_plan_source_roles_version") != "workflow-plan-source-roles/1"
        or worker.task["result"].get("workflow_plan_source_roles_version") != "workflow-plan-source-roles/1"
    ):
        raise DomainError(
            "plan_scope_version", "接收范围执行版本或独立初次冻结见证不一致，未派发或降级。", 409
        )
    return True


def inventory_issues(value, token, choices):
    issues = []
    observations = value["observations"]
    ids = [f"O{index:02}" for index in range(1, len(observations) + 1)]
    if value["input_sha256"] != token or [o["observation_id"] for o in observations] != ids:
        issues.append("input_sha256须对应本次原文，观察按O01起连续编号，不得重复、遗漏或复用旧回执。")
    if [u["source_id"] for u in value["unit_checks"]] != list(choices):
        issues.append("原文每个SCOPE单元须按原序核对恰好一次，不得仅观察预期含限制的句子。")
    indexed = {o["observation_id"]: o for o in observations}
    for row in observations:
        if row["evidence"] not in choices:
            issues.append("每条范围观察须引用本次动作或传递原句编号，不填概括或旧解释。")
        restriction = row["restriction_evidence"]
        if restriction and restriction not in choices:
            issues.append("限制证据只能引用本次连续原句编号。")
        if (
            row["kind"]
            in {
                "recipient_exclusivity",
                "specific_recipient_exclusion",
                "action_permission",
                "explicitly_unrestricted",
            }
            and restriction not in choices
        ):
            issues.append(
                "排他、特定排除、许可或明确不限制必须有独立定位的原话依据，命名目的地不是排他证据。"
            )
        if row["kind"] == "named_destination" and (restriction or row["condition"]):
            issues.append("普通命名目的地不能补造接收范围限制或条件。")
        if row["condition"] and (
            restriction not in choices or row["condition"] not in choices[restriction]["text"]
        ):
            issues.append("限制条件必须是所引限制原句内的连续逐字原话，没有条件留空。")
        if row["kind"] == "recipient_exclusivity" and row["transfer_scope"] == "none":
            issues.append("实际接收范围排他须对应实际／尝试／计划传递；纯动作许可不能装成物品接收范围。")
        if not row["reason"].strip():
            issues.append("每条观察须说明限制附着于哪次动作、内容、阶段及接收范围，未知是合法结论。")
    for row in value["unit_checks"]:
        linked = row["observation_ids"]
        if len(linked) != len(set(linked)) or any(identifier not in indexed for identifier in linked):
            issues.append("单元覆盖只能关联本次O编号，不得重复或新增观察。")
        if (row["status"] == "covered") != bool(linked):
            issues.append("covered须关联本句观察；无关或未知单元不能伪称已有观察覆盖。")
        if any(
            row["source_id"] not in (indexed[i]["evidence"], indexed[i]["restriction_evidence"])
            for i in linked
            if i in indexed
        ):
            issues.append("单元覆盖必须对应所关联观察的动作或限制原句，不能移用别句。")
        if not row["reason"].strip():
            issues.append("每个原文单元均须说明是否涉及接收范围。")
    referenced = {identifier for row in value["unit_checks"] for identifier in row["observation_ids"]}
    if referenced != set(ids):
        issues.append("每条观察须有对应原句覆盖，不能生成无出处的范围。")
    return issues


def inventory_supported(value):
    return all(o["kind"] != "unknown" for o in value["observations"]) and all(
        u["status"] != "unknown" for u in value["unit_checks"]
    )


def inventory_valid(receipt, source):
    try:
        value = ScopeInventory.model_validate(receipt["inventory"]).model_dump()
        choices = {r["source_id"]: r for r in units(source)}
        token = sha({"version": VERSION, "source": source})
        return (
            receipt["version"] == VERSION
            and receipt["input_sha256"] == token
            and receipt["source_sha256"] == sha(source)
            and receipt["sources"] == list(choices.values())
            and not inventory_issues(value, token, choices)
            and receipt["status"] == ("supported" if inventory_supported(value) else "needs_review")
        )
    except ValueError, TypeError, KeyError:
        return False


def binding_issues(value, receipt):
    """Interpretations consume prior observations; they cannot create scope proof."""
    issues = []
    indexed = {o["observation_id"]: o for o in receipt["inventory"]["observations"]}
    used = set()
    keys = ("supplier", "recipient", "transfer_object", "transfer_scope")
    for index, item in enumerate(value["items"], 1):
        identifiers = item["scope_observation_ids"]
        if len(identifiers) != len(set(identifiers)) or any(i not in indexed for i in identifiers):
            issues.append(f"P{index:02}范围绑定只能引用本次既有O编号，不重复或补造。")
            continue
        used.update(identifiers)
        rows = [indexed[i] for i in identifiers]
        for row in rows:
            if row["kind"] in {"recipient_exclusivity", "explicitly_unrestricted"}:
                wanted = "only" if row["kind"] == "recipient_exclusivity" else "unrestricted"
                if item["recipient_scope"] != wanted or any(item[k] != row[k] for k in keys):
                    issues.append(
                        f"P{index:02}须保留已观察限制的同一物品、双方、阶段与范围，不可换成另一传递或清掉真限制。"
                    )
                if row["condition"] != item["scope_condition"]:
                    issues.append(
                        f"P{index:02}限制条件须逐字对应独立观察condition；无条件留空，不能扩大或缩小原接收范围。"
                    )
        kind = (
            "recipient_exclusivity"
            if item["recipient_scope"] == "only"
            else "explicitly_unrestricted"
            if item["recipient_scope"] == "unrestricted"
            else None
        )
        if kind and not any(r["kind"] == kind and all(item[k] == r[k] for k in keys) for r in rows):
            issues.append(
                f"P{index:02}的{item['recipient_scope']}没有同一传递的明确来源观察；命名目的地、一份抄件、另项许可或特定接收人排除都不能证明排他。保留原收件方与原动作，不新增范围。"
            )
    if used != set(indexed):
        issues.append("原文范围观察均须在解释中保持并绑定，不能遗漏真实限制或只挑有利观察。")
    return issues


def core_value(value):
    return {
        "items": [
            {k: deepcopy(v) for k, v in item.items() if k != "scope_observation_ids"}
            for item in value["items"]
        ],
        "note": value["note"],
    }


def bindings(value):
    return {
        "version": VERSION,
        "items": [
            {"item_id": f"P{index:02}", "observation_ids": deepcopy(item["scope_observation_ids"])}
            for index, item in enumerate(value["items"], 1)
        ],
    }


def bindings_valid(contract, binding, receipt):
    try:
        if binding["version"] != VERSION or [r["item_id"] for r in binding["items"]] != [
            f"P{i:02}" for i in range(1, len(contract["items"]) + 1)
        ]:
            return False
        value = {
            "items": [
                {**item, "scope_observation_ids": row["observation_ids"]}
                for item, row in zip(contract["items"], binding["items"], strict=True)
            ],
            "note": contract["note"],
        }
        return inventory_supported(receipt["inventory"]) and not binding_issues(value, receipt)
    except ValueError, TypeError, KeyError:
        return False


async def observe(worker, source):
    sources = units(source)
    choices = {row["source_id"]: row for row in sources}
    token = sha({"version": VERSION, "source": source})
    schema = ScopeInventory.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [token]
    schema["properties"]["unit_checks"].update(minItems=len(sources), maxItems=len(sources))
    schema["$defs"]["UnitCheck"]["properties"]["source_id"]["enum"] = list(choices)
    schema["$defs"]["UnitCheck"]["properties"]["observation_ids"]["items"]["enum"] = [
        f"O{i:02}" for i in range(1, 25)
    ]
    fields = schema["$defs"]["ScopeObservation"]["properties"]
    fields["observation_id"]["enum"] = [f"O{i:02}" for i in range(1, 25)]
    fields["evidence"]["enum"] = list(choices)
    fields["restriction_evidence"]["enum"] = ["", *choices]
    checked = await worker.grounded(
        {
            "动作": ACTION,
            "写前准备契约版本": "workflow-plan-preparation/1",
            "workflow_plan_source_roles_version": "workflow-plan-source-roles/1",
            "接收范围核对执行版本": VERSION,
            "input_sha256": token,
            "本章工作章纲原文": source,
            "可引用原文": sources,
            "分类口径": {
                "recipient_exclusivity": "原话明确排除其他所有接收方，并约束同一次传递；分别定位传递原句、排他限制和连续原话条件／时点。",
                "named_destination": "本次寄送／交给／告知对象已命名，但未声明只限该方；一份抄件或只写一个收件人不证明排他。",
                "specific_recipient_exclusion": "只排除特定对象接收或知情，保留原否定和阶段，不扩成除当前收件人以外其他人皆被排除。",
                "action_permission": "只准／只能限制的是行动、携带、使用或物品范围，不是实际资料接收范围；原许可对象在reason与原句保留，无实际传递则双方与物品留空、阶段none。",
                "explicitly_unrestricted": "原话明确不限制接收方，不能把未写限制当成明确不限制。",
                "unknown": "范围类别、条件或限制与哪次传递的归属无法仅由原话确认，不猜测或补造。",
            },
            "要求": "先独立读取本章原话，尚无P卡、解释、范围判断、期望方向、旧保持结论或正文。按原文先后列出与接收范围相关的观察O01起连续编号；普通命名目的地、真正排他、单独禁止某人和动作许可必须分别表达，不能借同段一个只字将别的邮寄变为排他。只记录原话已经指定的参与方、内容及实际／尝试／计划阶段，未明字段留空，不选择角色或新增锁定。原件／副本、独立传递、条件及否定分别保持，回执未到不撤销已经寄出。每个SCOPE单元按原序核对一次：covered关联本句观察编号，not_scope_related说明无接收范围事项，真不明unknown。每个观察须有本次原句出处；evidence与restriction_evidence只填SCOPE编号，condition只填限制原句内连续原话、没有条件留空；对普通命名目标restriction_evidence及condition均空。此为AI来源观察，不是作者条件或已发生故事事实。",
        },
        ScopeInventory,
        {"scope": "仅本章工作章纲原文及稳定来源编号；无P卡或解释、候选、正文、旧检查、作者原因、未来规划"},
        lambda value: inventory_issues(value, token, choices),
        contract=schema,
        max_output=8192,
        diagnostic_label="写前接收范围来源观察",
    )
    return {
        "version": VERSION,
        "input_sha256": token,
        "source_sha256": sha(source),
        "sources": sources,
        "inventory": checked,
        "status": "supported" if inventory_supported(checked) else "needs_review",
    }
