"""Author direction remains separate from genre guidance and generated story facts."""

from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .db import DomainError


CONTRACT_VERSION = "1.0.0"
ShortText = Annotated[str, Field(min_length=1, max_length=2000)]


class AuthorIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_target: str = Field(default="", max_length=4000)
    core_hook: str = Field(default="", max_length=4000)
    direction: str = Field(default="", max_length=6000)
    fixed_results: list[ShortText] = Field(default_factory=list, max_length=20)
    prohibitions: list[ShortText] = Field(default_factory=list, max_length=20)
    open_details: list[ShortText] = Field(default_factory=list, max_length=20)
    ending: str = Field(default="", max_length=4000)


def assemble_contract(payload=None):
    payload = payload or {}
    fields = payload.get("intent_contract")
    try:
        structured = AuthorIntentFields.model_validate(fields or {}).model_dump()
    except ValueError as error:
        raise DomainError("author_contract", "已采用作者契约的结构无法读取，请先核对该设定。", 409) from error
    author_text = payload.get("author_text", payload.get("text", ""))
    return {
        "contract_version": CONTRACT_VERSION,
        "configured": bool(payload),
        "legacy_unstructured": fields is None and bool(payload),
        "author_text": author_text if isinstance(author_text, str) else "",
        "intent_contract": structured,
        "provenance": deepcopy(
            payload.get("provenance", {"origin": "legacy_text" if payload else "unconfigured"})
        ),
        "priority": [
            "作者原话与已确认锁定；发现冲突先指出并等待作者明确改约",
            "本次不冲突的明确要求与固定项",
            "情绪、钩子与尚未锁定的方向倾向",
            "题材频道与写法倾向",
        ],
        "policy": "情绪是读者体验目标，钩子是核心追问，方向包含固定结果和终局；普通细节可在开放空间内补足。未锁定倾向不追加硬检查；全书结果只在相应阶段核查，不要求每章提前兑现。",
    }


def authored_payload(data, source_candidate=None):
    """Source metadata is derived from a real local proposal, never client-provided."""
    fields = data.intent_contract.model_dump() if data.intent_contract else None
    old_fields = (source_candidate or {}).get("payload", {}).get("intent_contract", {})
    old_provenance = (source_candidate or {}).get("payload", {}).get("provenance", {})
    sources = {}
    for key, value in (fields or {}).items():
        sources[key] = {
            "origin": "author_confirmed_proposal"
            if source_candidate and value == old_fields.get(key)
            else "author_edited"
            if source_candidate
            else "author_provided",
            "proposal_origin": deepcopy(old_provenance.get("field_sources", {}).get(key))
            if source_candidate
            else None,
        }
    return {
        "text": data.text,
        "intent_contract": fields,
        "provenance": {
            "origin": "author_confirmed_after_proposal" if source_candidate else "author_form",
            "source_candidate_id": source_candidate["id"] if source_candidate else None,
            "field_sources": sources,
        },
    }


def legacy_contract():
    """No new rules or inferred intent are retrofitted into existing attempts."""
    return {
        "contract_version": "legacy-unconfigured",
        "configured": False,
        "legacy_unconfigured": True,
        "legacy_unstructured": True,
        "author_text": "",
        "intent_contract": AuthorIntentFields().model_dump(),
        "provenance": {"origin": "legacy_task"},
        "priority": [],
        "policy": "旧任务沿用原有原话和设定；不追溯添加新契约约束。",
        "source_artifact_id": None,
        "source_story_revision": None,
    }


async def compile_contract(worker, request, context):
    author_text = request["instruction"]

    def validate(output):
        return [
            f"{key}[{i}]必须逐字引用作者原话；缺少明确要求时留空，不把推导写成锁定项。"
            for key in ("fixed_results", "prohibitions")
            for i, value in enumerate(output[key])
            if value not in author_text
        ]

    fields = await worker.grounded(
        {
            "动作": "整理作者情绪钩子与方向契约",
            "作者原话": author_text,
            "要求": "只整理本次作者原话，不写正文或百科。emotion_target是希望读者经历的感受；core_hook是追问或持续期待；direction是作者要的事件走向；ending是终局方向。fixed_results与prohibitions只能逐字摘取原话中的明确结果或禁止项，没有就留空，不能从频道、题材或常识推断硬锁。其余普通空白可提出简短建议，后端将所有非逐字内容标为AI提案；不默认女频恋爱或男频后宫。open_details说明AI可发挥的小细节，不得改变明确事件和结果。未决定终局可以留空或提出暂定方案，不能伪称作者已指定。",
        },
        AuthorIntentFields,
        context,
        validate,
        4096,
    )
    origins = {}
    for key, value in fields.items():
        values = value if isinstance(value, list) else [value]
        origins[key] = [
            {
                "value": item,
                "origin": "author_quote"
                if item and item in author_text
                else "ai_proposal"
                if item
                else "not_provided",
                "quote": item if item and item in author_text else "",
            }
            for item in values
        ]
    return {
        "author_text": author_text,
        "intent_contract": fields,
        "provenance": {"origin": "ai_compilation_candidate", "field_sources": origins},
        "contract_version": CONTRACT_VERSION,
        "note": "仅为待采用的契约提案；作者原话由后端回填，非逐字字段标为AI提案，不自动修改正文或事实。",
    }
