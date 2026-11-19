"""Framed, checksummed manuscript backups shared with the offline Web workspace."""

from hashlib import sha256
import json
import re
from tempfile import TemporaryFile

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .db import DomainError
from .manuscript_exchange import ManuscriptPackage, ReferenceSelection, export_manuscript, import_manuscript
from .workflow_archive import COMPLETION

BACKUP_IMPORT_PATH = "/api/manuscript-backups/import"
BACKUP_MEDIA_TYPE = "application/x-ainovel-web-backup"
MAX_BACKUP_BYTES = 128 * 1024 * 1024
MAX_FRAMES = 250_000
TRANSFER_CHUNK_BYTES = 256 * 1024
TITLE = b"ainovel-web-backup/1\n"
SEAL = b"sha256 "
DIGEST = re.compile(rb"[a-f0-9]{64}")

ROOTS = frozenset(("format", "novel", "branch", "chapters", "revisions", "source_revisions", "evidence"))
HISTORIES = {
    "project_history": ("adopted_revision_id", "versions"),
    "settings_history": ("adopted_revision_id", "versions", "source_devices"),
    "outline_history": ("adopted_revision_id", "versions", "source_devices", "required_story_records"),
    "workflow_history": ("inspections", "completions"),
    "story_history": ("adopted_heads", "versions", "source_devices", "native_history"),
    "reference_history": ("adopted_heads", "versions", "source_devices", "excerpts"),
    "setting_use_history": ("adopted_heads", "versions", "native_bindings"),
    "snapshot_history": ("entries",),
}


def _invalid():
    return DomainError("manuscript_backup", "独立备份的格式、分段或校验值无效，未导入作品。")


def size_error():
    return DomainError("file_limit", "独立作品备份超过 128 MiB，已保存的作品和历史仍可使用。", 413)


def _frame_limit():
    return DomainError("file_limit", "独立作品备份记录超过 250,000 项，已保存的作品和历史仍可使用。", 413)


def _check_saved_text_size(tx, branch_id):
    # The UTF-8 text also appears in the output frames. Reject its lower bound before loading all revisions.
    size = tx.execute(
        """
        WITH deliveries(payload) AS (
            SELECT payload FROM artifacts
             WHERE branch_id=:branch AND kind=:completion AND status='accepted'
            UNION ALL
            SELECT h.payload FROM manuscript_workflow_history h
             WHERE h.branch_id=:branch AND h.kind=:completion AND NOT EXISTS (
                SELECT 1 FROM artifacts a WHERE a.id=h.id AND a.branch_id=h.branch_id
                 AND a.kind=h.kind AND a.status='accepted'
             )
        )
        SELECT (
            SELECT total(length(CAST(r.content AS BLOB))) FROM revisions r
             JOIN chapters c ON c.id=r.chapter_id WHERE c.branch_id=:branch
        ) + (
            SELECT total(length(CAST(json_extract(payload,'$.content') AS BLOB)))
             FROM manuscript_revision_sources WHERE branch_id=:branch
        ) + (
            SELECT total(length(CAST(m.value AS BLOB)))
             FROM deliveries d, json_each(d.payload,'$.materials') m WHERE m.type='text'
        )
        """,
        {"branch": branch_id, "completion": COMPLETION},
    ).fetchone()[0]
    if size > MAX_BACKUP_BYTES:
        raise size_error()


def _frames(pack):
    for name, value in pack.items():
        if name in HISTORIES and value is None:
            continue
        fields = (
            ((f"{name}.{key}", item) for key, item in value.items())
            if name in HISTORIES
            else ((name, value),)
        )
        for path, item in fields:
            if not isinstance(item, list):
                yield path, "value", item
                continue
            yield path, "list", None
            yield from ((path, "item", record) for record in item)


def _backup_chunks(package):
    yield TITLE
    seal = sha256()
    for index, frame in enumerate(_frames(package.model_dump(mode="json"))):
        if index >= MAX_FRAMES:
            raise _frame_limit()
        raw = json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        digest = sha256(raw).hexdigest().encode("ascii")
        seal.update(digest)
        yield digest + b" " + raw + b"\n"
    yield SEAL + seal.hexdigest().encode("ascii") + b"\n"


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON number")


class BackupReader:
    def __init__(self):
        self.pack = {}
        self.pending = bytearray()
        self.size = 0
        self.count = 0
        self.opened = False
        self.sealed = False
        self.seal = sha256()

    def feed(self, chunk):
        self.size += len(chunk)
        if self.size > MAX_BACKUP_BYTES:
            raise size_error()
        cursor = 0
        while cursor < len(chunk):
            end = chunk.find(b"\n", cursor)
            if end == -1:
                self.pending.extend(chunk[cursor:])
                break
            if self.pending:
                self.pending.extend(chunk[cursor:end])
                line = self.pending
                self.pending = bytearray()
            else:
                line = chunk[cursor:end]
            self._line(line)
            cursor = end + 1

    def _line(self, line):
        if not self.opened:
            if line != TITLE[:-1]:
                raise _invalid()
            self.opened = True
            return
        if self.sealed:
            raise _invalid()
        if line.startswith(SEAL):
            if line[len(SEAL) :] != self.seal.hexdigest().encode("ascii"):
                raise _invalid()
            self.sealed = True
            return
        if self.count >= MAX_FRAMES:
            raise _frame_limit()
        digest, space, payload = line.partition(b" ")
        if not space or not DIGEST.fullmatch(digest) or digest != sha256(payload).hexdigest().encode("ascii"):
            raise _invalid()
        try:
            frame = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_invalid_constant
            )
        except ValueError, RecursionError:
            raise _invalid() from None
        self._record(frame)
        self.count += 1
        self.seal.update(digest)

    def _record(self, frame):
        if not isinstance(frame, list) or len(frame) != 3 or not isinstance(frame[0], str):
            raise _invalid()
        path, kind, value = frame
        name, separator, field = path.partition(".")
        if not separator and name in ROOTS:
            target, key = self.pack, name
        elif field in HISTORIES.get(name, ()):
            target, key = self.pack.setdefault(name, {}), field
        else:
            raise _invalid()
        if kind == "item" and isinstance(target.get(key), list):
            target[key].append(value)
        elif key not in target and kind == "list" and value is None:
            target[key] = []
        elif key not in target and kind == "value" and not isinstance(value, list):
            target[key] = value
        else:
            raise _invalid()

    def package(self):
        if not self.sealed or self.pending:
            raise _invalid()
        try:
            return ManuscriptPackage.model_validate(self.pack)
        except ValidationError:
            raise _invalid() from None


class _BackupDownload(StreamingResponse):
    def __init__(self, file, size):
        self.file = file
        super().__init__(
            iter(lambda: file.read(TRANSFER_CHUNK_BYTES), b""),
            media_type=BACKUP_MEDIA_TYPE,
            headers={
                "Content-Disposition": 'attachment; filename="ainovel-work.ainovel-backup"',
                "Content-Length": str(size),
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.file.close()


def _prepare_download(package, directory):
    file = TemporaryFile(dir=directory)
    try:
        size = 0
        for chunk in _backup_chunks(package):
            size += len(chunk)
            if size > MAX_BACKUP_BYTES:
                raise size_error()
            file.write(chunk)
        file.seek(0)
        return _BackupDownload(file, size)
    except BaseException:
        file.close()
        raise


def _import_backup(domain, reader):
    try:
        return import_manuscript(domain, reader.package())
    except UnicodeError, RecursionError:
        raise _invalid() from None


def register_routes(app, domain):
    @app.get(
        "/api/branches/{branch_id}/manuscript-backup",
        response_class=StreamingResponse,
        responses={200: {"content": {BACKUP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def get_backup(branch_id: str, include_reference_evidence_for: ReferenceSelection = None):
        try:
            package = export_manuscript(
                domain,
                branch_id,
                include_reference_evidence_for=include_reference_evidence_for or (),
                before_read=_check_saved_text_size,
            )
            return _prepare_download(package, domain.db.path.parent)
        except OSError:
            raise DomainError(
                "manuscript_backup_storage", "无法写入独立备份，请检查本机磁盘空间后重试。", 507
            ) from None
        except UnicodeError, ValidationError, RecursionError:
            raise DomainError(
                "manuscript_backup", "当前作品有无法写入独立备份的记录，已保存的作品保留。"
            ) from None

    @app.post(
        BACKUP_IMPORT_PATH,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def restore_backup(request: Request):
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            != "application/octet-stream"
        ):
            raise DomainError("manuscript_backup_type", "请使用独立备份文件作为二进制请求正文。", 415)
        reader = BackupReader()
        try:
            async for chunk in request.stream():
                if chunk:
                    await run_in_threadpool(reader.feed, chunk)
        except ClientDisconnect:
            raise DomainError("manuscript_backup_interrupted", "独立备份传输中断，未导入作品。") from None
        return await run_in_threadpool(_import_backup, domain, reader)
