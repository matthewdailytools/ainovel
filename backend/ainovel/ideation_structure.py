"""A complete, editable story spine; structural checks are not literary approval."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator


VERSION = "1.0.0"


def substantive(value: str) -> str:
    if value.casefold() in {"待定", "待补充", "未定", "略", "tbd", "...", "……"}:
        raise ValueError("结构内容不能只用待定或省略占位，请给出一个可修改的具体方案。")
    return value


Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300), AfterValidator(substantive)
]
Detail = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=700), AfterValidator(substantive)
]
BeatNumber = Annotated[int, Field(strict=True, ge=1, le=9)]
STAGES = ("开端", "发展", "转折", "高潮", "结局")


class StructurePart(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoryBeat(StructurePart):
    number: BeatNumber
    stage: Literal["开端", "发展", "转折", "高潮", "结局"]
    event: Short = Field(description="本节点具体发生的事，不能只写逐渐成长、揭开真相等概述。")
    caused_by: list[BeatNumber] = Field(
        max_length=4, description="承接的前序节点编号；开端为空，其余包含紧邻前一节点。"
    )
    cause: Short = Field(description="前序结果为什么导致本次事件；开端写打破原有生活的起因。")
    choice: Short = Field(description="主角在本次处境下作出的选择或行动。")
    result: Short = Field(description="行动带来的实际结果、代价或处境变化，并为后续事件提供依据。")


class StoryPayoff(StructurePart):
    promise: Short = Field(description="与主线有关的关键追问、承诺或伏笔。")
    planted_at: BeatNumber
    resolved_at: BeatNumber
    resolution: Short = Field(description="如何回收，不能只写以后揭晓或待定。")


class CompleteIdea(StructurePart):
    premise: Short
    reader_promise: Short = Field(description="读者为什么继续读，本书的具体看点和主要阅读体验。")
    protagonist: Detail = Field(description="主角是谁、想达成什么、为什么必须行动，以及其局限和变化。")
    world: Detail = Field(description="支撑主线所需的背景、重要角色或关系，以及不能随意突破的规则。")
    main_conflict: Detail = Field(description="阻止目标实现的具体力量、双方诉求及失败后果。")
    beats: list[StoryBeat] = Field(min_length=5, max_length=9)
    payoffs: list[StoryPayoff] = Field(min_length=1, max_length=4)
    ending: Detail = Field(
        description="主冲突的具体处理、人物付出的代价和最终处境。开放结局也须回答本书主线。"
    )
    continuation: Detail = Field(
        description="长篇说明阶段如何推进与产生新任务；短故事说明如何集中收束，不强行续写。"
    )
    proposed_details: Detail = Field(
        description="哪些补充是可修改的AI方案；作者尚未确定的细节与叙述顺序可保持开放。"
    )

    @model_validator(mode="after")
    def coherent_structure(self):
        stages = [beat.stage for beat in self.beats]
        if stages[0] != "开端" or stages[-1] != "结局" or not set(STAGES) <= set(stages):
            raise ValueError("故事必须覆盖开端、发展、转折、高潮、结局，并由开端起、结局收束。")
        if not stages.index("发展") < stages.index("转折") < stages.index("高潮"):
            raise ValueError("主线须先发展、形成关键转折，再进入高潮。")
        if stages.count("开端") != 1 or stages.count("结局") != 1:
            raise ValueError("主线以一个开端引入、一个结局收束，阶段变化放在中间节点。")
        for index, beat in enumerate(self.beats, 1):
            if beat.number != index:
                raise ValueError("节点编号须从1连续递增。")
            if index == 1 and beat.caused_by:
                raise ValueError("开端不能引用尚未发生的节点。")
            if index > 1 and (
                index - 1 not in beat.caused_by
                or len(set(beat.caused_by)) != len(beat.caused_by)
                or any(source >= index for source in beat.caused_by)
            ):
                raise ValueError("每个后续节点须承接前一节点的结果，不能断链、自引或引用未来。")
        for payoff in self.payoffs:
            if not 1 <= payoff.planted_at < payoff.resolved_at <= len(self.beats):
                raise ValueError("伏笔须先铺设后回收，且引用实际存在的节点。")
        if len(render_idea(self)) > 6000:
            raise ValueError("完整方案超过6000字，请精简说明，保留全部结构和因果关系。")
        return self


def render_idea(idea: CompleteIdea) -> str:
    sections = [
        f"一句话故事：{idea.premise}",
        f"读者看点：{idea.reader_promise}",
        f"人物目标与动机：{idea.protagonist}",
        f"背景与关键关系：{idea.world}",
        f"核心矛盾与风险：{idea.main_conflict}",
        "故事主线（按因果展开，不限定正文叙述顺序）：",
    ]
    for beat in idea.beats:
        sources = "、".join(str(number) for number in beat.caused_by)
        sections.append(
            f"{beat.number}. {beat.stage}：{beat.event}\n"
            f"起因{'（承接节点' + sources + '）' if sources else ''}：{beat.cause}\n"
            f"主角选择：{beat.choice}\n结果与代价：{beat.result}"
        )
    sections.append("主要伏笔与承诺回收：")
    for payoff in idea.payoffs:
        sections.append(
            f"· {payoff.promise}（节点{payoff.planted_at}铺设，节点{payoff.resolved_at}回收）：{payoff.resolution}"
        )
    sections.extend(
        [
            f"结局与人物变化：{idea.ending}",
            f"持续推进或收束：{idea.continuation}",
            f"可调整的方案细节：{idea.proposed_details}",
        ]
    )
    return "\n\n".join(sections)


async def generate_structure(worker, request, context):
    output = await worker.call(
        {
            "action": "ideation.structure",
            "动作": "构思结构完整的小说",
            "构思结构版本": VERSION,
            "作者想法": request["instruction"],
            "固定条件": request["fixed"],
            "写作偏好": request["writing_profile"],
            "要求": (
                "给出一个结构完整、可修改的原创小说构思，约1000至1800字，不写正文。"
                "围绕同一主角目标与主冲突，以5至9个主线节点覆盖开端、发展、关键转折、高潮、结局。"
                "每个节点说明具体事件、起因、主角选择及结果代价，后一节点必须承接前一节点的结果。"
                "高潮由前文行动、关系或伏笔支撑，不能凭空新增关键能力解决问题；主线的重要承诺须回收。"
                "给出一个明确的建议结局，交代主冲突和人物变化，不能只说待定、揭开真相或以后续写。"
                "作者指定开放结局时仍说明本书主冲突如何处理，后续人生、远景或非核心细节可以开放。"
                "尊重已采用设定、作者禁止项、明确事件及关系；缺失信息提出一个自洽方案并标明为AI建议，"
                "不把建议变成作者锁定或已发生事实。长篇说明阶段成果如何产生新任务，短故事集中收束。"
                "因果节点是规划骨架，不强制三幕式、固定章节数、战斗、系统、恋爱或最终叙述顺序。"
            ),
        },
        schema=CompleteIdea,
        context=context,
        max_output=request["max_output_tokens"],
        structure_repairs=1,
    )
    idea = CompleteIdea.model_validate(output)
    return {
        "text": render_idea(idea),
        "idea_structure": idea.model_dump(),
        "idea_structure_version": VERSION,
        "idea_structure_revision": 0,
        "structure_note": "已检查结构覆盖与因果引用；情节可信度与文学质量仍由作者核对。编辑后原结构检查仅对应生成稿。",
    }
