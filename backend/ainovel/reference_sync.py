"""Opt-in formal M2 saved-card sync and offline archive without source text by default."""

from hashlib import sha256
import json

from pydantic import ValidationError

from .db import DomainError
from .domain import utf16_slice
from .project_sync import FORMAT
from .reference_portability import (
    CARD_FIELDS,
    CardCitation,
    ReferenceCardHead,
    ReferenceCardHeadObject,
    ReferenceCardHistoryEntry,
    ReferenceCardVersion,
    ReferenceCardVersionObject,
    ReferenceHistory,
    missing_card_fields,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS portable_reference_cards(
 branch_id TEXT NOT NULL REFERENCES branches(id),id TEXT NOT NULL,payload TEXT NOT NULL,
 source_device TEXT,change_id TEXT,sequence INTEGER,PRIMARY KEY(branch_id,id));
CREATE TABLE IF NOT EXISTS portable_reference_heads(
 branch_id TEXT NOT NULL REFERENCES branches(id),session_id TEXT NOT NULL,card_id TEXT NOT NULL,
 adopted_id TEXT NOT NULL,native_base_id TEXT,head_change_id TEXT,head_device_id TEXT,
 PRIMARY KEY(branch_id,session_id,card_id),
 FOREIGN KEY(branch_id,adopted_id) REFERENCES portable_reference_cards(branch_id,id));
CREATE TABLE IF NOT EXISTS portable_reference_excerpts(
 branch_id TEXT NOT NULL REFERENCES branches(id),version_id TEXT NOT NULL,quote TEXT NOT NULL,
 PRIMARY KEY(branch_id,version_id),
 FOREIGN KEY(branch_id,version_id) REFERENCES portable_reference_cards(branch_id,id));
"""


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _version_id(branch_id, version):
    return _digest(
        json.dumps(
            [branch_id, version.model_dump(exclude={"id"}, mode="json")],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _key(value):
    return value.session_id, value.card_id


def reference_head_object_id(branch_id, session_id, card_id):
    scope = json.dumps([branch_id, session_id, card_id], ensure_ascii=False, separators=(",", ":"))
    return f"reference-card:{_digest(scope)}"


def version_object(branch_id, version):
    return ReferenceCardVersionObject(
        format=FORMAT, kind="reference-card-version", branch_id=branch_id, version=version
    )


def head_object(branch_id, head):
    return ReferenceCardHeadObject(
        format=FORMAT, kind="reference-card-head", branch_id=branch_id, **head.model_dump()
    )


def parse_reference_object(branch_id, object_id, payload, tombstone):
    version = object_id.startswith("reference-card-version:")
    try:
        schema = ReferenceCardVersionObject if version else ReferenceCardHeadObject
        value = schema.model_validate(payload)
    except ValidationError as error:
        raise DomainError("sync_reference", "手法卡未通过正式版本格式校验。", 409) from error
    expected = (
        f"reference-card-version:{value.version.id}"
        if version
        else reference_head_object_id(branch_id, value.session_id, value.card_id)
    )
    if tombstone or branch_id != value.branch_id or object_id != expected:
        raise DomainError("sync_reference", "手法卡须属于同一作品，归档须提交新版本。", 409)
    if version:
        validate_reference_version(branch_id, value.version)
    return value


def validate_reference_version(branch_id, version):
    if _version_id(branch_id, version) != version.id or version.parent_id == version.id:
        raise DomainError("sync_reference", "手法卡正式修订编号与内容不一致。", 409)


def validate_version_parent(version, lookup):
    if version.parent_id:
        parent = lookup(version.parent_id)
        if not parent or _key(parent) != _key(version) or parent.revision >= version.revision:
            raise DomainError("sync_reference", "缺少同一手法卡的较早父修订。", 409)
        while version.citation and not parent.citation and parent.parent_id:
            ancestor = lookup(parent.parent_id)
            if not ancestor or _key(ancestor) != _key(parent) or ancestor.revision >= parent.revision:
                raise DomainError("sync_reference", "手法卡来源历史不完整。", 409)
            parent = ancestor
        if version.citation and parent.citation:
            source = (
                "book_id",
                "unit_id",
                "file_sha256",
                "selection_start",
                "selection_end",
                "selection_sha256",
            )
            if any(getattr(version.citation, key) != getattr(parent.citation, key) for key in source):
                raise DomainError("sync_reference", "更换手法卡原始选段须新建参考会话。", 409)


def _citation(source, excerpt):
    selection = source["quote"]
    start = selection.find(excerpt)
    if start < 0:
        raise DomainError("reference_citation", "手法卡证据已无法定位到保存选段。", 409)
    exact_start = exact_end = None
    if selection.find(excerpt, start + 1) == -1:
        exact_start = source["start"] + len(selection[:start].encode("utf-16-le")) // 2
        exact_end = exact_start + len(excerpt.encode("utf-16-le")) // 2
    return CardCitation(
        book_id=source["book_id"],
        unit_id=source["unit_id"],
        file_sha256=source["file_hash"],
        title=source["title"],
        selection_start=source["start"],
        selection_end=source["end"],
        selection_sha256=_digest(selection),
        evidence_sha256=_digest(excerpt),
        evidence_start=exact_start,
        evidence_end=exact_end,
    )


def _native_version(branch_id, session_id, card_id, parent, revision, card, snapshot, source):
    data = {
        "id": "0" * 64,
        "parent_id": parent,
        "session_id": session_id,
        "card_id": card_id,
        "revision": revision,
        "assessment": "archived",
    }
    if card is not None:
        missing = missing_card_fields(card)
        if missing:
            data.update(assessment="incomplete", missing_fields=missing)
        else:
            data.update(
                assessment="ready",
                card={key: card[key] for key in CARD_FIELDS},
                citation=_citation(source, card["evidence"]),
                goal=snapshot["goal"],
            )
    version = ReferenceCardVersion.model_validate(data)
    return version.model_copy(update={"id": _version_id(branch_id, version)})


def _native_catalog(tx, branch_id):
    versions, heads, excerpts = {}, {}, {}
    rows = tx.execute(
        "SELECT * FROM reference_sessions r WHERE json_extract(r.payload,'$.branch_id')=? OR EXISTS("
        "SELECT 1 FROM json_each(r.payload,'$.history') h WHERE json_extract(h.value,'$.branch_id')=?) "
        "ORDER BY r.id",
        (branch_id, branch_id),
    )
    for row in rows:
        content = json.loads(row["payload"])
        lineage = {}
        active = {}
        snapshots = [*content.get("history", []), {**content, "revision": row["revision"]}]
        for snapshot in sorted(snapshots, key=lambda value: value["revision"]):
            # Old scope-less history cannot establish which work authorized the transfer.
            cards = (
                {card["id"]: card for card in snapshot["cards"]}
                if snapshot.get("branch_id") == branch_id
                else {}
            )
            for card_id in sorted(lineage.keys() | cards.keys()):
                previous = lineage.get(card_id)
                card = cards.get(card_id)
                if card is None and previous.assessment == "archived":
                    continue
                version = _native_version(
                    branch_id,
                    row["id"],
                    card_id,
                    previous.id if previous else None,
                    snapshot["revision"],
                    card,
                    snapshot,
                    content["source"],
                )
                lineage[card_id] = versions[version.id] = version
                if previous is None or version.model_dump(
                    exclude={"id", "parent_id", "revision"}
                ) != previous.model_dump(exclude={"id", "parent_id", "revision"}):
                    active[card_id] = version.id
                if version.assessment == "ready":
                    excerpts[version.id] = card["evidence"]
        heads.update({(row["id"], identifier): value for identifier, value in active.items()})
    return versions, heads, excerpts


def _stored_catalog(tx, branch_id):
    return {
        row["id"]: (ReferenceCardVersion.model_validate_json(row["payload"]), row["source_device"])
        for row in tx.execute(
            "SELECT id,payload,source_device FROM portable_reference_cards WHERE branch_id=?", (branch_id,)
        )
    }


def _adopted_heads(tx, branch_id, native):
    heads = dict(native)
    for row in tx.execute("SELECT * FROM portable_reference_heads WHERE branch_id=?", (branch_id,)):
        key = row["session_id"], row["card_id"]
        if native.get(key) == row["native_base_id"]:
            heads[key] = row["adopted_id"]
    return heads


def read_reference_version(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT payload FROM portable_reference_cards WHERE branch_id=? AND id=?",
        (branch_id, identifier),
    ).fetchone()
    return (
        ReferenceCardVersion.model_validate_json(row["payload"])
        if row
        else _native_catalog(tx, branch_id)[0].get(identifier)
    )


def selectable_reference_cards(tx, branch_id):
    native, native_heads, _ = _native_catalog(tx, branch_id)
    stored = _stored_catalog(tx, branch_id)
    heads = set(_adopted_heads(tx, branch_id, native_heads).values())
    pins = {}
    for row in tx.execute(
        "SELECT id,payload FROM reference_sessions WHERE json_extract(payload,'$.branch_id')=?", (branch_id,)
    ):
        session = json.loads(row["payload"])
        pins.update(
            ((row["id"], card_id), revision)
            for card_id, revision in session.get("pinned_revisions", {}).items()
            if card_id in session["selected_ids"]
        )
    pinned = {version.id for version in native.values() if pins.get(_key(version)) == version.revision}
    versions = {**native, **{key: value for key, (value, _) in stored.items()}}
    eligible = heads | pinned
    return [
        (version, version.id in pinned)
        for version in _ordered(versions)
        if version.id in eligible and version.assessment == "ready"
    ]


def read_reference_head(tx, branch_id, session_id, card_id):
    native = _native_catalog(tx, branch_id)[1]
    return _adopted_heads(tx, branch_id, native).get((session_id, card_id))


def read_reference_head_object(tx, branch_id, object_id):
    native = _native_catalog(tx, branch_id)[1]
    for (session_id, card_id), version_id in _adopted_heads(tx, branch_id, native).items():
        if reference_head_object_id(branch_id, session_id, card_id) == object_id:
            return head_object(
                branch_id, ReferenceCardHead(session_id=session_id, card_id=card_id, revision_id=version_id)
            )
    return None


def has_reference_source(tx, branch_id, version_id):
    return bool(
        tx.execute(
            "SELECT 1 FROM portable_reference_cards WHERE branch_id=? AND id=? AND change_id IS NOT NULL",
            (branch_id, version_id),
        ).fetchone()
    )


def _ordered(versions):
    remaining, ready, ordered = dict(versions), set(), []
    while remaining:
        available = [
            version for version in remaining.values() if not version.parent_id or version.parent_id in ready
        ]
        if not available:
            raise DomainError("sync_reference", "手法卡父修订缺失或构成循环。", 409)
        for version in sorted(available, key=lambda item: item.id):
            ordered.append(remaining.pop(version.id))
            ready.add(version.id)
    return ordered


def draft_reference_objects(tx, branch_id):
    native, native_heads, _ = _native_catalog(tx, branch_id)
    stored = _stored_catalog(tx, branch_id)
    versions = {**native, **{key: value for key, (value, _) in stored.items()}}
    active = _adopted_heads(tx, branch_id, native_heads)
    known = {
        row["id"]
        for row in tx.execute(
            "SELECT id FROM portable_reference_cards WHERE branch_id=? AND change_id IS NOT NULL",
            (branch_id,),
        )
    }
    outgoing = [version_object(branch_id, item) for item in _ordered(versions) if item.id not in known]
    return [
        *outgoing,
        *(
            head_object(branch_id, ReferenceCardHead(session_id=session, card_id=card, revision_id=value))
            for (session, card), value in sorted(active.items())
        ),
    ]


def observe_reference_version(tx, branch_id, version, receipt):
    validate_reference_version(branch_id, version)
    validate_version_parent(version, lambda identifier: read_reference_version(tx, branch_id, identifier))
    if (existing := read_reference_version(tx, branch_id, version.id)) and existing != version:
        raise DomainError("sync_reference", "同一正式手法卡修订已存在不同内容。", 409)
    tx.execute(
        "INSERT INTO portable_reference_cards VALUES(?,?,?,?,?,?) ON CONFLICT(branch_id,id) DO UPDATE SET "
        "source_device=CASE WHEN portable_reference_cards.sequence IS NULL "
        "THEN COALESCE(portable_reference_cards.source_device,excluded.source_device) ELSE excluded.source_device END,"
        "change_id=excluded.change_id,sequence=excluded.sequence "
        "WHERE portable_reference_cards.sequence IS NULL OR excluded.sequence<portable_reference_cards.sequence",
        (
            branch_id,
            version.id,
            version.model_dump_json(),
            receipt.device_id,
            receipt.change_id,
            receipt.sequence,
        ),
    )


def apply_reference_head(tx, branch_id, value, receipt=None):
    row = tx.execute(
        "SELECT payload FROM portable_reference_cards WHERE branch_id=? AND id=?",
        (branch_id, value.revision_id),
    ).fetchone()
    if not row or _key(ReferenceCardVersion.model_validate_json(row["payload"])) != _key(value):
        raise DomainError("sync_reference", "手法卡头缺少同一张卡的来源修订，待历史到齐。", 409)
    native = _native_catalog(tx, branch_id)[1]
    tx.execute(
        "INSERT INTO portable_reference_heads VALUES(?,?,?,?,?,?,?) ON CONFLICT(branch_id,session_id,card_id) "
        "DO UPDATE SET adopted_id=excluded.adopted_id,native_base_id=excluded.native_base_id,"
        "head_change_id=excluded.head_change_id,head_device_id=excluded.head_device_id",
        (
            branch_id,
            value.session_id,
            value.card_id,
            value.revision_id,
            native.get(_key(value)),
            receipt.change_id if receipt else None,
            receipt.device_id if receipt else None,
        ),
    )


def reference_card_at(tx, branch_id, session_id, card_id, revision, *, version_id=None):
    native, _, _ = _native_catalog(tx, branch_id)
    stored = _stored_catalog(tx, branch_id)
    versions = {**native, **{key: value for key, (value, _) in stored.items()}}
    matches = [
        value
        for value in versions.values()
        if _key(value) == (session_id, card_id)
        and value.revision == revision
        and (version_id is None or value.id == version_id)
    ]
    if len(matches) > 1:
        raise DomainError("reference_citation", "该手法卡修订号存在分支，请选择准确的来源版本编号。", 409)
    if not matches:
        raise DomainError("reference_citation", "手法卡来源修订尚未同步。", 404)
    return matches[0]


def list_reference_history(tx, branch_id, session_id=None):
    native, native_heads, raw = _native_catalog(tx, branch_id)
    stored = _stored_catalog(tx, branch_id)
    versions = {**native, **{key: value for key, (value, _) in stored.items()}}
    heads = _adopted_heads(tx, branch_id, native_heads)
    rows = []
    for version in versions.values():
        if session_id is not None and version.session_id != session_id:
            continue
        _, state = _reference_excerpt(tx, branch_id, version, raw.get(version.id))
        rows.append(
            ReferenceCardHistoryEntry(
                version=version,
                adopted=heads.get(_key(version)) == version.id,
                source_state=state,
                source_device=stored[version.id][1] if version.id in stored else None,
            )
        )
    return sorted(
        rows, key=lambda row: (_key(row.version), not row.adopted, -row.version.revision, row.version.id)
    )


def _verified_excerpt(version, quote):
    if not quote or not version.citation or _digest(quote) != version.citation.evidence_sha256:
        raise DomainError("reference_citation", "所选原文与手法卡的来源摘要不一致。", 409)
    citation = version.citation
    if len(quote) > 2000 or (
        citation.evidence_start is not None
        and len(quote.encode("utf-16-le")) // 2 != citation.evidence_end - citation.evidence_start
    ):
        raise DomainError("reference_citation", "原文长度与资料选区不一致。", 409)
    return quote


def reference_excerpt(tx, branch_id, version):
    return _reference_excerpt(tx, branch_id, version, _native_catalog(tx, branch_id)[2].get(version.id))


def _reference_excerpt(tx, branch_id, version, raw):
    if version.assessment != "ready":
        return None, "no_evidence"
    if raw:
        return _verified_excerpt(version, raw), "local"
    row = tx.execute(
        "SELECT quote FROM portable_reference_excerpts WHERE branch_id=? AND version_id=?",
        (branch_id, version.id),
    ).fetchone()
    if row:
        return _verified_excerpt(version, row["quote"]), "included_excerpt"
    citation = version.citation
    if citation.evidence_start is not None:
        unit = tx.execute(
            "SELECT u.content FROM source_units u JOIN books b ON b.id=u.book_id "
            "WHERE u.id=? AND b.id=? AND b.file_hash=?",
            (citation.unit_id, citation.book_id, citation.file_sha256),
        ).fetchone()
        if unit:
            try:
                selection = utf16_slice(unit["content"], citation.selection_start, citation.selection_end)[1]
                if _digest(selection) == citation.selection_sha256:
                    quote = utf16_slice(unit["content"], citation.evidence_start, citation.evidence_end)[1]
                    return _verified_excerpt(version, quote), "local"
            except DomainError:
                pass
    return None, "metadata_only"


def validate_reference_archive(history, branch_id):
    versions = {version.id: version for version in history.versions}
    heads = {_key(head) for head in history.adopted_heads}
    if len(versions) != len(history.versions) or len(heads) != len(history.adopted_heads):
        raise DomainError("reference_archive", "手法卡档案中的版本或采用头重复。", 409)
    if not (history.source_devices.keys() | history.excerpts.keys()) <= versions.keys():
        raise DomainError("reference_archive", "手法卡原文或设备来源没有对应版本。", 409)
    for version in _ordered(versions):
        validate_reference_version(branch_id, version)
        validate_version_parent(version, versions.get)
    for head in history.adopted_heads:
        if head.revision_id not in versions or _key(versions[head.revision_id]) != _key(head):
            raise DomainError("reference_archive", "手法卡采用头指向其他记录。", 409)
    for version_id, quote in history.excerpts.items():
        _verified_excerpt(versions[version_id], quote)


def export_reference_archive(tx, branch_id, *, include_evidence_for=()):
    native, native_heads, raw = _native_catalog(tx, branch_id)
    stored = _stored_catalog(tx, branch_id)
    versions = {**native, **{key: value for key, (value, _) in stored.items()}}
    requested = set(include_evidence_for)
    if not versions and not requested:
        return None
    excerpts = {}
    for identifier in sorted(requested):
        if identifier not in versions:
            raise DomainError("reference_citation", "选带的原文不属于当前作品手法卡。", 409)
        excerpt, _ = _reference_excerpt(tx, branch_id, versions[identifier], raw.get(identifier))
        if not excerpt:
            raise DomainError("reference_citation", "选带的手法卡没有可核对的原文。", 409)
        excerpts[identifier] = excerpt
    heads = _adopted_heads(tx, branch_id, native_heads)
    history = ReferenceHistory(
        adopted_heads=[
            ReferenceCardHead(session_id=session, card_id=card, revision_id=identifier)
            for (session, card), identifier in sorted(heads.items())
        ],
        versions=_ordered(versions),
        source_devices={key: device for key, (_, device) in stored.items() if device},
        excerpts=excerpts,
    )
    validate_reference_archive(history, branch_id)
    return history


def import_reference_archive(tx, branch_id, history):
    validate_reference_archive(history, branch_id)
    tx.executemany(
        "INSERT INTO portable_reference_cards VALUES(?,?,?,?,NULL,NULL)",
        [
            (branch_id, version.id, version.model_dump_json(), history.source_devices.get(version.id))
            for version in history.versions
        ],
    )
    for head in history.adopted_heads:
        apply_reference_head(tx, branch_id, head)
    tx.executemany(
        "INSERT INTO portable_reference_excerpts VALUES(?,?,?)",
        [(branch_id, version_id, quote) for version_id, quote in history.excerpts.items()],
    )
