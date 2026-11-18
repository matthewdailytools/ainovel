"""Opt-in SQLite client for the portable manuscript sync protocol."""

from contextlib import contextmanager
from hashlib import sha256
from ipaddress import ip_address
import json
import re
from threading import Lock
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Body
import httpx
from pydantic import Field, SecretStr, TypeAdapter, ValidationError

from .cloud_sync import ComparedConflict, MAX_CURSOR, Pulled, Receipt
from .db import DomainError, dumps, uid
from .manuscript_exchange import PortableBranch, PortableChapter, PortableRevision, PortableWork
from .models import Input

FORMAT = "ainovel-manuscript-sync/1"
RESPONSE_LIMIT = 8 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_sync(
 branch_id TEXT PRIMARY KEY REFERENCES branches(id),
 origin_sha256 TEXT NOT NULL, enabled INTEGER NOT NULL, cursor INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS desktop_sync_objects(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 remote_revision TEXT NOT NULL, sequence INTEGER NOT NULL, local_sha256 TEXT NOT NULL,
 PRIMARY KEY(branch_id,object_id));
CREATE TABLE IF NOT EXISTS desktop_sync_outbox(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), change_id TEXT NOT NULL,
 object_id TEXT NOT NULL, request TEXT NOT NULL, local_sha256 TEXT NOT NULL,
 conflict_id TEXT, PRIMARY KEY(branch_id,change_id), UNIQUE(branch_id,object_id));
CREATE TABLE IF NOT EXISTS desktop_sync_conflicts(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), id TEXT NOT NULL,
 object_id TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(branch_id,id));
CREATE TABLE IF NOT EXISTS desktop_sync_deferred(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 receipt TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(branch_id,object_id));
"""


class Connection(Input):
    origin: str = Field(min_length=1, max_length=512)
    token: SecretStr


class Resolve(Connection):
    parent_revision: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    local_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    choice: Literal["current", "incoming", "local"]
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


class BranchObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["branch"]
    novel: PortableWork
    branch: PortableBranch


class ChapterObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["chapter"]
    chapter: PortableChapter
    head: PortableRevision


def _sha(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _digest(value):
    if value:
        # Scope the identity to author-visible fields and avoid echoing derived stale/counter updates.
        value = {**value}
        field, derived = ("branch", "story_revision") if value["kind"] == "branch" else ("chapter", "stale")
        value[field] = {key: content for key, content in value[field].items() if key != derived}
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _origin(value):
    try:
        uri = urlsplit(value)
        host = uri.hostname
        port = uri.port
    except ValueError as error:
        raise DomainError("sync_origin", "同步服务地址格式无效。") from error
    if (
        not host
        or uri.username is not None
        or uri.password is not None
        or uri.path not in {"", "/"}
        or uri.query
        or uri.fragment
        or any(char.isspace() or ord(char) < 32 for char in value)
        or ("%" in host or "\\" in value)
        or not (uri.scheme == "https" or (uri.scheme == "http" and host in {"127.0.0.1", "::1"}))
    ):
        raise DomainError("sync_origin", "请使用带证书的 HTTPS 服务或回环 HTTP 地址，不附加路径或凭据。")
    try:
        host = str(ip_address(host))
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
            raise DomainError("sync_origin", "同步服务主机名无效。") from None
    formatted = f"[{host}]" if ":" in host else host
    return f"{uri.scheme}://{formatted}" + (f":{port}" if port is not None else "")


def _network(origin, token, method, path, payload=None):
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
            with client.stream(
                method, origin + path, headers={"Authorization": f"Bearer {token}"}, json=payload
            ) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > RESPONSE_LIMIT:
                        raise DomainError("sync_response", "同步服务响应超过本机限制。", 502)
                    body.extend(chunk)
                result = json.loads(body)
                if not response.is_success:
                    code = result.get("code") if isinstance(result, dict) else None
                    raise DomainError(
                        code if code in {"sync_head_changed", "sync_conflict_resolved"} else "sync_remote",
                        f"同步服务拒绝此操作（HTTP {response.status_code}）。请核对设备凭据或刷新比较。",
                        409 if response.status_code == 409 else 502,
                    )
                return result
    except (httpx.HTTPError, ValueError, RecursionError) as error:
        raise DomainError("sync_transport", "同步未确认成功。本机操作仍可查询或重试。", 502) from error


def _parse(cls, value):
    try:
        data = cls.model_validate(value)
    except ValidationError as error:
        raise DomainError("sync_input", "请核对同步参数和设备凭据。") from error
    token = data.token.get_secret_value()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise DomainError("sync_auth", "请填写该作品的设备凭据。")
    return data, _origin(data.origin), token


def _local(tx, branch_id, object_id):
    if object_id == f"branch:{branch_id}":
        branch = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
        novel = tx.execute("SELECT * FROM novels WHERE id=?", (branch["novel_id"],)).fetchone()
        return BranchObject(format=FORMAT, kind="branch", novel=dict(novel), branch=dict(branch)).model_dump()
    if not object_id.startswith("chapter:"):
        return None
    identifier = object_id.removeprefix("chapter:")
    chapter = tx.execute(
        "SELECT id,branch_id,title,position,revision,archived,stale FROM chapters WHERE branch_id=? AND id=?",
        (branch_id, identifier),
    ).fetchone()
    if not chapter or chapter["archived"]:
        return None
    row = tx.execute(
        "SELECT r.*, (SELECT p.id FROM revisions p WHERE p.chapter_id=r.chapter_id AND p.revision=r.revision-1) AS parent_id "
        "FROM revisions r WHERE r.chapter_id=? AND r.revision=?",
        (identifier, chapter["revision"]),
    ).fetchone()
    if not row:
        raise DomainError("sync_history", "当前章稿无法核对修订历史。", 409)
    return ChapterObject(
        format=FORMAT,
        kind="chapter",
        chapter=dict(chapter),
        head={**dict(row), "sha256": _sha(row["content"])},
    ).model_dump()


def _shadow(tx, branch_id, object_id):
    return tx.execute(
        "SELECT * FROM desktop_sync_objects WHERE branch_id=? AND object_id=?", (branch_id, object_id)
    ).fetchone()


def _remember(tx, branch_id, event, local_digest):
    tx.execute(
        "INSERT INTO desktop_sync_objects VALUES(?,?,?,?,?) ON CONFLICT(branch_id,object_id) DO UPDATE SET "
        "remote_revision=excluded.remote_revision,sequence=excluded.sequence,local_sha256=excluded.local_sha256 "
        "WHERE excluded.sequence>=desktop_sync_objects.sequence",
        (branch_id, event.object_id, event.head_revision, event.sequence, local_digest),
    )


def _apply_chapter(tx, domain, branch_id, event):
    chapter_id = event.object_id.removeprefix("chapter:")
    row = tx.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if row and row["branch_id"] != branch_id:
        raise DomainError("sync_scope", "远端章节编号已经用于其他工作版本。", 409)
    if event.tombstone:
        if row and not row["archived"]:
            tx.execute("UPDATE chapters SET archived=1 WHERE id=?", (chapter_id,))
            tx.execute("UPDATE facts SET stale=1 WHERE chapter_id=?", (chapter_id,))
            tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        return
    try:
        data = ChapterObject.model_validate(event.payload)
    except ValidationError as error:
        raise DomainError("sync_format", "收到无法读取的同步章稿。", 409) from error
    info, head = data.chapter, data.head
    if (
        info.id != chapter_id
        or info.branch_id != branch_id
        or info.archived
        or head.chapter_id != chapter_id
        or head.revision != info.revision
        or head.sha256 != _sha(head.content)
    ):
        raise DomainError("sync_scope", "远端章稿的引用或校验值和当前工作版本不一致。", 409)
    collision = tx.execute(
        "SELECT id FROM chapters WHERE branch_id=? AND position=? AND archived=0 AND id<>?",
        (branch_id, info.position, chapter_id),
    ).fetchone()
    if collision:
        raise DomainError("sync_order", "远端章位与本机另一章冲突。调整章序后可重新拉取。", 409)
    if not row:
        identifier = (
            head.id if not tx.execute("SELECT 1 FROM revisions WHERE id=?", (head.id,)).fetchone() else uid()
        )
        tx.execute(
            "INSERT INTO chapters VALUES(?,?,?,?,?,?,0,0)",
            (chapter_id, branch_id, info.position, info.title, 0, head.content),
        )
        tx.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
            (identifier, chapter_id, 0, head.content, f"云同步初始稿，来源修订 {head.id}", head.created),
        )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        return
    current = _local(tx, branch_id, event.object_id)
    if row["content"] != head.content or (current and current["head"]["id"] != head.id):
        domain.revise_tx(tx, dict(row), head.content, f"云同步，来源修订 {head.id}")
    tx.execute(
        "UPDATE chapters SET position=?,title=?,archived=0 WHERE id=?",
        (info.position, info.title, chapter_id),
    )
    tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))


def _apply(tx, domain, branch_id, event):
    if event.object_id.startswith("chapter:"):
        _apply_chapter(tx, domain, branch_id, event)
        return
    if event.object_id != f"branch:{branch_id}" or event.tombstone:
        raise DomainError("sync_scope", "同步对象超出当前文稿范围，需要人工检查。", 409)
    try:
        data = BranchObject.model_validate(event.payload)
    except ValidationError as error:
        raise DomainError("sync_format", "收到无法读取的工作版本资料。", 409) from error
    local = _local(tx, branch_id, event.object_id)
    if (
        data.branch.id != branch_id
        or data.novel.id != local["novel"]["id"]
        or data.branch.novel_id != data.novel.id
    ):
        raise DomainError("sync_scope", "远端作品和本机工作版本的标识不一致。", 409)
    tx.execute(
        "UPDATE novels SET title=?,brief=? WHERE id=?", (data.novel.title, data.novel.brief, data.novel.id)
    )
    tx.execute(
        "UPDATE branches SET name=?,mode=?,story_revision=story_revision+1 WHERE id=?",
        (data.branch.name, data.branch.mode, branch_id),
    )


def _verified_digest(event):
    if event.tombstone and event.payload is None and event.object_id.startswith("chapter:"):
        return _digest(None)
    schema = ChapterObject if event.object_id.startswith("chapter:") else BranchObject
    try:
        payload = schema.model_validate(event.payload).model_dump()
    except ValidationError as error:
        raise DomainError("sync_format", "同步对象格式不支持当前文稿。", 409) from error
    if event.tombstone:
        raise DomainError("sync_format", "同步对象和删除标记不一致。", 409)
    return _digest(payload)


class DesktopSync:
    def __init__(self, domain, transport=_network):
        self.domain = domain
        self.transport = transport
        self.operations = Lock()
        with domain.db.connect() as tx:
            tx.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")

    @contextmanager
    def _operation(self):
        if not self.operations.acquire(blocking=False):
            raise DomainError("sync_busy", "有同步正在运行，请稍后重试。", 409)
        try:
            yield
        finally:
            self.operations.release()

    def _state(self, branch_id):
        self.domain.branch(branch_id)
        with self.domain.db.connect() as tx:
            tx.execute("BEGIN")
            row = tx.execute("SELECT * FROM desktop_sync WHERE branch_id=?", (branch_id,)).fetchone()
            pending = tx.execute(
                "SELECT count(*) FROM desktop_sync_outbox WHERE branch_id=?", (branch_id,)
            ).fetchone()[0]
            return {
                "enabled": bool(row and row["enabled"]),
                "work_id": branch_id,
                "cursor": row["cursor"] if row else 0,
                "pending": pending,
                "conflicts": [
                    {"id": item["id"], "object_id": item["object_id"]}
                    for item in tx.execute(
                        "SELECT * FROM desktop_sync_conflicts WHERE branch_id=?", (branch_id,)
                    )
                ],
                "deferred": [
                    {"object_id": item["object_id"], "reason": item["reason"]}
                    for item in tx.execute(
                        "SELECT * FROM desktop_sync_deferred WHERE branch_id=?", (branch_id,)
                    )
                ],
            }

    def _bound(self, branch_id, origin):
        with self.domain.db.connect() as tx:
            row = tx.execute(
                "SELECT * FROM desktop_sync WHERE branch_id=? AND enabled=1", (branch_id,)
            ).fetchone()
            if not row or row["origin_sha256"] != _sha(origin):
                raise DomainError("sync_binding", "请先为此版本启用对应的同步地址。", 409)
            return row["cursor"]

    def _call(self, branch_id, origin, token, path, schema, *, method="GET", data=None):
        raw = self.transport(origin, token, method, f"/api/sync/works/{branch_id}{path}", data)
        try:
            return TypeAdapter(schema).validate_python(raw)
        except ValidationError as error:
            raise DomainError("sync_response", "同步服务返回了无法核验的回执。", 502) from error

    def enable(self, branch_id, origin, token):
        with self._operation():
            self.domain.branch(branch_id)
            self._call(branch_id, origin, token, "/pull?after=0&limit=1", Pulled)
            with self.domain.db.connect(True) as tx:
                row = tx.execute(
                    "SELECT origin_sha256 FROM desktop_sync WHERE branch_id=?", (branch_id,)
                ).fetchone()
                if row and row["origin_sha256"] != _sha(origin):
                    raise DomainError(
                        "sync_binding", "此版本已有另一同步历史，请使用独立工作版本连接新服务。", 409
                    )
                tx.execute(
                    "INSERT INTO desktop_sync(branch_id,origin_sha256,enabled) VALUES(?,?,1) "
                    "ON CONFLICT(branch_id) DO UPDATE SET enabled=1",
                    (branch_id, _sha(origin)),
                )
            return self._state(branch_id)

    def disable(self, branch_id):
        with self._operation():
            self.domain.branch(branch_id)
            with self.domain.db.connect(True) as tx:
                tx.execute("UPDATE desktop_sync SET enabled=0 WHERE branch_id=?", (branch_id,))
            return self._state(branch_id)

    def _defer(self, tx, branch_id, event, reason):
        tx.execute(
            "INSERT INTO desktop_sync_deferred VALUES(?,?,?,?) ON CONFLICT(branch_id,object_id) DO UPDATE SET "
            "receipt=excluded.receipt,reason=excluded.reason",
            (branch_id, event.object_id, event.model_dump_json(), reason),
        )

    def _consume(self, tx, branch_id, incoming, queued=None):
        object_id = incoming.object_id
        if incoming.status == "conflict":
            tx.execute(
                "INSERT OR IGNORE INTO desktop_sync_conflicts VALUES(?,?,?,?)",
                (branch_id, incoming.change_id, object_id, incoming.model_dump_json()),
            )
            return
        if incoming.status == "resolved":
            tx.execute(
                "DELETE FROM desktop_sync_conflicts WHERE branch_id=? AND id=?",
                (branch_id, incoming.conflict_id),
            )
        known = _shadow(tx, branch_id, object_id)
        tx.execute(
            "DELETE FROM desktop_sync_deferred WHERE branch_id=? AND object_id=?", (branch_id, object_id)
        )
        if known and known["sequence"] >= incoming.sequence:
            return
        local = _local(tx, branch_id, object_id)
        local_digest = _digest(local)
        if queued is not None and queued["conflict_id"] is None:
            _remember(tx, branch_id, incoming, queued["local_sha256"])
            return
        try:
            identical = local_digest == _verified_digest(incoming)
        except DomainError as error:
            self._defer(tx, branch_id, incoming, error.message)
            return
        clean = local is None if known is None else known["local_sha256"] == local_digest
        if queued is not None and queued["conflict_id"] is not None:
            clean = queued["local_sha256"] == local_digest
        if not identical and not clean:
            self._defer(tx, branch_id, incoming, "本机已有未同步修改，待与远端版本比较。")
            return
        if not identical:
            try:
                _apply(tx, self.domain, branch_id, incoming)
            except DomainError as error:
                self._defer(tx, branch_id, incoming, error.message)
                return
        _remember(tx, branch_id, incoming, _digest(_local(tx, branch_id, object_id)))

    def _pull(self, branch_id, origin, token):
        for _ in range(8):
            cursor = self._bound(branch_id, origin)
            page = self._call(branch_id, origin, token, f"/pull?after={cursor}&limit=50", Pulled)
            sequences = [row.sequence for row in page.changes]
            if (
                not 0 <= page.cursor <= MAX_CURSOR
                or sequences != list(range(cursor + 1, cursor + 1 + len(sequences)))
                or (
                    page.cursor != (sequences[-1] if sequences else cursor)
                    or (page.has_more and not sequences)
                )
            ):
                raise DomainError("sync_cursor", "同步游标与响应事件不连续，保留本机游标。", 502)
            with self.domain.db.connect(True) as tx:
                for row in page.changes:
                    queued = tx.execute(
                        "SELECT * FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                        (branch_id, row.change_id),
                    ).fetchone()
                    self._consume(tx, branch_id, row, queued)
                    if queued:
                        tx.execute(
                            "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                            (branch_id, row.change_id),
                        )
                tx.execute("UPDATE desktop_sync SET cursor=? WHERE branch_id=?", (page.cursor, branch_id))
            if not page.has_more:
                break

    def _prepare(self, branch_id):
        with self.domain.db.connect(True) as tx:
            for row in list(
                tx.execute("SELECT receipt FROM desktop_sync_deferred WHERE branch_id=?", (branch_id,))
            ):
                self._consume(tx, branch_id, Receipt.model_validate_json(row["receipt"]))
            object_ids = [f"branch:{branch_id}"] + [
                f"chapter:{row['id']}"
                for row in tx.execute(
                    "SELECT id FROM chapters WHERE branch_id=? ORDER BY position,id", (branch_id,)
                )
            ]
            for object_id in object_ids:
                if tx.execute(
                    "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND object_id=? UNION ALL "
                    "SELECT 1 FROM desktop_sync_conflicts WHERE branch_id=? AND object_id=? LIMIT 1",
                    (branch_id, object_id, branch_id, object_id),
                ).fetchone():
                    continue
                value = _local(tx, branch_id, object_id)
                known = _shadow(tx, branch_id, object_id)
                current = _digest(value)
                if (value is None and not known) or (known and current == known["local_sha256"]):
                    continue
                change = uid()
                request = {
                    "change_id": change,
                    "object_id": object_id,
                    "parent_revision": known["remote_revision"] if known else None,
                    "payload": value,
                    "tombstone": value is None,
                }
                tx.execute(
                    "INSERT INTO desktop_sync_outbox VALUES(?,?,?,?,?,NULL)",
                    (branch_id, change, object_id, dumps(request), current),
                )

    def _push(self, branch_id, origin, token):
        with self.domain.db.connect() as tx:
            todo = [
                dict(row)
                for row in tx.execute(
                    "SELECT * FROM desktop_sync_outbox WHERE branch_id=? ORDER BY rowid LIMIT 100",
                    (branch_id,),
                )
            ]
        for row in todo:
            path = f"/conflicts/{row['conflict_id']}/resolve" if row["conflict_id"] else "/push"
            try:
                result = self._call(
                    branch_id, origin, token, path, Receipt, method="POST", data=json.loads(row["request"])
                )
            except DomainError as error:
                if row["conflict_id"] and error.code in {"sync_head_changed", "sync_conflict_resolved"}:
                    with self.domain.db.connect(True) as tx:
                        tx.execute(
                            "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                            (branch_id, row["change_id"]),
                        )
                raise
            if result.change_id != row["change_id"] or result.object_id != row["object_id"]:
                raise DomainError("sync_response", "同步回执和本机准备提交的对象不一致。", 502)
            with self.domain.db.connect(True) as tx:
                self._consume(tx, branch_id, result, row)
                tx.execute(
                    "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                    (branch_id, result.change_id),
                )

    def run(self, branch_id, origin, token):
        with self._operation():
            self._bound(branch_id, origin)
            self._pull(branch_id, origin, token)
            self._prepare(branch_id)
            self._push(branch_id, origin, token)
            self._pull(branch_id, origin, token)
            return self._state(branch_id)

    def compare(self, branch_id, origin, token, identifier):
        self._bound(branch_id, origin)
        remote = self._call(branch_id, origin, token, f"/conflicts/{identifier}", ComparedConflict)
        with self.domain.db.connect() as tx:
            tx.execute("BEGIN")
            local = _local(tx, branch_id, remote.object_id)
            return {"remote": remote.model_dump(), "local": local, "local_sha256": _digest(local)}

    def resolve(self, branch_id, origin, token, identifier, data):
        with self._operation():
            self._bound(branch_id, origin)
            remote = self._call(branch_id, origin, token, f"/conflicts/{identifier}", ComparedConflict)
            with self.domain.db.connect(True) as tx:
                existing = tx.execute(
                    "SELECT * FROM desktop_sync_outbox WHERE branch_id=? AND object_id=?",
                    (branch_id, remote.object_id),
                ).fetchone()
                if existing:
                    if existing["conflict_id"] != identifier or existing["change_id"] != data.idempotency_key:
                        raise DomainError("sync_pending", "该对象有待确认的提交，请先运行同步。", 409)
                else:
                    local = _local(tx, branch_id, remote.object_id)
                    if (
                        data.local_sha256 != _digest(local)
                        or data.parent_revision != remote.head_revision
                        or remote.resolved_by
                    ):
                        raise DomainError("sync_compare", "本机或云端内容已变化，请重新比较。", 409)
                    request = {
                        "change_id": data.idempotency_key,
                        "parent_revision": data.parent_revision,
                        "choice": "merge" if data.choice == "local" else data.choice,
                    }
                    if data.choice == "local":
                        request.update(payload=local, tombstone=local is None)
                    tx.execute(
                        "INSERT INTO desktop_sync_outbox VALUES(?,?,?,?,?,?)",
                        (
                            branch_id,
                            data.idempotency_key,
                            remote.object_id,
                            dumps(request),
                            data.local_sha256,
                            identifier,
                        ),
                    )
            self._push(branch_id, origin, token)
            self._pull(branch_id, origin, token)
            return self._state(branch_id)


def register_routes(app, domain):
    client = DesktopSync(domain) if domain is not None else None
    prefix = "/api/branches/{branch_id}/desktop-sync"

    @app.get(prefix)
    def sync_status(branch_id: str):
        return client._state(branch_id)

    @app.post(prefix + "/enable")
    def enable_sync(branch_id: str, data: dict = Body()):
        _, origin, token = _parse(Connection, data)
        return client.enable(branch_id, origin, token)

    @app.post(prefix + "/disable")
    def disable_sync(branch_id: str):
        return client.disable(branch_id)

    @app.post(prefix + "/run")
    def run_sync(branch_id: str, data: dict = Body()):
        _, origin, token = _parse(Connection, data)
        return client.run(branch_id, origin, token)

    @app.post(prefix + "/conflicts/{conflict_id}/compare")
    def compare_sync(branch_id: str, conflict_id: str, data: dict = Body()):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", conflict_id):
            raise DomainError("sync_conflict", "同步冲突编号无效。")
        _, origin, token = _parse(Connection, data)
        return client.compare(branch_id, origin, token, conflict_id)

    @app.post(prefix + "/conflicts/{conflict_id}/resolve")
    def resolve_sync(branch_id: str, conflict_id: str, data: dict = Body()):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", conflict_id):
            raise DomainError("sync_conflict", "同步冲突编号无效。")
        request, origin, token = _parse(Resolve, data)
        return client.resolve(branch_id, origin, token, conflict_id, request)
