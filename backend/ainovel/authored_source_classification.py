"""Versioned source-only classification; unrelated scope never drops plot meaning."""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from . import authored_source_scope as authored
from .author_source_clarification import ConstraintCheck, SourceCheck
from .db import DomainError
from .models import Input

VERSION = "authored-source-scope/2"
ACTION = "独立核对作者来源分类与完整原意保持"
GUIDANCE = (
    "本步骤分类接收范围，不补齐故事的全部事实或知识。未声明排他不等于范围无法理解，"
    "普通命名目标使用named_destination而非unknown；未写限制为unspecified，不是明确unrestricted。"
    "失败购买仍可明确尝试双方与目标，阶段attempted且不得改成交；双方均未交付、测量方法、"
    "角色明确不知道原因、不能推出角色全知等没有接收范围事项的原句使用not_scope_related，"
    "不要为填覆盖表虚造unknown观察、补接收人或补全人物全部知识。"
    "剧情里明确的未知是已明确要求，不能当成核对器不理解原文。"
    "请求行为与请求所求许可、物品、答案分别判断；提出申请不表示得到许可或取得物品，"
    "已经说出请求也不因没有答复而变成请求未发生，不把所求物品反向当成申请人交付给受理人。"
    "一句有多个事项时分开保留，真正仅向、特定对象不得获知、条件时点、许可及归还要求不可丢。"
    "not_scope_related只免接收范围观察，不免全部原句的情节解释与原意保持。"
    "仅在本次确有范围问题且回指、同一实例、限制归属或条件无法确认时unknown，不能猜测消除。"
)


class ClassificationCheck(SourceCheck):
    classification_result: Literal["pass", "fail", "unknown"]


class ItemCheck(Input):
    item_id: str
    result: Literal["pass", "fail", "unknown"]
    evidence_ids: list[str] = Field(max_length=6)
    reason: str = Field(min_length=1, max_length=1200)


class Preservation(Input):
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_checks: list[ClassificationCheck]
    item_checks: list[ItemCheck]
    fixed_checks: list[SourceCheck]
    author_contract_check: ConstraintCheck
    summary: str = Field(max_length=2000)


def observation_request(entry, execution_version=None):
    """Pure versioned request builder; legacy output is kept byte-for-byte."""
    from . import authored_source_execution as execution

    execution_version = execution_version or execution.LEGACY_VERSION
    core = authored.core
    units = core.units(entry["text"])
    choices = {u["source_id"]: u for u in units}
    token = authored.token(entry, VERSION)
    schema = core.ScopeInventory.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [token]
    schema["properties"]["unit_checks"].update(minItems=len(units), maxItems=len(units))
    schema["$defs"]["UnitCheck"]["properties"]["source_id"]["enum"] = list(choices)
    ids = [f"O{i:02}" for i in range(1, 25)]
    schema["$defs"]["UnitCheck"]["properties"]["observation_ids"]["items"]["enum"] = ids
    fields = schema["$defs"]["ScopeObservation"]["properties"]
    fields["observation_id"]["enum"] = ids
    fields["evidence"]["enum"] = list(choices)
    fields["restriction_evidence"]["enum"] = ["", *choices]
    instruction = {
        "动作": authored.ACTION,
        authored.KEY: VERSION,
        "input_sha256": token,
        "作者本次情节原话": entry["text"],
        "可引用原文": units,
        "分类口径": {
            "recipient_exclusivity": "明确排除其他所有接收者，定位同一传递、原件/副本、阶段与原条件。",
            "named_destination": "只命名本次传递目的地，不是排他；传递尝试失败不令明确的双方与目标未知。",
            "specific_recipient_exclusion": "只排除某人接收或知情，不扩成排除所有人。",
            "action_permission": "许可使用/携带/另抄限制动作或物品，不等于已交付，不冒充请求已获准。",
            "explicitly_unrestricted": "原话明确不限制接收方；未写限制不能用此类。",
            "unknown": "确有范围问题但仅据原句不能确认其回指、同一实例、限制归属或条件，不补剧情。",
        },
        "范围分类边界": GUIDANCE,
        "要求": "先独立读全部原话，没有P、旧观察、期望方向、正文、候选、全书约定或自由原因。O01起连续编号，全部SCOPE单元按原序恰好核对一次；covered绑定本句动作或限制O，not_scope_related仍在后续完整解释保留原句，真正不明unknown。evidence及restriction_evidence只填本次编号，条件为限制句连续原话，没有条件空。保留真实否定、角色、物品、不同次传递和阶段。未知有效，不能因覆盖要求造观察或强行通过。",
    }
    repair = {
        "引用修正要求": "本阶段没有正文、P或旧观察。只依据给定作者原话和SCOPE编号修正引用与分类；非范围事实和明确的剧情未知不造范围unknown，真正范围歧义保留unknown，不删限制、不补角色或剧情。"
    }

    def validator(value):
        return (
            execution.inventory_issues_detailed(value, token, choices)
            if execution_version == execution.VERSION
            else core.inventory_issues(value, token, choices)
        )

    if execution_version == execution.VERSION:
        instruction.update({execution.KEY: execution.VERSION, "直接引用覆盖与请求边界": execution.GUIDANCE})
        repair["引用修正要求"] += execution.GUIDANCE
    elif execution_version != execution.LEGACY_VERSION:
        raise DomainError("authored_execution_version", "未知来源观察执行模板，未派发。", 409)
    return {
        "instruction": instruction,
        "schema": core.ScopeInventory,
        "context": {"scope": "仅作者本次原话及稳定编号，无旧P/旧观察/原因/正文/候选/未来章"},
        "validator": validator,
        "contract": schema,
        "max_output": 8192,
        "diagnostic_label": "作者来源范围分类",
        "repair_feedback": lambda: deepcopy(repair),
    }


async def observe(worker, entry):
    from . import authored_source_execution as execution

    version = worker.task["result"].get(execution.KEY, execution.LEGACY_VERSION)
    if version == execution.VERSION:
        execution.ensure_runtime(worker.task, worker.domain, worker.settings)
    from .source_observation_recheck import output_version

    output = output_version(worker)
    if output is not None:
        from .source_scope_observation import observe as observe_grouped

        value = await observe_grouped(
            worker, entry["text"], token=authored.token(entry, VERSION), source_kind="author_plot"
        )
        return {
            "version": VERSION,
            "input_sha256": authored.token(entry, VERSION),
            "source_sha256": entry["sha256"],
            "sources": authored.core.units(entry["text"]),
            "inventory": value,
            "status": "supported" if authored.core.inventory_supported(value) else "needs_review",
            **execution.record_metadata(worker.task),
            "source_observation_output_version": output,
        }
    request = observation_request(entry, version)
    repair = request.pop("repair_feedback")

    def repair_feedback():
        if version == execution.VERSION:
            execution.ensure_runtime(worker.task, worker.domain, worker.settings)
        return repair()

    value = await worker.grounded(**request, repair_feedback=repair_feedback)
    return {
        "version": VERSION,
        "input_sha256": authored.token(entry, VERSION),
        "source_sha256": entry["sha256"],
        "sources": authored.core.units(entry["text"]),
        "inventory": value,
        "status": "supported" if authored.core.inventory_supported(value) else "needs_review",
        **execution.record_metadata(worker.task),
    }


def fixed_sources(worker, entry):
    payload = worker.task["payload"]
    values = list(payload.get("fixed", []))
    if worker.task["kind"] == "workflow":
        for plot in payload.get("plots", []):
            if plot["number"] == entry["number"]:
                values.extend(v for v in plot["fixed"] if v not in values)
    elif payload.get("action", "").startswith("reference."):
        ref = worker.task["result"].get("reference_snapshot", {}).get("payload", {})
        values.extend(v for v in ref.get("fixed", []) if v not in values)
    return [{"source_id": f"FIX{i:03}", "text": v} for i, v in enumerate(values, 1)]


def source_inventory(record):
    current = record
    while current.get("inherited"):
        current = current["inherited"]["scope_receipt"]
        if not isinstance(current, dict):
            raise ValueError("invalid inherited source")
    return current["phases"]["observe"]["response"]


def preservation_input(worker, record):
    return {
        "version": VERSION,
        "source_sha256": record["source_sha256"],
        "inventory_sha256": authored.sha(source_inventory(record)),
        "contract_sha256": authored.sha(record["contract"]),
        "fixed": fixed_sources(worker, record["source"]),
        "author_contract": deepcopy(worker.author_snapshot()),
    }


def _reference_details(row, path, choices, *, source_only=False):
    evidence = row["evidence_ids"]
    details = []
    invalid = list(dict.fromkeys(i for i in evidence if i not in choices))
    if invalid:
        allowed = "SCOPE原句" if source_only else "SCOPE／FIX原句"
        details.append(
            f"{path}.evidence_ids含非法编号{','.join(invalid)}；只允许本次{allowed}编号，"
            "O编号属于AI观察，不是原句证据"
        )
    if len(evidence) != len(set(evidence)):
        details.append(f"{path}.evidence_ids有重复编号，须逐项去重")
    if not row["reason"].strip():
        details.append(f"{path}.reason为空，须说明原文依据")
    if source_only and row["result"] == "pass" and not evidence:
        details.append(f"{path}.evidence_ids为空，pass须有本次SCOPE原句依据")
    return "；".join(details)


def issues(value, entry, contract, basis, *, detailed=False):
    errors = []
    sources = authored.core.units(entry["text"])
    expected = {
        "source_checks": [s["source_id"] for s in sources],
        "fixed_checks": [s["source_id"] for s in basis["fixed"]],
    }
    choices = set(expected["source_checks"] + expected["fixed_checks"])
    if value["input_sha256"] != authored.sha(basis):
        errors.append("保持输入必须绑定本次完整来源、观察、P、固定项和采用约定。")
    for field, ids in expected.items():
        if [r["source_id"] for r in value[field]] != ids:
            errors.append(f"{field}须按全部原编号顺序各核一次，不因not_scope_related省略。")
        for row in value[field]:
            evidence = row["evidence_ids"]
            if (
                len(evidence) != len(set(evidence))
                or any(i not in choices for i in evidence)
                or not row["reason"].strip()
            ):
                message = "保持只引用本次完整连续来源编号并说明理由。"
                if detailed:
                    message += " " + _reference_details(row, f"{field}.{row['source_id']}", choices)
                errors.append(message)
            if (row["result"] == "pass" or row.get("classification_result") == "pass") and row[
                "source_id"
            ] not in evidence:
                errors.append("原句分类/原意或固定项通过须引用该项自身来源，未知可留空。")
    if [r["item_id"] for r in value["item_checks"]] != [
        f"P{i:02}" for i in range(1, len(contract["items"]) + 1)
    ]:
        errors.append("全部P须按原序各核一次，不能只检查有O绑定的项。")
    for row in value["item_checks"]:
        evidence = row["evidence_ids"]
        if (
            len(evidence) != len(set(evidence))
            or any(i not in expected["source_checks"] for i in evidence)
            or not row["reason"].strip()
            or (row["result"] == "pass" and not evidence)
        ):
            message = "P通过须回到本次原话，不能从观察、固定项或候选补造情节依据。"
            if detailed:
                message += " " + _reference_details(
                    row,
                    f"item_checks.{row['item_id']}",
                    expected["source_checks"],
                    source_only=True,
                )
            errors.append(message)
    if not value["author_contract_check"]["reason"].strip():
        errors.append("采用约定保持须明确说明，不能空白通过。")
    return errors


def detailed_issues(value, entry, contract, basis):
    """Locate reference errors without changing acceptance or legacy feedback."""
    return issues(value, entry, contract, basis, detailed=True)


def passed(value):
    return (
        all(
            r["result"] == "pass"
            for key in ("source_checks", "item_checks", "fixed_checks")
            for r in value[key]
        )
        and all(r["classification_result"] == "pass" for r in value["source_checks"])
        and value["author_contract_check"]["result"] == "pass"
    )


def receipt_valid(record, text, owner_ids=None):
    from .plot_audit import ScopedAuthorPlot, author_contract_issues

    try:
        from .authored_source_execution import record_valid, same_record_metadata

        if not record_valid(record):
            return False
        entry = record["source"]
        contract = ScopedAuthorPlot.model_validate(record["contract"]).model_dump()
        if (
            record["version"] != VERSION
            or entry["text"] != text
            or entry["sha256"] != authored.sha(text)
            or record["source_sha256"] != entry["sha256"]
            or author_contract_issues(contract, text, scoped=True)
        ):
            return False
        if record.get("inherited"):
            prior = record["inherited"]["scope_receipt"]
            slot = record["phases"]["preserve"]
            basis = record["preservation_input"]
            value = Preservation.model_validate(slot["response"]).model_dump()
            inventory = source_inventory(prior)
            return (
                record["state"] == "ready"
                and record["contract"] == record["inherited"]["contract"]
                and record["inherited"]["source_policy_version"] == VERSION
                and authored.receipt_valid(prior, text)
                and set(record["phases"]) == {"preserve"}
                and slot["status"] == "completed"
                and slot["response_sha256"] == authored.sha(slot["response"])
                and (owner_ids is None or slot["owner_task_id"] in owner_ids)
                and basis["version"] == VERSION
                and basis["source_sha256"] == authored.sha(text)
                and basis["inventory_sha256"] == authored.sha(inventory)
                and basis["contract_sha256"] == authored.sha(contract)
                and not issues(value, entry, contract, basis)
                and passed(value)
            )
        phases = record["phases"]
        if set(phases) != {"observe", "interpret", "preserve"} or any(
            s["status"] != "completed"
            or s["response_sha256"] != authored.sha(s["response"])
            or (owner_ids is not None and s["owner_task_id"] not in owner_ids)
            for s in phases.values()
        ):
            return False
        inventory, interpretation = phases["observe"]["response"], phases["interpret"]["response"]
        if not same_record_metadata(record, inventory) or not record_valid(
            inventory, record.get("authored_source_observation_execution_snapshot")
        ):
            return False
        value = Preservation.model_validate(phases["preserve"]["response"]).model_dump()
        basis = record["preservation_input"]
        return (
            record["state"] == "ready"
            and authored.inventory_valid(inventory, entry)
            and inventory["version"] == VERSION
            and inventory["status"] == "supported"
            and interpretation["contract"] == contract
            and interpretation["bindings"] == record["bindings"]
            and authored.binding_valid(contract, record["bindings"], inventory)
            and all(i["certainty"] == "explicit" for i in contract["items"])
            and all(b["status"] == "supported" for b in record["bindings"]["items"])
            and basis["version"] == VERSION
            and basis["source_sha256"] == authored.sha(text)
            and basis["inventory_sha256"] == authored.sha(inventory)
            and basis["contract_sha256"] == authored.sha(contract)
            and not issues(value, entry, contract, basis)
            and passed(value)
        )
    except KeyError, TypeError, ValueError:
        return False


def completed_unverified_record(domain, current, entry, record):
    """The existing clarification entry accepts known /2 stops, never unknown calls."""
    from .plot_audit import ScopedAuthorPlot, author_contract_issues

    owners = {t["id"] for t in authored.ancestry(current, domain)}
    try:
        from .authored_source_execution import record_valid, same_record_metadata

        if not record_valid(record):
            raise ValueError("invalid observation execution record")
        if (
            record["state"] != "unverified"
            or record["source"] != entry
            or record["source_sha256"] != entry["sha256"]
            or record.get("inherited")
        ):
            raise ValueError("not a known stop")
        phases = record["phases"]
        if set(phases) not in (
            {"observe"},
            {"observe", "interpret"},
            {"observe", "interpret", "preserve"},
        ) or any(
            s["status"] != "completed"
            or s["owner_task_id"] not in owners
            or s["response_sha256"] != authored.sha(s["response"])
            for s in phases.values()
        ):
            raise ValueError("unknown phase")
        inventory = phases["observe"]["response"]
        if (
            not same_record_metadata(record, inventory)
            or inventory["version"] != VERSION
            or not authored.inventory_valid(inventory, entry)
        ):
            raise ValueError("invalid inventory")
        if "interpret" not in phases:
            if inventory["status"] != "needs_review":
                raise ValueError("missing interpretation")
            return record
        interpreted = phases["interpret"]["response"]
        contract = ScopedAuthorPlot.model_validate(record["contract"]).model_dump()
        if (
            inventory["status"] != "supported"
            or interpreted["contract"] != contract
            or interpreted["bindings"] != record["bindings"]
            or not authored.binding_valid(contract, record["bindings"], inventory)
            or author_contract_issues(contract, entry["text"], scoped=True)
        ):
            raise ValueError("invalid interpretation")
        explicit = (
            bool(contract["items"])
            and all(i["certainty"] == "explicit" for i in contract["items"])
            and all(i["status"] == "supported" for i in record["bindings"]["items"])
        )
        if "preserve" not in phases:
            if explicit:
                raise ValueError("missing preservation")
            return record
        basis = record["preservation_input"]
        value = Preservation.model_validate(phases["preserve"]["response"]).model_dump()
        if (
            not explicit
            or basis["version"] != VERSION
            or basis["source_sha256"] != entry["sha256"]
            or basis["inventory_sha256"] != authored.sha(inventory)
            or basis["contract_sha256"] != authored.sha(contract)
            or issues(value, entry, contract, basis)
            or passed(value)
        ):
            raise ValueError("not a known unverified preservation")
        return record
    except (KeyError, TypeError, ValueError) as error:
        raise DomainError(
            "author_clarification_checkpoint",
            "仅已完整返回的新版来源待核停点可补充，未知调用、失效或已通过结果不重做。",
            409,
        ) from error


def preservation_request(record, inventory):
    """Rebuild the original preservation request without worker state or I/O."""
    entry = record["source"]
    basis = record["preservation_input"]
    schema = Preservation.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [authored.sha(basis)]
    return {
        "instruction": {
            "动作": ACTION,
            authored.KEY: VERSION,
            "input_sha256": authored.sha(basis),
            "作者本次原话": entry["text"],
            "全部原话连续单元": inventory["sources"],
            "先行分类（AI提案不是真值）": inventory["inventory"],
            "完整P解释（AI提案非新条件）": record["contract"],
            "本章及全书固定条件": basis["fixed"],
            "采用创作约定": basis["author_contract"],
            "分类边界": GUIDANCE,
            "要求": "独立逐全部SCOPE原句核查分类是否漏掉真范围要求，以及P是否保留完整原意；不能凭先行观察或P自称通过。每句source_checks给classification_result和result，全部P按P01起在item_checks各一次，全部固定项各一次；pass必须给本项来源编号。not_scope_related不免剧情：保留失败/未交付、角色不知道或不能推出全知、请求与许可/取得区别、归还条件、真排他、特定禁止、原件副本、阶段时点和否定。source/fixed通过只引用自己的原句，P依据只能本次原话；新增无源内容或遗漏原句fail，不能确认unknown。全书未到的后续结果只核解释保留，不要求提前兑现。此处没有正文、候选、旧结论或自由原因，不补剧情，不是文学批准。",
        },
        "schema": Preservation,
        "context": {"scope": "完整作者原话、固定项和采用约定的独立保持；无正文/候选/未来章"},
        "validator": lambda value: issues(value, entry, record["contract"], basis),
        "max_output": 4096,
        "contract": schema,
        "diagnostic_label": "作者来源分类与完整原意保持",
        "repair_feedback": lambda: {
            "引用修正要求": "本步骤没有正文或候选。只回到全部SCOPE原句、全部P及FIX，按完整编号顺序核分类和原意；not_scope_related不准漏原句，真正unknown/fail不为修引用改pass。"
        },
    }


async def independent_preserve(worker, record, inventory, owners):
    from .workflow_plan_preparation import phase
    from .authored_source_execution import ensure_runtime

    ensure_runtime(worker.task, worker.domain, worker.settings)

    entry = record["source"]
    basis = preservation_input(worker, record)
    if "preservation_input" in record and record["preservation_input"] != basis:
        raise DomainError("authored_scope_changed", "独立保持输入改变，未重派旧步骤。", 409)
    record["preservation_input"] = basis
    worker.checkpoint(worker.task["result"])
    request = preservation_request(record, inventory)
    request["diagnostic_legacy_validator"] = request["validator"]
    request["validator"] = lambda value: detailed_issues(value, entry, record["contract"], basis)
    result = await phase(
        worker,
        record,
        "preserve",
        "checking",
        lambda: worker.grounded(**request),
        owner_ids=owners,
    )
    record["state"] = "ready" if passed(result) else "unverified"


async def prepare(worker, entry):
    from .plot_audit import interpret_plot, ScopedAuthorPlot
    from .workflow_plan_preparation import phase
    from . import authored_source_execution as execution

    if (
        not authored.validate_task(worker.task, worker.domain)
        or worker.task["result"][authored.KEY] != VERSION
    ):
        raise DomainError("authored_scope_version", "本任务未冻结新版作者来源政策。", 409)
    execution.ensure_runtime(worker.task, worker.domain, worker.settings)
    records = worker.task["result"].setdefault(authored.RECEIPTS, {})
    record = records.get(entry["key"])
    inherited = worker.task["result"][authored.SNAPSHOT]["inherited"]
    if record is None:
        record = records[entry["key"]] = {
            "version": VERSION,
            "source": deepcopy(entry),
            "source_sha256": entry["sha256"],
            "phases": {},
            "inherited": deepcopy(inherited),
            "state": "pending",
            **execution.record_metadata(worker.task),
        }
        worker.checkpoint(worker.task["result"])
    if (
        record.get("source") != entry
        or record.get("version") != VERSION
        or record.get("inherited") != inherited
    ):
        raise DomainError("authored_scope_changed", "新版来源回执与冻结原话不符。", 409)
    owners = {t["id"] for t in authored.ancestry(worker.task, worker.domain)}
    if inherited and record["state"] not in {"ready", "unverified"}:
        prior = inherited.get("scope_receipt")
        if inherited.get("source_policy_version") != VERSION or not authored.receipt_valid(
            prior, entry["text"]
        ):
            raise DomainError("authored_scope_changed", "继承来源缺少已核实的原意保持。", 409)
        record["contract"] = deepcopy(inherited["contract"])
        await independent_preserve(worker, record, source_inventory(prior), owners)
        worker.checkpoint(worker.task["result"])
    if record["state"] == "ready":
        if record.get("preservation_input") != preservation_input(worker, record) or not receipt_valid(
            record, entry["text"], owners
        ):
            raise DomainError("authored_scope_changed", "新版来源或独立保持已改变，未复用。", 409)
        return deepcopy(record["contract"])
    if record["state"] == "unverified":
        if authored.executable(worker.task):
            raise DomainError(
                "author_source_unverified",
                "来源观察或完整原意保持尚未核实；不代表作者原话有误，未生成正文。",
                409,
            )
        return deepcopy(record["contract"])
    if not authored.core.units(entry["text"]):
        record.update(state="unverified", contract={"items": [], "note": "来源没有可核对原话，未补造。"})
    else:
        execution.ensure_runtime(worker.task, worker.domain, worker.settings)
        inventory = await phase(
            worker, record, "observe", "observing", lambda: observe(worker, entry), owner_ids=owners
        )
        if not authored.inventory_valid(inventory, entry) or inventory["version"] != VERSION:
            raise DomainError("authored_scope_changed", "新版分类未绑定本次来源。", 409)
        if inventory["status"] != "supported":
            record.update(
                state="unverified",
                contract={
                    "items": [],
                    "note": "接收范围来源观察尚未核实，未认定作者原话有歧义；旧原话与新观察保留。",
                },
            )
        else:
            execution.ensure_runtime(worker.task, worker.domain, worker.settings)
            interpreted = await phase(
                worker,
                record,
                "interpret",
                "interpreting",
                lambda: interpret_plot(
                    worker,
                    entry["text"],
                    source_kind=entry["kind"],
                    source_position=entry["number"],
                    authored_inventory=inventory,
                ),
                owner_ids=owners,
            )
            record.update(
                contract=ScopedAuthorPlot.model_validate(interpreted["contract"]).model_dump(),
                bindings=deepcopy(interpreted["bindings"]),
            )
            explicit = (
                bool(record["contract"]["items"])
                and all(i["certainty"] == "explicit" for i in record["contract"]["items"])
                and all(i["status"] == "supported" for i in record["bindings"]["items"])
            )
            if not explicit:
                record["state"] = "unverified"
            else:
                await independent_preserve(worker, record, inventory, owners)
    worker.checkpoint(worker.task["result"])
    if record["state"] != "ready" and authored.executable(worker.task):
        raise DomainError(
            "author_source_unverified",
            "来源分类、解释或独立原意保持尚未核实；原话及报告保留，未生成正文。",
            409,
        )
    return deepcopy(record["contract"])
