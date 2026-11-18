"""Desktop sync projection for the author-confirmed setting-use history."""

from time import time

from . import outline_sync, settings_sync
from .db import DomainError, dumps
from .manuscript_exchange import source_revision
from .setting_use import KIND
from .setting_use_archive import export_setting_use_history
from .setting_use_portability import (
    SettingUseVersion,
    head_object,
    validate_head,
    validate_version_sources,
    version_object,
    version_slot,
)


def read_version(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT payload FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
        (identifier, branch_id, KIND),
    ).fetchone()
    return SettingUseVersion.model_validate_json(row["payload"]) if row else None


def read_head(tx, branch_id, slot):
    for row in tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(basis,'$.head')=1",
        (branch_id, KIND),
    ):
        version = SettingUseVersion.model_validate_json(row["payload"])
        if version_slot(branch_id, version) == slot:
            return head_object(branch_id, slot, version.id)
    return None


def draft_objects(tx, branch_id):
    history = export_setting_use_history(tx, branch_id)
    if not history:
        return
    versions = {entry.version.id: entry.version for entry in history.versions}
    emitted = set()
    for version in versions.values():
        # Archives may not list versions in parent-first order; the graph was validated above.
        path = []
        cursor = version
        while cursor and cursor.id not in emitted:
            path.append(cursor)
            cursor = versions.get(cursor.parent_id)
        for ancestor in reversed(path):
            emitted.add(ancestor.id)
            yield version_object(branch_id, ancestor)
    for slot, identifier in sorted(history.adopted_heads.items()):
        yield head_object(branch_id, slot, identifier)


def observe_version(tx, branch_id, version, receipt):
    validate_version_sources(
        branch_id,
        version,
        use_lookup=lambda identifier: read_version(tx, branch_id, identifier),
        revision_lookup=lambda identifier: source_revision(tx, branch_id, identifier),
        settings_lookup=lambda identifier: settings_sync.read_settings_version(tx, branch_id, identifier),
        outline_lookup=lambda identifier: outline_sync.read_outline_version(tx, branch_id, identifier),
    )
    if existing := read_version(tx, branch_id, version.id):
        if existing != version:
            raise DomainError("setting_use_integrity", "同一设定使用修订已存在另一份内容。", 409)
        tx.execute(
            "UPDATE artifacts SET basis=json_set(basis,'$.source_device',?) "
            "WHERE id=? AND json_extract(basis,'$.source_device') IS NULL",
            (receipt.device_id, version.id),
        )
        return
    chapter_id = version.evidence.chapter_id
    chapter = tx.execute(
        "SELECT b.novel_id FROM chapters c JOIN branches b ON b.id=c.branch_id WHERE c.id=? AND c.branch_id=?",
        (chapter_id, branch_id),
    ).fetchone()
    if not chapter:
        raise DomainError("setting_use_dependency", "设定使用证明所用章节尚未在本机到达。", 409)
    source = source_revision(tx, branch_id, version.evidence.revision_id)
    tx.execute(
        "INSERT INTO artifacts (id,novel_id,branch_id,chapter_id,kind,base_revision,payload,basis,status,created) "
        "VALUES (?,?,?,?,?,?,?,?,'accepted',?)",
        (
            version.id,
            chapter["novel_id"],
            branch_id,
            chapter_id,
            KIND,
            source.revision,
            version.model_dump_json(),
            dumps({"head": False, "source_device": receipt.device_id}),
            time(),
        ),
    )


def apply_head(tx, branch_id, value):
    chosen = validate_head(
        branch_id,
        value.slot_id,
        value.revision_id,
        lambda identifier: read_version(tx, branch_id, identifier),
    )
    tx.execute(
        "UPDATE artifacts SET basis=json_set(basis,'$.head',0) "
        "WHERE branch_id=? AND chapter_id=? AND kind=? AND status='accepted' "
        "AND json_extract(basis,'$.head')=1 AND id<>? "
        "AND json_extract(payload,'$.settings_version_id')=? AND json_extract(payload,'$.item_id')=?",
        (branch_id, chosen.evidence.chapter_id, KIND, chosen.id, chosen.settings_version_id, chosen.item_id),
    )
    tx.execute("UPDATE artifacts SET basis=json_set(basis,'$.head',1) WHERE id=?", (chosen.id,))
