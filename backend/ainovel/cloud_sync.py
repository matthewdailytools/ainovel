"""Isolated SQLite sync service for explicitly provisioned works and devices."""

from contextlib import contextmanager
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

from .db import DomainError
from .chapter_conflicts import verified_chapter
from .cloud_attachments import SCHEMA as ATTACHMENT_SCHEMA, register_routes as register_attachments
from .manuscript_exchange import RevisionObject, _source_link
from .outline_sync import OutlineVersionObject, parse_outline_object
from .outline_sync import validate_version_sources as validate_outline_sources
from .models import Input
from .project_sync import ProjectVersionObject, parse_project_object
from .reference_portability import ReferenceCardVersionObject
from .reference_sync import parse_reference_object, validate_version_parent as validate_reference_parent
from .settings_sync import SettingsVersionObject, parse_settings_object, validate_version_sources
from .story_sync import StoryVersionObject, parse_story_object
from .story_sync import validate_version_sources as validate_story_sources
from .workflow_archive import (
    ArchivedInspection,
    WORKFLOW_PREFIXES,
    parse_workflow_object,
    same_workflow_entry,
    validate_workflow_object,
)

MAX_OBJECT_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = MAX_OBJECT_BYTES * 2
MAX_PULL_BYTES = MAX_OBJECT_BYTES * 4
MAX_CURSOR = (1 << 53) - 1
APP_ID = 0x414E5359
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
Authorization = Annotated[str, Header(max_length=512)]
Payload = dict[str, JsonValue]

SCHEMA = """
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


class SyncStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as tx:
            identifier = tx.execute("PRAGMA application_id").fetchone()[0]
            version = tx.execute("PRAGMA user_version").fetchone()[0]
            if identifier not in (0, APP_ID) or version > 1:
                raise DomainError("sync_schema", "同步数据库格式无法读取。")
            if (
                identifier == 0
                and tx.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise DomainError("sync_schema", "请选择独立的同步数据库。")
            tx.execute("PRAGMA journal_mode=WAL")
            tx.executescript(
                f"BEGIN IMMEDIATE;\n{SCHEMA}\n{ATTACHMENT_SCHEMA}\n"
                f"PRAGMA application_id={APP_ID};\nPRAGMA user_version=1;\nCOMMIT;"
            )

    @contextmanager
    def transaction(self, *, write=False, snapshot=False):
        tx = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        tx.row_factory = sqlite3.Row
        tx.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                tx.execute("BEGIN IMMEDIATE")
            elif snapshot:
                tx.execute("BEGIN")
            yield tx
            if write:
                tx.commit()
        finally:
            tx.close()

    def grant(self, work, data):
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
        return _receipt(row) if row else None

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

    def push(self, work, digest, data):
        with self.transaction(write=True) as tx:
            device = self._authorize(tx, work, digest)
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
        family = object_id.partition(":")[0].removesuffix("-version")
        contracts = {
            "project": ("立项", ProjectVersionObject, parse_project_object),
            "settings": ("设定", SettingsVersionObject, parse_settings_object),
            "outline": ("大纲", OutlineVersionObject, parse_outline_object),
            "story": ("故事记录", StoryVersionObject, parse_story_object),
            "reference-card": ("手法卡", ReferenceCardVersionObject, parse_reference_object),
        }
        if family not in contracts:
            return None
        title, immutable, parse = contracts[family]
        value = parse(work, object_id, payload, tombstone)
        error_code = "sync_reference" if family == "reference-card" else f"sync_{family}"

        def source(object_id, model):
            head = self._head(tx, work, object_id)
            return model.model_validate_json(self._change(tx, work, head)["payload"]) if head else None

        if not isinstance(value, immutable):
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

            def settings(identifier):
                stored = source(f"settings-version:{identifier}", SettingsVersionObject)
                return stored.version if stored else None

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

    def _push_workflow(self, tx, work, device, data, payload, fingerprint):
        entry = parse_workflow_object(work, data.object_id, data.payload, data.tombstone)

        def source(object_id):
            head = self._head(tx, work, object_id)
            return self._change(tx, work, head) if head else None

        def inspection(identifier):
            object_id = f"workflow-inspection:{identifier}"
            if row := source(object_id):
                return parse_workflow_object(work, object_id, json.loads(row["payload"]), row["tombstone"])
            return None

        def revision(identifier):
            if row := source(f"revision:{identifier}"):
                return RevisionObject.model_validate_json(row["payload"]).revision
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
            validate_workflow_object(entry, work, revision, inspection)
        except DomainError as error:
            if error.code in {"manuscript_workflow", "completion_integrity"}:
                raise DomainError("sync_workflow", error.message, error.status) from error
            raise
        return self._append(tx, work, device, data, fingerprint, payload, data.change_id, "applied")

    def receipt(self, work, digest, change_id):
        with self.transaction(snapshot=True) as tx:
            device = self._authorize(tx, work, digest)
            row = self._change(tx, work, change_id)
            if not row or row["device_id"] != device:
                raise DomainError("sync_receipt", "该设备没有对应回执。", 404)
            return _receipt(row)

    def change(self, work, digest, change_id):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            row = self._change(tx, work, change_id)
            if not row:
                raise DomainError("sync_change", "找不到所选变更。", 404)
            return _receipt(row)

    def pull(self, work, digest, after, limit):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            bound = tx.execute("SELECT sequence FROM sync_works WHERE id=?", (work,)).fetchone()[0]
            if after > bound:
                raise DomainError("sync_cursor", "拉取游标超出当前作品的变更记录。", 409)
            changes = []
            size = 0
            for row in tx.execute(
                "SELECT * FROM sync_changes WHERE work_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (work, after, limit),
            ):
                size += len((row["payload"] or "").encode("utf-8"))
                if changes and size > MAX_PULL_BYTES:
                    break
                changes.append(_receipt(row))
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

    def conflicts(self, work, digest, after, limit):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            rows = list(
                tx.execute(
                    "SELECT * FROM sync_changes WHERE work_id=? AND sequence>? AND status='conflict' AND resolved_by IS NULL "
                    "ORDER BY sequence LIMIT ?",
                    (work, after, limit + 1),
                )
            )
            result = [self._conflict(tx, work, row) for row in rows[:limit]]
            cursor = result[-1].opened_sequence if result else after
            return Conflicts(conflicts=result, cursor=cursor, has_more=len(rows) > limit)

    def comparison(self, work, digest, identifier):
        with self.transaction(snapshot=True) as tx:
            self._authorize(tx, work, digest)
            row = self._change(tx, work, identifier)
            summary = self._conflict(tx, work, row)
            head = self._change(tx, work, summary.head_revision)
            base = self._change(tx, work, summary.parent_revision) if summary.parent_revision else None
            return ComparedConflict(
                **summary.model_dump(),
                incoming=_receipt(row),
                current=_receipt(head),
                base=_receipt(base) if base else None,
            )

    def resolve(self, work, digest, identifier, data):
        with self.transaction(write=True) as tx:
            device = self._authorize(tx, work, digest)
            request = data.model_dump(exclude={"content"} if data.choice != "merge_prose" else None)
            fingerprint = _digest(_encoded(["resolve", device, identifier, request]))
            if replay := self._replay(tx, work, data.change_id, fingerprint):
                return replay
            incoming = self._change(tx, work, identifier)
            conflict = self._conflict(tx, work, incoming)
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
            change = Push(
                change_id=data.change_id,
                object_id=conflict.object_id,
                parent_revision=data.parent_revision,
                payload=payload,
                tombstone=tombstone,
            )
            self._validate_formal(tx, work, change.object_id, payload, tombstone)
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
            allow_headers=["Authorization", "Content-Type"],
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

    @app.post(prefix + "/push")
    def push(work_id: Identifier, data: Push, authorization: Authorization = "") -> Receipt:
        return store.push(work_id, _credential(authorization), data)

    @app.get(prefix + "/pull")
    def pull(
        work_id: Identifier,
        after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        authorization: Authorization = "",
    ) -> Pulled:
        return store.pull(work_id, _credential(authorization), after, limit)

    @app.get(prefix + "/receipts/{change_id}")
    def receipt(work_id: Identifier, change_id: Identifier, authorization: Authorization = "") -> Receipt:
        return store.receipt(work_id, _credential(authorization), change_id)

    @app.get(prefix + "/changes/{change_id}")
    def change(work_id: Identifier, change_id: Identifier, authorization: Authorization = "") -> Receipt:
        return store.change(work_id, _credential(authorization), change_id)

    @app.get(prefix + "/conflicts")
    def conflicts(
        work_id: Identifier,
        after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        authorization: Authorization = "",
    ) -> Conflicts:
        return store.conflicts(work_id, _credential(authorization), after, limit)

    @app.get(prefix + "/conflicts/{conflict_id}")
    def comparison(
        work_id: Identifier,
        conflict_id: Identifier,
        authorization: Authorization = "",
    ) -> ComparedConflict:
        return store.comparison(work_id, _credential(authorization), conflict_id)

    @app.post(prefix + "/conflicts/{conflict_id}/resolve")
    def resolve(
        work_id: Identifier,
        conflict_id: Identifier,
        data: Resolution,
        authorization: Authorization = "",
    ) -> Receipt:
        return store.resolve(work_id, _credential(authorization), conflict_id, data)

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
