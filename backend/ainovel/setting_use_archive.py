"""Portable author proof revisions with explicit adopted heads and exact manuscript sources."""

from pydantic import ValidationError

from .db import DomainError, dumps
from .manuscript_exchange import source_revision
from .setting_use import KIND
from .setting_use_portability import (
    SettingUseArchiveEntry,
    SettingUseHistory,
    SettingUseVersion,
    validate_head,
    validate_version_sources,
    version_slot,
)


def export_setting_use_history(tx, branch_id):
    versions = []
    heads = {}
    try:
        for row in tx.execute(
            """
            SELECT id,chapter_id,payload,created,json_extract(basis,'$.head') AS head,
                   json_extract(basis,'$.source_device') AS source_device
              FROM artifacts
             WHERE branch_id=? AND kind=? AND status='accepted'
             ORDER BY rowid
            """,
            (branch_id, KIND),
        ):
            version = SettingUseVersion.model_validate_json(row["payload"])
            if version.id != row["id"] or version.evidence.chapter_id != row["chapter_id"]:
                raise DomainError("manuscript_setting_use", "本机设定使用证明的版本或章节标识不一致。", 409)
            versions.append(
                SettingUseArchiveEntry(
                    version=version, created=row["created"], source_device=row["source_device"]
                )
            )
            if row["head"]:
                slot = version_slot(branch_id, version)
                if slot in heads:
                    raise DomainError("manuscript_setting_use", "同一章和设定条目存在多个采用头。", 409)
                heads[slot] = version.id
        return SettingUseHistory(adopted_heads=heads, versions=versions) if versions else None
    except ValidationError as error:
        raise DomainError("manuscript_setting_use", "本机设定使用证明的父链或档案格式无效。", 409) from error


def validate_setting_use_history(history, branch_id, chapters, revisions, settings_history, outline_history):
    if not history:
        return
    versions = {entry.version.id: entry.version for entry in history.versions}
    settings = {version.id: version for version in settings_history.versions} if settings_history else {}
    outlines = {version.id: version for version in outline_history.versions} if outline_history else {}
    chapter_ids = {chapter.id for chapter in chapters}
    try:
        for version in versions.values():
            if version.evidence.chapter_id not in chapter_ids:
                raise DomainError("setting_use_integrity", "证明的章节目录不在同一工作版本。", 409)
            validate_version_sources(
                branch_id,
                version,
                use_lookup=versions.get,
                revision_lookup=revisions.get,
                settings_lookup=settings.get,
                outline_lookup=outlines.get,
            )
        for slot, identifier in history.adopted_heads.items():
            validate_head(branch_id, slot, identifier, versions.get)
    except DomainError as error:
        raise DomainError(
            "manuscript_setting_use", f"稿件包设定使用证明未通过校验。{error.message}", 409
        ) from error


def import_setting_use_history(tx, branch_id, history):
    novel_id = tx.execute("SELECT novel_id FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
    adopted = set(history.adopted_heads.values())
    for entry in history.versions:
        version = entry.version
        if tx.execute("SELECT 1 FROM artifacts WHERE id=?", (version.id,)).fetchone():
            raise DomainError("manuscript_conflict", "设定使用证明编号已在本机使用，原有作品未改动。", 409)
        source = source_revision(tx, branch_id, version.evidence.revision_id)
        basis = {"head": version.id in adopted}
        if entry.source_device:
            basis["source_device"] = entry.source_device
        tx.execute(
            "INSERT INTO artifacts (id,novel_id,branch_id,chapter_id,kind,base_revision,payload,basis,status,created) "
            "VALUES (?,?,?,?,?,?,?,?,'accepted',?)",
            (
                version.id,
                novel_id,
                branch_id,
                version.evidence.chapter_id,
                KIND,
                source.revision,
                version.model_dump_json(),
                dumps(basis),
                entry.created,
            ),
        )
