"""Opt-in SQLite client for the portable manuscript sync protocol."""

from contextlib import contextmanager
from hashlib import sha256
from ipaddress import ip_address
import json
import re
from threading import Lock
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Body
import httpx
from pydantic import Field, SecretStr, StringConstraints, TypeAdapter, ValidationError, model_validator

from . import (
    outline_sync,
    reference_sync,
    restore_state_desktop,
    restore_state_sync,
    setting_use_desktop,
    settings_sync,
    story_sync,
    workflow_archive,
)
from .chapter_adoption import adopted_chapter_revision, remember_chapter_adoption
from .chapter_conflicts import (
    ChapterObject,
    fork_chapter_tx,
    fork_fingerprint,
    fork_receipt,
    verified_chapter,
)
from .cloud_ledger import LEDGER_HEADER
from .cloud_sync import (
    CHAPTER_DIRECTORY_PREFIX,
    CHAPTER_ORDER_PART_PREFIX,
    ChapterOrderBatch,
    ChapterOrderConflicts,
    ChapterOrderMove,
    ChapterOrderResolution,
    Checkpoint,
    ComparedConflict,
    Identifier,
    MAX_CURSOR,
    MAX_DIRECTORY_CHAPTERS,
    MAX_OBJECT_BYTES,
    Pulled,
    Receipt,
)
from .db import DomainError, dumps, uid
from .manuscript_exchange import (
    PortableBranch,
    PortableEvidence,
    PortableWork,
    RevisionObject,
    _evidence,
    native_revision,
    source_revision,
    source_revisions,
    store_portable_evidence,
    store_source_revision,
)
from .models import Input
from .project_sync import (
    SCHEMA as PROJECT_SCHEMA,
    ProjectVersionObject,
    apply_project_head,
    current_project_source,
    draft_project_objects,
    has_project_source,
    head_object,
    observe_project_version,
    parse_project_object,
    read_project_head,
    read_project_version,
    version_object,
)
from .setting_use_portability import HEAD_PREFIX as SETTING_USE_HEAD
from .setting_use_portability import VERSION_PREFIX as SETTING_USE_VERSION
from .setting_use_portability import SettingUseVersionObject, parse_setting_use_object
from .setting_use_portability import version_object as setting_use_version_object
from .workflow_privacy import REFERENCE_SCOPE, sync_completion_report, sync_has_reference_selection

FORMAT = "ainovel-manuscript-sync/1"
RESPONSE_LIMIT = 8 * 1024 * 1024
LEDGER_REPLACED = "无法确认此同步记录属于当前云账本。原工作版本可继续编辑和备份，请从独立副本连接当前云端。"
CONFLICT_REJECTIONS = {"sync_head_changed": 409, "sync_conflict_resolved": 409}
ORDER_REJECTIONS = {
    "sync_chapter_order_changed": "章位比较后已变化，请重新核对目录。",
    "sync_chapter_order_scheduled": "该章已编入正式大纲，请保留其章位。",
    "sync_chapter_order_limit": "当前目录无法分配新章位，请先核对目录。",
    "sync_reserved": "请重新提交章位调整。",
}
PROSE_MERGE_REJECTIONS = {
    **CONFLICT_REJECTIONS,
    "sync_chapter": 409,
    "sync_history": 409,
    "sync_input": 422,
    "sync_replay": 409,
    "sync_object_size": 413,
    "sync_request_size": 413,
}
WORKFLOW_REJECTIONS = {
    "sync_workflow": "云端未能核对这份体检或交付历史，原记录已保留本机。",
    "sync_workflow_reference_scope": REFERENCE_SCOPE,
    "sync_workflow_dependency": "云端尚缺原章稿修订或通过的体检单，将继续等待来源。",
    "sync_parent_unknown": "云端无法核对父修订，原记录已保留本机。",
    "sync_object_size": "同步对象超过单次大小限制，原记录已保留本机。",
}
SETTING_USE_REJECTIONS = {
    "setting_use_dependency": "云端尚缺设定使用证明依赖的正文、设定或大纲修订，已保留待同步记录。",
    "setting_use_integrity": "云端未能核对设定使用证明，原记录已保留本机。",
    "sync_setting_use": "云端未能核对设定使用证明的历史链，将等待原修订。",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_sync(
 branch_id TEXT PRIMARY KEY REFERENCES branches(id),
 origin_sha256 TEXT NOT NULL, enabled INTEGER NOT NULL, cursor INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS desktop_sync_ledgers(
 branch_id TEXT PRIMARY KEY REFERENCES desktop_sync(branch_id),
 ledger_id TEXT NOT NULL CHECK(length(ledger_id)=32 AND ledger_id NOT GLOB '*[^a-f0-9]*'));
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
CREATE TABLE IF NOT EXISTS desktop_sync_chapter_order(
 branch_id TEXT PRIMARY KEY REFERENCES desktop_sync(branch_id), request TEXT NOT NULL,
 token_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS desktop_sync_chapter_bases(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 remote_revision TEXT NOT NULL, chapter TEXT, native_revision TEXT, remote_sha256 TEXT,
 order_revision TEXT NOT NULL, order_sequence INTEGER NOT NULL,
 PRIMARY KEY(branch_id,object_id));
CREATE TABLE IF NOT EXISTS desktop_sync_directory_outbox(
 branch_id TEXT PRIMARY KEY REFERENCES desktop_sync(branch_id), request TEXT NOT NULL,
 error TEXT);
CREATE TABLE IF NOT EXISTS desktop_sync_directory_parts(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), change_id TEXT NOT NULL,
 object_id TEXT NOT NULL, receipt TEXT NOT NULL, parent_receipt TEXT, PRIMARY KEY(branch_id,change_id));
CREATE INDEX IF NOT EXISTS desktop_sync_directory_part_objects
 ON desktop_sync_directory_parts(branch_id,object_id);
CREATE TABLE IF NOT EXISTS desktop_sync_deferred(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 receipt TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(branch_id,object_id));
CREATE TABLE IF NOT EXISTS desktop_sync_prose_merges(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id),change_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,receipt TEXT,
 PRIMARY KEY(branch_id,change_id));
CREATE TABLE IF NOT EXISTS desktop_sync_story_pending(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 reason TEXT NOT NULL, PRIMARY KEY(branch_id,object_id));
CREATE TABLE IF NOT EXISTS desktop_sync_workflow_pending(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 reason TEXT NOT NULL, PRIMARY KEY(branch_id,object_id));
CREATE TABLE IF NOT EXISTS desktop_sync_dirty(
 branch_id TEXT NOT NULL REFERENCES desktop_sync(branch_id), object_id TEXT NOT NULL,
 PRIMARY KEY(branch_id,object_id));
CREATE TABLE IF NOT EXISTS extension_migrations(name TEXT PRIMARY KEY,applied REAL NOT NULL);
INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'bootstrap' FROM desktop_sync WHERE enabled=1
 AND NOT EXISTS(SELECT 1 FROM extension_migrations WHERE name='desktop-sync-dirty/1');
INSERT OR IGNORE INTO extension_migrations VALUES('desktop-sync-dirty/1',unixepoch());
INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'story-bootstrap' FROM desktop_sync WHERE enabled=1
 AND NOT EXISTS(SELECT 1 FROM extension_migrations WHERE name='desktop-sync-story/1');
INSERT OR IGNORE INTO extension_migrations VALUES('desktop-sync-story/1',unixepoch());
INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'reference-bootstrap' FROM desktop_sync WHERE enabled=1
 AND NOT EXISTS(SELECT 1 FROM extension_migrations WHERE name='desktop-sync-reference/1');
INSERT OR IGNORE INTO extension_migrations VALUES('desktop-sync-reference/1',unixepoch());
CREATE TRIGGER IF NOT EXISTS desktop_sync_reference_edit AFTER UPDATE OF revision ON reference_sessions BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT s.branch_id,'reference-session:'||NEW.id FROM desktop_sync s WHERE s.enabled=1
 AND s.branch_id IN (json_extract(OLD.payload,'$.branch_id'),json_extract(NEW.payload,'$.branch_id'));
 END;
INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'workflow-bootstrap' FROM desktop_sync WHERE enabled=1
 AND NOT EXISTS(SELECT 1 FROM extension_migrations WHERE name='desktop-sync-workflow/1');
INSERT OR IGNORE INTO extension_migrations VALUES('desktop-sync-workflow/1',unixepoch());
CREATE TRIGGER IF NOT EXISTS desktop_sync_chapter_added AFTER INSERT ON chapters
 WHEN EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.branch_id,'chapter:'||NEW.id);
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_chapter_changed AFTER UPDATE OF title,position,revision,archived ON chapters
 WHEN EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.branch_id,'chapter:'||NEW.id);
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_revision_added AFTER INSERT ON revisions BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT c.branch_id,'revision:'||NEW.id FROM chapters c JOIN desktop_sync s ON s.branch_id=c.branch_id
 WHERE c.id=NEW.chapter_id AND s.enabled=1;
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_source_added AFTER INSERT ON manuscript_revision_sources
 WHEN EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.branch_id,'revision:'||NEW.id);
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_branch_changed AFTER UPDATE OF name,mode ON branches
 WHEN EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.id,'branch:'||NEW.id);
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_novel_changed AFTER UPDATE OF title,brief ON novels BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT b.id,'branch:'||b.id FROM branches b JOIN desktop_sync s ON s.branch_id=b.id
 WHERE b.novel_id=NEW.id AND s.enabled=1;
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_accepted_artifact AFTER INSERT ON artifacts
 WHEN NEW.status='accepted'
 AND NEW.kind IN('project_brief','conception_completion','workflow_inspection','book_completion','portable_workbench_evidence')
 AND EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.branch_id,
 CASE WHEN NEW.kind='project_brief' THEN 'project:'||NEW.branch_id ELSE 'evidence:'||NEW.id END);
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_confirmed_outline AFTER INSERT ON outline_revisions
 WHEN NEW.confirmed=1 AND EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.branch_id,'evidence:'||NEW.id);
 END;
CREATE TRIGGER IF NOT EXISTS desktop_sync_story_history AFTER INSERT ON story_record_history BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT r.branch_id,'story:'||r.id FROM story_records r JOIN desktop_sync s ON s.branch_id=r.branch_id
 WHERE r.id=NEW.record_id AND s.enabled=1;
 END;
INSERT OR IGNORE INTO desktop_sync_dirty
 SELECT branch_id,'setting-use-bootstrap' FROM desktop_sync WHERE enabled=1
 AND NOT EXISTS(SELECT 1 FROM extension_migrations WHERE name='desktop-sync-setting-use/1');
INSERT OR IGNORE INTO extension_migrations VALUES('desktop-sync-setting-use/1',unixepoch());
CREATE TRIGGER IF NOT EXISTS desktop_sync_setting_use_adopted AFTER UPDATE OF basis ON artifacts
 WHEN NEW.kind='setting_use' AND NEW.status='accepted'
 AND json_extract(NEW.basis,'$.head')=1 AND json_extract(OLD.basis,'$.head') IS NOT 1
 AND EXISTS(SELECT 1 FROM desktop_sync WHERE branch_id=NEW.branch_id AND enabled=1) BEGIN
 INSERT OR IGNORE INTO desktop_sync_dirty VALUES(NEW.branch_id,'setting-use-version:'||NEW.id);
 END;
"""


class Connection(Input):
    origin: str = Field(min_length=1, max_length=512)
    token: SecretStr


class ResolveChapterOrder(Connection, ChapterOrderResolution):
    pass


class ComparedAction(Connection):
    parent_revision: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    local_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


class Resolve(ComparedAction):
    choice: Literal["current", "incoming", "local", "merge"]
    content: str | None = Field(default=None, max_length=250_000)

    @model_validator(mode="after")
    def check_prose(self):
        if (self.choice == "merge") != (self.content is not None):
            raise ValueError("人工合并须提供编辑后的正文。")
        return self


class ForkChapter(ComparedAction):
    choice: Literal["current", "incoming", "local", "base"]
    branch_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class BranchObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["branch"]
    novel: PortableWork
    branch: PortableBranch


class EvidenceObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["evidence"]
    document: PortableEvidence


class ChapterDirectoryMove(ChapterOrderMove):
    receipt_id: Identifier


class ChapterDirectory(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["chapter-directory"]
    branch_id: Identifier
    moves: list[ChapterDirectoryMove] = Field(min_length=2, max_length=MAX_DIRECTORY_CHAPTERS)


def _sha(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _digest(value):
    if value and value["kind"] in {"branch", "chapter"}:
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


def _headers(token, ledger_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if ledger_id is not None:
        headers[LEDGER_HEADER] = ledger_id
    return headers


def _network(origin, token, method, path, payload=None, *, ledger_id=None):
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
            with client.stream(
                method, origin + path, headers=_headers(token, ledger_id), json=payload
            ) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > RESPONSE_LIMIT:
                        raise DomainError("sync_response", "同步服务响应超过本机限制。", 502)
                    body.extend(chunk)
                if response.status_code == 204 and not body:
                    return None
                result = json.loads(body)
                if not response.is_success:
                    code = result.get("code") if isinstance(result, dict) else None
                    if code == "sync_ledger_replaced" and response.status_code == 409:
                        raise DomainError(code, LEDGER_REPLACED, 409)
                    if code == "sync_receipt" and response.status_code == 404:
                        raise DomainError(code, "该设备没有对应回执。", 404)
                    if (
                        ("/chapter-order-conflicts" in path or "/chapter-order-batches" in path)
                        and isinstance(code, str)
                        and code in ORDER_REJECTIONS
                        and response.status_code == 409
                    ):
                        raise DomainError(code, ORDER_REJECTIONS[code], 409)
                    if (
                        isinstance(code, str)
                        and code in SETTING_USE_REJECTIONS
                        and response.status_code == 409
                    ):
                        raise DomainError(code, SETTING_USE_REJECTIONS[code], 409)
                    if isinstance(code, str) and code in WORKFLOW_REJECTIONS:
                        raise DomainError(code, WORKFLOW_REJECTIONS[code], response.status_code)
                    if isinstance(code, str) and (
                        PROSE_MERGE_REJECTIONS.get(code) == response.status_code
                        or code == "sync_auth"
                        and response.status_code == 401
                    ):
                        raise DomainError(
                            code,
                            f"同步服务拒绝此操作（HTTP {response.status_code}）。请核对设备凭据或刷新比较。",
                            response.status_code,
                        )
                    raise DomainError(
                        code
                        if isinstance(code, str)
                        and code
                        in {
                            "sync_story",
                            "sync_story_dependency",
                            "sync_story_evidence",
                        }
                        else "sync_remote",
                        f"同步服务拒绝此操作（HTTP {response.status_code}）。请核对设备凭据或刷新比较。",
                        409 if response.status_code == 409 else 502,
                    )
                if result is None:
                    raise DomainError("sync_response", "同步服务未返回可核验的回执。", 502)
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
    if object_id.startswith(restore_state_desktop.PREFIXES):
        value = restore_state_desktop.read_object(tx, branch_id, object_id)
        return value.model_dump() if value else None
    if object_id.startswith(SETTING_USE_VERSION):
        identifier = object_id.removeprefix(SETTING_USE_VERSION)
        version = setting_use_desktop.read_version(tx, branch_id, identifier)
        return setting_use_version_object(branch_id, version).model_dump() if version else None
    if object_id.startswith(SETTING_USE_HEAD):
        head = setting_use_desktop.read_head(tx, branch_id, object_id.removeprefix(SETTING_USE_HEAD))
        return head.model_dump() if head else None
    if object_id.startswith("reference-card-version:"):
        identifier = object_id.removeprefix("reference-card-version:")
        version = reference_sync.read_reference_version(tx, branch_id, identifier)
        return reference_sync.version_object(branch_id, version).model_dump() if version else None
    if object_id.startswith("reference-card:"):
        head = reference_sync.read_reference_head_object(tx, branch_id, object_id)
        return head.model_dump() if head else None
    if object_id.startswith("story-version:"):
        version = story_sync.read_story_version(tx, branch_id, object_id.removeprefix("story-version:"))
        return story_sync.version_object(branch_id, version).model_dump() if version else None
    if object_id.startswith("story:"):
        identifier = object_id.removeprefix("story:")
        adopted = story_sync.read_story_head(tx, branch_id, identifier)
        return story_sync.head_object(branch_id, identifier, adopted).model_dump() if adopted else None
    if object_id.startswith("outline-version:"):
        version = outline_sync.read_outline_version(tx, branch_id, object_id.removeprefix("outline-version:"))
        return outline_sync.version_object(branch_id, version).model_dump() if version else None
    if object_id == f"outline:{branch_id}":
        adopted = outline_sync.read_outline_head(tx, branch_id)
        return outline_sync.head_object(branch_id, adopted).model_dump() if adopted else None
    if object_id.startswith("settings-version:"):
        version = settings_sync.read_settings_version(
            tx, branch_id, object_id.removeprefix("settings-version:")
        )
        return settings_sync.version_object(branch_id, version).model_dump() if version else None
    if object_id == f"settings:{branch_id}":
        adopted = settings_sync.read_settings_head(tx, branch_id)
        return settings_sync.head_object(branch_id, adopted).model_dump() if adopted else None
    if object_id.startswith("project-version:"):
        version = read_project_version(tx, branch_id, object_id.removeprefix("project-version:"))
        return version_object(branch_id, version).model_dump() if version else None
    if object_id == f"project:{branch_id}":
        adopted = read_project_head(tx, branch_id)
        return head_object(branch_id, adopted).model_dump() if adopted else None
    if object_id.startswith("revision:"):
        row = source_revision(tx, branch_id, object_id.removeprefix("revision:"))
        return _revision_object(branch_id, row) if row else None
    if object_id.startswith("evidence:"):
        document_id = object_id.removeprefix("evidence:")
        for document in _evidence(tx, branch_id):
            if document["id"] == document_id:
                return {"format": FORMAT, "kind": "evidence", "document": document}
        return None
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


def _revision_object(branch_id, row):
    return RevisionObject(format=FORMAT, kind="revision", branch_id=branch_id, revision=row).model_dump()


def _shadow(tx, branch_id, object_id):
    return tx.execute(
        "SELECT * FROM desktop_sync_objects WHERE branch_id=? AND object_id=?", (branch_id, object_id)
    ).fetchone()


def _chapter_base(tx, branch_id, object_id, revision):
    return tx.execute(
        "SELECT * FROM desktop_sync_chapter_bases WHERE branch_id=? AND object_id=? AND remote_revision=?",
        (branch_id, object_id, revision),
    ).fetchone()


def _chapter_at(payload, position):
    return {**payload, "chapter": {**payload["chapter"], "position": position}}


def _order_content_sha(payload):
    return _sha(
        json.dumps(_chapter_at(payload, 1), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _write_chapter_base(tx, branch_id, event, local_chapter):
    if event.tombstone:
        values = (None, None, None)
    else:
        if local_chapter is None:
            return
        values = (
            dumps(local_chapter["chapter"]),
            local_chapter["head"]["id"],
            _order_content_sha(event.payload),
        )
    tx.execute(
        "INSERT INTO desktop_sync_chapter_bases VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(branch_id,object_id) "
        "DO UPDATE SET remote_revision=excluded.remote_revision,chapter=excluded.chapter,"
        "native_revision=excluded.native_revision,remote_sha256=excluded.remote_sha256,"
        "order_revision=excluded.order_revision,order_sequence=excluded.order_sequence",
        (branch_id, event.object_id, event.head_revision, *values, event.head_revision, event.sequence),
    )


def _remember(tx, branch_id, event, local_digest, *, chapter_base=None):
    stored = tx.execute(
        "INSERT INTO desktop_sync_objects VALUES(?,?,?,?,?) ON CONFLICT(branch_id,object_id) DO UPDATE SET "
        "remote_revision=excluded.remote_revision,sequence=excluded.sequence,local_sha256=excluded.local_sha256 "
        "WHERE excluded.sequence>=desktop_sync_objects.sequence",
        (branch_id, event.object_id, event.head_revision, event.sequence, local_digest),
    )
    if stored.rowcount and event.object_id.startswith("chapter:"):
        _write_chapter_base(tx, branch_id, event, chapter_base)
    if event.object_id.startswith((SETTING_USE_HEAD, SETTING_USE_VERSION, *restore_state_desktop.PREFIXES)):
        tx.execute(
            "DELETE FROM desktop_sync_workflow_pending WHERE branch_id=? AND object_id=?",
            (branch_id, event.object_id),
        )


def _source_extension(tx, branch_id, head, local):
    history = []
    row = source_revision(tx, branch_id, head.id)
    if row != head:
        return history
    while row != local:
        if not row or tx.execute("SELECT 1 FROM revisions WHERE id=?", (row.id,)).fetchone():
            return []
        history.append(row)
        row = source_revision(tx, branch_id, row.parent_id) if row.parent_id else None
    return list(reversed(history))


def _remove_source_copies(tx, branch_id, history):
    tx.executemany(
        "DELETE FROM manuscript_revision_sources WHERE branch_id=? AND id=?",
        [(branch_id, revision.id) for revision in history],
    )


def _apply_chapter(tx, domain, branch_id, event, *, force_revision=False):
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
        history = _source_extension(tx, branch_id, head, None)
        if len(history) != head.revision + 1:
            history = [
                head.model_copy(
                    update={
                        "id": uid(),
                        "revision": 0,
                        "parent_id": None,
                        "cause": f"云同步初始稿，来源修订 {head.id}",
                    }
                )
            ]
        tx.execute(
            "INSERT INTO chapters VALUES(?,?,?,?,?,?,0,0)",
            (chapter_id, branch_id, info.position, info.title, history[-1].revision, head.content),
        )
        tx.executemany(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
            [(r.id, chapter_id, r.revision, r.content, r.cause, r.created) for r in history],
        )
        _remove_source_copies(tx, branch_id, history)
        story_sync.remember_adopted_chapter_source(tx, branch_id, head)
        remember_chapter_adoption(tx, branch_id, head)
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        return
    local = tx.execute(
        "SELECT id FROM revisions WHERE chapter_id=? AND revision=?",
        (chapter_id, row["revision"]),
    ).fetchone()
    if not local:
        raise DomainError("sync_history", "本机当前章稿缺少修订记录。", 409)
    if (
        row["content"] == head.content
        and local["id"] != head.id
        and source_revision(tx, branch_id, head.id) != head
    ):
        # The same prose may still be a new formal revision with its own facts and references.
        raise DomainError("sync_history", "同文章稿仍等待云端采用所引用的准确原修订。", 409)
    history = _source_extension(tx, branch_id, head, source_revision(tx, branch_id, local["id"]))
    if history:
        chapter = dict(row)
        for revision in history:
            chapter["revision"] = domain.revise_tx(
                tx,
                chapter,
                revision.content,
                revision.cause,
                revision_id=revision.id,
                created=revision.created,
            )
        _remove_source_copies(tx, branch_id, history)
    elif row["content"] != head.content or force_revision:
        domain.revise_tx(tx, dict(row), head.content, f"云同步，来源修订 {head.id}")
    tx.execute(
        "UPDATE chapters SET position=?,title=?,archived=0 WHERE id=?",
        (info.position, info.title, chapter_id),
    )
    story_sync.remember_adopted_chapter_source(tx, branch_id, head)
    remember_chapter_adoption(tx, branch_id, head)
    tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))


def _apply(tx, domain, branch_id, event, *, prose_merge=False):
    if event.object_id.startswith(restore_state_desktop.HEAD_PREFIXES):
        value = restore_state_sync.parse_restore_state_object(
            branch_id, event.object_id, event.payload, event.tombstone
        )
        restore_state_desktop.apply_head(tx, branch_id, value)
        return
    if event.object_id.startswith(SETTING_USE_HEAD):
        value = parse_setting_use_object(branch_id, event.object_id, event.payload, event.tombstone)
        setting_use_desktop.apply_head(tx, branch_id, value)
        # The remote adoption itself must not be requeued as a local edit.
        tx.execute(
            "DELETE FROM desktop_sync_dirty WHERE branch_id=? AND object_id=?",
            (branch_id, SETTING_USE_VERSION + value.revision_id),
        )
        return
    if event.object_id.startswith("reference-card:"):
        value = reference_sync.parse_reference_object(
            branch_id, event.object_id, event.payload, event.tombstone
        )
        reference_sync.apply_reference_head(tx, branch_id, value, event)
        return
    if event.object_id.startswith("story:"):
        value = story_sync.parse_story_object(branch_id, event.object_id, event.payload, event.tombstone)
        story_sync.apply_story_head(tx, branch_id, value, event)
        return
    if event.object_id.startswith("outline:"):
        value = outline_sync.parse_outline_object(branch_id, event.object_id, event.payload, event.tombstone)
        with restore_state_desktop.dependent_heads(tx, branch_id):
            outline_sync.apply_outline_head(tx, branch_id, value, event)
        return
    if event.object_id.startswith("settings:"):
        value = settings_sync.parse_settings_object(
            branch_id, event.object_id, event.payload, event.tombstone
        )
        settings_sync.apply_settings_head(tx, branch_id, value, event)
        return
    if event.object_id.startswith("project:"):
        value = parse_project_object(branch_id, event.object_id, event.payload, event.tombstone)
        apply_project_head(tx, branch_id, value, event)
        return
    if event.object_id.startswith("revision:"):
        try:
            value = RevisionObject.model_validate(event.payload)
        except ValidationError as error:
            raise DomainError("sync_history", "来源修订格式无效。", 409) from error
        if (
            event.tombstone
            or value.branch_id != branch_id
            or event.object_id != f"revision:{value.revision.id}"
        ):
            raise DomainError("sync_scope", "来源稿件与工作版本或同步编号不符。", 409)
        store_source_revision(tx, branch_id, value.revision)
        return
    if event.object_id.startswith("evidence:"):
        try:
            wrapped = EvidenceObject.model_validate(event.payload)
        except ValidationError as error:
            raise DomainError("sync_format", "收到不可读取的只读依据。", 409) from error
        if event.tombstone or event.object_id != f"evidence:{wrapped.document.id}":
            raise DomainError("sync_scope", "云端依据与编号不匹配。", 409)
        store_portable_evidence(tx, branch_id, wrapped.document)
        return
    if event.object_id.startswith("chapter:"):
        with restore_state_desktop.dependent_heads(
            tx, branch_id, chapter_id=event.object_id.removeprefix("chapter:")
        ):
            _apply_chapter(tx, domain, branch_id, event, force_revision=prose_merge)
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


def _verified_digest(branch_id, event):
    if event.object_id.startswith(restore_state_desktop.PREFIXES):
        return _digest(
            restore_state_sync.parse_restore_state_object(
                branch_id, event.object_id, event.payload, event.tombstone
            ).model_dump()
        )
    if event.object_id.startswith((SETTING_USE_HEAD, SETTING_USE_VERSION)):
        return _digest(
            parse_setting_use_object(branch_id, event.object_id, event.payload, event.tombstone).model_dump()
        )
    if event.object_id.startswith(("reference-card:", "reference-card-version:")):
        return _digest(
            reference_sync.parse_reference_object(
                branch_id, event.object_id, event.payload, event.tombstone
            ).model_dump()
        )
    if event.object_id.startswith(("story:", "story-version:")):
        return _digest(
            story_sync.parse_story_object(
                branch_id, event.object_id, event.payload, event.tombstone
            ).model_dump()
        )
    if event.object_id.startswith(("outline:", "outline-version:")):
        return _digest(
            outline_sync.parse_outline_object(
                branch_id, event.object_id, event.payload, event.tombstone
            ).model_dump()
        )
    if event.object_id.startswith(("settings:", "settings-version:")):
        return _digest(
            settings_sync.parse_settings_object(
                branch_id, event.object_id, event.payload, event.tombstone
            ).model_dump()
        )
    if event.object_id.startswith(("project:", "project-version:")):
        return _digest(
            parse_project_object(branch_id, event.object_id, event.payload, event.tombstone).model_dump()
        )
    if event.tombstone and event.payload is None and event.object_id.startswith("chapter:"):
        return _digest(None)
    schema = (
        ChapterObject
        if event.object_id.startswith("chapter:")
        else RevisionObject
        if event.object_id.startswith("revision:")
        else EvidenceObject
        if event.object_id.startswith("evidence:")
        else BranchObject
    )
    try:
        payload = schema.model_validate(event.payload).model_dump()
    except ValidationError as error:
        raise DomainError("sync_format", "同步对象格式不支持当前文稿。", 409) from error
    if event.tombstone:
        raise DomainError("sync_format", "同步对象和删除标记不一致。", 409)
    return _digest(payload)


def _is_prose_merge(queued):
    return bool(queued and queued["conflict_id"] and json.loads(queued["request"])["choice"] == "merge_prose")


def _prose_merge_record(tx, branch_id, key, fingerprint):
    row = tx.execute(
        "SELECT fingerprint,receipt FROM desktop_sync_prose_merges WHERE branch_id=? AND change_id=?",
        (branch_id, key),
    ).fetchone()
    if row and row["fingerprint"] != fingerprint:
        raise DomainError("idempotency", "章节合并的提交编号已用于其他内容。", 409)
    return row


def _record_prose_merge(tx, branch_id, event):
    chapter = verified_chapter(branch_id, event.object_id, event.payload)
    known = _shadow(tx, branch_id, event.object_id)
    local_revision_id = None
    if known and known["remote_revision"] == event.change_id:
        local = _local(tx, branch_id, event.object_id)
        if local and local["head"]["sha256"] == chapter.head.sha256:
            local_revision_id = local["head"]["id"]
    receipt = {
        "change_id": event.change_id,
        "conflict_id": event.conflict_id,
        "source_revision_id": chapter.head.id,
        "source_parent_id": chapter.head.parent_id,
        "local_revision_id": local_revision_id,
    }
    tx.execute(
        "UPDATE desktop_sync_prose_merges SET receipt=? WHERE branch_id=? AND change_id=?",
        (dumps(receipt), branch_id, event.change_id),
    )


class DesktopSync:
    def __init__(self, domain, transport=_network):
        self.domain = domain
        self.transport = transport
        self.operations = Lock()
        with domain.db.connect() as tx:
            tx.executescript(
                "BEGIN IMMEDIATE;\n"
                + SCHEMA
                + PROJECT_SCHEMA
                + settings_sync.SCHEMA
                + outline_sync.SCHEMA
                + story_sync.SCHEMA
                + restore_state_desktop.SCHEMA
                + "\nCOMMIT;"
            )

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
            row = self._binding(tx, branch_id)
            pending = tx.execute(
                "SELECT count(*) FROM (SELECT object_id FROM desktop_sync_outbox WHERE branch_id=? "
                "UNION SELECT object_id FROM desktop_sync_dirty WHERE branch_id=?)",
                (branch_id, branch_id),
            ).fetchone()[0]
            order = tx.execute(
                "SELECT request FROM desktop_sync_chapter_order WHERE branch_id=?", (branch_id,)
            ).fetchone()
            directory = tx.execute(
                "SELECT request,error FROM desktop_sync_directory_outbox WHERE branch_id=?", (branch_id,)
            ).fetchone()
            return {
                "bound": row is not None,
                "enabled": bool(row and row["enabled"]),
                "work_id": branch_id,
                "cursor": row["cursor"] if row else 0,
                "ledger_id": row["ledger_id"] if row else None,
                "pending": pending + bool(order) + bool(directory),
                "chapter_order_pending": json.loads(order["request"]) if order else None,
                "project_source": current_project_source(tx, branch_id),
                "settings_source": settings_sync.current_settings_source(tx, branch_id),
                "outline_source": outline_sync.current_outline_source(tx, branch_id),
                "conflicts": [
                    {"id": item["id"], "object_id": item["object_id"]}
                    for item in tx.execute(
                        "SELECT * FROM desktop_sync_conflicts WHERE branch_id=?", (branch_id,)
                    )
                ],
                "deferred": [
                    {"object_id": item["object_id"], "reason": item["reason"]}
                    for item in tx.execute(
                        "SELECT object_id,reason FROM desktop_sync_deferred WHERE branch_id=? UNION ALL "
                        "SELECT p.object_id,p.reason FROM desktop_sync_story_pending p WHERE p.branch_id=? "
                        "AND NOT EXISTS(SELECT 1 FROM desktop_sync_deferred d "
                        "WHERE d.branch_id=p.branch_id AND d.object_id=p.object_id) UNION ALL "
                        "SELECT p.object_id,p.reason FROM desktop_sync_workflow_pending p WHERE p.branch_id=? "
                        "AND NOT EXISTS(SELECT 1 FROM desktop_sync_deferred d "
                        "WHERE d.branch_id=p.branch_id AND d.object_id=p.object_id)",
                        (branch_id, branch_id, branch_id),
                    )
                ]
                + (
                    [
                        {
                            "object_id": CHAPTER_DIRECTORY_PREFIX
                            + json.loads(directory["request"])["change_id"],
                            "reason": directory["error"],
                        }
                    ]
                    if directory and directory["error"]
                    else []
                ),
            }

    @staticmethod
    def _binding(tx, branch_id):
        return tx.execute(
            "SELECT s.*, l.ledger_id FROM desktop_sync s LEFT JOIN desktop_sync_ledgers l USING(branch_id) "
            "WHERE s.branch_id=?",
            (branch_id,),
        ).fetchone()

    def _bound(self, branch_id, origin, *, allow_disabled=False):
        with self.domain.db.connect() as tx:
            row = self._binding(tx, branch_id)
            if not row or row["origin_sha256"] != _sha(origin) or (not row["enabled"] and not allow_disabled):
                raise DomainError("sync_binding", "请先为此版本启用对应的同步地址。", 409)
            return row

    def _call(self, branch_id, origin, token, path, schema, *, method="GET", data=None, ledger_id=None):
        raw = self.transport(
            origin, token, method, f"/api/sync/works/{branch_id}{path}", data, ledger_id=ledger_id
        )
        try:
            return TypeAdapter(schema).validate_python(raw)
        except ValidationError as error:
            raise DomainError("sync_response", "同步服务返回了无法核验的回执。", 502) from error

    @staticmethod
    def _check_ledger(binding, checkpoint):
        if binding and (
            binding["ledger_id"] != checkpoint.ledger_id or checkpoint.cursor < binding["cursor"]
        ):
            raise DomainError("sync_ledger_replaced", LEDGER_REPLACED, 409)

    def _verified_ledger(self, branch_id, origin, token):
        binding = self._bound(branch_id, origin)
        if not binding["ledger_id"]:
            raise DomainError("sync_ledger_replaced", LEDGER_REPLACED, 409)
        checkpoint = self._call(branch_id, origin, token, "/checkpoint", Checkpoint)
        self._check_ledger(binding, checkpoint)
        return binding["ledger_id"]

    def enable(self, branch_id, origin, token):
        with self._operation():
            self.domain.branch(branch_id)
            checkpoint = self._call(branch_id, origin, token, "/checkpoint", Checkpoint)
            with self.domain.db.connect(True) as tx:
                row = self._binding(tx, branch_id)
                if row and row["origin_sha256"] != _sha(origin):
                    raise DomainError(
                        "sync_binding", "此版本已有另一同步历史，请使用独立工作版本连接新服务。", 409
                    )
                self._check_ledger(row, checkpoint)
                tx.execute(
                    "INSERT INTO desktop_sync(branch_id,origin_sha256,enabled) VALUES(?,?,1) "
                    "ON CONFLICT(branch_id) DO UPDATE SET enabled=1",
                    (branch_id, _sha(origin)),
                )
                if not row:
                    tx.execute(
                        "INSERT INTO desktop_sync_ledgers VALUES(?,?)", (branch_id, checkpoint.ledger_id)
                    )
                tx.execute("INSERT OR IGNORE INTO desktop_sync_dirty VALUES(?,'bootstrap')", (branch_id,))
            return self._state(branch_id)

    def disable(self, branch_id):
        with self._operation():
            self.domain.branch(branch_id)
            with self.domain.db.connect(True) as tx:
                tx.execute("UPDATE desktop_sync SET enabled=0 WHERE branch_id=?", (branch_id,))
            return self._state(branch_id)

    def revoke_device(self, branch_id, origin, token):
        with self._operation():
            self._bound(branch_id, origin, allow_disabled=True)
            self._call(branch_id, origin, token, "/device-session", type(None), method="DELETE")
            with self.domain.db.connect(True) as tx:
                tx.execute("UPDATE desktop_sync SET enabled=0 WHERE branch_id=?", (branch_id,))
            return self._state(branch_id)

    def _defer(self, tx, branch_id, event, reason):
        tx.execute(
            "INSERT INTO desktop_sync_deferred VALUES(?,?,?,?) ON CONFLICT(branch_id,object_id) DO UPDATE SET "
            "receipt=excluded.receipt,reason=excluded.reason "
            "WHERE json_extract(excluded.receipt,'$.sequence')>=json_extract(desktop_sync_deferred.receipt,'$.sequence')",
            (branch_id, event.object_id, event.model_dump_json(), reason),
        )

    def _pending_story(self, tx, branch_id, object_id, reason):
        tx.execute(
            "INSERT INTO desktop_sync_story_pending VALUES(?,?,?) "
            "ON CONFLICT(branch_id,object_id) DO UPDATE SET reason=excluded.reason",
            (branch_id, object_id, reason),
        )

    def _pending_workflow(self, tx, branch_id, object_id, reason):
        tx.execute(
            "INSERT INTO desktop_sync_workflow_pending VALUES(?,?,?) "
            "ON CONFLICT(branch_id,object_id) DO UPDATE SET reason=excluded.reason",
            (branch_id, object_id, reason),
        )

    def _verify_workflow(self, tx, branch_id, entry, *, hydrate=False):
        return workflow_archive.validate_workflow_object(
            entry,
            branch_id,
            lambda identifier: source_revision(tx, branch_id, identifier),
            lambda identifier: workflow_archive.read_workflow_entry(
                tx, branch_id, workflow_archive.INSPECTION, identifier
            ),
            setting_use=lambda identifier: setting_use_desktop.read_version(tx, branch_id, identifier),
            settings=lambda identifier: settings_sync.read_settings_version(tx, branch_id, identifier),
            hydrate=hydrate,
        )

    def _reference_scope_blocked(self, tx, branch_id, payload):
        if sync_has_reference_selection(payload):
            return True
        identifier = sync_completion_report(payload)
        return bool(
            identifier and workflow_archive.inspection_retains_reference_selection(tx, branch_id, identifier)
        )

    def _consume_workflow(self, tx, branch_id, event):
        try:
            entry = workflow_archive.parse_workflow_object(
                branch_id, event.object_id, event.payload, event.tombstone
            )
            if isinstance(entry, workflow_archive.CompletionProof) and not _shadow(
                tx, branch_id, f"workflow-inspection:{entry.manifest.inspection_id}"
            ):
                raise DomainError("sync_workflow_dependency", "云端原体检单尚未通过本机核对。", 409)
            entry = self._verify_workflow(tx, branch_id, entry, hydrate=True)
            workflow_archive.observe_workflow_object(tx, branch_id, event, entry)
        except (DomainError, ValidationError) as error:
            reason = error.message if isinstance(error, DomainError) else "本机历史记录格式无法核对。"
            self._defer(tx, branch_id, event, reason)
            return
        _remember(tx, branch_id, event, _digest(event.payload))
        tx.execute(
            "DELETE FROM desktop_sync_workflow_pending WHERE branch_id=? AND object_id=?",
            (branch_id, event.object_id),
        )

    @staticmethod
    def _awaiting_directory(tx, branch_id, object_id, sequence):
        return (
            object_id.startswith("chapter:")
            and tx.execute(
                "SELECT 1 FROM desktop_sync_directory_parts WHERE branch_id=? AND object_id=? "
                "AND json_extract(receipt,'$.sequence')<?",
                (branch_id, object_id, sequence),
            ).fetchone()
        )

    @staticmethod
    def _read_directory(branch_id, event):
        manifest = ChapterDirectory.model_validate(event.payload)
        batch = ChapterOrderBatch(
            change_id=event.change_id,
            moves=[move.model_dump(exclude={"receipt_id"}) for move in manifest.moves],
        )
        if (
            manifest.branch_id != branch_id
            or event.object_id != CHAPTER_DIRECTORY_PREFIX + batch.change_id
            or event.head_revision != batch.change_id
            or event.status != "applied"
            or event.tombstone
            or event.parent_revision is not None
            or event.conflict_id is not None
            or len({move.position for move in batch.moves}) != len(batch.moves)
            or len({move.next_position for move in batch.moves}) != len(batch.moves)
        ):
            raise DomainError("sync_order", "目录批次清单与采用回执不一致。", 409)
        return manifest, batch

    def _directory_sources(self, tx, branch_id, event, manifest):
        sources = []
        for index, move in enumerate(manifest.moves):
            expected_id = CHAPTER_ORDER_PART_PREFIX + _sha(
                dumps([branch_id, event.change_id, move.chapter_id])
            )
            object_id = f"chapter:{move.chapter_id}"
            row = tx.execute(
                "SELECT receipt,parent_receipt FROM desktop_sync_directory_parts WHERE branch_id=? AND change_id=?",
                (branch_id, move.receipt_id),
            ).fetchone()
            if row is None:
                raise DomainError("sync_order", "目录批次正在等待完整章节回执。", 409)
            part = Receipt.model_validate_json(row["receipt"])
            after = verified_chapter(branch_id, part.object_id, part.payload)
            if (
                move.receipt_id != expected_id
                or part.change_id != expected_id
                or part.head_revision != expected_id
                or part.parent_revision != move.head_revision
                or part.object_id != object_id
                or part.status != "applied"
                or part.tombstone
                or part.conflict_id is not None
                or part.device_id != event.device_id
                or part.sequence != event.sequence - len(manifest.moves) + index
                or after.chapter.position != move.next_position
            ):
                raise DomainError("sync_order", "目录批次的章节回执与清单不符。", 409)
            shadow = _shadow(tx, branch_id, object_id)
            if not shadow or shadow["sequence"] >= part.sequence:
                raise DomainError("sync_order", "目录批次正在等待原章位及章节采用依据。", 409)
            base = _chapter_base(tx, branch_id, object_id, shadow["remote_revision"])
            if not base or not base["chapter"] or base["order_sequence"] >= part.sequence:
                raise DomainError("sync_order", "目录批次正在等待原章位及章节采用依据。", 409)
            chapter = json.loads(base["chapter"])
            native = native_revision(tx, branch_id, base["native_revision"])
            if not native:
                raise DomainError("sync_order", "无法读取目录批次的本机原稿。", 409)
            projection = {
                "format": FORMAT,
                "kind": "chapter",
                "chapter": chapter,
                "head": native.model_dump(),
            }
            if chapter["position"] != move.position or shadow["local_sha256"] != _digest(projection):
                raise DomainError("sync_order", "目录批次不能改动原章稿或原章位。", 409)
            adopts_prose = shadow["remote_revision"] == move.head_revision
            remote_sha = (
                base["remote_sha256"]
                if base["order_revision"] == move.head_revision
                else self._deferred_directory_parent_sha(
                    tx, branch_id, object_id, move, part, base["order_sequence"], row["parent_receipt"]
                )
            )
            if remote_sha != _order_content_sha(part.payload):
                raise DomainError("sync_order", "目录批次不能改动原章稿或原章位。", 409)
            sources.append((part, _chapter_at(projection, move.next_position), adopts_prose))
        return sources

    @staticmethod
    def _deferred_directory_parent(tx, branch_id, object_id, revision):
        return tx.execute(
            "SELECT receipt FROM desktop_sync_deferred WHERE branch_id=? AND object_id=? "
            "AND json_extract(receipt,'$.change_id')=? UNION ALL "
            "SELECT receipt FROM desktop_sync_directory_parts WHERE branch_id=? AND object_id=? "
            "AND change_id=? LIMIT 1",
            (branch_id, object_id, revision, branch_id, object_id, revision),
        ).fetchone()

    @classmethod
    def _deferred_directory_parent_sha(cls, tx, branch_id, object_id, move, part, order_sequence, retained):
        if retained is None:
            row = cls._deferred_directory_parent(tx, branch_id, object_id, move.head_revision)
            retained = row["receipt"] if row else None
        if retained is None:
            raise DomainError("sync_order", "目录批次正在等待尚未采用的原章稿回执。", 409)
        parent = Receipt.model_validate_json(retained)
        original = verified_chapter(branch_id, parent.object_id, parent.payload)
        if (
            parent.object_id != object_id
            or parent.change_id != move.head_revision
            or parent.head_revision != move.head_revision
            or parent.status not in {"applied", "resolved"}
            or parent.tombstone
            or not order_sequence < parent.sequence < part.sequence
            or original.chapter.position != move.position
        ):
            raise DomainError("sync_order", "目录批次的原章稿回执或原章位不符。", 409)
        return _order_content_sha(parent.payload)

    def _consume_directory(self, tx, branch_id, event):
        if _shadow(tx, branch_id, event.object_id):
            return
        try:
            manifest, batch = self._read_directory(branch_id, event)
            sources = self._directory_sources(tx, branch_id, event, manifest)
            pending = tx.execute(
                "SELECT request FROM desktop_sync_directory_outbox WHERE branch_id=?", (branch_id,)
            ).fetchone()
            own = pending and ChapterOrderBatch.model_validate_json(pending["request"]) == batch
            active = {
                row["id"]: row["position"]
                for row in tx.execute(
                    "SELECT id,position FROM chapters WHERE branch_id=? AND archived=0", (branch_id,)
                )
            }
            positions = {move.chapter_id: move.position for move in manifest.moves}
            desired = {move.chapter_id: move.next_position for move in manifest.moves}
            current = {chapter_id: active.get(chapter_id) for chapter_id in positions}
            if not own and current not in (positions, desired):
                raise DomainError("sync_order", "本机相关章位已变化，整组云端目录待核对。", 409)
            apply = not own and current != desired
            if apply and len(set((active | desired).values())) != len(active):
                raise DomainError("sync_order", "本机其他章节占用目标章位，整组云端目录待核对。", 409)
        except (DomainError, ValidationError, UnicodeError) as error:
            reason = error.message if isinstance(error, DomainError) else "目录批次格式无法核对。"
            self._defer(tx, branch_id, event, reason)
            return
        if apply:
            tx.executemany(
                "UPDATE chapters SET position=? WHERE branch_id=? AND id=? AND archived=0",
                [(position, branch_id, chapter_id) for chapter_id, position in desired.items()],
            )
            tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        for part, projection, adopts_prose in sources:
            digest = _digest(projection)
            if adopts_prose:
                _remember(tx, branch_id, part, digest, chapter_base=projection)
            else:
                # Advance only the directory. The older prose CAS base exposes the competing draft for comparison.
                tx.execute(
                    "UPDATE desktop_sync_objects SET local_sha256=? WHERE branch_id=? AND object_id=?",
                    (digest, branch_id, part.object_id),
                )
                tx.execute(
                    "UPDATE desktop_sync_chapter_bases SET chapter=?,order_revision=?,order_sequence=?,remote_sha256=? "
                    "WHERE branch_id=? AND object_id=?",
                    (
                        dumps(projection["chapter"]),
                        part.change_id,
                        part.sequence,
                        _order_content_sha(part.payload),
                        branch_id,
                        part.object_id,
                    ),
                )
            tx.execute(
                "DELETE FROM desktop_sync_directory_parts WHERE branch_id=? AND change_id=?",
                (branch_id, part.change_id),
            )
        if own:
            tx.execute("DELETE FROM desktop_sync_directory_outbox WHERE branch_id=?", (branch_id,))
        tx.execute(
            "DELETE FROM desktop_sync_deferred WHERE branch_id=? AND object_id=?",
            (branch_id, event.object_id),
        )
        _remember(tx, branch_id, event, _digest(event.payload))

    def _consume(self, tx, branch_id, incoming, queued=None):
        object_id = incoming.object_id
        if incoming.change_id.startswith(CHAPTER_ORDER_PART_PREFIX):
            parent = self._deferred_directory_parent(tx, branch_id, object_id, incoming.parent_revision)
            tx.execute(
                "INSERT OR IGNORE INTO desktop_sync_directory_parts VALUES(?,?,?,?,?)",
                (
                    branch_id,
                    incoming.change_id,
                    object_id,
                    incoming.model_dump_json(),
                    parent["receipt"] if parent else None,
                ),
            )
            return
        if object_id.startswith(CHAPTER_DIRECTORY_PREFIX):
            self._consume_directory(tx, branch_id, incoming)
            return
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
        directory_base = (
            _chapter_base(tx, branch_id, object_id, known["remote_revision"])
            if known and object_id.startswith("chapter:")
            else None
        )
        if directory_base and directory_base["order_sequence"] > incoming.sequence:
            # A source receipt may remain pending for prose comparison after its directory has advanced.
            return
        if tx.execute(
            "SELECT 1 FROM desktop_sync_deferred WHERE branch_id=? AND object_id=? "
            "AND json_extract(receipt,'$.sequence')>?",
            (branch_id, object_id, incoming.sequence),
        ).fetchone():
            return
        if self._awaiting_directory(tx, branch_id, object_id, incoming.sequence):
            self._defer(tx, branch_id, incoming, "章节正在等待完整章序批次。")
            return
        tx.execute(
            "DELETE FROM desktop_sync_deferred WHERE branch_id=? AND object_id=?", (branch_id, object_id)
        )
        if object_id.startswith(workflow_archive.WORKFLOW_PREFIXES):
            self._consume_workflow(tx, branch_id, incoming)
            return
        immutable = object_id.startswith(
            (
                "project-version:",
                "settings-version:",
                "outline-version:",
                "story-version:",
                "reference-card-version:",
                SETTING_USE_VERSION,
                *restore_state_desktop.VERSION_PREFIXES,
            )
        )
        if known and known["sequence"] >= incoming.sequence and not immutable:
            return
        if immutable:
            try:
                if object_id.startswith(restore_state_desktop.VERSION_PREFIXES):
                    wrapped = restore_state_sync.parse_restore_state_object(
                        branch_id, object_id, incoming.payload, incoming.tombstone
                    )
                    restore_state_desktop.observe_version(tx, branch_id, wrapped.version)
                elif object_id.startswith(SETTING_USE_VERSION):
                    wrapped = parse_setting_use_object(
                        branch_id, object_id, incoming.payload, incoming.tombstone
                    )
                    setting_use_desktop.observe_version(tx, branch_id, wrapped.version, incoming)
                elif object_id.startswith("reference-card-version:"):
                    wrapped = reference_sync.parse_reference_object(
                        branch_id, object_id, incoming.payload, incoming.tombstone
                    )
                    reference_sync.observe_reference_version(tx, branch_id, wrapped.version, incoming)
                elif object_id.startswith("story-version:"):
                    wrapped = story_sync.parse_story_object(
                        branch_id, object_id, incoming.payload, incoming.tombstone
                    )
                    story_sync.observe_story_version(tx, branch_id, wrapped.version, incoming)
                elif object_id.startswith("outline-version:"):
                    wrapped = outline_sync.parse_outline_object(
                        branch_id, object_id, incoming.payload, incoming.tombstone
                    )
                    outline_sync.observe_outline_version(tx, branch_id, wrapped.version, incoming)
                elif object_id.startswith("settings-version:"):
                    wrapped = settings_sync.parse_settings_object(
                        branch_id, object_id, incoming.payload, incoming.tombstone
                    )
                    settings_sync.observe_settings_version(tx, branch_id, wrapped.version, incoming)
                else:
                    wrapped = parse_project_object(branch_id, object_id, incoming.payload, incoming.tombstone)
                    observe_project_version(tx, branch_id, wrapped.version, incoming)
            except DomainError as error:
                self._defer(tx, branch_id, incoming, error.message)
                return
            _remember(tx, branch_id, incoming, _digest(wrapped.model_dump()))
            return
        if queued is not None and queued["conflict_id"] is None:
            if object_id.startswith(restore_state_desktop.HEAD_PREFIXES):
                value = restore_state_sync.parse_restore_state_object(
                    branch_id, object_id, incoming.payload, incoming.tombstone
                )
                restore_state_desktop.acknowledge_head(tx, branch_id, value)
            chapter = json.loads(queued["request"])["payload"] if object_id.startswith("chapter:") else None
            _remember(tx, branch_id, incoming, queued["local_sha256"], chapter_base=chapter)
            return
        restore_head = object_id.startswith(restore_state_desktop.HEAD_PREFIXES)
        try:
            local = _local(tx, branch_id, object_id)
            local_digest = _digest(local)
            identical = local_digest == _verified_digest(branch_id, incoming)
        except DomainError as error:
            self._defer(tx, branch_id, incoming, error.message)
            return
        clean = local is None if known is None else known["local_sha256"] == local_digest
        if queued is not None and queued["conflict_id"] is not None:
            clean = queued["local_sha256"] == local_digest
        adopted = incoming
        directory_base_position = None
        if (
            not identical
            and not clean
            and queued is None
            and local is not None
            and known
            and object_id.startswith("chapter:")
            and not incoming.tombstone
            and incoming.parent_revision == known["remote_revision"]
        ):
            if directory_base and directory_base["chapter"]:
                position = json.loads(directory_base["chapter"])["position"]
                if (
                    local["chapter"]["position"] != position
                    and incoming.payload["chapter"]["position"] == position
                    and _digest(_chapter_at(local, position)) == known["local_sha256"]
                ):
                    # Take the later prose against the exact known base while retaining the author's local order.
                    clean = True
                    directory_base_position = position
                    adopted = incoming.model_copy(
                        update={"payload": _chapter_at(incoming.payload, local["chapter"]["position"])}
                    )
        if restore_head and not identical and not clean and queued is None:
            try:
                value = restore_state_sync.parse_restore_state_object(
                    branch_id, object_id, incoming.payload, incoming.tombstone
                )
                clean = restore_state_desktop.matches_current(tx, branch_id, value)
            except DomainError as error:
                self._defer(tx, branch_id, incoming, error.message)
                return
        if not identical and not clean:
            self._defer(tx, branch_id, incoming, "本机已有未同步修改，待与远端版本比较。")
            return
        if not identical or restore_head:
            tx.execute("SAVEPOINT apply_sync_object")
            try:
                _apply(tx, self.domain, branch_id, adopted, prose_merge=_is_prose_merge(queued))
            except DomainError as error:
                tx.execute("ROLLBACK TO apply_sync_object")
                self._defer(tx, branch_id, incoming, error.message)
                return
            finally:
                tx.execute("RELEASE apply_sync_object")
        applied = _local(tx, branch_id, object_id)
        if directory_base_position is not None:
            applied = _chapter_at(applied, directory_base_position)
        _remember(tx, branch_id, incoming, _digest(applied), chapter_base=applied)

    def _pull(self, branch_id, origin, token, ledger_id):
        for _ in range(8):
            cursor = self._bound(branch_id, origin)["cursor"]
            page = self._call(
                branch_id, origin, token, f"/pull?after={cursor}&limit=50", Pulled, ledger_id=ledger_id
            )
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
                    if _is_prose_merge(queued) and (
                        row.status != "resolved"
                        or row.conflict_id != queued["conflict_id"]
                        or row.object_id != queued["object_id"]
                    ):
                        raise DomainError("sync_response", "章节合并回执与比较冲突不一致。", 502)
                    self._consume(tx, branch_id, row, queued)
                    if queued:
                        if _is_prose_merge(queued):
                            _record_prose_merge(tx, branch_id, row)
                        tx.execute(
                            "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                            (branch_id, row.change_id),
                        )
                tx.execute("UPDATE desktop_sync SET cursor=? WHERE branch_id=?", (page.cursor, branch_id))
                for deferred in list(
                    tx.execute(
                        "SELECT receipt FROM desktop_sync_deferred WHERE branch_id=? "
                        "ORDER BY json_extract(receipt,'$.sequence')",
                        (branch_id,),
                    )
                ):
                    self._consume(tx, branch_id, Receipt.model_validate_json(deferred["receipt"]))
            if not page.has_more:
                return
        raise DomainError("sync_pages", "尚有云端历史待读取，已保留游标，请再次同步。", 409)

    def _hydrate_chapter_bases(self, branch_id, origin, token, ledger_id):
        with self.domain.db.connect() as tx:
            known = list(
                tx.execute(
                    "SELECT s.* FROM desktop_sync_objects s LEFT JOIN desktop_sync_chapter_bases b "
                    "ON b.branch_id=s.branch_id AND b.object_id=s.object_id AND b.remote_revision=s.remote_revision "
                    "WHERE s.branch_id=? AND s.object_id LIKE 'chapter:%' AND b.object_id IS NULL LIMIT 100",
                    (branch_id,),
                )
            )
        for shadow in known:
            object_id, revision = shadow["object_id"], shadow["remote_revision"]
            try:
                event = self._call(
                    branch_id, origin, token, f"/changes/{revision}", Receipt, ledger_id=ledger_id
                )
                if (
                    event.object_id != object_id
                    or event.change_id != revision
                    or event.head_revision != revision
                ):
                    continue
                chapter = None if event.tombstone else verified_chapter(branch_id, object_id, event.payload)
            except DomainError as error:
                if error.status >= 500:
                    break
                continue
            with self.domain.db.connect(True) as tx:
                live = _shadow(tx, branch_id, object_id)
                if not live or dict(live) != dict(shadow):
                    continue
                projection = chapter.model_dump() if chapter else None
                if projection and shadow["local_sha256"] != _digest(projection):
                    adopted = adopted_chapter_revision(tx, branch_id, chapter.head.id)
                    if adopted:
                        projection["chapter"]["revision"] = adopted.revision
                        projection["head"] = adopted.model_dump()
                if shadow["local_sha256"] == _digest(projection):
                    _write_chapter_base(tx, branch_id, event, projection)

    @staticmethod
    def _closed_directory_moves(moves):
        remaining = {move.position: move for move in moves}
        closed = []
        while remaining:
            path = {}
            position = next(iter(remaining))
            while move := remaining.pop(position, None):
                path[position] = move
                position = move.next_position
            if position in path:
                while position in path:
                    move = path.pop(position)
                    closed.append(move)
                    position = move.next_position
        return closed

    def _stage_directory(self, tx, branch_id):
        active = {
            row["id"]: row["position"]
            for row in tx.execute(
                "SELECT id,position FROM chapters WHERE branch_id=? AND archived=0", (branch_id,)
            )
        }
        old = {}
        unknown = set()
        for row in tx.execute(
            "SELECT s.object_id,s.remote_revision,b.object_id AS based,b.chapter "
            "FROM desktop_sync_objects s LEFT JOIN desktop_sync_chapter_bases b ON b.branch_id=s.branch_id "
            "AND b.object_id=s.object_id AND b.remote_revision=s.remote_revision "
            "WHERE s.branch_id=? AND s.object_id LIKE 'chapter:%'",
            (branch_id,),
        ):
            identifier = row["object_id"].removeprefix("chapter:")
            if not row["based"]:
                unknown.add(identifier)
            elif row["chapter"]:
                old[identifier] = (json.loads(row["chapter"])["position"], row["remote_revision"])
        held = {
            row["object_id"]
            for row in tx.execute(
                "SELECT object_id FROM desktop_sync_directory_parts WHERE branch_id=?", (branch_id,)
            )
        }
        pending = tx.execute(
            "SELECT request,error FROM desktop_sync_directory_outbox WHERE branch_id=?", (branch_id,)
        ).fetchone()
        if pending:
            batch = ChapterOrderBatch.model_validate_json(pending["request"])
            if pending["error"] and all(
                active.get(move.chapter_id) == old.get(move.chapter_id, (None,))[0] for move in batch.moves
            ):
                tx.execute("DELETE FROM desktop_sync_directory_outbox WHERE branch_id=?", (branch_id,))
                pending = None
            else:
                held.update(f"chapter:{move.chapter_id}" for move in batch.moves)
        moves = [
            ChapterOrderMove(
                chapter_id=identifier, position=before, next_position=active[identifier], head_revision=head
            )
            for identifier, (before, head) in old.items()
            if identifier in active and active[identifier] != before
        ]
        if unknown or active.keys() != old.keys():
            moves = self._closed_directory_moves(moves)
        if len(moves) < 2:
            return held | {f"chapter:{identifier}" for identifier in unknown}
        participants = {f"chapter:{move.chapter_id}" for move in moves}
        if (
            not pending
            and not participants & held
            and all(
                not tx.execute(
                    "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND object_id=? UNION ALL "
                    "SELECT 1 FROM desktop_sync_conflicts WHERE branch_id=? AND object_id=? LIMIT 1",
                    (branch_id, object_id, branch_id, object_id),
                ).fetchone()
                for object_id in participants
            )
        ):
            batch = ChapterOrderBatch(change_id=uid(), moves=sorted(moves, key=lambda move: move.position))
            tx.execute(
                "INSERT INTO desktop_sync_directory_outbox(branch_id,request) VALUES(?,?)",
                (branch_id, batch.model_dump_json()),
            )
        return held | participants | {f"chapter:{identifier}" for identifier in unknown}

    def _prepare(self, branch_id):
        with self.domain.db.connect(True) as tx:
            if not tx.execute(
                "SELECT 1 FROM desktop_sync_dirty WHERE branch_id=? UNION ALL "
                "SELECT 1 FROM desktop_sync_workflow_pending WHERE branch_id=? LIMIT 1",
                (branch_id, branch_id),
            ).fetchone():
                return
            directory_members = self._stage_directory(tx, branch_id)
            object_ids = [
                f"chapter:{row['id']}"
                for row in tx.execute(
                    "SELECT id FROM chapters WHERE branch_id=? ORDER BY position,id", (branch_id,)
                )
            ]
            native_ids = [
                (row["revision"], row["id"])
                for row in tx.execute(
                    "SELECT r.id,r.revision FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
                    "WHERE c.branch_id=?",
                    (branch_id,),
                )
            ]
            sources = source_revisions(tx, branch_id)
            histories = [
                f"revision:{identifier}"
                for _, identifier in sorted([*native_ids, *((row.revision, row.id) for row in sources)])
            ]
            documents = {
                f"evidence:{doc['id']}": {"format": FORMAT, "kind": "evidence", "document": doc}
                for doc in _evidence(tx, branch_id)
                if doc["kind"] != "project" and len(dumps(doc).encode("utf-8")) <= MAX_OBJECT_BYTES - 4096
            }
            projects = {
                f"project-version:{value.version.id}"
                if isinstance(value, ProjectVersionObject)
                else f"project:{branch_id}": value.model_dump()
                for value in draft_project_objects(tx, branch_id)
            }
            settings = {
                f"settings-version:{value.version.id}"
                if isinstance(value, settings_sync.SettingsVersionObject)
                else f"settings:{branch_id}": value.model_dump()
                for value in settings_sync.draft_settings_objects(tx, branch_id)
            }
            tx.execute("DELETE FROM desktop_sync_story_pending WHERE branch_id=?", (branch_id,))
            stories = {}
            unavailable = set()
            for wrapped in story_sync.draft_story_objects(
                tx,
                branch_id,
                on_pending=lambda record_id, reason: self._pending_story(
                    tx, branch_id, f"story:{record_id}", reason
                ),
            ):
                value = wrapped.model_dump()
                if isinstance(wrapped, story_sync.StoryVersionObject):
                    version = wrapped.version
                    if ({version.parent_id, *version.dependencies.values()} & unavailable) or (
                        len(dumps(value).encode("utf-8")) > MAX_OBJECT_BYTES
                    ):
                        unavailable.add(version.id)
                        continue
                    key = f"story-version:{version.id}"
                else:
                    key = f"story:{wrapped.record_id}"
                    if wrapped.revision_id in unavailable:
                        self._pending_story(
                            tx, branch_id, key, "故事版本或其依赖超过同步体积限制，已保留本机记录。"
                        )
                        continue
                stories[key] = value
            outlines = {
                f"outline-version:{value.version.id}"
                if isinstance(value, outline_sync.OutlineVersionObject)
                else f"outline:{branch_id}": value.model_dump()
                for value in outline_sync.draft_outline_objects(tx, branch_id)
            }
            handcraft = {}
            for value in reference_sync.draft_reference_objects(tx, branch_id):
                if isinstance(value, reference_sync.ReferenceCardVersionObject):
                    key = f"reference-card-version:{value.version.id}"
                else:
                    key = reference_sync.reference_head_object_id(branch_id, value.session_id, value.card_id)
                handcraft[key] = value.model_dump()
            workflows = self._prepare_workflow(tx, branch_id)
            setting_uses = {
                (SETTING_USE_VERSION + value.version.id)
                if isinstance(value, SettingUseVersionObject)
                else (SETTING_USE_HEAD + value.slot_id): value.model_dump()
                for value in setting_use_desktop.draft_objects(tx, branch_id)
            }
            restore_states = {}
            unavailable = set()
            for entry in restore_state_desktop.draft_objects(
                tx, branch_id, lambda address, reason: self._pending_workflow(tx, branch_id, address, reason)
            ):
                address = restore_state_sync.object_id(entry)
                value = entry.model_dump()
                if isinstance(entry, restore_state_sync.VERSION_OBJECTS):
                    if (entry.version.parent_id in unavailable) or len(
                        dumps(value).encode("utf-8")
                    ) > MAX_OBJECT_BYTES:
                        unavailable.add(entry.version.id)
                        self._pending_workflow(
                            tx, branch_id, address, "事实集合、篇幅版本或其父修订超过同步体积限制。"
                        )
                        continue
                elif entry.revision_id in unavailable:
                    self._pending_workflow(
                        tx, branch_id, address, "事实集合或篇幅版本超过同步体积限制，已保留本机记录。"
                    )
                    continue
                restore_states[address] = value
            formal = {
                **projects,
                **handcraft,
                **settings,
                **stories,
                **outlines,
                **setting_uses,
                **restore_states,
                **workflows,
            }
            blocked = False
            for object_id in [f"branch:{branch_id}", *histories, *object_ids, *formal, *documents]:
                if object_id in directory_members:
                    blocked = True
                    continue
                if tx.execute(
                    "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND object_id=? UNION ALL "
                    "SELECT 1 FROM desktop_sync_conflicts WHERE branch_id=? AND object_id=? LIMIT 1",
                    (branch_id, object_id, branch_id, object_id),
                ).fetchone():
                    blocked = True
                    continue
                value = formal.get(object_id) or documents.get(object_id) or _local(tx, branch_id, object_id)
                if sync_has_reference_selection(value):
                    self._pending_workflow(tx, branch_id, object_id, REFERENCE_SCOPE)
                    continue
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
            if not blocked:
                # Any newer edit made while sending opens a fresh dirty marker in its own transaction.
                tx.execute("DELETE FROM desktop_sync_dirty WHERE branch_id=?", (branch_id,))

    def _prepare_workflow(self, tx, branch_id):
        objects = {}
        # Chapters and source revisions enter the outbox earlier. Within this family,
        # the verified inspection must be present before any completion that cites it.
        for sync_kind, kind, identifier in workflow_archive.workflow_ids(tx, branch_id):
            object_id = f"{sync_kind}:{identifier}"
            if _shadow(tx, branch_id, object_id):
                continue
            try:
                entry = workflow_archive.read_workflow_entry(tx, branch_id, kind, identifier)
                value = workflow_archive.workflow_object(branch_id, entry)
                if self._reference_scope_blocked(tx, branch_id, value):
                    raise DomainError("sync_workflow_reference_scope", REFERENCE_SCOPE, 409)
                if len(dumps(value).encode("utf-8")) > MAX_OBJECT_BYTES:
                    raise DomainError("sync_object_size", "体检或交付历史超过单次同步大小，已保留本机。")
                self._verify_workflow(tx, branch_id, entry)
                if isinstance(entry, workflow_archive.ArchivedCompletion):
                    prerequisite = f"workflow-inspection:{entry.manifest.inspection_id}"
                    if prerequisite not in objects and not _shadow(tx, branch_id, prerequisite):
                        raise DomainError("sync_workflow_dependency", "完本交付需等待其原体检单先同步。")
            except (DomainError, ValidationError) as error:
                reason = error.message if isinstance(error, DomainError) else "本机历史记录格式无法核对。"
                self._pending_workflow(tx, branch_id, object_id, reason)
                continue
            objects[object_id] = value
        return objects

    def _push_directory(self, branch_id, origin, token, ledger_id):
        with self.domain.db.connect() as tx:
            row = tx.execute(
                "SELECT request,error FROM desktop_sync_directory_outbox WHERE branch_id=?", (branch_id,)
            ).fetchone()
        if not row or row["error"]:
            return False
        # run has verified the current ledger and pulled existing receipts before retrying this immutable request.
        batch = ChapterOrderBatch.model_validate_json(row["request"])
        try:
            result = self._call(
                branch_id,
                origin,
                token,
                "/chapter-order-batches",
                Receipt,
                method="POST",
                data=batch.model_dump(),
                ledger_id=ledger_id,
            )
        except DomainError as error:
            if error.code not in {
                "sync_chapter_order_changed",
                "sync_chapter_order_limit",
                "sync_object_size",
                "sync_request_size",
                "sync_replay",
                "sync_reserved",
            } or error.status not in {409, 413}:
                raise
            with self.domain.db.connect(True) as tx:
                tx.execute(
                    "UPDATE desktop_sync_directory_outbox SET error=? WHERE branch_id=? AND request=?",
                    (error.message, branch_id, row["request"]),
                )
            return False
        _, confirmed = self._read_directory(branch_id, result)
        if confirmed != batch:
            raise DomainError("sync_response", "目录批次回执与本机提交不一致。", 502)
        # The pull is the only write path; it verifies and adopts every child before clearing the request.
        return True

    def _push(self, branch_id, origin, token, ledger_id):
        with self.domain.db.connect() as tx:
            todo = [
                dict(row)
                for row in tx.execute(
                    "SELECT * FROM desktop_sync_outbox WHERE branch_id=? ORDER BY rowid LIMIT 100",
                    (branch_id,),
                )
            ]
        for row in todo:
            request = json.loads(row["request"])
            if row["object_id"].startswith((*workflow_archive.WORKFLOW_PREFIXES, "evidence:")):
                with self.domain.db.connect(True) as tx:
                    if self._reference_scope_blocked(tx, branch_id, request.get("payload")):
                        self._pending_workflow(tx, branch_id, row["object_id"], REFERENCE_SCOPE)
                        tx.execute(
                            "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                            (branch_id, row["change_id"]),
                        )
                        continue
            path = f"/conflicts/{row['conflict_id']}/resolve" if row["conflict_id"] else "/push"
            try:
                result = self._call(
                    branch_id, origin, token, path, Receipt, method="POST", data=request, ledger_id=ledger_id
                )
            except DomainError as error:
                known_rejections = PROSE_MERGE_REJECTIONS if _is_prose_merge(row) else CONFLICT_REJECTIONS
                if row["conflict_id"] and known_rejections.get(error.code) == error.status:
                    with self.domain.db.connect(True) as tx:
                        tx.execute(
                            "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                            (branch_id, row["change_id"]),
                        )
                if row["object_id"].startswith(("story:", "story-version:")) and error.code in {
                    "sync_story",
                    "sync_story_dependency",
                    "sync_story_evidence",
                }:
                    with self.domain.db.connect(True) as tx:
                        self._pending_story(tx, branch_id, row["object_id"], error.message)
                    continue
                if row["object_id"].startswith((SETTING_USE_HEAD, SETTING_USE_VERSION)) and (
                    error.code in SETTING_USE_REJECTIONS and error.status == 409
                ):
                    with self.domain.db.connect(True) as tx:
                        self._pending_workflow(tx, branch_id, row["object_id"], error.message)
                    continue
                if row["object_id"].startswith(restore_state_desktop.PREFIXES) and error.code in {
                    restore_state_sync.DEPENDENCY,
                    restore_state_sync.INTEGRITY,
                    "sync_object_size",
                }:
                    with self.domain.db.connect(True) as tx:
                        self._pending_workflow(tx, branch_id, row["object_id"], error.message)
                        if row["conflict_id"] is None and row["object_id"].startswith(
                            restore_state_desktop.HEAD_PREFIXES
                        ):
                            try:
                                current = _local(tx, branch_id, row["object_id"])
                            except DomainError:
                                continue
                            if _digest(current) != row["local_sha256"]:
                                tx.execute(
                                    "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                                    (branch_id, row["change_id"]),
                                )
                                tx.execute(
                                    "INSERT OR IGNORE INTO desktop_sync_dirty VALUES(?,?)",
                                    (branch_id, "restore-state"),
                                )
                    continue
                if error.code == "sync_workflow_reference_scope" or (
                    row["object_id"].startswith(workflow_archive.WORKFLOW_PREFIXES)
                    and error.code in WORKFLOW_REJECTIONS
                ):
                    with self.domain.db.connect(True) as tx:
                        self._pending_workflow(tx, branch_id, row["object_id"], error.message)
                        if error.code != "sync_workflow_dependency":
                            tx.execute(
                                "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                                (branch_id, row["change_id"]),
                            )
                    continue
                raise
            if result.change_id != row["change_id"] or result.object_id != row["object_id"]:
                raise DomainError("sync_response", "同步回执和本机准备提交的对象不一致。", 502)
            if _is_prose_merge(row):
                # Apply the resolved head only after its new immutable source reaches the pull cursor.
                continue
            with self.domain.db.connect(True) as tx:
                self._consume(tx, branch_id, result, row)
                tx.execute(
                    "DELETE FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                    (branch_id, result.change_id),
                )
                tx.execute(
                    "DELETE FROM desktop_sync_story_pending WHERE branch_id=? AND object_id=?",
                    (branch_id, result.object_id),
                )

    def run(self, branch_id, origin, token):
        with self._operation():
            ledger_id = self._verified_ledger(branch_id, origin, token)
            self._hydrate_chapter_bases(branch_id, origin, token, ledger_id)
            self._pull(branch_id, origin, token, ledger_id)
            self._prepare(branch_id)
            sent_directory = self._push_directory(branch_id, origin, token, ledger_id)
            self._push(branch_id, origin, token, ledger_id)
            self._pull(branch_id, origin, token, ledger_id)
            if sent_directory:
                self._prepare(branch_id)
                self._push(branch_id, origin, token, ledger_id)
                self._pull(branch_id, origin, token, ledger_id)
            self._observe_chapter_order(branch_id, origin, token, ledger_id)
            return self._state(branch_id)

    def chapter_order_conflicts(self, branch_id, origin, token):
        ledger_id = self._verified_ledger(branch_id, origin, token)
        return self._call(
            branch_id, origin, token, "/chapter-order-conflicts", ChapterOrderConflicts, ledger_id=ledger_id
        )

    @staticmethod
    def _check_order_sources(tx, branch_id, data):
        from .outline_workspace import _chapter_plans, _decode, _latest

        outline = _decode(_latest(tx, branch_id, confirmed=True))
        if outline and any(
            row["chapter_id"] == data.chapter_id for _, _, row in _chapter_plans(outline["plan"])
        ):
            raise DomainError(
                "sync_chapter_order_scheduled", ORDER_REJECTIONS["sync_chapter_order_scheduled"], 409
            )
        for chapter_id in data.heads:
            object_id = f"chapter:{chapter_id}"
            local = _local(tx, branch_id, object_id)
            known = _shadow(tx, branch_id, object_id)
            clean = known["local_sha256"] == _digest(local) if known else local is None
            unsettled = tx.execute(
                "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND object_id=? "
                "UNION SELECT 1 FROM desktop_sync_conflicts WHERE branch_id=? AND object_id=? LIMIT 1",
                (branch_id, object_id, branch_id, object_id),
            ).fetchone()
            if not clean or unsettled:
                raise DomainError(
                    "sync_pending", "这些章节有待同步的本机修改或分歧，请先核对正文与目录。", 409
                )

    @staticmethod
    def _verify_order_receipt(branch_id, data, receipt):
        parent = data.heads.get(data.chapter_id)
        if (
            parent is None
            or receipt.change_id != data.change_id
            or receipt.head_revision != data.change_id
            or receipt.parent_revision != parent
            or receipt.object_id != f"chapter:{data.chapter_id}"
            or receipt.status != "applied"
            or receipt.conflict_id is not None
            or receipt.tombstone
        ):
            raise DomainError("sync_response", "章位调整回执与所选章节及原章位不符。", 502)
        chapter = verified_chapter(branch_id, receipt.object_id, receipt.payload)
        if not data.position < chapter.chapter.position <= MAX_CURSOR:
            raise DomainError("sync_response", "章位调整回执与所选章节及原章位不符。", 502)
        return chapter

    def _observe_chapter_order(self, branch_id, origin, token, ledger_id):
        with self.domain.db.connect() as tx:
            row = tx.execute(
                "SELECT request FROM desktop_sync_chapter_order WHERE branch_id=?", (branch_id,)
            ).fetchone()
        if not row:
            return
        encoded = row["request"]
        cursor = self._bound(branch_id, origin)["cursor"]
        try:
            request = ChapterOrderResolution.model_validate_json(encoded)
            observed = self._call(
                branch_id, origin, token, f"/changes/{request.change_id}", Receipt, ledger_id=ledger_id
            )
            after = self._verify_order_receipt(branch_id, request, observed)
            if not 0 < observed.sequence <= cursor:
                return
            parent_id = request.heads[request.chapter_id]
            parent = self._call(
                branch_id, origin, token, f"/changes/{parent_id}", Receipt, ledger_id=ledger_id
            )
            if (
                parent.change_id != parent_id
                or parent.head_revision != parent_id
                or parent.object_id != observed.object_id
                or parent.status not in {"applied", "resolved"}
                or parent.tombstone
                or not 0 < parent.sequence < observed.sequence
            ):
                return
            before = verified_chapter(branch_id, parent.object_id, parent.payload)
        except DomainError, ValidationError, UnicodeError:
            # These optional reads cannot establish the outcome. Keep the original request for later.
            return
        moved = before.chapter.model_copy(update={"position": after.chapter.position})
        if before.chapter.position != request.position or moved != after.chapter or before.head != after.head:
            return
        with self.domain.db.connect(True) as tx:
            tx.execute(
                "DELETE FROM desktop_sync_chapter_order WHERE branch_id=? AND request=?",
                (branch_id, encoded),
            )

    def resolve_chapter_order(self, branch_id, origin, token, data):
        with self._operation():
            ledger_id = self._verified_ledger(branch_id, origin, token)
            request = data.model_dump(include=set(ChapterOrderResolution.model_fields))
            if data.chapter_id not in data.heads:
                raise DomainError("sync_input", "请选择刚核对的冲突章节。", 422)
            encoded = json.dumps(request, sort_keys=True)
            try:
                receipt = self._call(
                    branch_id, origin, token, f"/receipts/{data.change_id}", Receipt, ledger_id=ledger_id
                )
            except DomainError as error:
                if error.code != "sync_receipt" or error.status != 404:
                    raise
                receipt = None
            with self.domain.db.connect(True) as tx:
                pending = tx.execute(
                    "SELECT request,token_sha256 FROM desktop_sync_chapter_order WHERE branch_id=?",
                    (branch_id,),
                ).fetchone()
                if pending and pending["request"] != encoded:
                    raise DomainError("sync_pending", "有一项章位调整等待回执，请先重试原提交。", 409)
                if not receipt:
                    if pending and pending["token_sha256"] != _sha(token):
                        raise DomainError("sync_pending", "请使用提交本次章位调整的设备凭据重试。", 409)
                    self._check_order_sources(tx, branch_id, data)
                if not pending:
                    tx.execute(
                        "INSERT INTO desktop_sync_chapter_order VALUES(?,?,?)",
                        (branch_id, encoded, _sha(token)),
                    )

            if not receipt or not pending:
                # With no matching local request, let the ledger also verify the replay fingerprint.
                try:
                    receipt = self._call(
                        branch_id,
                        origin,
                        token,
                        "/chapter-order-conflicts/resolve",
                        Receipt,
                        method="POST",
                        data=request,
                        ledger_id=ledger_id,
                    )
                except DomainError as error:
                    rejected = error.code in {"sync_replay", "sync_chapter", *ORDER_REJECTIONS}
                    too_large = error.code in {"sync_object_size", "sync_request_size"}
                    if (rejected and error.status == 409) or (too_large and error.status == 413):
                        with self.domain.db.connect(True) as tx:
                            tx.execute(
                                "DELETE FROM desktop_sync_chapter_order WHERE branch_id=? AND request=?",
                                (branch_id, encoded),
                            )
                    raise
            self._verify_order_receipt(branch_id, data, receipt)

            self._pull(branch_id, origin, token, ledger_id)
            with self.domain.db.connect(True) as tx:
                tx.execute(
                    "DELETE FROM desktop_sync_chapter_order WHERE branch_id=? AND request=?",
                    (branch_id, encoded),
                )
            return {"status": self._state(branch_id), "receipt": receipt}

    def compare(self, branch_id, origin, token, identifier):
        ledger_id = self._verified_ledger(branch_id, origin, token)
        remote = self._call(
            branch_id, origin, token, f"/conflicts/{identifier}", ComparedConflict, ledger_id=ledger_id
        )
        with self.domain.db.connect() as tx:
            tx.execute("BEGIN")
            local = _local(tx, branch_id, remote.object_id)
            result = {"remote": remote.model_dump(), "local": local, "local_sha256": _digest(local)}
            readers = {
                "project": read_project_version,
                "settings": settings_sync.read_settings_version,
                "outline": outline_sync.read_outline_version,
                "story": story_sync.read_story_version,
                "reference-card": reference_sync.read_reference_version,
                "setting-use": setting_use_desktop.read_version,
                "fact-set-head": restore_state_desktop.read_version,
                "length-target-head": restore_state_desktop.read_version,
            }
            family = remote.object_id.partition(":")[0]
            if family in readers and (
                family in {"story", "reference-card", "setting-use", "fact-set-head", "length-target-head"}
                or remote.object_id == f"{family}:{branch_id}"
            ):
                read_version = readers[family]
                versions = {}
                for payload in (
                    local,
                    remote.current.payload,
                    remote.incoming.payload,
                    remote.base.payload if remote.base else None,
                ):
                    if payload and (identifier := payload.get("revision_id")):
                        if version := read_version(tx, branch_id, identifier):
                            versions[identifier] = version.model_dump()
                label = (
                    "reference"
                    if family == "reference-card"
                    else family.removesuffix("-head").replace("-", "_")
                )
                result[f"{label}_versions"] = versions
            return result

    def resolve(self, branch_id, origin, token, identifier, data):
        with self._operation():
            ledger_id = self._verified_ledger(branch_id, origin, token)
            merge = data.choice == "merge"
            fingerprint = (
                _sha(dumps([identifier, data.parent_revision, data.local_sha256, data.content]))
                if merge
                else None
            )
            with self.domain.db.connect() as tx:
                record = (
                    _prose_merge_record(tx, branch_id, data.idempotency_key, fingerprint) if merge else None
                )
            if record and record["receipt"]:
                return {**self._state(branch_id), "merge_receipt": json.loads(record["receipt"])}
            remote = self._call(
                branch_id, origin, token, f"/conflicts/{identifier}", ComparedConflict, ledger_id=ledger_id
            )
            with self.domain.db.connect(True) as tx:
                record = (
                    _prose_merge_record(tx, branch_id, data.idempotency_key, fingerprint) if merge else None
                )
                if record and record["receipt"]:
                    return {**self._state(branch_id), "merge_receipt": json.loads(record["receipt"])}
                existing = tx.execute(
                    "SELECT * FROM desktop_sync_outbox WHERE branch_id=? AND object_id=?",
                    (branch_id, remote.object_id),
                ).fetchone()
                if existing:
                    if (
                        existing["conflict_id"] != identifier
                        or existing["change_id"] != data.idempotency_key
                        or _is_prose_merge(existing) != merge
                    ):
                        raise DomainError("sync_pending", "该对象有待确认的提交，请先运行同步。", 409)
                else:
                    local = _local(tx, branch_id, remote.object_id)
                    if (
                        data.local_sha256 != _digest(local)
                        or data.parent_revision != remote.head_revision
                        or remote.resolved_by
                    ):
                        raise DomainError("sync_compare", "本机或云端内容已变化，请重新比较。", 409)
                    if (
                        data.choice == "local"
                        and local
                        and local["kind"] == "project-head"
                        and not has_project_source(tx, branch_id, local["revision_id"])
                    ):
                        raise DomainError(
                            "sync_pending", "本机立项刚有新确认，请先同步源修订，再比较冲突。", 409
                        )
                    if (
                        data.choice == "local"
                        and local
                        and local["kind"] == "settings-head"
                        and not settings_sync.has_settings_source(tx, branch_id, local["revision_id"])
                    ):
                        raise DomainError(
                            "sync_pending", "本机设定刚有新确认，请先同步源修订，再比较冲突。", 409
                        )
                    if (
                        data.choice == "local"
                        and local
                        and local["kind"] == "outline-head"
                        and not outline_sync.has_outline_source(tx, branch_id, local["revision_id"])
                    ):
                        raise DomainError(
                            "sync_pending", "本机大纲刚有新确认，请先同步源修订，再比较冲突。", 409
                        )
                    if (
                        data.choice == "local"
                        and local
                        and local["kind"] == "story-head"
                        and not story_sync.has_story_source(tx, branch_id, local["revision_id"])
                    ):
                        raise DomainError(
                            "sync_pending", "本机故事记录刚有新确认，请先同步源修订，再比较冲突。", 409
                        )
                    if (
                        data.choice == "local"
                        and remote.object_id.startswith("reference-card:")
                        and (
                            not local
                            or not reference_sync.has_reference_source(tx, branch_id, local["revision_id"])
                        )
                    ):
                        raise DomainError(
                            "sync_pending", "本机没有已同步的手法卡修订，请先同步源修订，再比较冲突。", 409
                        )
                    if (
                        data.choice == "local"
                        and remote.object_id.startswith(SETTING_USE_HEAD)
                        and (
                            not local
                            or not _shadow(tx, branch_id, SETTING_USE_VERSION + local["revision_id"])
                        )
                    ):
                        raise DomainError(
                            "sync_pending", "本机没有已同步的设定使用修订，请先同步源修订，再比较冲突。", 409
                        )
                    if data.choice == "local" and remote.object_id.startswith(
                        restore_state_desktop.HEAD_PREFIXES
                    ):
                        prefix = (
                            restore_state_desktop.FACT_VERSION
                            if remote.object_id.startswith(restore_state_desktop.FACT_HEAD)
                            else restore_state_desktop.LENGTH_VERSION
                        )
                        if not local or not _shadow(tx, branch_id, prefix + local["revision_id"]):
                            raise DomainError(
                                "sync_pending", "请先同步本机事实或篇幅的来源修订，再比较冲突。", 409
                            )
                    request = {
                        "change_id": data.idempotency_key,
                        "parent_revision": data.parent_revision,
                        "choice": "merge" if data.choice == "local" else data.choice,
                    }
                    if data.choice == "local":
                        request.update(payload=local, tombstone=local is None)
                    if merge:
                        for payload in (local, remote.current.payload, remote.incoming.payload):
                            verified_chapter(branch_id, remote.object_id, payload)
                        request.update(choice="merge_prose", content=data.content)
                        if not record:
                            tx.execute(
                                "INSERT INTO desktop_sync_prose_merges VALUES(?,?,?,NULL)",
                                (branch_id, data.idempotency_key, fingerprint),
                            )
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
            self._push(branch_id, origin, token, ledger_id)
            self._pull(branch_id, origin, token, ledger_id)
            state = self._state(branch_id)
            if merge:
                with self.domain.db.connect() as tx:
                    record = _prose_merge_record(tx, branch_id, data.idempotency_key, fingerprint)
                if not record or not record["receipt"]:
                    raise DomainError("sync_pending", "章节合并已提交，等待同一提交编号的确认回执。", 409)
                state["merge_receipt"] = json.loads(record["receipt"])
            return state

    def fork(self, branch_id, origin, token, identifier, data):
        with self._operation():
            self._bound(branch_id, origin)
            fingerprint = fork_fingerprint(branch_id, identifier, data)
            with self.domain.db.connect() as tx:
                prior = fork_receipt(tx, data.idempotency_key, fingerprint)
            if prior:
                return prior
            ledger_id = self._verified_ledger(branch_id, origin, token)
            remote = self._call(
                branch_id, origin, token, f"/conflicts/{identifier}", ComparedConflict, ledger_id=ledger_id
            )
            if remote.head_revision != data.parent_revision or remote.resolved_by:
                raise DomainError("sync_compare", "云端内容已变化，请重新比较。", 409)
            with self.domain.db.connect(True) as tx:
                if prior := fork_receipt(tx, data.idempotency_key, fingerprint):
                    return prior
                local = _local(tx, branch_id, remote.object_id)
                if data.local_sha256 != _digest(local):
                    raise DomainError("sync_compare", "本机内容已变化，请重新比较。", 409)
                side = {"current": remote.current, "incoming": remote.incoming, "base": remote.base}
                candidate = side.get(data.choice)
                selected = verified_chapter(
                    branch_id,
                    remote.object_id,
                    local if data.choice == "local" else candidate.payload if candidate else None,
                )
                return fork_chapter_tx(tx, self.domain, branch_id, identifier, data, selected, fingerprint)


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

    @app.post(prefix + "/revoke-device")
    def revoke_sync_device(branch_id: str, data: dict = Body()):
        _, origin, token = _parse(Connection, data)
        return client.revoke_device(branch_id, origin, token)

    @app.post(prefix + "/run")
    def run_sync(branch_id: str, data: dict = Body()):
        _, origin, token = _parse(Connection, data)
        return client.run(branch_id, origin, token)

    @app.post(prefix + "/chapter-order-conflicts")
    def read_chapter_order(branch_id: str, data: dict = Body()) -> ChapterOrderConflicts:
        _, origin, token = _parse(Connection, data)
        return client.chapter_order_conflicts(branch_id, origin, token)

    @app.post(prefix + "/chapter-order-conflicts/resolve")
    def resolve_chapter_order(branch_id: str, data: dict = Body()):
        request, origin, token = _parse(ResolveChapterOrder, data)
        return client.resolve_chapter_order(branch_id, origin, token, request)

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

    @app.post(prefix + "/conflicts/{conflict_id}/fork")
    def fork_sync(branch_id: str, conflict_id: str, data: dict = Body()):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", conflict_id):
            raise DomainError("sync_conflict", "同步冲突编号无效。")
        request, origin, token = _parse(ForkChapter, data)
        return client.fork(branch_id, origin, token, conflict_id, request)
