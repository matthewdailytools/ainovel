"""SQLite projection of the cloud's confirmed facts and explicit length targets."""

from contextlib import contextmanager
from json import loads

from pydantic import ValidationError

from . import outline_sync, restore_state_sync as wire
from .chapter_adoption import adopted_chapter_revision
from .db import DomainError, dumps, uid
from .manuscript_exchange import source_revision
from .portable_restore_sources import _fact_quote, portable_fact
from .workflow_inspection import LENGTH_TARGET_KIND, _save_length_target, current_length_target

FACT_VERSION = wire.FACT_VERSION + ":"
LENGTH_VERSION = wire.LENGTH_VERSION + ":"
FACT_HEAD = wire.FACT_HEAD + ":"
LENGTH_HEAD = wire.LENGTH_HEAD + ":"
VERSION_PREFIXES = (FACT_VERSION, LENGTH_VERSION)
HEAD_PREFIXES = (FACT_HEAD, LENGTH_HEAD)
PREFIXES = VERSION_PREFIXES + HEAD_PREFIXES

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS desktop_sync_restore_versions(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), id TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(branch_id,id));
CREATE TABLE IF NOT EXISTS desktop_sync_restore_heads(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 adopted_id TEXT, detached_id TEXT, native_basis TEXT,
 PRIMARY KEY(branch_id,object_id),
 FOREIGN KEY(branch_id,adopted_id) REFERENCES desktop_sync_restore_versions(branch_id,id),
 FOREIGN KEY(branch_id,detached_id) REFERENCES desktop_sync_restore_versions(branch_id,id));
INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'restore-state-bootstrap' FROM desktop_sync WHERE enabled=1
 AND NOT EXISTS(SELECT 1 FROM extension_migrations WHERE name='desktop-sync-restore-state/1');
INSERT OR IGNORE INTO extension_migrations VALUES('desktop-sync-restore-state/1',unixepoch());
CREATE TRIGGER IF NOT EXISTS desktop_sync_fact_added AFTER INSERT ON facts BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'restore-state' FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1;
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_fact_changed AFTER UPDATE OF stale ON facts
 WHEN OLD.stale IS NOT NEW.stale BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'restore-state' FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1;
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_length_target_added AFTER INSERT ON artifacts
 WHEN NEW.kind='{LENGTH_TARGET_KIND}' AND NEW.status='accepted' BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'restore-state' FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1;
 END;
"""


def _missing(label):
    return DomainError(wire.DEPENDENCY, label, 409)


def read_version(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT payload FROM desktop_sync_restore_versions WHERE branch_id=? AND id=?",
        (branch_id, identifier),
    ).fetchone()
    if row:
        payload = loads(row["payload"])
        schema = wire.FactSetVersion if "facts" in payload else wire.LengthTargetVersion
        return schema.model_validate(payload)
    return None


def _store_version(tx, branch_id, version):
    tx.execute(
        "INSERT OR IGNORE INTO desktop_sync_restore_versions VALUES(?,?,?)",
        (branch_id, version.id, dumps(version.model_dump())),
    )


def _chapter(tx, branch_id, chapter_id):
    return tx.execute(
        "SELECT c.id,c.archived,c.revision,r.id AS revision_id FROM chapters c "
        "JOIN revisions r ON r.chapter_id=c.id AND r.revision=c.revision WHERE c.branch_id=? AND c.id=?",
        (branch_id, chapter_id),
    ).fetchone()


def _fact_data(tx, branch_id, chapter_id, preferred=None):
    chapter = _chapter(tx, branch_id, chapter_id)
    if not chapter:
        return None
    revision_id = chapter["revision_id"]
    sources = (
        [preferred]
        if preferred
        else [
            row["source_id"]
            for row in tx.execute(
                "SELECT source_id FROM manuscript_chapter_adoptions WHERE branch_id=? AND native_id=? "
                "ORDER BY rowid DESC",
                (branch_id, revision_id),
            )
        ]
    )
    for identifier in sources:
        adopted = adopted_chapter_revision(tx, branch_id, identifier)
        if adopted and adopted.id == revision_id:
            revision_id = identifier
            break
    facts = []
    if not chapter["archived"]:
        facts = [
            portable_fact(row, revision_id).model_dump()
            for row in tx.execute(
                "SELECT * FROM facts WHERE branch_id=? AND chapter_id=? AND stale=0 AND source_revision=? "
                "ORDER BY evidence_start,evidence_end,subject,kind,statement,truth,story_time",
                (branch_id, chapter_id, chapter["revision"]),
            )
        ]
    return {"chapter_id": chapter_id, "chapter_revision_id": revision_id, "facts": facts}


def _length_data(tx, branch_id, scope, scope_id, preferred=None):
    current = current_length_target(tx, branch_id, scope, scope_id, include_cleared=True)
    if not current:
        return None
    outline_id = None
    if scope == "volume":
        outline_id = (preferred if current["targets"] is None else None) or outline_sync.read_outline_head(
            tx, branch_id
        )
        outline = outline_sync.read_outline_version(tx, branch_id, outline_id) if outline_id else None
        if not outline or not any(volume.id == scope_id for volume in outline.plan.volumes):
            return None
    if scope == "chapter" and not _chapter(tx, branch_id, scope_id):
        return None
    return {
        "scope": scope,
        "scope_id": scope_id,
        "outline_version_id": outline_id,
        "targets": current["targets"],
    }


def _slot(tx, branch_id, address):
    return tx.execute(
        "SELECT adopted_id,detached_id,native_basis FROM desktop_sync_restore_heads "
        "WHERE branch_id=? AND object_id=?",
        (branch_id, address),
    ).fetchone()


def _save_slot(tx, branch_id, address, adopted, detached, native_basis):
    tx.execute(
        "INSERT INTO desktop_sync_restore_heads VALUES(?,?,?,?,?) ON CONFLICT(branch_id,object_id) "
        "DO UPDATE SET adopted_id=excluded.adopted_id,detached_id=excluded.detached_id,"
        "native_basis=excluded.native_basis",
        (branch_id, address, adopted, detached, native_basis),
    )


def _scopes(tx, branch_id):
    yield from (
        ("facts", row["id"])
        for row in tx.execute("SELECT id FROM chapters WHERE branch_id=? ORDER BY position,id", (branch_id,))
    )
    yield from (
        (row["scope"], row["scope_id"])
        for row in tx.execute(
            "SELECT DISTINCT json_extract(payload,'$.scope') AS scope,"
            "json_extract(payload,'$.scope_id') AS scope_id FROM artifacts "
            "WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY scope,scope_id",
            (branch_id, LENGTH_TARGET_KIND),
        )
    )


def _address(branch_id, scope, identifier):
    # The addresses use the same digests as portable head payloads.
    values = [branch_id, identifier] if scope == "facts" else [branch_id, scope, identifier]
    prefix = FACT_HEAD if scope == "facts" else LENGTH_HEAD
    return prefix + wire.canonical_digest(values)


def _basis(tx, branch_id, scope, identifier, preferred=None):
    return (
        _fact_data(tx, branch_id, identifier, preferred)
        if scope == "facts"
        else _length_data(tx, branch_id, scope, identifier, preferred)
    )


def _version_basis(version):
    basis = version.model_dump(exclude={"parent_id", "id"})
    if isinstance(version, wire.FactSetVersion):
        basis["facts"].sort(
            key=lambda row: (
                row["evidence"]["start"],
                row["evidence"]["end"],
                row["subject"],
                row["kind"],
                row["statement"],
                row["truth"],
                row["story_time"],
            )
        )
    return basis


def _preferred(version):
    if isinstance(version, wire.FactSetVersion):
        return version.chapter_revision_id
    return version.outline_version_id if version else None


def _draft(tx, branch_id, scope, identifier, cached):
    adopted_id = cached["adopted_id"] if cached else None
    adopted = read_version(tx, branch_id, adopted_id)
    preferred = _preferred(adopted)
    basis = _basis(tx, branch_id, scope, identifier, preferred)
    if cached and cached["native_basis"] == wire.canonical_digest(basis):
        return read_version(tx, branch_id, cached["detached_id"]) or adopted
    if not basis or (scope == "facts" and not adopted and not basis["facts"]):
        return None
    if adopted and _version_basis(adopted) == basis:
        return adopted
    if scope == "facts" and preferred:
        basis = _basis(tx, branch_id, scope, identifier)
    body = {"parent_id": adopted_id, **basis}
    schema = wire.FactSetVersion if scope == "facts" else wire.LengthTargetVersion
    try:
        return schema(id=wire.version_id(branch_id, body), **body)
    except (ValidationError, UnicodeError) as error:
        raise DomainError(wire.INTEGRITY, "本机的事实集合或篇幅目标超出同步范围。", 409) from error


def read_object(tx, branch_id, address):
    if address.startswith(VERSION_PREFIXES):
        version = read_version(tx, branch_id, address.partition(":")[2])
        return wire.version_object(branch_id, version) if version else None
    for scope, identifier in _scopes(tx, branch_id):
        if _address(branch_id, scope, identifier) == address:
            version = _draft(tx, branch_id, scope, identifier, _slot(tx, branch_id, address))
            return wire.head_object(branch_id, version) if version else None
    return None


def draft_objects(tx, branch_id, on_pending):
    heads = []
    for scope, identifier in _scopes(tx, branch_id):
        address = _address(branch_id, scope, identifier)
        try:
            version = _draft(tx, branch_id, scope, identifier, _slot(tx, branch_id, address))
        except DomainError as error:
            on_pending(address, error.message)
            continue
        if version:
            _store_version(tx, branch_id, version)
            heads.append(wire.head_object(branch_id, version))
    for row in tx.execute(
        "SELECT id FROM desktop_sync_restore_versions WHERE branch_id=? ORDER BY rowid",
        (branch_id,),
    ):
        yield wire.version_object(branch_id, read_version(tx, branch_id, row["id"]))
    yield from heads


def observe_version(tx, branch_id, version):
    wire.validate_version_sources(
        branch_id,
        version,
        revision_lookup=lambda identifier: source_revision(tx, branch_id, identifier),
        outline_lookup=lambda identifier: outline_sync.read_outline_version(tx, branch_id, identifier),
        parent_lookup=lambda identifier: read_version(tx, branch_id, identifier),
        chapter_lookup=lambda identifier: _chapter(tx, branch_id, identifier),
    )
    if (existing := read_version(tx, branch_id, version.id)) and existing != version:
        raise DomainError(wire.INTEGRITY, "同一事实或篇幅修订已有不同内容。", 409)
    _store_version(tx, branch_id, version)


def _head_version(tx, branch_id, head):
    return wire.validate_head(branch_id, head, lambda identifier: read_version(tx, branch_id, identifier))


def _scope(version):
    if isinstance(version, wire.FactSetVersion):
        return "facts", version.chapter_id
    return version.scope, version.scope_id


def _native_changes(tx, branch_id, version):
    """Resolve the exact adopted chapter/outline before inspecting or changing native state."""
    if isinstance(version, wire.FactSetVersion):
        chapter = _chapter(tx, branch_id, version.chapter_id)
        actual = adopted_chapter_revision(tx, branch_id, version.chapter_revision_id)
        if (
            not chapter
            or (chapter["archived"] and version.facts)
            or (not chapter["archived"] and (not actual or actual.id != chapter["revision_id"]))
        ):
            raise _missing("事实须等待本机采用其准确来源章稿。")
        return actual
    if version.scope == "chapter" and not _chapter(tx, branch_id, version.scope_id):
        raise _missing("篇幅目标须等待本机章节目录。")
    if (
        version.scope == "volume"
        and version.targets is not None
        and outline_sync.read_outline_head(tx, branch_id) != version.outline_version_id
    ):
        raise _missing("卷篇幅目标须等待本机采用其准确大纲。")
    return None


def matches_current(tx, branch_id, head):
    chosen = _head_version(tx, branch_id, head)
    cached = _slot(tx, branch_id, wire.object_id(head))
    if cached and cached["detached_id"] not in (None, chosen.id):
        return False
    _native_changes(tx, branch_id, chosen)
    scope, identifier = _scope(chosen)
    return _basis(tx, branch_id, scope, identifier, _preferred(chosen)) == _version_basis(chosen)


def apply_head(tx, branch_id, head):
    chosen = _head_version(tx, branch_id, head)
    native = _native_changes(tx, branch_id, chosen)
    scope, identifier = _scope(chosen)
    preferred = _preferred(chosen)
    if _basis(tx, branch_id, scope, identifier, preferred) != _version_basis(chosen):
        if isinstance(chosen, wire.FactSetVersion):
            tx.execute(
                "UPDATE facts SET stale=1 WHERE branch_id=? AND chapter_id=? AND stale=0",
                (branch_id, identifier),
            )
            for fact in chosen.facts:
                # Verification uses the rebound native text, whose exact equivalence was established above.
                row = _fact_quote(fact, native)
                tx.execute(
                    "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        uid(),
                        branch_id,
                        identifier,
                        native.revision,
                        row.subject,
                        row.kind,
                        row.statement,
                        row.truth,
                        row.story_time,
                        row.evidence,
                        fact.evidence.start,
                        fact.evidence.end,
                    ),
                )
            tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        else:
            current = current_length_target(tx, branch_id, scope, identifier, include_cleared=True)
            targets = chosen.targets.model_dump() if chosen.targets is not None else None
            _save_length_target(tx, branch_id, scope, identifier, current, targets)
    basis = wire.canonical_digest(_basis(tx, branch_id, scope, identifier, preferred))
    _save_slot(tx, branch_id, wire.object_id(head), chosen.id, None, basis)


def acknowledge_head(tx, branch_id, head):
    chosen = _head_version(tx, branch_id, head)
    address = wire.object_id(head)
    cached = _slot(tx, branch_id, address)
    detached = cached["detached_id"] if cached else None
    basis = cached["native_basis"] if detached else None
    _save_slot(tx, branch_id, address, chosen.id, detached if detached != chosen.id else None, basis)


@contextmanager
def dependent_heads(tx, branch_id, *, chapter_id=None):
    """Remote pieces may move a source without creating a local fact or target edit."""
    scopes = (
        [("facts", chapter_id)]
        if chapter_id
        else [(scope, identifier) for scope, identifier in _scopes(tx, branch_id) if scope == "volume"]
    )
    previous = []
    for scope, identifier in scopes:
        address = _address(branch_id, scope, identifier)
        cached = _slot(tx, branch_id, address)
        adopted = cached["adopted_id"] if cached else None
        proposed = _draft(tx, branch_id, scope, identifier, cached)
        if proposed or adopted:
            previous.append((scope, identifier, address, adopted, proposed))
    yield
    for scope, identifier, address, adopted, proposed in previous:
        detached = proposed if proposed and proposed.id != adopted else None
        if detached:
            _store_version(tx, branch_id, detached)
        version = read_version(tx, branch_id, adopted)
        basis = _basis(tx, branch_id, scope, identifier, _preferred(version))
        _save_slot(
            tx, branch_id, address, adopted, detached.id if detached else None, wire.canonical_digest(basis)
        )
