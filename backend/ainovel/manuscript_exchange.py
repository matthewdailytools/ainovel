"""Portable manuscript and revision history for desktop and offline Web."""

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .db import DomainError
from .models import Input

FORMAT = "ainovel-manuscript/1"
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PortableWork(Input):
    id: Identifier
    title: Title
    brief: str = Field(max_length=30_000)
    created: float = Field(ge=0, allow_inf_nan=False)


class PortableBranch(Input):
    id: Identifier
    novel_id: Identifier
    name: Title
    mode: Literal["advisory", "assisted", "automatic"]
    story_revision: int = Field(ge=0, strict=True)
    created: float = Field(ge=0, allow_inf_nan=False)


class PortableChapter(Input):
    id: Identifier
    branch_id: Identifier
    position: int = Field(ge=1, strict=True)
    title: Title
    revision: int = Field(ge=0, strict=True)
    archived: Literal[0, 1]
    stale: Literal[0, 1]


class PortableRevision(Input):
    id: Identifier
    chapter_id: Identifier
    revision: int = Field(ge=0, strict=True)
    parent_id: Identifier | None
    content: str = Field(max_length=250_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cause: str = Field(max_length=4000)
    created: float = Field(ge=0, allow_inf_nan=False)


class ManuscriptPackage(Input):
    format: Literal["ainovel-manuscript/1"]
    novel: PortableWork
    branch: PortableBranch
    chapters: list[PortableChapter] = Field(max_length=5000)
    revisions: list[PortableRevision] = Field(max_length=50_000)


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _index(package):
    if package.branch.novel_id != package.novel.id:
        raise DomainError("manuscript_owner", "工作版本须属于包中的作品。")
    chapters = {row.id: row for row in package.chapters}
    if len(chapters) != len(package.chapters) or any(
        row.branch_id != package.branch.id for row in chapters.values()
    ):
        raise DomainError("manuscript_owner", "章节标识须唯一且属于包中的工作版本。")
    positions = [row.position for row in chapters.values() if not row.archived]
    if len(positions) != len(set(positions)):
        raise DomainError("manuscript_order", "未归档章节的位置须唯一。")
    revisions = {row.id: row for row in package.revisions}
    if len(revisions) != len(package.revisions):
        raise DomainError("manuscript_revision", "修订标识须唯一。")
    histories = {key: [] for key in chapters}
    for row in revisions.values():
        if row.chapter_id not in chapters or _digest(row.content) != row.sha256:
            raise DomainError("manuscript_integrity", "修订的章节引用或正文校验值无效。")
        histories[row.chapter_id].append(row)
    for key, rows in histories.items():
        rows.sort(key=lambda row: row.revision)
        if not rows or rows[-1].revision != chapters[key].revision:
            raise DomainError("manuscript_history", "章稿当前修订缺失。")
        for index, row in enumerate(rows):
            if row.revision != index or row.parent_id != (rows[index - 1].id if index else None):
                raise DomainError("manuscript_history", "章稿修订须从零开始完整连接。")
    return histories


def export_manuscript(domain, branch_id):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        branch = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not branch:
            raise DomainError("not_found", "工作版本不存在。", 404)
        novel = tx.execute("SELECT * FROM novels WHERE id=?", (branch["novel_id"],)).fetchone()
        chapters = [
            dict(row)
            for row in tx.execute(
                "SELECT id,branch_id,position,title,revision,archived,stale FROM chapters "
                "WHERE branch_id=? ORDER BY position,id",
                (branch_id,),
            )
        ]
        revisions = []
        previous = {}
        for row in tx.execute(
            "SELECT r.* FROM revisions r JOIN chapters c ON c.id=r.chapter_id WHERE c.branch_id=? "
            "ORDER BY r.chapter_id,r.revision",
            (branch_id,),
        ):
            revisions.append(
                {**dict(row), "parent_id": previous.get(row["chapter_id"]), "sha256": _digest(row["content"])}
            )
            previous[row["chapter_id"]] = row["id"]
        return ManuscriptPackage.model_validate(
            {
                "format": FORMAT,
                "novel": dict(novel),
                "branch": dict(branch),
                "chapters": chapters,
                "revisions": revisions,
            }
        )


def import_manuscript(domain, package):
    histories = _index(package)
    with domain.db.connect(True) as tx:
        if (
            tx.execute("SELECT 1 FROM novels WHERE id=?", (package.novel.id,)).fetchone()
            or tx.execute("SELECT 1 FROM branches WHERE id=?", (package.branch.id,)).fetchone()
            or any(
                tx.execute("SELECT 1 FROM chapters WHERE id=?", (row.id,)).fetchone()
                for row in package.chapters
            )
            or any(
                tx.execute("SELECT 1 FROM revisions WHERE id=?", (row.id,)).fetchone()
                for row in package.revisions
            )
        ):
            raise DomainError("manuscript_conflict", "此包标识已在本机使用，导入保留现有作品。", 409)
        novel, branch = package.novel, package.branch
        tx.execute("INSERT INTO novels VALUES(?,?,?,?)", (novel.id, novel.title, novel.brief, novel.created))
        tx.execute(
            "INSERT INTO branches(id,novel_id,name,mode,story_revision,created) VALUES(?,?,?,?,?,?)",
            (branch.id, branch.novel_id, branch.name, branch.mode, branch.story_revision, branch.created),
        )
        tx.executemany(
            "INSERT INTO chapters(id,branch_id,position,title,revision,content,archived,stale) VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    row.id,
                    row.branch_id,
                    row.position,
                    row.title,
                    row.revision,
                    histories[row.id][-1].content,
                    row.archived,
                    row.stale,
                )
                for row in package.chapters
            ],
        )
        tx.executemany(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
            [
                (row.id, row.chapter_id, row.revision, row.content, row.cause, row.created)
                for row in package.revisions
            ],
        )
    return {"id": novel.id, "branch_id": branch.id}


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/manuscript-package")
    def get_package(branch_id: str) -> ManuscriptPackage:
        return export_manuscript(domain, branch_id)

    @app.post("/api/manuscript-packages/import", status_code=201)
    def import_package(data: ManuscriptPackage):
        return import_manuscript(domain, data)
