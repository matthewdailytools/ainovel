"""Immutable cloud versions for chapter facts and explicit length targets."""

from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .db import DomainError
from .models import Input
from .portable_restore_sources import CheckpointFact, CheckpointLengthTarget, _fact_quote
from .project_portability import Identifier
from .setting_use_portability import FORMAT, Digest, canonical_digest
from .story_portability import MAX_VERSIONS

FACT_VERSION = "fact-set-version"
FACT_HEAD = "fact-set-head"
LENGTH_VERSION = "length-target-version"
LENGTH_HEAD = "length-target-head"
INTEGRITY = "sync_restore_state_integrity"
DEPENDENCY = "sync_restore_state_dependency"


class FactSetVersion(Input):
    id: Digest
    parent_id: Digest | None
    chapter_id: Identifier
    chapter_revision_id: Identifier
    facts: list[CheckpointFact] = Field(max_length=MAX_VERSIONS)


class LengthTargetVersion(CheckpointLengthTarget):
    id: Digest
    parent_id: Digest | None
    outline_version_id: Identifier | None

    @model_validator(mode="after")
    def check_target_basis(self):
        if (self.scope == "volume") != (self.outline_version_id is not None):
            raise ValueError("卷级篇幅目标须指定冻结大纲版本。")
        if self.targets is not None and all(value is None for value in self.targets.model_dump().values()):
            raise ValueError("数值目标至少须填写一项，清空请使用空值。")
        return self


class FactSetVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["fact-set-version"]
    branch_id: Identifier
    version: FactSetVersion


class LengthTargetVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["length-target-version"]
    branch_id: Identifier
    version: LengthTargetVersion


class FactSetHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["fact-set-head"]
    branch_id: Identifier
    chapter_id: Identifier
    revision_id: Digest


class LengthTargetHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["length-target-head"]
    branch_id: Identifier
    scope: Literal["chapter", "volume", "book"]
    scope_id: Identifier | None
    revision_id: Digest

    @model_validator(mode="after")
    def check_scope(self):
        if (self.scope == "book") != (self.scope_id is None):
            raise ValueError("篇幅目标采用范围须对应作品、卷或章。")
        return self


VERSION_OBJECTS = (FactSetVersionObject, LengthTargetVersionObject)
_OBJECTS = {
    FACT_VERSION: FactSetVersionObject,
    FACT_HEAD: FactSetHeadObject,
    LENGTH_VERSION: LengthTargetVersionObject,
    LENGTH_HEAD: LengthTargetHeadObject,
}
RESTORE_STATE_PREFIXES = tuple(f"{kind}:" for kind in _OBJECTS)


def version_id(branch_id, version):
    return canonical_digest([branch_id, version])


def head_address(branch_id, value):
    if isinstance(value, (FactSetVersion, FactSetHeadObject)):
        return f"{FACT_HEAD}:{canonical_digest([branch_id, value.chapter_id])}"
    return f"{LENGTH_HEAD}:{canonical_digest([branch_id, value.scope, value.scope_id])}"


def object_id(value):
    if isinstance(value, VERSION_OBJECTS):
        return f"{value.kind}:{value.version.id}"
    return head_address(value.branch_id, value)


def version_object(branch_id, version):
    if isinstance(version, FactSetVersion):
        return FactSetVersionObject(format=FORMAT, kind=FACT_VERSION, branch_id=branch_id, version=version)
    return LengthTargetVersionObject(format=FORMAT, kind=LENGTH_VERSION, branch_id=branch_id, version=version)


def head_object(branch_id, version):
    if isinstance(version, FactSetVersion):
        return FactSetHeadObject(
            format=FORMAT,
            kind=FACT_HEAD,
            branch_id=branch_id,
            chapter_id=version.chapter_id,
            revision_id=version.id,
        )
    return LengthTargetHeadObject(
        format=FORMAT,
        kind=LENGTH_HEAD,
        branch_id=branch_id,
        scope=version.scope,
        scope_id=version.scope_id,
        revision_id=version.id,
    )


def parse_restore_state_object(branch_id, identifier, payload, tombstone):
    schema = _OBJECTS.get(identifier.partition(":")[0])
    if not schema or tombstone:
        raise DomainError(INTEGRITY, "事实和篇幅目标须以正式版本记录变更。", 409)
    try:
        value = schema.model_validate(payload)
        address = object_id(value)
    except (ValidationError, UnicodeError) as error:
        raise DomainError(INTEGRITY, "事实或篇幅目标的同步格式无效。", 409) from error
    if value.branch_id != branch_id or address != identifier:
        raise DomainError(INTEGRITY, "事实或篇幅目标的归属和编号无法核对。", 409)
    if isinstance(value, VERSION_OBJECTS):
        version = value.version
        if version.id == version.parent_id or version.id != version_id(
            branch_id, version.model_dump(exclude={"id"})
        ):
            raise DomainError(INTEGRITY, "事实或篇幅目标的版本摘要或父修订无效。", 409)
    return value


def validate_version_sources(
    branch_id, version, *, revision_lookup, outline_lookup, parent_lookup, chapter_lookup=None
):
    if version.parent_id:
        parent = parent_lookup(version.parent_id)
        if parent is None:
            raise DomainError(DEPENDENCY, "请先同步此事实或篇幅目标的父修订。", 409)
        if type(parent) is not type(version) or head_address(branch_id, parent) != head_address(
            branch_id, version
        ):
            raise DomainError(INTEGRITY, "事实或篇幅目标的父修订属于其他范围。", 409)
    if isinstance(version, FactSetVersion):
        _validate_fact_set(version, revision_lookup)
    elif version.scope == "volume":
        outline = outline_lookup(version.outline_version_id)
        if outline is None:
            raise DomainError(DEPENDENCY, "请先同步卷级篇幅目标所用大纲。", 409)
        if not any(volume.id == version.scope_id for volume in outline.plan.volumes):
            raise DomainError(INTEGRITY, "篇幅目标所在卷不属于冻结大纲。", 409)
    elif version.scope == "chapter" and chapter_lookup and not chapter_lookup(version.scope_id):
        raise DomainError(DEPENDENCY, "请先同步篇幅目标所在的章节目录。", 409)


def _validate_fact_set(version, revision_lookup):
    revision = revision_lookup(version.chapter_revision_id)
    if revision is None:
        raise DomainError(DEPENDENCY, "请先同步事实引用的保存章稿。", 409)
    if revision.chapter_id != version.chapter_id or revision.id != version.chapter_revision_id:
        raise DomainError(INTEGRITY, "正式事实须引用同一章节的确切保存修订。", 409)
    for fact in version.facts:
        if (fact.evidence.chapter_id, fact.evidence.revision_id) != (version.chapter_id, revision.id):
            raise DomainError(INTEGRITY, "事实原文与集合的章节或修订不一致。", 409)
        try:
            _fact_quote(fact, revision)
        except (DomainError, ValidationError) as error:
            raise DomainError(INTEGRITY, "事实原文选区或摘要无效。", 409) from error


def validate_head(branch_id, head, version_lookup):
    version = version_lookup(head.revision_id)
    if version is None:
        raise DomainError(DEPENDENCY, "请先同步要采用的事实或篇幅目标修订。", 409)
    if head_address(branch_id, head) != head_address(branch_id, version):
        raise DomainError(INTEGRITY, "采用头须引用同一范围的正式修订。", 409)
    return version
