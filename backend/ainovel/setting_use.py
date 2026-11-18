"""Author-confirmed uses of non-world settings in saved manuscript passages."""

from hashlib import sha256
import json
from time import time
from typing import Annotated, Literal

from fastapi import Query
from pydantic import Field, StringConstraints

from .db import DomainError, dumps
from .manuscript_exchange import native_revision, source_revision
from .models import Input
from .outline_sync import read_outline_head
from .outline_workspace import _latest as latest_outline, chapter_basis
from .settings_portability import SettingsEvidence
from .settings_sync import _evidence_quote, settings_source_id
from .story_sync import _adopted_chapter_revision

KIND = "setting_use"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SettingUseBasis(Input):
    outline_version_id: Identifier
    settings_version_id: Identifier
    chapter_revision_id: Identifier
    chapter_revision: int
    chapter_sha256: Digest


class SettingUseConfirm(Input):
    outline_version_id: Identifier
    settings_version_id: Identifier
    item_id: Identifier
    parent_id: Digest | None
    chapter_sha256: Digest
    evidence: SettingsEvidence
    idempotency_key: str = Field(min_length=8, max_length=128)


class SettingUseWithdraw(Input):
    idempotency_key: str = Field(min_length=8, max_length=128)


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


class SettingUseRecord(Input):
    version: SettingUseVersion
    status: Literal["current", "outdated", "superseded", "withdrawn", "missing"]
    created: float
    quote: str | None


class SettingUseOption(Input):
    item_id: Identifier
    title: str
    category_id: Identifier
    category_title: str
    head_id: Digest | None


class SettingUseView(Input):
    chapter_id: Identifier
    basis: SettingUseBasis | None
    options: list[SettingUseOption]
    records: list[SettingUseRecord]
    next_before_id: Digest | None


def _digest(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def version_id(branch_id, version):
    return _digest([branch_id, version])


def _chapter(tx, chapter_id):
    row = tx.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not row:
        raise DomainError("not_found", "章节不存在。", 404)
    return row


def _context(tx, chapter):
    if chapter["archived"] or chapter["stale"] or not chapter["content"].strip():
        return None, {}, None
    branch_id, chapter_id = chapter["branch_id"], chapter["id"]
    outline = latest_outline(tx, branch_id, True)
    if not outline:
        return None, {}, None
    try:
        plan = chapter_basis(tx, branch_id, chapter_id, outline["id"])
    except DomainError as error:
        if not (error.code.startswith(("conception_", "outline_")) or error.code == "project_brief_changed"):
            raise
        return None, {}, None
    settings_version_id = settings_source_id(tx, branch_id, plan["setting_confirmation_id"])
    if not settings_version_id:
        return None, {}, None
    row = tx.execute(
        "SELECT id FROM revisions WHERE chapter_id=? AND revision=?", (chapter_id, chapter["revision"])
    ).fetchone()
    saved = native_revision(tx, branch_id, row["id"]) if row else None
    if not saved or saved.content != chapter["content"]:
        return None, {}, None
    return (
        {
            "outline_version_id": read_outline_head(tx, branch_id),
            "settings_version_id": settings_version_id,
            "chapter_revision_id": saved.id,
            "chapter_revision": saved.revision,
            "chapter_sha256": saved.sha256,
        },
        {item["id"]: item for item in plan["settings"] if item.get("category_id") not in (None, "world")},
        saved,
    )


def _key(version):
    return version.settings_version_id, version.item_id


def _lookup(tx, chapter, identifier):
    row = tx.execute(
        "SELECT rowid,id,base_revision,payload,created FROM artifacts WHERE branch_id=? AND chapter_id=? AND kind=? "
        "AND status='accepted' AND id=?",
        (chapter["branch_id"], chapter["id"], KIND, identifier),
    ).fetchone()
    if not row:
        raise DomainError("setting_use_missing", "找不到属于本章的设定使用记录。", 404)
    return row


def _head_ids(tx, chapter):
    rows = tx.execute(
        "SELECT id,json_extract(payload,'$.settings_version_id') AS settings_version_id,"
        "json_extract(payload,'$.item_id') AS item_id FROM artifacts WHERE branch_id=? AND chapter_id=? "
        "AND kind=? AND status='accepted' AND json_extract(basis,'$.head')=1",
        (chapter["branch_id"], chapter["id"], KIND),
    )
    return {(row["settings_version_id"], row["item_id"]): row["id"] for row in rows}


def _verified_quote(tx, branch_id, version, saved=None):
    source = saved or source_revision(tx, branch_id, version.evidence.revision_id)
    if not source or source.sha256 != version.chapter_sha256:
        raise DomainError("setting_use_evidence", "找不到确切的正文修订或全文校验值已变。", 409)
    try:
        quote = _evidence_quote(version.evidence, source)
    except DomainError as error:
        raise DomainError(
            "setting_use_evidence", "正文选区或校验值不一致，请重新选择保存稿中的原文。", 409
        ) from error
    if not quote.strip():
        raise DomainError("setting_use_evidence", "请选择含正文的有效选区。", 409)
    return quote


def _present(tx, chapter, row, heads, context):
    version = SettingUseVersion.model_validate_json(row["payload"])
    basis, options, _ = context
    branch_id = chapter["branch_id"]
    try:
        quote = _verified_quote(tx, branch_id, version)
    except DomainError as error:
        if error.code != "setting_use_evidence":
            raise
        quote = None
    if heads.get(_key(version)) != version.id:
        status = "superseded"
    elif version.action == "withdrawn":
        status = "withdrawn"
    elif quote is None:
        status = "missing"
    elif (
        not basis
        or version.item_id not in options
        or version.settings_version_id != basis["settings_version_id"]
        or version.outline_version_id != basis["outline_version_id"]
    ):
        status = "outdated"
    else:
        native = _adopted_chapter_revision(tx, branch_id, version.evidence.revision_id)
        status = "current" if native and native.id == basis["chapter_revision_id"] else "outdated"
    return {"version": version, "status": status, "created": row["created"], "quote": quote}


def current_outline_uses(tx, branch_id, outline_version_id, settings_version_id):
    """Return author-confirmed proofs for a caller's already validated current outline."""
    if not outline_version_id or not settings_version_id:
        return {}
    rows = tx.execute(
        "SELECT a.payload,a.created,r.id AS native_revision_id FROM artifacts a "
        "JOIN chapters c ON c.id=a.chapter_id AND c.branch_id=a.branch_id "
        "JOIN revisions r ON r.chapter_id=c.id AND r.revision=c.revision "
        "WHERE a.branch_id=? AND a.kind=? AND a.status='accepted' AND c.archived=0 AND c.stale=0 "
        "AND json_extract(a.basis,'$.head')=1 AND json_extract(a.payload,'$.action')='confirmed' "
        "AND json_extract(a.payload,'$.outline_version_id')=? "
        "AND json_extract(a.payload,'$.settings_version_id')=?",
        (branch_id, KIND, outline_version_id, settings_version_id),
    ).fetchall()
    result = {}
    for row in rows:
        version = SettingUseVersion.model_validate_json(row["payload"])
        native = _adopted_chapter_revision(tx, branch_id, version.evidence.revision_id)
        if not native or native.id != row["native_revision_id"]:
            continue
        try:
            quote = _verified_quote(tx, branch_id, version, native)
        except DomainError as error:
            if error.code != "setting_use_evidence":
                raise
            continue
        result[version.evidence.chapter_id, version.item_id] = {
            "version": version.model_dump(),
            "status": "current",
            "created": row["created"],
            "quote": quote,
        }
    return result


def _replay(tx, chapter, key, fingerprint):
    row = tx.execute("SELECT artifact_id,result FROM receipts WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    if json.loads(row["result"]).get("setting_use_fingerprint") != fingerprint:
        raise DomainError("idempotency", "提交标识已用于其他操作。", 409)
    return _lookup(tx, chapter, row["artifact_id"])


def _store(tx, chapter, payload, key, fingerprint, base_revision):
    branch_id, chapter_id = chapter["branch_id"], chapter["id"]
    identifier = version_id(branch_id, payload)
    prior = tx.execute("SELECT 1 FROM artifacts WHERE id=?", (identifier,)).fetchone()
    if not prior:
        novel_id = tx.execute("SELECT novel_id FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
        tx.execute(
            "INSERT INTO artifacts (id,novel_id,branch_id,chapter_id,kind,base_revision,payload,basis,status,created) "
            "VALUES (?,?,?,?,?,?,?,'{}','accepted',?)",
            (
                identifier,
                novel_id,
                branch_id,
                chapter_id,
                KIND,
                base_revision,
                dumps({"id": identifier, **payload}),
                time(),
            ),
        )
    _lookup(tx, chapter, identifier)
    if payload["parent_id"]:
        tx.execute(
            "UPDATE artifacts SET basis=json_set(basis,'$.head',0) WHERE id=?", (payload["parent_id"],)
        )
    tx.execute("UPDATE artifacts SET basis=json_set(basis,'$.head',1) WHERE id=?", (identifier,))
    tx.execute(
        "INSERT INTO receipts VALUES (?,?,?)",
        (key, identifier, dumps({"setting_use_fingerprint": fingerprint})),
    )
    return _lookup(tx, chapter, identifier)


def _confirm(tx, chapter, data):
    branch_id = chapter["branch_id"]
    chapter_id = chapter["id"]
    payload = data.model_dump(exclude={"idempotency_key"}) | {"action": "confirmed", "actor": "author"}
    fingerprint = _digest([branch_id, chapter_id, payload])
    if row := _replay(tx, chapter, data.idempotency_key, fingerprint):
        return row
    basis, options, saved = _context(tx, chapter)
    if (
        not basis
        or (data.outline_version_id, data.settings_version_id, data.evidence.revision_id, data.chapter_sha256)
        != (
            basis["outline_version_id"],
            basis["settings_version_id"],
            basis["chapter_revision_id"],
            basis["chapter_sha256"],
        )
        or data.evidence.chapter_id != chapter_id
    ):
        raise DomainError("setting_use_changed", "已保存章稿、确认设定或大纲已变，请重新选择正文原文。", 409)
    if data.item_id not in options:
        raise DomainError("setting_use_unplanned", "请选择本章大纲挂载的已确认非世界设定。", 409)
    if _head_ids(tx, chapter).get((data.settings_version_id, data.item_id)) != data.parent_id:
        raise DomainError("setting_use_conflict", "这条使用记录已有新版本，请重新读取比较。", 409)
    _verified_quote(tx, branch_id, SettingUseVersion(id=version_id(branch_id, payload), **payload), saved)
    return _store(tx, chapter, payload, data.idempotency_key, fingerprint, saved.revision)


def _withdraw(tx, chapter, identifier, data):
    fingerprint = _digest([chapter["branch_id"], chapter["id"], identifier, "withdrawn"])
    if row := _replay(tx, chapter, data.idempotency_key, fingerprint):
        return row
    row = _lookup(tx, chapter, identifier)
    version = SettingUseVersion.model_validate_json(row["payload"])
    if version.action != "confirmed" or _head_ids(tx, chapter).get(_key(version)) != identifier:
        raise DomainError("setting_use_conflict", "这条使用记录已有新版本，请重新读取比较。", 409)
    payload = version.model_dump(exclude={"id"}) | {"parent_id": identifier, "action": "withdrawn"}
    return _store(tx, chapter, payload, data.idempotency_key, fingerprint, row["base_revision"])


def register_routes(app, domain):
    path = "/api/chapters/{chapter_id}/setting-uses"

    @app.get(path)
    def setting_uses(
        chapter_id: Identifier,
        before_id: Digest | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> SettingUseView:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            chapter = _chapter(tx, chapter_id)
            context = _context(tx, chapter)
            basis, choices, _ = context
            heads = _head_ids(tx, chapter)
            previous = _lookup(tx, chapter, before_id)["rowid"] if before_id else None
            rows = tx.execute(
                "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND chapter_id=? AND kind=? "
                "AND status='accepted' AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?",
                (chapter["branch_id"], chapter_id, KIND, previous, previous, limit + 1),
            ).fetchall()
            return {
                "chapter_id": chapter_id,
                "basis": basis,
                "options": [
                    {
                        "item_id": item["id"],
                        "title": item["title"],
                        "category_id": item["category_id"],
                        "category_title": item["category_title"],
                        "head_id": heads.get((basis["settings_version_id"], item["id"])),
                    }
                    for item in choices.values()
                ],
                "records": [_present(tx, chapter, row, heads, context) for row in rows[:limit]],
                "next_before_id": rows[limit - 1]["id"] if len(rows) > limit else None,
            }

    @app.get(path + "/{use_id}")
    def get_setting_use(chapter_id: Identifier, use_id: Digest) -> SettingUseRecord:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            chapter = _chapter(tx, chapter_id)
            return _present(
                tx, chapter, _lookup(tx, chapter, use_id), _head_ids(tx, chapter), _context(tx, chapter)
            )

    @app.post(path, status_code=201)
    def confirm_setting_use(chapter_id: Identifier, data: SettingUseConfirm) -> SettingUseRecord:
        with domain.db.connect(True) as tx:
            chapter = _chapter(tx, chapter_id)
            return _present(
                tx, chapter, _confirm(tx, chapter, data), _head_ids(tx, chapter), _context(tx, chapter)
            )

    @app.post(path + "/{use_id}/withdraw", status_code=201)
    def withdraw_setting_use(
        chapter_id: Identifier, use_id: Digest, data: SettingUseWithdraw
    ) -> SettingUseRecord:
        with domain.db.connect(True) as tx:
            chapter = _chapter(tx, chapter_id)
            return _present(
                tx,
                chapter,
                _withdraw(tx, chapter, use_id, data),
                _head_ids(tx, chapter),
                _context(tx, chapter),
            )
