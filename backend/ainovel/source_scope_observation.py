"""Source-only grouped observations projected into the unchanged scope contract."""

import re
from typing import Literal

from pydantic import Field

from . import authored_source_scope as authored
from . import workflow_plan_recipient_scope as core
from .db import DomainError
from .models import Input

VERSION = "source-scope-observation/1"
SOURCE_KINDS = {"author_plot", "author_instruction", "workflow_plan"}
GUIDANCE = (
    "逐个SCOPE单元按原序分类；不输出O编号、evidence或覆盖表，这些由服务器生成。"
    "scope_related表示本句是范围观察的行动或限制锚；同一句可有多次不同的行动，分别记录。"
    "限制位于另一句时，只在行动句生成一条观察并引用restriction_source_id，限制句分类scope_related，"
    "可无自身观察，不为覆盖重复生成同一观察。not_scope_related和unknown均不生成观察；"
    "已知行动与真正未知范围同句时可用unknown观察，不能为消除unknown猜测。"
    "普通交给、寄往、告知、已提出请求及失败交易可明确行动对象而未限定其他接收者；"
    "使用named_destination，不因接收人未命名就造范围unknown，未指定的身份留空，不选人或补姓名。"
    "普通行动发生的时间／归还期限仍留在原句供全部情节解释和独立保持核对，"
    "不是接收限制条件，named_destination没有restriction_source_id或condition字段。"
    "recipient_exclusivity只表示同次传递明确排除所有其他接收者；specific_recipient_exclusion"
    "只排除特定接收者，不扩大到所有人。action_permission限制行动、使用、携带或物品，"
    "不证明已获所求许可／取得物品／实际交付；明确许可本身与请求行为分别保留。"
    "explicitly_unrestricted必须有明确不限制接收方的来源，未声明限制只能是unspecified，不能伪称unrestricted。"
    "所有限制类均须引用非空的restriction_source_id，可为本句或本次另句；condition只填该限制句"
    "连续逐字的限制条件，确实没有条件才空，不把普通事件时点搬入限制。"
    "未发生交付、双方均未交付、明确不知道原因等可为not_scope_related，不虚构接收人或知识。"
    "但不得向某方交付或不得让某方知情的明确限制仍须保留；不能按none阶段一律忽略。"
    "没有交给乙不等于没有交给任何人，请求被拒不等于请求未发生。"
    "不同原件／副本、两次传递、已经完成与未来安排分别保留；回指、实例或限制归属真不明仍unknown。"
    "本步骤不判断全部情节保持或文学质量；not_scope_related不豁免后续对全部原文的解释及独立保持，"
    "不能删原事件、时点、否定或许可来让范围核对通过。"
)


class Participants(Input):
    supplier: str = Field(max_length=200)
    recipient: str = Field(max_length=200)
    transfer_object: str = Field(max_length=200)
    transfer_scope: Literal["attempted", "completed", "planned", "none"]
    reason: str = Field(min_length=1, max_length=1200)


class NamedDestination(Participants):
    kind: Literal["named_destination"]


class Restriction(Participants):
    kind: Literal[
        "recipient_exclusivity",
        "specific_recipient_exclusion",
        "action_permission",
        "explicitly_unrestricted",
    ]
    restriction_source_id: str = Field(min_length=1, max_length=32)
    condition: str = Field(max_length=800)


class UnknownObservation(Participants):
    kind: Literal["unknown"]


class SourceUnit(Input):
    source_id: str
    classification: Literal["scope_related", "not_scope_related", "unknown"]
    observations: list[NamedDestination | Restriction | UnknownObservation] = Field(max_length=24)
    reason: str = Field(min_length=1, max_length=800)


class SourceObservation(Input):
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: list[SourceUnit] = Field(min_length=1)
    note: str = Field(max_length=2000)


def _sources(source, token):
    sources = core.units(source)
    if not sources:
        raise ValueError("来源没有可核对的SCOPE原文单元，未生成观察。")
    # The caller owns the source/phase basis; never derive or replace its token.
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
        raise ValueError("来源核对身份须为调用方冻结的SHA256。")
    return {unit["source_id"]: unit for unit in sources}


def schema_for(source, token):
    """Bind required unit order/count and restriction references to this source."""
    sources = _sources(source, token)
    schema = SourceObservation.model_json_schema()
    schema["properties"]["input_sha256"]["enum"] = [token]
    schema["properties"]["units"].update(minItems=len(sources), maxItems=len(sources))
    schema["$defs"]["SourceUnit"]["properties"]["source_id"]["enum"] = list(sources)
    schema["$defs"]["Restriction"]["properties"]["restriction_source_id"]["enum"] = list(sources)
    return schema


def project(value, source, token):
    """Validate grouped output and mechanically project it, without changing facts."""
    choices = _sources(source, token)
    value = SourceObservation.model_validate(value).model_dump()
    if value["input_sha256"] != token:
        raise ValueError("input_sha256未绑定本次来源核对。")
    if [unit["source_id"] for unit in value["units"]] != list(choices):
        raise ValueError("每个SCOPE单元须按原序恰好一次，缺失、重复或乱序均不投影。")
    observations = []
    links = {source_id: [] for source_id in choices}
    for unit in value["units"]:
        if not unit["reason"].strip():
            raise ValueError(f"{unit['source_id']}的分类理由为空。")
        if unit["classification"] != "scope_related" and unit["observations"]:
            raise ValueError(f"{unit['source_id']}的非范围／未知单元不能同时声明已知观察覆盖。")
        seen = set()
        for observation in unit["observations"]:
            identity = core.sha(observation)
            if identity in seen:
                raise ValueError(f"{unit['source_id']}重复生成相同观察，未合并或重复覆盖。")
            seen.add(identity)
            restriction = observation.get("restriction_source_id", "")
            if restriction and restriction not in choices:
                raise ValueError(f"{unit['source_id']}的限制来源不属于本次原文。")
            identifier = f"O{len(observations) + 1:02}"
            observations.append(
                {
                    "observation_id": identifier,
                    **{
                        key: observation[key]
                        for key in (
                            "kind",
                            "supplier",
                            "recipient",
                            "transfer_object",
                            "transfer_scope",
                            "reason",
                        )
                    },
                    "evidence": unit["source_id"],
                    "restriction_evidence": restriction,
                    "condition": observation.get("condition", ""),
                }
            )
            links[unit["source_id"]].append(identifier)
            if restriction and restriction != unit["source_id"]:
                links[restriction].append(identifier)
    checks = []
    for unit in value["units"]:
        linked = links[unit["source_id"]]
        related = unit["classification"] == "scope_related"
        if related != bool(linked):
            raise ValueError(f"{unit['source_id']}的分类与本句行动／限制锚覆盖冲突。")
        checks.append(
            {
                "source_id": unit["source_id"],
                "status": "covered" if related else unit["classification"],
                "observation_ids": linked,
                "reason": unit["reason"],
            }
        )
    inventory = core.ScopeInventory.model_validate(
        {"input_sha256": token, "observations": observations, "unit_checks": checks, "note": value["note"]}
    ).model_dump()
    errors = core.inventory_issues(inventory, token, choices)
    if errors:
        raise ValueError("；".join(errors))
    return inventory


def issues(value, source, token):
    """Use the same strict projection for grounded repairs and final readback."""
    try:
        project(value, source, token)
    except (ValueError, TypeError, KeyError) as error:
        return [str(error)]
    return []


def observation_request(source, *, token, source_kind):
    if source_kind not in SOURCE_KINDS:
        raise DomainError("source_scope_observation", "未知来源身份，未派发来源核对。", 409)
    schema = schema_for(source, token)
    is_plan = source_kind == "workflow_plan"
    instruction = {
        "动作": core.ACTION if is_plan else authored.ACTION,
        "来源观察输出契约版本": VERSION,
        "来源身份": source_kind,
        "来源角色": (
            "本章有效AI章纲，不是作者新增原话或已经发生的事实。completed按本章要求完成的动作判断，"
            "planned只表示本章表达的未来安排；观察阶段不替既有开放细节选择流程作选择。"
            if is_plan
            else "作者本次冻结情节原话／有效澄清，不补新剧情，不反推作者已有明确排他。"
        ),
        "input_sha256": token,
        "本次来源原文": source,
        "可引用原文": core.units(source),
        "分组分类边界": GUIDANCE,
        "要求": "只读取本次原文，不接收旧观察、P、正文、候选、旧错误或期望答案。每个单元按原序分类，"
        "限制引用可跨本次单元，真正unknown保留。输出不会证明完整剧情保持或文学通过。",
    }
    return {
        "instruction": instruction,
        "schema": SourceObservation,
        "context": {"scope": "仅本次来源原文及SCOPE编号，无P、旧观察、正文、候选、旧检查、规划或自由原因"},
        "validator": lambda value: issues(value, source, token),
        "contract": schema,
        "max_output": 8192,
        "diagnostic_label": "分组来源范围观察",
        "repair_feedback": lambda: {
            "来源观察输出契约版本": VERSION,
            "引用修正要求": "只按原始SCOPE来源修正本次分组结构与分类；普通时点不填接收限制，"
            "许可／请求／未交付不混淆，真unknown保留，不删原事件或按旧结论强制pass。",
        },
    }


async def observe(worker, source: str, *, token: str, source_kind: str) -> dict:
    value = await worker.grounded(**observation_request(source, token=token, source_kind=source_kind))
    try:
        return project(value, source, token)
    except (ValueError, TypeError, KeyError) as error:
        raise DomainError(
            "source_scope_observation", "新来源观察无法投影到原范围契约，未继续。", 409
        ) from error
