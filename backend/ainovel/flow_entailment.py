"""Verify independently observed transfer semantics using candidate prose only."""

from copy import deepcopy
import hashlib
from typing import Literal

from pydantic import Field

from .db import DomainError, dumps
from .evidence import resolve_field, source_schema, source_units
from .models import Input

KEY = "flow_source_entailment_version"
VERSION = "source-flow-entailment/1"
ACTION = "独立核对正文流向观察的来源蕴含"
OBSERVATION_VERSION = "source-role-observation/2"

# Names alone do not decide whether a request denotes the speech act or the
# requested answer/object. This guidance is independent of the expected plot.
OBJECT_GUIDANCE = (
    "按待识别内容本身的所指区分方向，不只按句子里谁索取。若内容是‘询问／请求／邀请／通知本身’，"
    "supplier是发出这次言语行为的人，recipient是被问／被请求／被邀请／被通知的人；发问人提出问题"
    "不等于已经取得答案。若内容是‘答案／知识／实物’，supplier是给出或拟给出该答案／内容的人，"
    "recipient是取得或索取该内容的人。例如甲问乙如何修船：‘修船办法的询问’从甲到乙，"
    "‘修船办法／回答’如由乙讲解则从乙到甲；同一交谈可含两个不同内容，不能因主题相同将两者"
    "都映射为同一个object_id。内容所指不明就ambiguous，不靠期待的方向猜测。阶段仍独立核对："
    "内容本身是询问／请求时，已经问出口／请求已送达可证明该言语行为completed；拒绝答复不否定"
    "已经发问。内容是所求答案／实物时，发起索取只是requested，答复已说出／物品已交付才是completed。"
    "只打算明日发问是planned；明确被打断且未问出口不能当已经发问，不因句中有问字就认定发生。"
)


FIELDS = ("object_id", "supplier_id", "recipient_id", "stage", "certainty", "evidence")


class FlowCheck(Input):
    flow_id: str
    result: Literal["supported", "unknown", "contradicted"]
    verified_fields: list[
        Literal["object_id", "supplier_id", "recipient_id", "stage", "certainty", "evidence"]
    ] = Field(max_length=6)
    evidence: str = Field(max_length=800)
    reason: str = Field(min_length=1, max_length=1200)


class FlowChecks(Input):
    input_sha256: str = Field(min_length=64, max_length=64)
    checks: list[FlowCheck] = Field(min_length=1, max_length=24)
    note: str = Field(max_length=2000)


def enabled(worker, version=None):
    result = (getattr(worker, "task", None) or {}).get("result") or {}
    if KEY not in result:
        return False
    if (
        result[KEY] != VERSION
        or result.get("plot_contract_version") != "author-plot-audit/2"
        or (version is not None and version != "author-plot-audit/2")
    ):
        raise DomainError("flow_entailment_version", "流向来源核对的冻结版本无法确认，未升级旧报告。", 409)
    return True


def check_issues(value, identifiers, token, sources, text):
    issues = []
    if value["input_sha256"] != token:
        issues.append("input_sha256须对应本次正文、名称表和观察提案，不能复用旧报告。")
    if sorted(row["flow_id"] for row in value["checks"]) != sorted(identifiers):
        issues.append("每个流向观察须恰好核对一次，不得遗漏、重复或新增。")
    for row in value["checks"]:
        resolve_field(row, "evidence", sources)
        verified = row["verified_fields"]
        if len(verified) != len(set(verified)):
            issues.append("verified_fields不能重复。")
        if row["result"] == "supported" and set(verified) != set(FIELDS):
            issues.append("supported须独立核实对象、双方、阶段、确定性和原引文全部字段。")
        if row["result"] == "contradicted" and set(verified) == set(FIELDS):
            issues.append("contradicted不能同时宣称原观察全部字段已受支持，须保留明确反证及未获支持的字段。")
        quote = row["evidence"]
        if quote and (not quote.strip() or quote not in text):
            issues.append("来源蕴含证据只能引用本次正文的连续原文。")
        if (row["result"] != "unknown" or verified) and not quote.strip():
            issues.append("支持或反证须有本次正文依据；缺少支持而没有明确反证应为unknown。")
        if not row["reason"].strip():
            issues.append("每项结论须说明对象、双方、阶段与引文是否共同得到支持。")
    return issues


async def review_flows(worker, text, entities, objects, observations):
    """One batch checks all relevant observations, including favorable ones."""
    proposals = [
        {"flow_id": f"F{index:02}", **deepcopy(flow)}
        for index, flow in enumerate(observations, 1)
        if flow["object_id"] in objects
    ]
    binding = {
        "version": VERSION,
        "text": text,
        "entities": entities,
        "objects": objects,
        "proposals": proposals,
    }
    token = hashlib.sha256(dumps(binding).encode()).hexdigest()
    if not proposals:
        return {
            "version": VERSION,
            "input_sha256": token,
            "status": "not_applicable",
            "proposals": [],
            "checks": [],
        }
    units = source_units(text, "ENTAIL")
    sources = {unit["source_id"]: unit for unit in units}
    identifiers = [row["flow_id"] for row in proposals]
    contract = source_schema(FlowChecks.model_json_schema(), units)
    contract["properties"]["input_sha256"]["enum"] = [token]
    contract["properties"]["checks"].update(minItems=len(proposals), maxItems=len(proposals))
    contract["$defs"]["FlowCheck"]["properties"]["flow_id"]["enum"] = identifiers
    checked = await worker.grounded(
        {
            "动作": ACTION,
            "流向来源蕴含版本": VERSION,
            "input_sha256": token,
            "本次正文": text,
            "实体别名对照": entities,
            "待识别物品名称": objects,
            "待核对观察提案": proposals,
            "可引用原文": units,
            "内容与阶段口径": OBJECT_GUIDANCE,
            "本步字段口径": "ambiguous是原观察certainty的取值，复核result只用supported／unknown／contradicted；无法确认时填unknown，不将ambiguous写入result。",
            "部分核实边界": "verified_fields只列本次连续原文已确认支持的原观察字段。supported须完整列出object_id、supplier_id、recipient_id、stage、certainty、evidence；整条unknown仍可保留已确认的部分字段，便于区分无关角色或仅未来安排，不把一处不明扩展为所有方面不明。每个所列字段均须由evidence本次原文支持，不能只因提案自称明确就列入。角色未知编号unknown不等于已确认某个身份，stage=unknown也不等于已确认当前发生。evidence字段指原观察引文确实支持所述观察，而非只是句子存在。",
            "判定": {
                "supported": "观察的具体内容、给出方、取得方、阶段和原引文均由正文支持；不能只证明主题相同。引文真实存在不等于它支持全部观察。",
                "contradicted": "正文明确给出与该观察相反的对象、方向或阶段，须引用反证。仅缺少支持、原引文不蕴含观察或没有说清不能判反证，应unknown。",
                "unknown": "对象所指、角色映射、阶段或原引文支持不足且没有明确反证；不猜测，不改写正文或观察来通过。",
            },
            "要求": "只核实每条观察是否由本次正文支持，不判断是否符合任何情节要求。名称表没有预期方向；不得补造角色身份或依旧报告认定。逐项检查object_id实际所指、supplier_id、recipient_id、stage、certainty和原引文所述行为。原观察证据也只是待核实提案，不能循环佐证；可回到本次全文核对但须定位连续原文。supported须全部实质断言受支持；unknown和contradicted均为有效结果，不为通过而删项或重造方向。只返回原flow_id的判断，不生成替代观察、不读取全书未来、规划或作者期望。",
        },
        FlowChecks,
        {"scope": "仅本次正文、无方向名称表与观察提案；未提供原情节、预期方向、逐项对照结论或旧报告"},
        lambda value: check_issues(value, identifiers, token, sources, text),
        contract=contract,
        max_output=8192,
        diagnostic_label="正文流向观察来源蕴含核对",
    )
    verdicts = {row["flow_id"]: row for row in checked["checks"]}
    return {
        "version": VERSION,
        "input_sha256": token,
        "status": "supported"
        if all(row["result"] == "supported" for row in checked["checks"])
        else "needs_review",
        "proposals": proposals,
        **checked,
        "supported_observations": [
            {key: value for key, value in row.items() if key != "flow_id"}
            for row in proposals
            if verdicts[row["flow_id"]]["result"] == "supported"
        ],
    }


def unresolved_for_item(report, item, object_id, entity_ids):
    """A verified unrelated/future branch does not add an author obligation."""
    checks = {row["flow_id"]: row for row in report["checks"]}
    stages = {"completed"} if item["transfer_scope"] == "completed" else {"offered", "requested", "completed"}
    supplier, recipient = entity_ids[item["supplier"]], entity_ids[item["recipient"]]
    unresolved = []
    for flow in report["proposals"]:
        row = checks[flow["flow_id"]]
        if row["result"] == "contradicted":
            continue
        verified = set(row["verified_fields"])
        if "object_id" in verified and flow["object_id"] != object_id:
            continue
        if row["result"] == "supported" and (
            flow["certainty"] == "explicit"
            and flow["stage"] != "unknown"
            and "unknown" not in (flow["supplier_id"], flow["recipient_id"])
        ):
            continue
        if "stage" in verified and flow["stage"] != "unknown" and flow["stage"] not in stages:
            continue

        def could_be(field, entity):
            return field not in verified or flow[field] == "unknown" or flow[field] == entity

        reverse_possible = could_be("supplier_id", recipient) and could_be("recipient_id", supplier)
        outside_only = (
            item.get("recipient_scope") == "only"
            and could_be("supplier_id", supplier)
            and not ("recipient_id" in verified and flow["recipient_id"] == recipient)
        )
        if reverse_possible or outside_only:
            unresolved.append({"observation": deepcopy(flow), "check": deepcopy(row)})
    return unresolved
