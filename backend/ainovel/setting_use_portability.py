"""Official setting-use versions and their portable adoption addresses."""

from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .db import DomainError
from .models import Input
from .project_portability import Identifier, check_version_graph
from .settings_portability import DeviceName, SettingsEvidence

FORMAT = "ainovel-manuscript-sync/1"
VERSION_PREFIX = "setting-use-version:"
HEAD_PREFIX = "setting-use:"
MAX_VERSIONS = 20_000
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SettingUseVersion(Input):
    id: Digest
    parent_id: Digest | None
    outline_version_id: Identifier
    settings_version_id: Identifier
    item_id: Identifier
    evidence: SettingsEvidence
    chapter_sha256: Digest
    action: Literal["confirmed", "withdrawn"]
    actor: Literal["author"]


class SettingUseVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["setting-use-version"]
    branch_id: Identifier
    version: SettingUseVersion


class SettingUseHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["setting-use-head"]
    branch_id: Identifier
    slot_id: Digest
    revision_id: Digest


class SettingUseArchiveEntry(Input):
    version: SettingUseVersion
    created: float = Field(ge=0, allow_inf_nan=False)
    source_device: DeviceName | None = Field(default=None, exclude_if=lambda value: value is None)


class SettingUseHistory(Input):
    adopted_heads: dict[Digest, Digest] = Field(max_length=MAX_VERSIONS)
    versions: list[SettingUseArchiveEntry] = Field(min_length=1, max_length=MAX_VERSIONS)
    native_bindings: dict[Identifier, Identifier] = Field(
        default_factory=dict, max_length=MAX_VERSIONS, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def check_links(self):
        versions = check_version_graph(None, [entry.version for entry in self.versions], "设定使用")
        if not set(self.adopted_heads.values()) <= versions.keys():
            raise ValueError("稿件包缺少采用的设定使用证据。")
        origins = {version.evidence.revision_id for version in versions.values()}
        if not self.native_bindings.keys() <= origins:
            raise ValueError("稿件包的章节采用关系须指向设定使用历史所用的原始章稿。")
        return self


def canonical_digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(data.encode("utf-8")).hexdigest()


def version_id(branch_id, version):
    return canonical_digest([branch_id, version])


def slot_id(branch_id, chapter_id, settings_version_id, item_id):
    return canonical_digest([branch_id, chapter_id, settings_version_id, item_id])


def version_slot(branch_id, version):
    return slot_id(branch_id, version.evidence.chapter_id, version.settings_version_id, version.item_id)


def version_object(branch_id, version):
    return SettingUseVersionObject(
        format=FORMAT, kind="setting-use-version", branch_id=branch_id, version=version
    )


def head_object(branch_id, slot, revision_id):
    return SettingUseHeadObject(
        format=FORMAT, kind="setting-use-head", branch_id=branch_id, slot_id=slot, revision_id=revision_id
    )


def validate_version(branch_id, version):
    if (
        version.id != version_id(branch_id, version.model_dump(exclude={"id"}))
        or version.parent_id == version.id
        or (version.action == "withdrawn" and not version.parent_id)
    ):
        raise DomainError("setting_use_integrity", "设定使用证明编号或撤回来源无法核对。", 409)


def parse_setting_use_object(branch_id, object_id, payload, tombstone):
    schema = SettingUseVersionObject if object_id.startswith(VERSION_PREFIX) else SettingUseHeadObject
    try:
        value = schema.model_validate(payload)
    except ValidationError as error:
        raise DomainError("setting_use_integrity", "设定使用证明未通过正式格式校验。", 409) from error
    version = isinstance(value, SettingUseVersionObject)
    expected = VERSION_PREFIX + value.version.id if version else HEAD_PREFIX + value.slot_id
    if tombstone or value.branch_id != branch_id or object_id != expected:
        raise DomainError("setting_use_integrity", "设定使用证明须属于同一工作版本，撤回须新增修订。", 409)
    if version:
        validate_version(branch_id, value.version)
    return value


def validate_head(branch_id, slot, revision_id, use_lookup):
    version = use_lookup(revision_id)
    if not version:
        raise DomainError("setting_use_dependency", "采用的设定使用证明尚未到达。", 409)
    validate_version(branch_id, version)
    if version.id != revision_id or slot != version_slot(branch_id, version):
        raise DomainError("setting_use_integrity", "设定使用证明的采用头不属于对应章稿和设定条目。", 409)
    return version


def validate_version_sources(
    branch_id, version, *, use_lookup, revision_lookup, settings_lookup, outline_lookup
):
    from .setting_review import outline_setting_items
    from .settings_sync import _evidence_quote

    validate_version(branch_id, version)
    if version.parent_id:
        parent = validate_head(branch_id, version_slot(branch_id, version), version.parent_id, use_lookup)
        if version.action == "withdrawn" and (
            parent.action != "confirmed"
            or parent.model_dump(exclude={"id", "parent_id", "action"})
            != version.model_dump(exclude={"id", "parent_id", "action"})
        ):
            raise DomainError("setting_use_integrity", "撤回只能保留被撤回修订的原始依据。", 409)
    settings = settings_lookup(version.settings_version_id)
    outline = outline_lookup(version.outline_version_id)
    revision = revision_lookup(version.evidence.revision_id)
    if not settings or not outline or not revision:
        raise DomainError("setting_use_dependency", "证明所用的正式设定、大纲或章稿尚未到达。", 409)
    if (
        settings.id != version.settings_version_id
        or outline.id != version.outline_version_id
        or outline.settings_version_id != settings.id
        or revision.id != version.evidence.revision_id
        or revision.chapter_id != version.evidence.chapter_id
        or revision.sha256 != version.chapter_sha256
        or sha256(revision.content.encode("utf-8")).hexdigest() != revision.sha256
    ):
        raise DomainError(
            "setting_use_integrity", "设定使用证明与稳定版本、所属章节或全文校验值不匹配。", 409
        )
    eligible = outline_setting_items(
        [category.model_dump() for category in settings.categories],
        {item_id for item_id, origin in settings.origins.items() if origin.evidence},
    )
    if not any(
        item["id"] == version.item_id and item.get("category_id") not in (None, "world") for item in eligible
    ):
        raise DomainError("setting_use_integrity", "证明所用条目须为该设定版本正式采用的非世界设定。", 409)
    if not any(
        chapter.chapter_id == version.evidence.chapter_id and version.item_id in chapter.setting_ids
        for volume in outline.plan.volumes
        for unit in volume.units
        for chapter in unit.chapters
    ):
        raise DomainError("setting_use_integrity", "所用设定未挂载到该大纲的对应章节。", 409)
    try:
        quote = _evidence_quote(version.evidence, revision)
    except DomainError as error:
        raise DomainError("setting_use_integrity", "证明的正文选区或选句校验值不一致。", 409) from error
    if not quote.strip():
        raise DomainError("setting_use_integrity", "证明缺少有效正文选区。", 409)
    return quote
