"""Read-only checkpoint history transferred with a portable manuscript."""

from hashlib import sha256
import json
from time import time
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .db import DomainError, uid
from .models import Input
from .project_portability import Identifier

ARCHIVE_KIND = "portable_checkpoint_archive"
VERSION = "ainovel-portable-checkpoint/1"
MAX_ENTRIES = 5000
MAX_CHAPTERS = 5000
MAX_BODY = 4 * 1024 * 1024
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(ge=0, le=9_007_199_254_740_991, strict=True)]
Flag = Annotated[int, Field(ge=0, le=1, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=200)]


class CheckpointChapter(Input):
    id: Identifier
    position: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    title: Text
    revision: Count
    revision_id: Identifier
    sha256: Hash
    archived: Flag
    stale: Flag


class CheckpointShared(Input):
    project_id: Identifier | None
    settings_id: Identifier | None
    outline_id: Identifier | None


class PortableCheckpoint(Input):
    version: Literal["ainovel-portable-checkpoint/1"]
    id: Identifier
    branch_id: Identifier
    label: Text
    scope: Literal["volume", "book"]
    scope_id: Identifier | None
    purpose: Literal["manual", "milestone", "pre_restore"]
    created: float = Field(ge=0, strict=True, allow_inf_nan=False)
    source_manifest_sha256: Hash
    story_revision: Count
    scoped_chapter_ids: list[Identifier] = Field(max_length=MAX_CHAPTERS)
    chapters: list[CheckpointChapter] = Field(max_length=MAX_CHAPTERS)
    shared: CheckpointShared
    reports: dict[Literal["workflow_inspection", "book_completion"], Identifier]


class PortableCheckpointEntry(Input):
    id: Identifier
    sha256: Hash
    body: str = Field(min_length=1, max_length=MAX_BODY)


class PortableCheckpointHistory(Input):
    entries: list[PortableCheckpointEntry] = Field(min_length=1, max_length=MAX_ENTRIES)


def checkpoint_error(message="恢复点格式、标识或校验值无效。", status=400):
    return DomainError("snapshot_source", message, status)


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate checkpoint key")
    return value


def decode_checkpoint(entry, branch_id, *, status=400):
    try:
        if sha256(entry.body.encode("utf-8")).hexdigest() != entry.sha256:
            raise ValueError("checkpoint digest mismatch")
        checkpoint = PortableCheckpoint.model_validate(
            json.loads(entry.body, object_pairs_hook=_unique_object)
        )
    except ValueError, UnicodeError, RecursionError:
        raise checkpoint_error(status=status) from None
    if checkpoint.id != entry.id or checkpoint.branch_id != branch_id:
        raise checkpoint_error("恢复点与此作品或原始编号不匹配。", status)
    if (
        not checkpoint.label.strip()
        or any(not chapter.title.strip() or not _utf8(chapter.title) for chapter in checkpoint.chapters)
        or not _utf8(checkpoint.label)
    ):
        raise checkpoint_error(status=status)
    return checkpoint


def _utf8(value):
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def validate_checkpoint_history(package, revisions):
    history = package.snapshot_history
    if not history:
        return
    project_ids = {row.id for row in package.project_history.versions} if package.project_history else set()
    settings_ids = (
        {row.id for row in package.settings_history.versions} if package.settings_history else set()
    )
    outlines = {row.id: row for row in package.outline_history.versions} if package.outline_history else {}
    workflow = package.workflow_history
    reports = {
        "workflow_inspection": {row.id for row in workflow.inspections} if workflow else set(),
        "book_completion": {row.id for row in workflow.completions} if workflow else set(),
    }
    seen = set()
    for entry in history.entries:
        state = decode_checkpoint(entry, package.branch.id)
        if state.id in seen or state.story_revision > package.branch.story_revision:
            raise checkpoint_error("恢复点编号重复或对应的故事修订超出稿件包范围。")
        seen.add(state.id)
        if (
            (state.shared.project_id is not None and state.shared.project_id not in project_ids)
            or (state.shared.settings_id is not None and state.shared.settings_id not in settings_ids)
            or (state.shared.outline_id is not None and state.shared.outline_id not in outlines)
            or any(identifier not in reports[kind] for kind, identifier in state.reports.items())
        ):
            raise checkpoint_error("恢复点缺少所引用的正式立项、设定、大纲或历史报告。")
        chapter_ids = [chapter.id for chapter in state.chapters]
        active = [chapter for chapter in state.chapters if not chapter.archived]
        positions = [chapter.position for chapter in active]
        if len(set(chapter_ids)) != len(chapter_ids) or positions != sorted(set(positions)):
            raise checkpoint_error("恢复点的章节编号或章节顺序无效。")
        for chapter in state.chapters:
            revision = revisions.get(chapter.revision_id)
            if not revision or (revision.chapter_id, revision.revision, revision.sha256) != (
                chapter.id,
                chapter.revision,
                chapter.sha256,
            ):
                raise checkpoint_error("恢复点缺少确切的原始章稿修订。")
        scoped = state.scoped_chapter_ids
        active_ids = [chapter.id for chapter in active]
        if state.scope == "book":
            if state.scope_id is not None or scoped != active_ids:
                raise checkpoint_error("整书恢复点与当时有效的章节目录不一致。")
        else:
            outline = outlines.get(state.shared.outline_id)
            volume = (
                next((row for row in outline.plan.volumes if row.id == state.scope_id), None)
                if outline and state.scope_id
                else None
            )
            volume_ids = (
                [chapter.chapter_id for unit in volume.units for chapter in unit.chapters] if volume else []
            )
            if (
                not scoped
                or len(set(scoped)) != len(scoped)
                or scoped != volume_ids
                or not set(scoped) <= set(active_ids)
            ):
                raise checkpoint_error("分卷恢复点与所引用正式卷纲的有效章节不一致。")


def archived_checkpoints(tx, branch_id):
    for row in tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid DESC",
        (branch_id, ARCHIVE_KIND),
    ):
        try:
            yield PortableCheckpointEntry.model_validate_json(row["payload"])
        except ValidationError:
            raise checkpoint_error(status=409) from None


def import_checkpoints(tx, branch, history):
    tx.executemany(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,'{}','accepted',?)",
        [
            (uid(), branch.novel_id, branch.id, ARCHIVE_KIND, entry.model_dump_json(), time())
            for entry in history.entries
        ],
    )


def read_archived_checkpoint(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(payload,'$.id')=?",
        (branch_id, ARCHIVE_KIND, identifier),
    ).fetchone()
    if not row:
        raise DomainError("not_found", "找不到指定的归档恢复点。", 404)
    try:
        entry = PortableCheckpointEntry.model_validate_json(row["payload"])
    except ValidationError:
        raise checkpoint_error(status=409) from None
    return decode_checkpoint(entry, branch_id, status=409)
