"""Explicit chapter-head adoption, distinct from historical story evidence bindings."""

from .db import DomainError
from .manuscript_exchange import native_revision, source_revision


def valid_chapter_adoption(source, native, *, source_is_native):
    return (
        source is not None
        and native is not None
        and source.id != native.id
        and source.chapter_id == native.chapter_id
        and source.sha256 == native.sha256
        and source.content == native.content
        and (not source_is_native or native.revision > source.revision)
    )


def _insert(tx, branch_id, source_id, native_id):
    tx.execute(
        "INSERT OR IGNORE INTO manuscript_chapter_adoptions VALUES(?,?,?)",
        (branch_id, source_id, native_id),
    )


def remember_chapter_adoption(tx, branch_id, source):
    """Record a cloud chapter head after its actual native application in the same transaction."""
    row = tx.execute(
        "SELECT r.id FROM chapters c JOIN revisions r ON r.chapter_id=c.id AND r.revision=c.revision "
        "WHERE c.branch_id=? AND c.id=? AND c.archived=0",
        (branch_id, source.chapter_id),
    ).fetchone()
    if not row or row["id"] == source.id:
        return
    actual = native_revision(tx, branch_id, row["id"])
    direct = native_revision(tx, branch_id, source.id)
    original = direct or source_revision(tx, branch_id, source.id)
    if original == source and valid_chapter_adoption(source, actual, source_is_native=direct is not None):
        _insert(tx, branch_id, source.id, actual.id)


def adopted_chapter_revision(tx, branch_id, source_id):
    direct = native_revision(tx, branch_id, source_id)
    original = direct or source_revision(tx, branch_id, source_id)
    if not original:
        return None
    rows = tx.execute(
        "SELECT a.native_id FROM manuscript_chapter_adoptions a JOIN revisions r ON r.id=a.native_id "
        "WHERE a.branch_id=? AND a.source_id=? ORDER BY r.revision DESC",
        (branch_id, source_id),
    ).fetchall()
    for row in rows:
        actual = native_revision(tx, branch_id, row["native_id"])
        if valid_chapter_adoption(original, actual, source_is_native=direct is not None):
            return actual
    return direct


def restore_chapter_adoption(tx, branch_id, source_id, native_id):
    direct = native_revision(tx, branch_id, source_id)
    original = direct or source_revision(tx, branch_id, source_id)
    actual = native_revision(tx, branch_id, native_id)
    if not valid_chapter_adoption(original, actual, source_is_native=direct is not None):
        raise DomainError("manuscript_setting_use", "恢复声明中的章节采用关系无法核对。", 409)
    _insert(tx, branch_id, source_id, native_id)
