"""Saved M2 card, exact source address, and opt-in manuscript archive."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .models import Input
from .project_portability import Identifier
from .settings_portability import DeviceName

HashId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Assessment = Literal["ready", "incomplete", "archived"]
EvidenceState = Literal["local", "included_excerpt", "metadata_only", "no_evidence"]
CardField = Literal["observation", "interpretation", "technique", "conditions", "exceptions", "evidence"]
CARD_FIELDS = ("observation", "interpretation", "technique", "conditions", "exceptions")
CardText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Revision = Annotated[int, Field(ge=0, le=9_007_199_254_740_991, strict=True)]


def missing_card_fields(card):
    return [key for key in (*CARD_FIELDS, "evidence") if not card.get(key, "").strip()]


class CardCitation(Input):
    book_id: Identifier
    unit_id: Identifier
    file_sha256: HashId
    title: str = Field(min_length=1, max_length=1000)
    selection_start: Revision
    selection_end: Revision
    selection_sha256: HashId
    evidence_sha256: HashId
    evidence_start: Revision | None
    evidence_end: Revision | None

    @model_validator(mode="after")
    def check_ranges(self):
        if not 0 <= self.selection_start < self.selection_end:
            raise ValueError("选段范围必须有效。")
        if (self.evidence_start is None) != (self.evidence_end is None):
            raise ValueError("证据的两个偏移须同时存在。")
        if self.evidence_start is not None and not (
            self.selection_start <= self.evidence_start < self.evidence_end <= self.selection_end
        ):
            raise ValueError("证据须位于保存的选段内。")
        return self


class FormalCard(Input):
    observation: CardText
    interpretation: CardText
    technique: CardText
    conditions: CardText
    exceptions: CardText

    @model_validator(mode="after")
    def check_text(self):
        if any(not line.strip() for line in self.model_dump().values()):
            raise ValueError("手法卡内容不可仅含空白。")
        return self


class ReferenceCardVersion(Input):
    id: HashId
    parent_id: HashId | None
    session_id: Identifier
    card_id: Identifier
    revision: Revision
    assessment: Assessment
    card: FormalCard | None = None
    citation: CardCitation | None = None
    goal: str | None = Field(default=None, max_length=3000)
    missing_fields: list[CardField] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def check_assessment(self):
        if self.assessment == "ready":
            if not self.card or not self.citation or not (self.goal or "").strip() or self.missing_fields:
                raise ValueError("完整手法卡须有内容、目标和引用地址。")
        elif self.card or self.citation or self.goal is not None:
            raise ValueError("未完成或已归档的手法卡仅交换状态。")
        if self.assessment == "incomplete" and (
            not self.missing_fields or len(set(self.missing_fields)) != len(self.missing_fields)
        ):
            raise ValueError("未完成手法卡须列明缺项。")
        if self.assessment == "archived" and self.missing_fields:
            raise ValueError("已归档的手法卡不携带编辑状态。")
        return self


class ReferenceCardHead(Input):
    session_id: Identifier
    card_id: Identifier
    revision_id: HashId


class ReferenceCardVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["reference-card-version"]
    branch_id: Identifier
    version: ReferenceCardVersion


class ReferenceCardHeadObject(ReferenceCardHead):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["reference-card-head"]
    branch_id: Identifier


class ReferenceHistory(Input):
    adopted_heads: list[ReferenceCardHead] = Field(max_length=5000)
    versions: list[ReferenceCardVersion] = Field(min_length=1, max_length=5000)
    source_devices: dict[HashId, DeviceName] = Field(default_factory=dict, max_length=5000)
    excerpts: dict[HashId, Annotated[str, StringConstraints(min_length=1, max_length=2000)]] = Field(
        default_factory=dict,
        max_length=5000,
        exclude_if=lambda value: not value,
    )


class ReferenceCardHistoryEntry(Input):
    version: ReferenceCardVersion
    adopted: bool
    source_state: EvidenceState
    source_device: DeviceName | None = None


class ReferenceCardEvidence(Input):
    version_id: HashId
    source_state: EvidenceState
    excerpt: str | None = None
