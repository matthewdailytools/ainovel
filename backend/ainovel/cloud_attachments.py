"""Explicitly selected, resumable attachments in the isolated work sync service."""

from contextlib import contextmanager
from hashlib import sha256
from time import time
from typing import Annotated

from fastapi import Body, Header
from fastapi.responses import Response, StreamingResponse
from pydantic import Field, StringConstraints, field_validator
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from . import cloud_ledger
from .db import DomainError
from .models import Input

CHUNK_BYTES = 512 * 1024
FILE_BYTES = 64 * 1024 * 1024
WORK_BYTES = 256 * 1024 * 1024
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
WorkId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
Authorization = Annotated[str, Header(max_length=512)]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_attachments(
 work_id TEXT NOT NULL REFERENCES sync_works(id), sha256 TEXT NOT NULL, name TEXT NOT NULL,
 size INTEGER NOT NULL, created REAL NOT NULL, sealed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(work_id,sha256));
CREATE TABLE IF NOT EXISTS sync_attachment_parts(
 work_id TEXT NOT NULL, sha256 TEXT NOT NULL, slot INTEGER NOT NULL, data BLOB NOT NULL,
 PRIMARY KEY(work_id,sha256,slot),
 FOREIGN KEY(work_id,sha256) REFERENCES sync_attachments(work_id,sha256) ON DELETE CASCADE);
"""


class BeginUpload(Input):
    sha256: Digest
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    size: int = Field(ge=1, le=FILE_BYTES, strict=True)

    @field_validator("name")
    @classmethod
    def plain_filename(cls, name):
        if name in {".", ".."} or any(char in "/\\" or ord(char) < 32 for char in name):
            raise ValueError("附件名需要是普通文件名。")
        return name


def _parts(size):
    return (size + CHUNK_BYTES - 1) // CHUNK_BYTES


def _row(tx, work, digest):
    row = tx.execute("SELECT * FROM sync_attachments WHERE work_id=? AND sha256=?", (work, digest)).fetchone()
    if not row:
        raise DomainError("sync_attachment_missing", "当前作品没有这份附件。", 404)
    return row


def _file(row):
    return {key: row[key] for key in ("sha256", "name", "size", "created", "sealed")}


class AttachmentDownload(StreamingResponse):
    def __init__(self, snapshot):
        super().__init__(
            (),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="ainovel-attachment"',
                "X-Content-Type-Options": "nosniff",
            },
        )
        self.snapshot = snapshot

    async def __call__(self, scope, receive, send):
        # Open and check the snapshot before sending headers. Keep it alive until streaming ends or aborts.
        size, parts = await run_in_threadpool(self.snapshot.__enter__)
        try:
            self.headers["Content-Length"] = str(size)
            self.body_iterator = iterate_in_threadpool(parts)
            await super().__call__(scope, receive, send)
        finally:
            self.snapshot.__exit__(None, None, None)


def register_routes(app, store, credential):
    prefix = "/api/sync/works/{work_id}/attachments"

    def authorize(tx, work_id, authorization, ledger_id):
        store._authorize(tx, work_id, credential(authorization))
        cloud_ledger.verify_header(tx, ledger_id)

    @app.get(prefix)
    def attachments(
        work_id: WorkId, authorization: Authorization = "", ledger_id: cloud_ledger.LedgerHeader = None
    ):
        with store.transaction(snapshot=True) as tx:
            authorize(tx, work_id, authorization, ledger_id)
            return {
                "chunk_bytes": CHUNK_BYTES,
                "max_file_bytes": FILE_BYTES,
                "files": [
                    _file(row)
                    for row in tx.execute(
                        "SELECT * FROM sync_attachments WHERE work_id=? AND sealed=1 ORDER BY created DESC,sha256",
                        (work_id,),
                    )
                ],
            }

    @app.post(prefix)
    def begin(
        work_id: WorkId,
        data: BeginUpload,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ):
        with store.transaction(write=True) as tx:
            authorize(tx, work_id, authorization, ledger_id)
            current = tx.execute(
                "SELECT * FROM sync_attachments WHERE work_id=? AND sha256=?", (work_id, data.sha256)
            ).fetchone()
            if current is None:
                used = tx.execute(
                    "SELECT COALESCE(SUM(size),0),COUNT(*) FROM sync_attachments WHERE work_id=?", (work_id,)
                ).fetchone()
                if used[0] + data.size > WORK_BYTES or used[1] >= 256:
                    raise DomainError("sync_attachment_quota", "当前作品的云附件已达配额。", 413)
                tx.execute(
                    "INSERT INTO sync_attachments VALUES(?,?,?,?,?,0)",
                    (work_id, data.sha256, data.name, data.size, time()),
                )
                current = _row(tx, work_id, data.sha256)
            if current["size"] != data.size:
                raise DomainError("sync_attachment_size", "同一文件哈希的大小不一致。", 409)
            received = [
                row["slot"]
                for row in tx.execute(
                    "SELECT slot FROM sync_attachment_parts WHERE work_id=? AND sha256=? ORDER BY slot",
                    (work_id, data.sha256),
                )
            ]
            return {**_file(current), "received": received, "chunk_bytes": CHUNK_BYTES}

    @app.post(prefix + "/{digest}/parts/{slot}", status_code=204)
    def put_part(
        work_id: WorkId,
        digest: Digest,
        slot: int,
        data: Annotated[bytes, Body(media_type="application/octet-stream")],
        authorization: Authorization = "",
        content_type: Annotated[str, Header(max_length=128)] = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ):
        with store.transaction(write=True) as tx:
            authorize(tx, work_id, authorization, ledger_id)
            if content_type.split(";", 1)[0].strip().casefold() != "application/octet-stream":
                raise DomainError("sync_attachment_content_type", "附件分片须使用二进制正文。", 415)
            row = _row(tx, work_id, digest)
            if not 0 <= slot < _parts(row["size"]) or len(data) != min(
                CHUNK_BYTES, row["size"] - slot * CHUNK_BYTES
            ):
                raise DomainError("sync_attachment_part", "附件分片的大小或序号无效。")
            previous = tx.execute(
                "SELECT data FROM sync_attachment_parts WHERE work_id=? AND sha256=? AND slot=?",
                (work_id, digest, slot),
            ).fetchone()
            if previous:
                if previous["data"] != data:
                    raise DomainError(
                        "sync_attachment_part", "此分片已有不同内容，请取消未完成上传后重试。", 409
                    )
            elif not row["sealed"]:
                tx.execute("INSERT INTO sync_attachment_parts VALUES(?,?,?,?)", (work_id, digest, slot, data))
        return Response(status_code=204)

    @app.post(prefix + "/{digest}/seal")
    def seal(
        work_id: WorkId,
        digest: Digest,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ):
        with store.transaction(write=True) as tx:
            authorize(tx, work_id, authorization, ledger_id)
            row = _row(tx, work_id, digest)
            if not row["sealed"]:
                checksum = sha256()
                count = 0
                for part in tx.execute(
                    "SELECT data FROM sync_attachment_parts WHERE work_id=? AND sha256=? ORDER BY slot",
                    (work_id, digest),
                ):
                    checksum.update(part["data"])
                    count += 1
                if count != _parts(row["size"]) or checksum.hexdigest() != digest:
                    raise DomainError("sync_attachment_integrity", "附件分片不完整或文件哈希不匹配。", 409)
                tx.execute(
                    "UPDATE sync_attachments SET sealed=1 WHERE work_id=? AND sha256=?", (work_id, digest)
                )
                row = _row(tx, work_id, digest)
            return _file(row)

    @app.delete(prefix + "/{digest}", status_code=204)
    def cancel(
        work_id: WorkId,
        digest: Digest,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ):
        with store.transaction(write=True) as tx:
            authorize(tx, work_id, authorization, ledger_id)
            tx.execute(
                "DELETE FROM sync_attachments WHERE work_id=? AND sha256=? AND sealed=0", (work_id, digest)
            )
        return Response(status_code=204)

    @app.get(prefix + "/{digest}/content")
    def download(
        work_id: WorkId,
        digest: Digest,
        authorization: Authorization = "",
        ledger_id: cloud_ledger.LedgerHeader = None,
    ):
        @contextmanager
        def snapshot():
            with store.transaction(snapshot=True, streaming=True) as tx:
                authorize(tx, work_id, authorization, ledger_id)
                row = _row(tx, work_id, digest)
                if not row["sealed"]:
                    raise DomainError("sync_attachment_missing", "附件尚未通过完整性检查。", 404)
                parts = tx.execute(
                    "SELECT data FROM sync_attachment_parts WHERE work_id=? AND sha256=? ORDER BY slot",
                    (work_id, digest),
                )
                yield row["size"], (part["data"] for part in parts)

        return AttachmentDownload(snapshot())
