"""Isolated SQLite sync service for explicitly provisioned works and devices."""

from contextlib import closing, contextmanager
from functools import cache
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sqlite3
from time import time
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import Field, JsonValue, StringConstraints, ValidationError, model_validator

from . import cloud_ledger
from .db import DomainError
from .chapter_conflicts import verified_chapter
from .cloud_attachments import SCHEMA as ATTACHMENT_SCHEMA, register_routes as register_attachments
from .manuscript_exchange import RevisionObject, _source_link
from .outline_sync import OutlineHeadObject, OutlineVersionObject, parse_outline_object
from .outline_sync import validate_version_sources as validate_outline_sources
from .models import Input
from .project_sync import ProjectVersionObject, parse_project_object
from .reference_portability import ReferenceCardVersionObject
from .reference_sync import parse_reference_object, validate_version_parent as validate_reference_parent
from . import restore_state_sync
from .setting_use_portability import (
    SettingUseVersionObject,
    parse_setting_use_object,
    validate_head as validate_setting_use_head,
    validate_version_sources as validate_setting_use_sources,
)
from .settings_sync import SettingsVersionObject, parse_settings_object, validate_version_sources
from .story_sync import StoryVersionObject, parse_story_object
from .story_sync import validate_version_sources as validate_story_sources
from .workflow_archive import (
    ArchivedInspection,
    WORKFLOW_PREFIXES,
    has_frozen_reference_selection,
    parse_workflow_object,
    same_workflow_entry,
    validate_workflow_object,
)
from .workflow_privacy import REFERENCE_SCOPE, sync_completion_report, sync_has_reference_selection

MAX_OBJECT_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = MAX_OBJECT_BYTES * 2
MAX_PULL_BYTES = MAX_OBJECT_BYTES * 4
MAX_CURSOR = (1 << 53) - 1
APP_ID = 0x414E5359
STORAGE_ERROR = "同步存储暂不可读。"
SQLITE_STORAGE_ERRORS = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_CANTOPEN,
    sqlite3.SQLITE_CORRUPT,
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_LOCKED,
    sqlite3.SQLITE_NOTADB,
    sqlite3.SQLITE_READONLY,
    sqlite3.SQLITE_SCHEMA,
}
WITHHELD_PREFIX = "withheld:"
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
Authorization = Annotated[str, Header(max_length=512)]
Payload = dict[str, JsonValue]

SCHEMA = (
    cloud_ledger.SCHEMA
    + """
CREATE TABLE IF NOT EXISTS sync_works(
 id TEXT PRIMARY KEY, sequence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sync_devices(
 work_id TEXT NOT NULL REFERENCES sync_works(id),
 id TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE, active INTEGER NOT NULL CHECK(active IN (0,1)),
 PRIMARY KEY(work_id,id));
CREATE TABLE IF NOT EXISTS sync_changes(
 work_id TEXT NOT NULL REFERENCES sync_works(id), change_id TEXT NOT NULL, object_id TEXT NOT NULL,
 device_id TEXT NOT NULL, parent_revision TEXT, sequence INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('applied','conflict','resolved')),
 head_revision TEXT NOT NULL, conflict_id TEXT, resolved_by TEXT,
 payload TEXT, tombstone INTEGER NOT NULL CHECK(tombstone IN (0,1)), request_sha256 TEXT NOT NULL,
 PRIMARY KEY(work_id,change_id), UNIQUE(work_id,sequence),
 FOREIGN KEY(work_id,device_id) REFERENCES sync_devices(work_id,id),
 FOREIGN KEY(work_id,parent_revision) REFERENCES sync_changes(work_id,change_id),
 FOREIGN KEY(work_id,head_revision) REFERENCES sync_changes(work_id,change_id),
 FOREIGN KEY(work_id,resolved_by) REFERENCES sync_changes(work_id,change_id),
 CHECK((tombstone=1 AND payload IS NULL) OR (tombstone=0 AND payload IS NOT NULL)));
CREATE TABLE IF NOT EXISTS sync_heads(
 work_id TEXT NOT NULL REFERENCES sync_works(id), object_id TEXT NOT NULL, revision TEXT NOT NULL,
 PRIMARY KEY(work_id,object_id),
 FOREIGN KEY(work_id,revision) REFERENCES sync_changes(work_id,change_id));
CREATE INDEX IF NOT EXISTS sync_pending ON sync_changes(work_id,sequence)
 WHERE status='conflict' AND resolved_by IS NULL;
"""
)


class BoundedRequests:
    """Bound JSON before FastAPI loads it, including chunked submissions."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                await JSONResponse(
                    {"code": "sync_request_size", "message": "同步请求超过服务大小限制。"}, status_code=413
                )(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body"):
                break
        sent = False

        async def buffered():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, buffered, send)


class GrantInput(Input):
    device_id: Identifier
    rotate: bool = Field(default=False, strict=True)


class Grant(Input):
    work_id: Identifier
    device_id: Identifier
    token: str


class Push(Input):
    change_id: Identifier
    object_id: Identifier
    parent_revision: Identifier | None
    tombstone: bool = Field(strict=True)
    payload: Payload | None

    @model_validator(mode="after")
    def check_payload(self):
        if self.tombstone != (self.payload is None):
            raise ValueError("删除记录须使用空 payload，其他记录须提供 JSON 对象。")
        return self


class Resolution(Input):
    change_id: Identifier
    parent_revision: Identifier
    choice: Literal["current", "incoming", "merge", "merge_prose"]
    payload: Payload | None = None
    tombstone: bool | None = Field(default=None, strict=True)
    content: str | None = Field(default=None, max_length=250_000)

    @model_validator(mode="after")
    def check_payload(self):
        if self.choice == "merge_prose":
            if self.content is None or self.payload is not None or self.tombstone is not None:
                raise ValueError("正文合并须提供作者编辑的正文。")
            return self
        if self.content is not None:
            raise ValueError("只有正文合并可提交编辑后的正文。")
        if self.choice == "merge":
            if self.tombstone is None or self.tombstone != (self.payload is None):
                raise ValueError("合并时须明确是否删除，且只在保留对象时提供 JSON 对象。")
        elif self.payload is not None or self.tombstone is not None:
            raise ValueError("选用已有版本时由服务端读取该版本。")
        return self


class Receipt(Input):
    sequence: int
    status: Literal["applied", "conflict", "resolved"]
    change_id: Identifier
    object_id: Identifier
    parent_revision: Identifier | None
    device_id: Identifier
    head_revision: Identifier
    conflict_id: Identifier | None
    payload: Payload | None
    tombstone: bool


class Pulled(Input):
    cursor: int
    has_more: bool
    changes: list[Receipt]


class Checkpoint(Input):
    ledger_id: cloud_ledger.LedgerId
    cursor: int = Field(ge=0, le=MAX_CURSOR, strict=True)


class Conflict(Input):
    conflict_id: Identifier
    object_id: Identifier
    parent_revision: Identifier | None
    conflict_head_revision: Identifier
    head_revision: Identifier
    opened_sequence: int
    resolved_by: Identifier | None


class Conflicts(Input):
    conflicts: list[Conflict]
    cursor: int
    has_more: bool


class ComparedConflict(Conflict):
    incoming: Receipt
    current: Receipt
    base: Receipt | None


def _encoded(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        result.encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise DomainError("sync_payload", "同步数据包含无效的 JSON 文本。") from error
    return result


def _payload(value):
    if value is None:
        return None
    encoded = _encoded(value)
    if len(encoded.encode("utf-8")) > MAX_OBJECT_BYTES:
        raise DomainError("sync_object_size", "单条同步对象超过 1 MiB。", 413)
    return encoded


def _digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _credential(header):
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or any(char.isspace() for char in token):
        raise DomainError("sync_auth", "同步凭据无效。", 401)
    return _digest(token)


def _receipt(row):
    return Receipt(
        sequence=row["sequence"],
        status=row["status"],
        change_id=row["change_id"],
        object_id=row["object_id"],
        parent_revision=row["parent_revision"],
        device_id=row["device_id"],
        head_revision=row["head_revision"],
        conflict_id=row["conflict_id"],
        payload=json.loads(row["payload"]) if row["payload"] is not None else None,
        tombstone=bool(row["tombstone"]),
    )


def _check_reserved(*identifiers, payload=None):
    if any(identifier.startswith(WITHHELD_PREFIX) for identifier in identifiers) or (
        payload and payload.get("kind") == "withheld"
    ):
        raise DomainError("sync_reserved", "同步保留标记不能作为业务提交。", 409)


def _inspection_dependency(payload):
    if identifier := sync_completion_report(payload):
        return identifier
    if payload and payload.get("kind") == "evidence":
        document = payload.get("document")
        if isinstance(document, dict) and document.get("kind") == "completion":
            try:
                identifier = json.loads(document.get("body")).get("manifest", {}).get("inspection_id")
                if isinstance(identifier, str):
                    return identifier
            except AttributeError, TypeError, ValueError, RecursionError:
                pass
    return None


def _withheld(tx, work, sequence):
    # Preserve contiguous slots without naming a real event, object or device.
    salt = 0
    while True:
        marker = _digest(_encoded(["sync-withheld/1", work, sequence, salt]))
        identifier = f"{WITHHELD_PREFIX}event:{marker}"
        object_id = f"{WITHHELD_PREFIX}object:{marker}"
        device_id = f"{WITHHELD_PREFIX}device:{marker}"
        # The namespace was not reserved at the time legacy records were written.
        if not tx.execute(
            "SELECT 1 FROM sync_changes WHERE work_id=? AND change_id=? UNION ALL "
            "SELECT 1 FROM sync_heads WHERE work_id=? AND object_id=? UNION ALL "
            "SELECT 1 FROM sync_devices WHERE work_id=? AND id=? LIMIT 1",
            (work, identifier, work, object_id, work, device_id),
        ).fetchone():
            break
        salt += 1
    return Receipt(
        sequence=sequence,
        status="applied",
        change_id=identifier,
        object_id=object_id,
        device_id=device_id,
        parent_revision=None,
        head_revision=identifier,
        conflict_id=None,
        payload={"kind": "withheld", "reason": "reference_scope"},
        tombstone=False,
    )


def _schema(tx):
    return [
        tuple(row)
        for row in tx.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE sql IS NOT NULL AND name NOT GLOB 'sqlite_*' ORDER BY type,name"
        )
    ]


@cache
def _expected_schema():
    with closing(sqlite3.connect(":memory:")) as tx:
        tx.executescript(SCHEMA + ATTACHMENT_SCHEMA)
        return _schema(tx)


def is_ledger_schema(tx):
    return (
        tx.execute("PRAGMA application_id").fetchone()[0] == APP_ID
        and tx.execute("PRAGMA user_version").fetchone()[0] == cloud_ledger.DB_VERSION
        and _schema(tx) == _expected_schema()
    )


class SyncStore:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(snapshot=True, create=True) as tx:
            identifier = tx.execute("PRAGMA application_id").fetchone()[0]
            version = tx.execute("PRAGMA user_version").fetchone()[0]
            if (identifier, version) not in {(0, 0), (APP_ID, 1), (APP_ID, cloud_ledger.DB_VERSION)}:
                raise DomainError("sync_schema", "同步数据库格式无法读取。")
            if (
                identifier == 0
                and tx.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise DomainError("sync_schema", "请选择独立的同步数据库。")
            tx.commit()
            tx.execute("PRAGMA journal_mode=WAL")
            if version < cloud_ledger.DB_VERSION:
                tx.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\n{ATTACHMENT_SCHEMA}")
                # A simultaneous startup may have already assigned this database its identity.
                cloud_ledger.initialize(tx, requires_header=identifier == APP_ID)
                tx.execute(f"PRAGMA application_id={APP_ID}")
                tx.execute(f"PRAGMA user_version={cloud_ledger.DB_VERSION}")
            cloud_ledger.identity(tx)
            tx.commit()

    def check_readiness(self):
        database = self.path.as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(database, uri=True, timeout=1)) as tx:
                tx.execute("BEGIN")
                if not is_ledger_schema(tx):
                    raise DomainError("sync_storage", STORAGE_ERROR, 503)
                cloud_ledger.identity(tx)
                tx.execute("SELECT id, sequence FROM sync_works LIMIT 1").fetchone()
        except sqlite3.Error as error:
            raise DomainError("sync_storage", STORAGE_ERROR, 503) from error

    @contextmanager
    def transaction(self, *, write=False, snapshot=False, streaming=False, create=False):
        # Only initialization may create a database. Requests must leave a missing restore target alone.
        database = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
        try:
            with closing(
                sqlite3.connect(
                    database, uri=True, isolation_level=None, timeout=10, check_same_thread=not streaming
                )
            ) as tx:
                tx.row_factory = sqlite3.Row
                tx.execute("PRAGMA foreign_keys=ON")
                tx.execute("PRAGMA temp_store=MEMORY")
                if write:
                    tx.execute("BEGIN IMMEDIATE")
                elif snapshot:
                    tx.execute("BEGIN")
                yield tx
                if write:
                    tx.commit()
        except sqlite3.DatabaseError as error:
            # Extended SQLite result codes share the low byte of the primary code.
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            if isinstance(error, sqlite3.OperationalError) or code in SQLITE_STORAGE_ERRORS:
                raise DomainError("sync_storage", STORAGE_ERROR, 503) from error
            raise

    def grant(self, work, data):
        _check_reserved(data.device_id)
        token = secrets.token_urlsafe(32)
        with self.transaction(write=True) as tx:
            tx.execute("INSERT OR IGNORE INTO sync_works(id) VALUES(?)", (work,))
            existing = tx.execute(
                "SELECT active FROM sync_devices WHERE work_id=? AND id=?", (work, data.device_id)
            ).fetchone()
            if existing and existing["active"] and not data.rotate:
                raise DomainError("sync_device_exists", "设备已有凭据。需要更换时请明确选择轮换。", 409)
            tx.execute(
                "INSERT INTO sync_devices VALUES(?,?,?,1) ON CONFLICT(work_id,id) DO UPDATE SET "
                "token_sha256=excluded.token_sha256,active=1",
                (work, data.device_id, _digest(token)),
            )
        return Grant(work_id=work, device_id=data.device_id, token=token)

    def revoke(self, work, device):
        with self.transaction(write=True) as tx:
            found = tx.execute("UPDATE sync_devices SET active=0 WHERE work_id=? AND id=?", (work, device))
            if not found.rowcount:
                raise DomainError("sync_device", "同步设备不存在。", 404)

    def revoke_current(self, work, digest, expected_device):
        with self.transaction(write=True) as tx:
            # Keep a revoked digest usable for replay of this operation until the device is reprovisioned.
            device = self._authorize(tx, work, digest, allow_inactive=True)
            if expected_device is not None and expected_device != device:
                raise DomainError("sync_device_mismatch", "设备编号与提供的同步凭据不匹配。", 403)
            tx.execute("UPDATE sync_devices SET active=0 WHERE work_id=? AND id=?", (work, device))

    @staticmethod
    def _authorize(tx, work, digest, *, allow_inactive=False):
        row = tx.execute(
            "SELECT id,active FROM sync_devices WHERE work_id=? AND token_sha256=?", (work, digest)
        ).fetchone()
        if not row or (not row["active"] and not allow_inactive):
            raise DomainError("sync_auth", "同步凭据无效。", 401)
        return row["id"]

    @staticmethod
    def _change(tx, work, revision):
        return tx.execute(
            "SELECT * FROM sync_changes WHERE work_id=? AND change_id=?", (work, revision)
        ).fetchone()

    @staticmethod
    def _head(tx, work, object_id):
        row = tx.execute(
            "SELECT revision FROM sync_heads WHERE work_id=? AND object_id=?", (work, object_id)
        ).fetchone()
        return row["revision"] if row else None

    def _replay(self, tx, work, identifier, fingerprint):
        row = self._change(tx, work, identifier)
        if row and not secrets.compare_digest(row["request_sha256"], fingerprint):
            raise DomainError("sync_replay", "同一变更编号不能用于其他提交。", 409)
        return self._public_receipt(tx, work, row) if row else None

    def _private_payload(self, tx, work, payload, reports):
        if sync_has_reference_selection(payload):
            return True
        identifier = _inspection_dependency(payload)
        if identifier is None:
            return False
        if identifier not in reports:
            # A later document projection can have a newer head. The signed
            # delivery still depends on the earlier verification record.
            reports[identifier] = any(
                sync_has_reference_selection(json.loads(row["payload"]))
                for row in tx.execute(
                    "SELECT payload FROM sync_changes WHERE work_id=? AND object_id IN (?,?) "
                    "AND payload IS NOT NULL",
                    (work, f"workflow-inspection:{identifier}", f"evidence:{identifier}"),
                )
            )
        return reports[identifier]

    def _public_receipt(self, tx, work, row):
        entry = _receipt(row)
        if self._private_event(tx, work, row, entry, {}):
            raise DomainError("sync_workflow_reference_scope", REFERENCE_SCOPE, 409)
        return entry

    def _private_event(self, tx, work, row, entry, reports):
        return self._private_payload(tx, work, entry.payload, reports) or (
            entry.status == "conflict" and self._private_conflict(tx, work, row, reports)
        )

    def _append(self, tx, work, device, data, fingerprint, payload, head, status, conflict_id=None):
        sequence = tx.execute("SELECT sequence FROM sync_works WHERE id=?", (work,)).fetchone()[0] + 1
        if sequence > MAX_CURSOR:
            raise DomainError("sync_cursor", "同步序号已达上限。", 409)
        tx.execute("UPDATE sync_works SET sequence=? WHERE id=?", (sequence, work))
        tx.execute(
            "INSERT INTO sync_changes(work_id,change_id,object_id,device_id,parent_revision,sequence,status,"
            "head_revision,conflict_id,payload,tombstone,request_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                work,
                data.change_id,
                data.object_id,
                device,
                data.parent_revision,
                sequence,
                status,
                head,
                conflict_id,
                payload,
                int(data.tombstone),
                fingerprint,
            ),
        )
        if status != "conflict":
            tx.execute(
                "INSERT INTO sync_heads VALUES(?,?,?) ON CONFLICT(work_id,object_id) DO UPDATE SET "
                "revision=excluded.revision",
                (work, data.object_id, data.change_id),
            )
        return _receipt(self._change(tx, work, data.change_id))

    def push(self, work, digest, data, *, ledger_id=None):
        with self.transaction(write=True) as tx:
            device = self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id)
            _check_reserved(data.object_id, data.change_id, payload=data.payload)
            if self._private_payload(tx, work, data.payload, {}):
                raise DomainError("sync_workflow_reference_scope", REFERENCE_SCOPE, 409)
            payload = _payload(data.payload)
            fingerprint = _digest(_encoded(["push", device, data.model_dump()]))
            if replay := self._replay(tx, work, data.change_id, fingerprint):
                return replay
            if data.object_id.startswith("revision:"):
                return self._push_source_revision(tx, work, device, data, payload, fingerprint)
            if data.object_id.startswith(WORKFLOW_PREFIXES):
                return self._push_workflow(tx, work, device, data, payload, fingerprint)
            formal = self._validate_formal(tx, work, data.object_id, data.payload, data.tombstone)
            if isinstance(
                formal,
                (
                    ProjectVersionObject,
                    SettingsVersionObject,
                    OutlineVersionObject,
                    StoryVersionObject,
                    ReferenceCardVersionObject,
                    SettingUseVersionObject,
                    *restore_state_sync.VERSION_OBJECTS,
                ),
            ):
                if data.parent_revision is not None:
                    base = self._change(tx, work, data.parent_revision)
                    if not base or base["object_id"] != data.object_id:
                        raise DomainError("sync_parent_unknown", "父修订不属于该正式对象。", 409)
                return self._append(tx, work, device, data, fingerprint, payload, data.change_id, "applied")
            if data.parent_revision is not None:
                parent = self._change(tx, work, data.parent_revision)
                if not parent or parent["object_id"] != data.object_id:
                    raise DomainError("sync_parent_unknown", "父修订尚未入库或不属于该对象。", 409)
            head = self._head(tx, work, data.object_id)
            if data.parent_revision == head:
                self._validate_restore_state_adoption(tx, work, formal)
                return self._append(tx, work, device, data, fingerprint, payload, data.change_id, "applied")
            return self._append(
                tx, work, device, data, fingerprint, payload, head, "conflict", data.change_id
            )

    def _push_source_revision(self, tx, work, device, data, payload, fingerprint):
        try:
            source = RevisionObject.model_validate(data.payload)
        except ValidationError as error:
            raise DomainError("sync_history", "来源修订格式无效。", 409) from error
        row = source.revision
        if (
            source.branch_id != work
            or data.object_id != f"revision:{row.id}"
            or _digest(row.content) != row.sha256
        ):
            raise DomainError("sync_history", "来源修订归属或正文哈希无效。", 409)
        head = self._head(tx, work, data.object_id)
        if head:
            existing = self._change(tx, work, head)
            if RevisionObject.model_validate_json(existing["payload"]) != source:
                raise DomainError("sync_history", "同一来源修订编号不能修改。", 409)
        if data.parent_revision is not None:
            base = self._change(tx, work, data.parent_revision)
            if not base or base["object_id"] != data.object_id:
                raise DomainError("sync_parent_unknown", "父修订不属于该对象。", 409)
        parent_id = self._head(tx, work, f"revision:{row.parent_id}") if row.parent_id else None
        parent = (
            RevisionObject.model_validate_json(self._change(tx, work, parent_id)["payload"]).revision
            if parent_id
            else None
        )
        if not _source_link(row, parent):
            raise DomainError("sync_history", "来源修订须等待同一章的连续父稿入库。", 409)
        return self._append(tx, work, device, data, fingerprint, payload, data.change_id, "applied")

    def _validate_formal(self, tx, work, object_id, payload, tombstone):
        if object_id.startswith(restore_state_sync.RESTORE_STATE_PREFIXES):
            return self._validate_restore_state(tx, work, object_id, payload, tombstone)
        family = object_id.partition(":")[0].removesuffix("-version")
        contracts = {
            "project": ("立项", ProjectVersionObject, parse_project_object),
            "settings": ("设定", SettingsVersionObject, parse_settings_object),
            "outline": ("大纲", OutlineVersionObject, parse_outline_object),
            "story": ("故事记录", StoryVersionObject, parse_story_object),
            "reference-card": ("手法卡", ReferenceCardVersionObject, parse_reference_object),
            "setting-use": ("设定的正文使用证据", SettingUseVersionObject, parse_setting_use_object),
        }
        if family not in contracts:
            return None
        title, immutable, parse = contracts[family]
        value = parse(work, object_id, payload, tombstone)
        error_code = f"sync_{'reference' if family == 'reference-card' else family.replace('-', '_')}"

        def source(object_id, model):
            stored = self._formal_payload(tx, work, object_id)
            return model.model_validate_json(stored) if stored else None

        def setting_use(identifier):
            stored = source(f"setting-use-version:{identifier}", SettingUseVersionObject)
            return stored.version if stored else None

        if not isinstance(value, immutable):
            if family == "setting-use":
                validate_setting_use_head(work, value.slot_id, value.revision_id, setting_use)
                return value
            stored = source(f"{family}-version:{value.revision_id}", immutable)
            if not stored or (family == "story" and stored.version.record_id != value.record_id):
                raise DomainError(error_code, f"尚未收到要采用的{title}修订。", 409)
            if family == "reference-card" and (stored.version.session_id, stored.version.card_id) != (
                value.session_id,
                value.card_id,
            ):
                raise DomainError(error_code, "手法卡采用头须指向同一张卡的修订。", 409)
            return value
        version = value.version
        if version.parent_id and not self._head(tx, work, f"{family}-version:{version.parent_id}"):
            raise DomainError(error_code, f"{title}要先提交连续父版本。", 409)
        if head := self._head(tx, work, object_id):
            previous = self._change(tx, work, head)
            if immutable.model_validate_json(previous["payload"]) != value:
                raise DomainError(error_code, f"同一{title}修订编号不能改写。", 409)

        def project_exists(identifier):
            return self._head(tx, work, f"project-version:{identifier}")

        def revision(identifier):
            stored = source(f"revision:{identifier}", RevisionObject)
            return stored.revision if stored else None

        if family == "settings":
            validate_version_sources(version, project_exists, revision)

        def settings(identifier):
            stored = source(f"settings-version:{identifier}", SettingsVersionObject)
            return stored.version if stored else None

        if family == "setting-use":

            def outline(identifier):
                stored = source(f"outline-version:{identifier}", OutlineVersionObject)
                return stored.version if stored else None

            validate_setting_use_sources(
                work,
                version,
                use_lookup=setting_use,
                revision_lookup=revision,
                settings_lookup=settings,
                outline_lookup=outline,
            )
        if family == "reference-card":

            def card(identifier):
                stored = source(f"reference-card-version:{identifier}", ReferenceCardVersionObject)
                return stored.version if stored else None

            validate_reference_parent(version, card)
        if family == "story":

            def story(identifier):
                stored = source(f"story-version:{identifier}", StoryVersionObject)
                return stored.version if stored else None

            validate_story_sources(version, story, revision)
        if family == "outline":

            def reference(identifier):
                stored = source(f"reference-card-version:{identifier}", ReferenceCardVersionObject)
                return stored.version if stored else None

            validate_outline_sources(
                version,
                project_exists,
                settings,
                lambda identifier: self._head(tx, work, f"chapter:{identifier}"),
                reference_lookup=reference,
            )
        return value

    def _formal_payload(self, tx, work, object_id):
        head = self._head(tx, work, object_id)
        if head:
            return self._change(tx, work, head)["payload"]
        return None

    def _restore_state_version(self, tx, work, kind, identifier):
        prefix, schema = (
            (restore_state_sync.FACT_VERSION, restore_state_sync.FactSetVersionObject)
            if kind in (restore_state_sync.FACT_VERSION, restore_state_sync.FACT_HEAD)
            else (restore_state_sync.LENGTH_VERSION, restore_state_sync.LengthTargetVersionObject)
        )
        stored = self._formal_payload(tx, work, f"{prefix}:{identifier}")
        return schema.model_validate_json(stored).version if stored else None

    def _validate_restore_state(self, tx, work, object_id, payload, tombstone):
        value = restore_state_sync.parse_restore_state_object(work, object_id, payload, tombstone)

        def version_lookup(identifier):
            return self._restore_state_version(tx, work, value.kind, identifier)

        if not isinstance(value, restore_state_sync.VERSION_OBJECTS):
            restore_state_sync.validate_head(work, value, version_lookup)
            return value

        def revision(identifier):
            stored = self._formal_payload(tx, work, f"revision:{identifier}")
            return RevisionObject.model_validate_json(stored).revision if stored else None

        def outline(identifier):
            stored = self._formal_payload(tx, work, f"outline-version:{identifier}")
            return OutlineVersionObject.model_validate_json(stored).version if stored else None

        restore_state_sync.validate_version_sources(
            work,
            value.version,
            revision_lookup=revision,
            outline_lookup=outline,
            parent_lookup=version_lookup,
            chapter_lookup=lambda identifier: self._head(tx, work, f"chapter:{identifier}"),
        )
        if previous := version_lookup(value.version.id):
            if previous != value.version:
                raise DomainError(restore_state_sync.INTEGRITY, "同一正式版本编号不能改写。", 409)
        return value

    def _validate_restore_state_adoption(self, tx, work, head):
        if isinstance(head, restore_state_sync.FactSetHeadObject):
            version = self._restore_state_version(tx, work, head.kind, head.revision_id)
            address = f"chapter:{head.chapter_id}"
            if self._head(tx, work, address) is None:
                raise DomainError(restore_state_sync.DEPENDENCY, "请先同步事实所在章节的目录。", 409)
            payload = self._formal_payload(tx, work, address)
            if payload is None:
                if not version.facts:
                    return
            else:
                try:
                    chapter = verified_chapter(work, address, json.loads(payload))
                except DomainError as error:
                    raise DomainError(
                        restore_state_sync.INTEGRITY, "事实所依据的云端章稿无法核验。", 409
                    ) from error
                if chapter.head.id == version.chapter_revision_id:
                    return
            raise DomainError(restore_state_sync.DEPENDENCY, "请先采用事实所依据的正式章稿。", 409)
        if isinstance(head, restore_state_sync.LengthTargetHeadObject) and head.scope == "volume":
            version = self._restore_state_version(tx, work, head.kind, head.revision_id)
            if version.targets is not None:
                payload = self._formal_payload(tx, work, f"outline:{work}")
                if not payload or OutlineHeadObject.model_validate_json(payload).revision_id != (
                    version.outline_version_id
                ):
                    raise DomainError(
                        restore_state_sync.DEPENDENCY, "请先采用卷级篇幅目标所依据的大纲。", 409
                    )

    def _push_workflow(self, tx, work, device, data, payload, fingerprint):
        entry = parse_workflow_object(work, data.object_id, data.payload, data.tombstone)

        def source(object_id):
            head = self._head(tx, work, object_id)
            return self._change(tx, work, head) if head else None

        def inspection(identifier):
            object_id = f"workflow-inspection:{identifier}"
            if row := source(object_id):
                entry = parse_workflow_object(work, object_id, json.loads(row["payload"]), row["tombstone"])
                if has_frozen_reference_selection(entry):
                    raise DomainError("sync_workflow_reference_scope", REFERENCE_SCOPE, 409)
                return entry
            return None

        def revision(identifier):
            if row := source(f"revision:{identifier}"):
                return RevisionObject.model_validate_json(row["payload"]).revision
            return None

        def setting_use(identifier):
            if row := source(f"setting-use-version:{identifier}"):
                return SettingUseVersionObject.model_validate_json(row["payload"]).version
            return None

        def settings(identifier):
            if row := source(f"settings-version:{identifier}"):
                return SettingsVersionObject.model_validate_json(row["payload"]).version
            return None

        other = "workflow-completion" if isinstance(entry, ArchivedInspection) else "workflow-inspection"
        if source(f"{other}:{entry.id}"):
            raise DomainError("sync_workflow", "体检和交付记录共用了同一来源编号。", 409)
        if previous := source(data.object_id):
            stored = parse_workflow_object(
                work, data.object_id, json.loads(previous["payload"]), previous["tombstone"]
            )
            if not same_workflow_entry(stored, entry):
                raise DomainError("sync_workflow", "同一体检或交付记录编号不能改写。", 409)
        if data.parent_revision is not None:
            base = self._change(tx, work, data.parent_revision)
            if not base or base["object_id"] != data.object_id:
                raise DomainError("sync_parent_unknown", "父修订不属于该历史记录。", 409)
        try:
            validate_workflow_object(
                entry, work, revision, inspection, setting_use=setting_use, settings=settings
            )
        except DomainError as error:
            if error.code in {"manuscript_workflow", "completion_integrity"}:
                raise DomainError("sync_workflow", error.message, error.status) from error
            raise
        return self._append(tx, work, device, data, fingerprint, payload, data.change_id, "applied")

    def checkpoint(self, work, digest):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            ledger_id, _ = cloud_ledger.identity(tx)
            cursor = tx.execute("SELECT sequence FROM sync_works WHERE id=?", (work,)).fetchone()[0]
            return Checkpoint(ledger_id=ledger_id, cursor=cursor)

    def receipt(self, work, digest, change_id, *, ledger_id=None):
        with self.transaction(snapshot=True) as tx:
            device = self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id, receipt_lookup=True)
            row = self._change(tx, work, change_id)
            if not row or row["device_id"] != device:
                raise DomainError("sync_receipt", "该设备没有对应回执。", 404)
            return self._public_receipt(tx, work, row)

    def change(self, work, digest, change_id, *, ledger_id=None):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id, receipt_lookup=True)
            row = self._change(tx, work, change_id)
            if not row:
                raise DomainError("sync_change", "找不到所选变更。", 404)
            return self._public_receipt(tx, work, row)

    def pull(self, work, digest, after, limit, *, ledger_id=None):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id)
            bound = tx.execute("SELECT sequence FROM sync_works WHERE id=?", (work,)).fetchone()[0]
            if after > bound:
                raise DomainError("sync_cursor", "拉取游标超出当前作品的变更记录。", 409)
            changes = []
            size = 0
            reports = {}
            for row in tx.execute(
                "SELECT * FROM sync_changes WHERE work_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (work, after, limit),
            ):
                entry = _receipt(row)
                withheld = self._private_event(tx, work, row, entry, reports)
                if withheld:
                    entry = _withheld(tx, work, row["sequence"])
                size += len((_payload(entry.payload) if withheld else row["payload"] or "").encode("utf-8"))
                if changes and size > MAX_PULL_BYTES:
                    break
                changes.append(entry)
            cursor = changes[-1].sequence if changes else after
            return Pulled(cursor=cursor, has_more=cursor < bound, changes=changes)

    def _conflict(self, tx, work, row):
        if not row or row["status"] != "conflict":
            raise DomainError("sync_conflict", "找不到对应的同步冲突。", 404)
        return Conflict(
            conflict_id=row["change_id"],
            object_id=row["object_id"],
            parent_revision=row["parent_revision"],
            conflict_head_revision=row["head_revision"],
            head_revision=self._head(tx, work, row["object_id"]),
            opened_sequence=row["sequence"],
            resolved_by=row["resolved_by"],
        )

    def _compared_rows(self, tx, work, summary):
        head = self._change(tx, work, summary.head_revision)
        base = self._change(tx, work, summary.parent_revision) if summary.parent_revision else None
        return head, base

    def _private_conflict(self, tx, work, row, reports, *, current=False):
        head = self._head(tx, work, row["object_id"]) if current else row["head_revision"]
        compared = [row, self._change(tx, work, head)]
        if row["parent_revision"]:
            compared.append(self._change(tx, work, row["parent_revision"]))
        return any(
            self._private_payload(tx, work, json.loads(event["payload"]), reports)
            for event in compared
            if event and event["payload"] is not None
        )

    def conflicts(self, work, digest, after, limit, *, ledger_id=None):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id)
            rows = list(
                tx.execute(
                    "SELECT * FROM sync_changes WHERE work_id=? AND sequence>? AND status='conflict' AND resolved_by IS NULL "
                    "ORDER BY sequence LIMIT ?",
                    (work, after, limit + 1),
                )
            )
            examined = rows[:limit]
            reports = {}
            result = [
                self._conflict(tx, work, row)
                for row in examined
                if not (
                    self._private_conflict(tx, work, row, reports)
                    or self._private_conflict(tx, work, row, reports, current=True)
                )
            ]
            cursor = examined[-1]["sequence"] if examined else after
            return Conflicts(conflicts=result, cursor=cursor, has_more=len(rows) > limit)

    def comparison(self, work, digest, identifier, *, ledger_id=None):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id)
            row = self._change(tx, work, identifier)
            summary = self._conflict(tx, work, row)
            head, base = self._compared_rows(tx, work, summary)
            return ComparedConflict(
                **summary.model_dump(),
                incoming=self._public_receipt(tx, work, row),
                current=self._public_receipt(tx, work, head),
                base=self._public_receipt(tx, work, base) if base else None,
            )

    def resolve(self, work, digest, identifier, data, *, ledger_id=None):
        with self.transaction(write=True) as tx:
            device = self._authorize(tx, work, digest)
            cloud_ledger.verify_header(tx, ledger_id)
            _check_reserved(data.change_id, payload=data.payload)
            request = data.model_dump(exclude={"content"} if data.choice != "merge_prose" else None)
            fingerprint = _digest(_encoded(["resolve", device, identifier, request]))
            if replay := self._replay(tx, work, data.change_id, fingerprint):
                return replay
            incoming = self._change(tx, work, identifier)
            conflict = self._conflict(tx, work, incoming)
            _check_reserved(conflict.object_id)
            if conflict.object_id.startswith(WORKFLOW_PREFIXES):
                raise DomainError("sync_workflow", "体检或交付历史记录不能通过冲突合并修改。", 409)
            if conflict.resolved_by:
                raise DomainError("sync_conflict_resolved", "冲突已有处理回执。", 409)
            if conflict.head_revision != data.parent_revision:
                raise DomainError("sync_head_changed", "比较后又有新修订，请重新读取冲突。", 409)
            if data.choice == "merge_prose":
                payload, tombstone = (
                    self._merge_chapter_prose(tx, work, device, conflict, incoming, data),
                    False,
                )
            elif data.choice == "merge":
                payload, tombstone = data.payload, data.tombstone
            else:
                chosen = (
                    incoming if data.choice == "incoming" else self._change(tx, work, conflict.head_revision)
                )
                payload, tombstone = _receipt(chosen).payload, bool(chosen["tombstone"])
            _check_reserved(payload=payload)
            if self._private_payload(tx, work, payload, {}):
                raise DomainError("sync_workflow_reference_scope", REFERENCE_SCOPE, 409)
            change = Push(
                change_id=data.change_id,
                object_id=conflict.object_id,
                parent_revision=data.parent_revision,
                payload=payload,
                tombstone=tombstone,
            )
            formal = self._validate_formal(tx, work, change.object_id, payload, tombstone)
            self._validate_restore_state_adoption(tx, work, formal)
            receipt = self._append(
                tx,
                work,
                device,
                change,
                fingerprint,
                _payload(payload),
                data.change_id,
                "resolved",
                identifier,
            )
            tx.execute(
                "UPDATE sync_changes SET resolved_by=? WHERE work_id=? AND change_id=?",
                (receipt.change_id, work, identifier),
            )
            return receipt

    def _merge_chapter_prose(self, tx, work, device, conflict, incoming, data):
        if not conflict.object_id.startswith("chapter:"):
            raise DomainError("sync_chapter", "仅章节冲突可提交人工合并正文。", 409)
        current = _receipt(self._change(tx, work, conflict.head_revision))
        selected = verified_chapter(work, conflict.object_id, current.payload)
        other = verified_chapter(work, conflict.object_id, _receipt(incoming).payload)
        for candidate in (selected, other):
            source = self._head(tx, work, f"revision:{candidate.head.id}")
            if (
                not source
                or RevisionObject.model_validate_json(self._change(tx, work, source)["payload"]).revision
                != candidate.head
            ):
                raise DomainError("sync_history", "比较章稿尚未收到可核对的原始修订。", 409)
        stable = _digest(
            _encoded([work, conflict.object_id, current.change_id, data.change_id, data.content])
        )
        revision = selected.head.model_copy(
            update={
                "id": stable,
                "revision": selected.head.revision + 1,
                "parent_id": selected.head.id,
                "content": data.content,
                "sha256": _digest(data.content),
                "cause": "作者合并同步冲突",
                "created": time(),
            }
        )
        source = RevisionObject(
            format="ainovel-manuscript-sync/1", kind="revision", branch_id=work, revision=revision
        )
        push = Push(
            change_id=f"merge-source-{stable}",
            object_id=f"revision:{stable}",
            parent_revision=None,
            payload=source.model_dump(),
            tombstone=False,
        )
        fingerprint = _digest(_encoded(["push", device, push.model_dump()]))
        self._push_source_revision(tx, work, device, push, _payload(push.payload), fingerprint)
        return {
            **selected.model_dump(),
            "chapter": {**selected.chapter.model_dump(), "revision": revision.revision, "stale": 0},
            "head": revision.model_dump(),
        }


def create_sync_app(database_path: Path, provisioning_token: str, browser_origins=()):
    if len(provisioning_token) < 32 or any(char.isspace() for char in provisioning_token):
        raise ValueError("同步开通凭据须至少 32 字符，且不含空白。")
    administrator = _digest(provisioning_token)
    store = SyncStore(database_path)
    app = FastAPI(title="ainovel isolated work sync", version="1", docs_url=None, redoc_url=None)
    app.add_middleware(BoundedRequests)
    for origin in browser_origins:
        parts = urlsplit(origin)
        if (
            not parts.hostname
            or origin != f"{parts.scheme}://{parts.netloc}"
            or parts.username is not None
            or "*" in origin
            or not (
                parts.scheme == "https"
                or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        ):
            raise ValueError("浏览器同步来源须为明确的 HTTPS 或回环 HTTP origin。")
    if browser_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(browser_origins),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", cloud_ledger.LEDGER_HEADER],
        )
    prefix = "/api/sync/works/{work_id}"

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    def domain_error(request, error):
        return JSONResponse({"code": error.code, "message": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    def invalid_request(request, error):
        # FastAPI's default validation response can echo an uploaded manuscript or a credential.
        return JSONResponse(
            {"code": "sync_input", "message": "同步参数格式无效，请核对对象编号、版本和正文提交结构。"},
            status_code=422,
        )

    def admin(header):
        if not secrets.compare_digest(_credential(header), administrator):
            raise DomainError("sync_auth", "同步凭据无效。", 401)

    @app.get("/api/sync/health")
    def sync_health():
        store.check_readiness()
        return {"status": "ready", "format": "ainovel-sync/1"}

    @app.post(prefix + "/devices", status_code=201)
    def provision(work_id: Identifier, data: GrantInput, authorization: Authorization = "") -> Grant:
        admin(authorization)
        return store.grant(work_id, data)

    @app.delete(prefix + "/devices/{device_id}", status_code=204)
    def revoke(work_id: Identifier, device_id: Identifier, authorization: Authorization = ""):
        admin(authorization)
        store.revoke(work_id, device_id)
        return Response(status_code=204)

    @app.delete(prefix + "/device-session", status_code=204)
    def revoke_current(
        work_id: Identifier, device_id: Identifier | None = None, authorization: Authorization = ""
    ):
        store.revoke_current(work_id, _credential(authorization), device_id)
        return Response(status_code=204)

    @app.get(prefix + "/checkpoint")
    def checkpoint(work_id: Identifier, authorization: Authorization = "") -> Checkpoint:
        return store.checkpoint(work_id, _credential(authorization))

    @app.post(prefix + "/push")
    def push(
        work_id: Identifier,
        data: Push,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> Receipt:
        return store.push(work_id, _credential(authorization), data, ledger_id=ledger_id)

    @app.get(prefix + "/pull")
    def pull(
        work_id: Identifier,
        after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> Pulled:
        return store.pull(work_id, _credential(authorization), after, limit, ledger_id=ledger_id)

    @app.get(prefix + "/receipts/{change_id}")
    def receipt(
        work_id: Identifier,
        change_id: Identifier,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> Receipt:
        return store.receipt(work_id, _credential(authorization), change_id, ledger_id=ledger_id)

    @app.get(prefix + "/changes/{change_id}")
    def change(
        work_id: Identifier,
        change_id: Identifier,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> Receipt:
        return store.change(work_id, _credential(authorization), change_id, ledger_id=ledger_id)

    @app.get(prefix + "/conflicts")
    def conflicts(
        work_id: Identifier,
        after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> Conflicts:
        return store.conflicts(work_id, _credential(authorization), after, limit, ledger_id=ledger_id)

    @app.get(prefix + "/conflicts/{conflict_id}")
    def comparison(
        work_id: Identifier,
        conflict_id: Identifier,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> ComparedConflict:
        return store.comparison(work_id, _credential(authorization), conflict_id, ledger_id=ledger_id)

    @app.post(prefix + "/conflicts/{conflict_id}/resolve")
    def resolve(
        work_id: Identifier,
        conflict_id: Identifier,
        data: Resolution,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ) -> Receipt:
        return store.resolve(work_id, _credential(authorization), conflict_id, data, ledger_id=ledger_id)

    register_attachments(app, store, _credential)
    return app


def sync_app_from_env():
    """Uvicorn factory for the separate sync service."""
    path = os.environ.get("AINOVEL_SYNC_DB")
    token = os.environ.get("AINOVEL_SYNC_PROVISION_TOKEN")
    if not path or not token:
        raise ValueError("请先提供独立同步数据库路径和开通凭据。")
    origins = tuple(
        item.strip() for item in os.environ.get("AINOVEL_SYNC_BROWSER_ORIGINS", "").split(",") if item.strip()
    )
    return create_sync_app(Path(path), token, origins)
