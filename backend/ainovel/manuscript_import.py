"""Preview-first, version-checked long manuscript import with immutable originals."""

import hashlib
import json
from pathlib import Path
import re
from time import time
from typing import Annotated, Literal

from fastapi import File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .db import DomainError, dumps, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS manuscript_imports(
 id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),filename TEXT NOT NULL,
 encoding TEXT NOT NULL,raw_hash TEXT NOT NULL,raw BLOB NOT NULL,content TEXT NOT NULL,
 base_story_revision INTEGER NOT NULL,segments TEXT NOT NULL,receipt TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS manuscript_import_drafts(
 import_id TEXT PRIMARY KEY REFERENCES manuscript_imports(id),revision INTEGER NOT NULL,mappings TEXT NOT NULL);
INSERT OR IGNORE INTO extension_migrations VALUES('manuscript-import/1',unixepoch());
"""
MAX_FILE = 20 * 1024 * 1024
HEADING = re.compile(
    r"^(?:#{1,4}\s+\S.*|第[零〇一二三四五六七八九十百千万两\d]+[章节回卷部集](?:[\s：:、.．].*|\S.{0,100})?|[序终尾楔]章(?:[\s：:].*)?)$"
)


def slice_encoded(encoded, start, end):
    if start < 0 or end <= start or end * 2 > len(encoded):
        raise DomainError("import_range", "章界超出原文范围。")
    try:
        return encoded[start * 2 : end * 2].decode("utf-16-le")
    except UnicodeDecodeError:
        raise DomainError("import_range", "章界不能拆开表情或扩展汉字。")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Segment(Strict):
    title: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Mapping(Segment):
    action: Literal["append", "replace", "skip"] = "append"
    chapter_id: str | None = None
    base_revision: int | None = Field(default=None, ge=0)
    allow_duplicate: bool = False


class Confirm(Strict):
    draft_revision: int | None = Field(default=None, ge=0)
    client_request_id: str = Field(min_length=8, max_length=128)
    base_story_revision: int = Field(ge=0)
    mappings: list[Mapping] = Field(min_length=1, max_length=1000)


class EditDraft(Strict):
    base_revision: int = Field(ge=0)
    mappings: list[Mapping] = Field(min_length=1, max_length=1000)


def split_manuscript(content, fallback):
    offsets, lines, current = [], content.splitlines(keepends=True), 0
    for line in lines:
        if HEADING.fullmatch(line.strip()):
            offsets.append((current, line.strip().lstrip("#").strip()[:200]))
        current += len(line.encode("utf-16-le")) // 2
    if not offsets:
        offsets = [(0, fallback)]
    elif offsets[0][0] > 0:
        offsets.insert(0, (0, "卷首／前言（请核对）"))
    end = len(content.encode("utf-16-le")) // 2
    return [
        {"title": title, "start": start, "end": offsets[i + 1][0] if i + 1 < len(offsets) else end}
        for i, (start, title) in enumerate(offsets)
    ]


def get_import(domain, branch_id, identifier):
    return domain.db.one(
        "SELECT * FROM manuscript_imports WHERE id=? AND branch_id=?", (identifier, branch_id)
    )


def public_preview(domain, row):
    draft = domain.db.all(
        "SELECT revision,mappings FROM manuscript_import_drafts WHERE import_id=?", (row["id"],)
    )
    segments = json.loads(row["segments"])
    encoded = row["content"].encode("utf-16-le")
    existing = domain.db.all(
        "SELECT id,title,revision,position,content FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position",
        (row["branch_id"],),
    )
    for segment in segments:
        text = slice_encoded(encoded, segment["start"], segment["end"])
        segment["chars"] = len(text)
        segment["excerpt"] = text[:250]
        segment["same_title"] = [c["id"] for c in existing if c["title"] == segment["title"]]
        segment["same_content"] = [c["id"] for c in existing if c["content"] == text]
    return {
        k: row[k]
        for k in ("id", "branch_id", "filename", "encoding", "raw_hash", "base_story_revision", "created")
    } | {
        "length_utf16": len(row["content"].encode("utf-16-le")) // 2,
        "segments": segments,
        "draft": {"revision": draft[0]["revision"], "mappings": json.loads(draft[0]["mappings"])}
        if draft
        else None,
        "existing": [{k: c[k] for k in ("id", "title", "revision", "position")} for c in existing],
        "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
        "coverage": "按明确标题行建议拆章；可能把卷标题或Markdown小节分开，请核对并调整。确认前不写入章节。原文件字节与解码文本保留，标题行仍在正文中。",
    }


def confirm_import(domain, branch_id, identifier, data):
    with domain.db.connect(True) as tx:
        row = tx.execute(
            "SELECT * FROM manuscript_imports WHERE id=? AND branch_id=?", (identifier, branch_id)
        ).fetchone()
        if not row:
            raise DomainError("not_found", "导入预览不存在。", 404)
        if row["receipt"]:
            receipt = json.loads(row["receipt"])
            if receipt["request"] != data.model_dump():
                raise DomainError("import_committed", "此预览已确认其他映射，请读取回执或重新预览。", 409)
            return receipt
        current = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
        saved_draft = tx.execute(
            "SELECT revision FROM manuscript_import_drafts WHERE import_id=?", (identifier,)
        ).fetchone()
        if data.draft_revision is not None and data.draft_revision != (saved_draft[0] if saved_draft else 0):
            raise DomainError("import_draft_changed", "映射在其他窗口更新，未导入，请重新载入比较。", 409)
        if current != data.base_story_revision or current != row["base_story_revision"]:
            raise DomainError("story_changed", "预览后作品已改变，未写入任何章节。请重新预览比较。", 409)
        cursor, prepared, targets = 0, [], set()
        existing = [
            dict(c)
            for c in tx.execute(
                "SELECT * FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position", (branch_id,)
            )
        ]
        by_id = {c["id"]: c for c in existing}
        content_seen = {c["content"] for c in existing if c["content"]}
        encoded = row["content"].encode("utf-16-le")
        for mapping in data.mappings:
            if mapping.start != cursor or mapping.end <= mapping.start:
                raise DomainError(
                    "import_range", "分段必须连续覆盖原文，不能重叠或遗漏；不导入的段请明确跳过。"
                )
            cursor = mapping.end
            text = slice_encoded(encoded, mapping.start, mapping.end)
            if not mapping.title.strip():
                raise DomainError("import_title", "章节标题不能只有空白。")
            if mapping.action != "skip" and (not text.strip() or len(text) > 250_000):
                raise DomainError("import_length", "待导入章节须有正文且不超过25万字符；请调整章界。")
            if mapping.action == "replace":
                old = by_id.get(mapping.chapter_id)
                if not old or old["revision"] != mapping.base_revision:
                    raise DomainError("revision_conflict", "替换目标已改变或不属于当前工作版本。", 409)
                if old["id"] in targets:
                    raise DomainError("import_mapping", "同一章节只能映射一个导入段。")
                targets.add(old["id"])
            elif mapping.chapter_id is not None or mapping.base_revision is not None:
                raise DomainError("import_mapping", "仅替换操作可指定目标章节和修订。")
            if mapping.action != "skip":
                unchanged_target = (
                    mapping.action == "replace" and by_id[mapping.chapter_id]["content"] == text
                )
                if text in content_seen and not mapping.allow_duplicate and not unchanged_target:
                    raise DomainError("import_duplicate", "检测到重复正文，须明确允许重复或跳过该段。", 409)
                content_seen.add(text)
            prepared.append((mapping, text))
        if cursor != len(row["content"].encode("utf-16-le")) // 2:
            raise DomainError("import_range", "映射未覆盖完整原文；请保留显式跳过范围。")
        if all(m.action == "skip" for m, _ in prepared):
            raise DomainError("import_empty", "未选择任何待导入段。")
        position = tx.execute(
            "SELECT COALESCE(MAX(position),0) FROM chapters WHERE branch_id=?", (branch_id,)
        ).fetchone()[0]
        results = []
        for mapping, text in prepared:
            if mapping.action == "skip":
                results.append(
                    {"action": "skip", "title": mapping.title, "start": mapping.start, "end": mapping.end}
                )
                continue
            if mapping.action == "replace":
                chapter = by_id[mapping.chapter_id]
                revision = domain.revise_tx(tx, chapter, text, "长篇导入替换；原稿历史保留")
                tx.execute("UPDATE chapters SET title=? WHERE id=?", (mapping.title, chapter["id"]))
                chapter_id = chapter["id"]
            else:
                position += 1
                chapter_id = uid()
                tx.execute(
                    "INSERT INTO chapters(id,branch_id,position,title) VALUES(?,?,?,?)",
                    (chapter_id, branch_id, position, mapping.title),
                )
                tx.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                    (uid(), chapter_id, 0, "", "导入前空稿", time()),
                )
                chapter = dict(tx.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
                revision = domain.revise_tx(tx, chapter, text, "长篇导入追加")
            results.append(
                {
                    "action": mapping.action,
                    "chapter_id": chapter_id,
                    "revision": revision,
                    "title": mapping.title,
                    "start": mapping.start,
                    "end": mapping.end,
                    "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
        receipt = {
            "id": identifier,
            "request": data.model_dump(),
            "raw_hash": row["raw_hash"],
            "results": results,
            "story_revision": tx.execute(
                "SELECT story_revision FROM branches WHERE id=?", (branch_id,)
            ).fetchone()[0],
            "created": time(),
            "atomic": True,
            "note": "所选范围在同一事务完成；跳过范围明确记录，替换保留原稿修订。",
        }
        tx.execute("UPDATE manuscript_imports SET receipt=? WHERE id=?", (dumps(receipt), identifier))
        return receipt


def register_routes(app, domain):
    @app.patch("/api/branches/{branch_id}/import-previews/{identifier}/draft")
    def save_draft(branch_id: str, identifier: str, data: EditDraft):
        with domain.db.connect(True) as tx:
            row = tx.execute(
                "SELECT receipt FROM manuscript_imports WHERE id=? AND branch_id=?", (identifier, branch_id)
            ).fetchone()
            if not row or row[0]:
                raise DomainError("import_committed", "预览不存在或已完成，不能修改映射草稿。", 409)
            old = tx.execute(
                "SELECT revision FROM manuscript_import_drafts WHERE import_id=?", (identifier,)
            ).fetchone()
            if data.base_revision != (old[0] if old else 0):
                raise DomainError("import_draft_changed", "映射在其他窗口更新，当前输入保留。", 409)
            tx.execute(
                "INSERT INTO manuscript_import_drafts VALUES(?,?,?) ON CONFLICT(import_id) DO UPDATE SET revision=excluded.revision,mappings=excluded.mappings",
                (identifier, data.base_revision + 1, dumps([m.model_dump() for m in data.mappings])),
            )
        return public_preview(domain, get_import(domain, branch_id, identifier))

    @app.post("/api/branches/{branch_id}/import-previews", status_code=201)
    async def preview(
        branch_id: str, file: Annotated[UploadFile, File()], encoding: Annotated[str, Form()] = "utf-8-sig"
    ):
        branch = domain.branch(branch_id)
        if encoding not in {"utf-8-sig", "utf-16", "gb18030"}:
            raise DomainError("encoding", "请选择UTF-8、UTF-16或GB18030。")
        raw = await file.read(MAX_FILE + 1)
        filename = Path(file.filename or "导入稿.txt").name
        if len(raw) > MAX_FILE or Path(filename).suffix.lower() not in {".txt", ".md"}:
            raise DomainError("format", "长篇导入支持20MB内TXT或Markdown。")
        try:
            content = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError, UnicodeError:
            raise DomainError("encoding", "无法按所选编码读取，请核对编码后重新预览。")
        if not content.strip() or "\x00" in content:
            raise DomainError("format", "未读到有效纯文本，请核对文件及编码。")
        segments = split_manuscript(content, Path(filename).stem[:200] or "导入章节")
        if len(segments) > 1000:
            raise DomainError("import_limit", "自动识别超过1000段，请分批预览。")
        identifier = uid()
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO manuscript_imports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    branch_id,
                    filename,
                    encoding,
                    hashlib.sha256(raw).hexdigest(),
                    raw,
                    content,
                    branch["story_revision"],
                    dumps(segments),
                    None,
                    time(),
                ),
            )
        return public_preview(domain, get_import(domain, branch_id, identifier))

    @app.get("/api/branches/{branch_id}/import-previews")
    def previews(branch_id: str):
        domain.branch(branch_id)
        return domain.db.all(
            "SELECT id,filename,encoding,base_story_revision,created,receipt IS NOT NULL AS committed FROM manuscript_imports WHERE branch_id=? ORDER BY created DESC LIMIT 100",
            (branch_id,),
        )

    @app.get("/api/branches/{branch_id}/import-previews/{identifier}")
    def get_preview(branch_id: str, identifier: str):
        return public_preview(domain, get_import(domain, branch_id, identifier))

    @app.post("/api/branches/{branch_id}/import-previews/{identifier}/read")
    def read_segment(branch_id: str, identifier: str, data: Segment):
        row = get_import(domain, branch_id, identifier)
        return {"content": slice_encoded(row["content"].encode("utf-16-le"), data.start, data.end)}

    @app.post("/api/branches/{branch_id}/import-previews/{identifier}/confirm")
    def confirm(branch_id: str, identifier: str, data: Confirm):
        return confirm_import(domain, branch_id, identifier, data)
