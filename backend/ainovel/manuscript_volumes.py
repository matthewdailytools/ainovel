"""Named chapter boundaries for the workspace directory."""

from pydantic import Field

from .db import DomainError, uid
from .models import Input

SCHEMA = """
CREATE TABLE IF NOT EXISTS manuscript_volumes(
 id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),title TEXT NOT NULL,
 start_chapter_id TEXT NOT NULL REFERENCES chapters(id),revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(branch_id,start_chapter_id));
INSERT OR IGNORE INTO extension_migrations VALUES('manuscript-volumes/1',unixepoch());
"""


class VolumeInput(Input):
    title: str = Field(min_length=1, max_length=120)
    start_chapter_id: str = Field(min_length=1, max_length=64)


class VolumeRevision(Input):
    base_revision: int = Field(ge=1)


class VolumeUpdate(VolumeInput, VolumeRevision):
    pass


class ManuscriptVolume(Input):
    id: str
    title: str
    start_chapter_id: str
    revision: int


def clean_title(title):
    title = title.strip()
    if not title or any(ord(char) < 32 for char in title):
        raise DomainError("volume_title", "请输入单行卷名。")
    return title


def validate_start(tx, branch_id, chapter_id):
    if not tx.execute(
        "SELECT 1 FROM chapters WHERE id=? AND branch_id=? AND archived=0",
        (chapter_id, branch_id),
    ).fetchone():
        raise DomainError("volume_start", "请选择本工作版本中仍在目录内的起始章。", 409)


def latest_volume(tx, branch_id, volume_id, base_revision):
    row = tx.execute(
        "SELECT id,title,start_chapter_id,revision FROM manuscript_volumes WHERE id=? AND branch_id=?",
        (volume_id, branch_id),
    ).fetchone()
    if not row:
        raise DomainError("not_found", "分卷不存在。", 404)
    if row["revision"] != base_revision:
        raise DomainError("revision_conflict", "分卷已有新版本，请重新读取后再保存。", 409)
    return dict(row)


def register_routes(app, domain):
    path = "/api/branches/{branch_id}/volumes"

    @app.get(path, response_model=list[ManuscriptVolume])
    def manuscript_volumes(branch_id: str):
        domain.branch(branch_id)
        return domain.db.all(
            """SELECT v.id,v.title,v.start_chapter_id,v.revision FROM manuscript_volumes v
            JOIN chapters c ON c.id=v.start_chapter_id WHERE v.branch_id=?
            ORDER BY c.archived,c.position,c.id""",
            (branch_id,),
        )

    @app.post(path, status_code=201, response_model=ManuscriptVolume)
    def create_volume(branch_id: str, data: VolumeInput):
        title = clean_title(data.title)
        with domain.db.connect(True) as tx:
            validate_start(tx, branch_id, data.start_chapter_id)
            existing = tx.execute(
                "SELECT id,title,start_chapter_id,revision FROM manuscript_volumes "
                "WHERE branch_id=? AND start_chapter_id=?",
                (branch_id, data.start_chapter_id),
            ).fetchone()
            if existing:
                if existing["title"] == title:
                    return dict(existing)
                raise DomainError("volume_start_used", "此章已有一个分卷名称。", 409)
            row = {"id": uid(), "title": title, "start_chapter_id": data.start_chapter_id, "revision": 1}
            tx.execute(
                "INSERT INTO manuscript_volumes(id,branch_id,title,start_chapter_id) VALUES(?,?,?,?)",
                (row["id"], branch_id, title, data.start_chapter_id),
            )
            return row

    @app.put(path + "/{volume_id}", response_model=ManuscriptVolume)
    def update_volume(branch_id: str, volume_id: str, data: VolumeUpdate):
        title = clean_title(data.title)
        with domain.db.connect(True) as tx:
            current = latest_volume(tx, branch_id, volume_id, data.base_revision)
            if current["start_chapter_id"] != data.start_chapter_id:
                validate_start(tx, branch_id, data.start_chapter_id)
                if tx.execute(
                    "SELECT 1 FROM manuscript_volumes WHERE branch_id=? AND start_chapter_id=?",
                    (branch_id, data.start_chapter_id),
                ).fetchone():
                    raise DomainError("volume_start_used", "此章已有一个分卷名称。", 409)
            if (current["title"], current["start_chapter_id"]) == (title, data.start_chapter_id):
                return current
            revision = current["revision"] + 1
            tx.execute(
                "UPDATE manuscript_volumes SET title=?,start_chapter_id=?,revision=? WHERE id=?",
                (title, data.start_chapter_id, revision, volume_id),
            )
            return {
                "id": volume_id,
                "title": title,
                "start_chapter_id": data.start_chapter_id,
                "revision": revision,
            }

    @app.delete(path + "/{volume_id}", response_model=ManuscriptVolume)
    def remove_volume(branch_id: str, volume_id: str, data: VolumeRevision):
        with domain.db.connect(True) as tx:
            current = latest_volume(tx, branch_id, volume_id, data.base_revision)
            tx.execute("DELETE FROM manuscript_volumes WHERE id=?", (volume_id,))
            return current
