from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

from .author_contract import AuthorIntentFields


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NovelInput(Input):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=30_000)


class ChapterInput(Input):
    title: str = Field(min_length=1, max_length=200)


class SaveInput(Input):
    base_revision: int = Field(ge=0)
    content: str = Field(max_length=250_000)


class RestoreInput(Input):
    base_revision: int = Field(ge=0)
    revision_id: str


class GenreProfileBasis(Input):
    source_artifact_id: str | None = Field(min_length=1, max_length=128)
    catalog_version: str = Field(min_length=1, max_length=100)


class AuthorContractBasis(Input):
    source_artifact_id: str | None = Field(min_length=1, max_length=128)
    contract_version: str = Field(min_length=1, max_length=100)


class ModuleInput(Input):
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128)
    action: Literal[
        "writing.advise",
        "writing.rewrite_selection",
        "writing.expand_plot",
        "review.chapter",
        "memory.extract",
        "outline.plan",
        "character.generate",
        "world.generate",
        "ideation.explore",
        "intent.compile",
        "revision.targeted",
        "reference.analyze",
        "reference.plan",
        "reference.draft",
        "reference.check",
    ]
    novel_id: str
    branch_id: str
    chapter_id: str | None = None
    base_revision: int | None = None
    instruction: str = Field(min_length=1, max_length=20_000)
    selection_start: int | None = Field(default=None, ge=0)
    selection_end: int | None = Field(default=None, ge=0)
    selection_text: str | None = Field(default=None, max_length=250_000)
    fixed: list[str] = Field(default_factory=list, max_length=30)
    writing_profile: str = Field(
        default="第三人称限定视角，现代白话，动作有因果，避免重复解释。", max_length=5000
    )
    reference_ids: list[str] = Field(default_factory=list, max_length=20)
    account: str
    max_output_tokens: int = Field(default=8192, ge=64, le=16_384)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    genre_profile_basis: GenreProfileBasis | None = None
    author_contract_basis: AuthorContractBasis | None = None
    source_review_id: str | None = Field(default=None, min_length=1, max_length=128)
    finding_indices: list[Annotated[int, Field(ge=0)]] = Field(default_factory=list, max_length=20)

    reference_session_id: str | None = None
    reference_revision: int | None = Field(default=None, ge=0)


class CandidateEdit(Input):
    text: str = Field(max_length=250_000)
    candidate_revision: int = Field(default=0, ge=0)


class CandidateVersionInput(Input):
    candidate_revision: int = Field(default=0, ge=0)


class AcceptInput(Input):
    idempotency_key: str = Field(min_length=8, max_length=128)
    candidate_revision: int = Field(default=0, ge=0)


class Fact(Input):
    subject: str = Field(min_length=1, max_length=200)
    kind: Literal["event", "knowledge", "capability", "resource", "relationship", "promise", "world"]
    statement: str = Field(min_length=1, max_length=2000)
    truth: Literal["fact", "belief", "uncertain", "plan"] = "fact"
    story_time: str = Field(default="未知", max_length=200)
    evidence: str = Field(
        min_length=1,
        max_length=2000,
        description="有可引用原文列表时只填source_id编号，由后端回填固定版本原文；否则填连续逐字原文。",
    )


class FactInput(Fact):
    chapter_id: str
    source_revision: int


class Plot(Input):
    number: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=200)
    plot: str = Field(min_length=1, max_length=5000)
    fixed: list[str] = Field(default_factory=list, max_length=20)


class WorkflowInput(Input):
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128)
    novel_id: str
    source_branch_id: str | None = None
    mode: Literal["assisted", "automatic"]
    brief: str = Field(min_length=1, max_length=30_000)
    fixed: list[str] = Field(default_factory=list, max_length=30)
    plots: list[Plot] = Field(default_factory=list, max_length=100)
    chapters: int = Field(default=36, ge=1, le=100)
    min_chars: int = Field(default=2500, ge=200, le=10_000)
    max_chars: int = Field(default=3500, ge=200, le=15_000)
    account: str
    adoption: Literal["manual", "automatic"] = "manual"
    writing_profile: str = Field(
        default="第三人称限定视角，克制白话，人物选择有依据，遵守能力和信息边界。", max_length=5000
    )
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    genre_profile_basis: GenreProfileBasis | None = None
    author_contract_basis: AuthorContractBasis | None = None


class FeedbackInput(Input):
    chapter_id: str
    category: Literal["事实错误", "理解问题", "审美偏好", "待判断"]
    text: str = Field(min_length=1, max_length=10_000)
    source_quote: str = Field(default="", max_length=2000)


class AssessmentInput(Input):
    branch_id: str
    category: Literal["engineering", "creative", "benefit"]
    result: Literal["passed", "failed", "unverified"]
    note: str = Field(min_length=1, max_length=15_000)
    minutes: dict[str, float] = Field(default_factory=dict)


class ReviewCheck(Input):
    condition: str
    result: Literal["pass", "fail", "unknown"]
    evidence: str = Field(
        description="有可引用原文时优先只填source_id编号，由后端回填原文；否则仅填连续逐字原文。未知时为空。"
    )


class Finding(Input):
    severity: Literal["blocker", "suggestion"]
    chapter: int = Field(ge=1, le=100)
    quote: str = Field(
        description="优先填该章节可引用原文的source_id编号；后端回填原文。不能概括或改写引文。"
    )
    reason: str
    repair: str


class Review(Input):
    checks: list[ReviewCheck]
    findings: list[Finding]
    summary: str


class MemoryFact(Fact):
    statement: str = Field(min_length=1, max_length=160)


class Memory(Input):
    facts: list[MemoryFact] = Field(max_length=6)
    summary: str = Field(min_length=1, max_length=400)


class Plan(Input):
    chapters: list[Plot]
    ending: str = Field(min_length=1, max_length=5000)


class Completion(Input):
    closed: bool
    promises: list[ReviewCheck]
    findings: list[Finding]
    summary: str


class TextEdit(Input):
    original: str = Field(
        min_length=1,
        max_length=15_000,
        description="优先填可替换原文的source_id，由后端回填原稿；否则填唯一出现的逐字原文。",
    )
    replacement: str = Field(max_length=20_000)


class RevisionPatch(Input):
    edits: list[TextEdit] = Field(min_length=1, max_length=6)
    reason: str


class DefinitionInput(Input):
    kind: Literal["intent", "reader", "world", "characters", "outline"]
    text: str = Field(min_length=1, max_length=20_000)
    audience: Literal["不限", "男频", "女频"] = "不限"
    genres: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=10
    )
    mechanisms: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=10
    )
    author_exceptions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=30
    )
    intent_contract: AuthorIntentFields | None = None
    intent_source_candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    base_story_revision: int = Field(ge=0)
