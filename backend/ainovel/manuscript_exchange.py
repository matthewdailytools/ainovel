"""Portable manuscript and revision history for desktop and offline Web."""

from hashlib import sha256
import json
from time import time
from typing import Annotated, Literal

from fastapi import Query
from pydantic import Field, StringConstraints

from .db import DomainError, dumps, uid
from .models import Input
from .outline_portability import OutlineHistory
from .project_portability import Identifier, ProjectHistory
from .project_sync import export_project_history, restore_project_history
from .reference_portability import HashId as ReferenceVersionId, ReferenceHistory
from .settings_portability import SettingsHistory
from .story_portability import PortableStoryArchive
from .workflow_archive import WorkflowHistory
from .workflow_privacy import REFERENCE_SCOPE, evidence_has_reference_selection

FORMAT = "ainovel-manuscript/1"
EVIDENCE = "portable_workbench_evidence"
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ReferenceSelection = Annotated[
    list[ReferenceVersionId] | None,
    Query(max_length=100, description="明确选带原文证据的手法卡修订 ID。默认只导出手法卡来源地址。"),
]


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


class RevisionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["revision"]
    branch_id: Identifier
    revision: PortableRevision


class EvidenceChapter(Input):
    id: Identifier
    revision: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)


class PortableEvidence(Input):
    id: Identifier
    kind: Literal["project", "settings", "outline", "inspection", "completion"]
    title: Title
    body: str = Field(min_length=1, max_length=250_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chapters: list[EvidenceChapter] = Field(default_factory=list, max_length=5000)


class ManuscriptPackage(Input):
    format: Literal["ainovel-manuscript/1"]
    novel: PortableWork
    branch: PortableBranch
    chapters: list[PortableChapter] = Field(max_length=5000)
    revisions: list[PortableRevision] = Field(max_length=50_000)
    evidence: list[PortableEvidence] = Field(
        default_factory=list, max_length=100, exclude_if=lambda value: not value
    )
    source_revisions: list[PortableRevision] = Field(
        default_factory=list, max_length=50_000, exclude_if=lambda value: not value
    )
    project_history: ProjectHistory | None = Field(default=None, exclude_if=lambda value: value is None)
    settings_history: SettingsHistory | None = Field(default=None, exclude_if=lambda value: value is None)
    outline_history: OutlineHistory | None = Field(default=None, exclude_if=lambda value: value is None)
    story_history: PortableStoryArchive | None = Field(default=None, exclude_if=lambda value: value is None)
    workflow_history: WorkflowHistory | None = Field(default=None, exclude_if=lambda value: value is None)
    reference_history: ReferenceHistory | None = Field(default=None, exclude_if=lambda value: value is None)


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def native_revision(tx, branch_id, revision_id):
    row = tx.execute(
        "SELECT r.*, p.id AS parent_id FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
        "LEFT JOIN revisions p ON p.chapter_id=r.chapter_id AND p.revision=r.revision-1 "
        "WHERE c.branch_id=? AND r.id=?",
        (branch_id, revision_id),
    ).fetchone()
    return PortableRevision(**dict(row), sha256=_digest(row["content"])) if row else None


def source_revision(tx, branch_id, revision_id):
    row = tx.execute(
        "SELECT payload FROM manuscript_revision_sources WHERE branch_id=? AND id=?",
        (branch_id, revision_id),
    ).fetchone()
    return (
        PortableRevision.model_validate_json(row["payload"])
        if row
        else native_revision(tx, branch_id, revision_id)
    )


def source_revisions(tx, branch_id):
    return [
        PortableRevision.model_validate_json(row["payload"])
        for row in tx.execute(
            "SELECT payload FROM manuscript_revision_sources WHERE branch_id=? ORDER BY rowid", (branch_id,)
        )
    ]


def _source_link(row, parent):
    return (
        row.parent_id is None
        if row.revision == 0
        else parent is not None
        and parent.chapter_id == row.chapter_id
        and parent.revision + 1 == row.revision
    )


def store_source_revision(tx, branch_id, row):
    existing = source_revision(tx, branch_id, row.id)
    chapter = tx.execute("SELECT branch_id FROM chapters WHERE id=?", (row.chapter_id,)).fetchone()
    if (existing and existing != row) or (chapter and chapter["branch_id"] != branch_id):
        raise DomainError("sync_history", "相同来源修订编号的内容或归属不一致。", 409)
    parent = source_revision(tx, branch_id, row.parent_id) if row.parent_id else None
    if _digest(row.content) != row.sha256 or not _source_link(row, parent):
        raise DomainError("sync_history", "来源稿件哈希无效或缺少连续父修订。", 409)
    if not existing:
        tx.execute(
            "INSERT INTO manuscript_revision_sources VALUES(?,?,?,?)",
            (branch_id, row.id, row.chapter_id, row.model_dump_json()),
        )


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
    for row in package.source_revisions:
        if row.id in revisions or _digest(row.content) != row.sha256:
            raise DomainError("manuscript_integrity", "来源修订编号重复或正文校验值无效。")
        revisions[row.id] = row
    for row in package.source_revisions:
        if not _source_link(row, revisions.get(row.parent_id)):
            raise DomainError("manuscript_history", "来源稿件缺少连续父修订。")
    seen = set()
    for document in package.evidence:
        if document.id in seen or _digest(document.body) != document.sha256:
            raise DomainError("manuscript_evidence", "依据编号重复或内容校验值无效。")
        seen.add(document.id)
        for reference in document.chapters:
            chapter = histories.get(reference.id, [])
            target = (
                revisions.get(reference.revision_id)
                if reference.revision_id
                else chapter[reference.revision]
                if reference.revision < len(chapter)
                else None
            )
            if not target or (target.chapter_id, target.revision, target.sha256) != (
                reference.id,
                reference.revision,
                reference.sha256,
            ):
                raise DomainError("manuscript_evidence", "依据与包中引用的章稿修订不一致。")
    if package.reference_history:
        from .reference_sync import validate_reference_archive

        validate_reference_archive(package.reference_history, package.branch.id)
    if package.settings_history:
        from .settings_archive import validate_settings_history

        validate_settings_history(package.settings_history, package.project_history, revisions)
    if package.outline_history:
        from .outline_archive import validate_outline_history

        validate_outline_history(
            package.outline_history,
            package.project_history,
            package.settings_history,
            package.chapters,
            package.reference_history,
        )
    if package.story_history:
        from .story_archive import validate_story_archive

        validate_story_archive(
            package.story_history, package.branch.id, revisions, {row.id: row for row in package.revisions}
        )
    if package.workflow_history:
        from .workflow_archive import validate_workflow_history

        validate_workflow_history(package.workflow_history, package.branch.id, revisions)
    return histories


def _evidence(tx, branch_id):
    documents = {}
    for row in tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid",
        (branch_id, EVIDENCE),
    ):
        document = json.loads(row["payload"])
        documents[document["id"]] = document

    def add(identifier, kind, title, value, chapters=()):
        body = json.dumps(value, ensure_ascii=False, indent=2)
        if len(body) > 250_000:
            return
        references = []
        for row in chapters:
            reference = {key: row[key] for key in ("id", "revision", "sha256")}
            version = tx.execute(
                "SELECT r.id,r.content FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
                "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
                (branch_id, row["id"], row["revision"]),
            ).fetchone()
            if version and _digest(version["content"]) == row["sha256"]:
                reference["revision_id"] = version["id"]
            references.append(reference)
        documents[identifier] = {
            "id": identifier,
            "kind": kind,
            "title": title,
            "body": body,
            "sha256": _digest(body),
            "chapters": references,
        }

    for kind, title, artifact_kind in (
        ("project", "立项卡", "project_brief"),
        ("settings", "作者确认的设定", "conception_completion"),
        ("inspection", "十处对账体检", "workflow_inspection"),
        ("completion", "完本交付清单", "book_completion"),
    ):
        row = tx.execute(
            "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
            "ORDER BY rowid DESC LIMIT 1",
            (branch_id, artifact_kind),
        ).fetchone()
        if not row:
            continue
        payload = json.loads(row["payload"])
        chapters = ()
        if kind == "settings":
            payload = {"revision": payload["revision"], "categories": payload["categories"]}
        if kind == "inspection":
            chapters = payload["chapters"]
            payload = {"scope": payload["scope"], "items": payload["items"]}
        if kind == "completion":
            chapters = payload["manifest"]["chapters"]
            payload = {key: payload[key] for key in ("manifest", "checks")}
        add(row["id"], kind, title, payload, chapters)

    outline = tx.execute(
        "SELECT id,revision,payload FROM outline_revisions WHERE branch_id=? AND confirmed=1 "
        "ORDER BY revision DESC LIMIT 1",
        (branch_id,),
    ).fetchone()
    if outline:
        add(
            outline["id"], "outline", f"确认大纲 · 修订 {outline['revision']}", json.loads(outline["payload"])
        )
    return list(documents.values())[-100:]


def store_portable_evidence(tx, branch_id, document):
    if _digest(document.body) != document.sha256:
        raise DomainError("manuscript_evidence", "依据内容校验失败。", 409)
    for reference in document.chapters:
        if reference.revision_id:
            row = source_revision(tx, branch_id, reference.revision_id)
            exact = row and (row.chapter_id, row.revision, row.sha256) == (
                reference.id,
                reference.revision,
                reference.sha256,
            )
        else:
            local = tx.execute(
                "SELECT r.content FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
                "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
                (branch_id, reference.id, reference.revision),
            ).fetchone()
            exact = local and _digest(local["content"]) == reference.sha256
        if not exact:
            raise DomainError("manuscript_evidence", "需要原始稿件包中的准确来源修订，先保留云端依据。", 409)
    existing = next((entry for entry in _evidence(tx, branch_id) if entry["id"] == document.id), None)
    data = document.model_dump()
    if existing == data:
        return
    if existing:
        raise DomainError("manuscript_evidence", "相同来源编号已有不同的只读依据。", 409)
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "SELECT ?,novel_id,id,?,?,'{}','accepted',? FROM branches WHERE id=?",
        (uid(), EVIDENCE, dumps(data), time(), branch_id),
    )


def export_manuscript(domain, branch_id, *, include_reference_evidence_for=(), before_read=None):
    from .outline_archive import export_outline_history, validate_outline_history
    from .reference_sync import export_reference_archive
    from .settings_archive import export_settings_history, validate_settings_history
    from .story_archive import export_story_archive, validate_story_archive
    from .workflow_archive import (
        export_workflow_history,
        has_frozen_reference_selection,
        validate_workflow_history,
    )

    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        branch = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not branch:
            raise DomainError("not_found", "工作版本不存在。", 404)
        if before_read:
            before_read(tx, branch_id)
        workflow = export_workflow_history(tx, branch_id)
        evidence = _evidence(tx, branch_id)
        if (workflow and any(map(has_frozen_reference_selection, workflow.inspections))) or any(
            map(evidence_has_reference_selection, evidence)
        ):
            raise DomainError("manuscript_reference_scope", REFERENCE_SCOPE + "本次稿件包未导出。", 409)
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
        package = ManuscriptPackage.model_validate(
            {
                "format": FORMAT,
                "novel": dict(novel),
                "branch": dict(branch),
                "chapters": chapters,
                "revisions": revisions,
                "evidence": evidence,
                "source_revisions": source_revisions(tx, branch_id),
                "project_history": export_project_history(tx, branch_id),
                "settings_history": export_settings_history(tx, branch_id),
                "outline_history": export_outline_history(tx, branch_id),
                "story_history": export_story_archive(tx, branch_id),
                "workflow_history": workflow,
                "reference_history": export_reference_archive(
                    tx, branch_id, include_evidence_for=include_reference_evidence_for
                ),
            }
        )
        if package.settings_history:
            validate_settings_history(
                package.settings_history,
                package.project_history,
                {row.id: row for row in [*package.revisions, *package.source_revisions]},
            )
        if package.outline_history:
            validate_outline_history(
                package.outline_history,
                package.project_history,
                package.settings_history,
                package.chapters,
                package.reference_history,
            )
        if package.story_history or package.workflow_history:
            native = {row.id: row for row in package.revisions}
            revisions = {**native, **{row.id: row for row in package.source_revisions}}
            if package.story_history:
                validate_story_archive(package.story_history, branch_id, revisions, native)
            if package.workflow_history:
                validate_workflow_history(package.workflow_history, branch_id, revisions)
        return package


def import_manuscript(domain, package):
    histories = _index(package)
    outline = None
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
        tx.executemany(
            "INSERT INTO manuscript_revision_sources VALUES(?,?,?,?)",
            [(branch.id, row.id, row.chapter_id, row.model_dump_json()) for row in package.source_revisions],
        )
        tx.executemany(
            "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
            "VALUES(?,?,?,?,?,'{}','accepted',?)",
            [
                (uid(), novel.id, branch.id, EVIDENCE, dumps(document.model_dump()), time())
                for document in package.evidence
            ],
        )
        if package.project_history:
            restore_project_history(tx, branch.id, package.project_history)
        if package.reference_history:
            from .reference_sync import import_reference_archive

            import_reference_archive(tx, branch.id, package.reference_history)
        if package.story_history:
            from .story_archive import import_story_archive

            import_story_archive(tx, branch.id, package.story_history)
        if package.settings_history:
            from .settings_archive import import_settings_history

            import_settings_history(tx, branch.id, package.settings_history)
        if package.outline_history:
            from .outline_archive import import_outline_history

            outline = import_outline_history(tx, branch.id, package.outline_history)
        if package.workflow_history:
            from .workflow_archive import import_workflow_history

            import_workflow_history(tx, branch.id, package.workflow_history)
    result = {"id": novel.id, "branch_id": branch.id}
    if outline:
        result["outline_restore"] = outline
    return result


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/manuscript-package")
    def get_package(
        branch_id: str,
        include_reference_evidence_for: ReferenceSelection = None,
    ) -> ManuscriptPackage:
        return export_manuscript(
            domain, branch_id, include_reference_evidence_for=include_reference_evidence_for or ()
        )

    @app.post("/api/manuscript-packages/import", status_code=201)
    def import_package(data: ManuscriptPackage):
        return import_manuscript(domain, data)

    @app.post("/api/branches/{branch_id}/manuscript-outline/restore")
    def restore_outline(branch_id: str):
        from .outline_archive import restore_outline_archive

        domain.branch(branch_id)
        with domain.db.connect(True) as tx:
            restored = restore_outline_archive(tx, branch_id)
            if not restored:
                raise DomainError("not_found", "没有可恢复的稿件包大纲。", 404)
            return restored

    @app.get("/api/branches/{branch_id}/manuscript-evidence")
    def get_evidence(branch_id: str) -> list[PortableEvidence]:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return _evidence(tx, branch_id)

    @app.get("/api/branches/{branch_id}/manuscript-sources/{revision_id}")
    def get_source(branch_id: str, revision_id: Identifier) -> PortableRevision:
        with domain.db.connect() as tx:
            row = source_revision(tx, branch_id, revision_id)
            if not row:
                raise DomainError("not_found", "这个工作版本中没有该来源修订。", 404)
            return row
