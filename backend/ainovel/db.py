from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from time import time
from uuid import uuid4


def uid():
    return uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class DomainError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS novels(id TEXT PRIMARY KEY,title TEXT NOT NULL,brief TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS branches(id TEXT PRIMARY KEY,novel_id TEXT NOT NULL REFERENCES novels(id),name TEXT NOT NULL,
 mode TEXT NOT NULL DEFAULT 'advisory',story_revision INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chapters(id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),
 position INTEGER NOT NULL,title TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,content TEXT NOT NULL DEFAULT '',
 archived INTEGER NOT NULL DEFAULT 0,stale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,chapter_id TEXT NOT NULL REFERENCES chapters(id),
 revision INTEGER NOT NULL,content TEXT NOT NULL,cause TEXT NOT NULL,created REAL NOT NULL,UNIQUE(chapter_id,revision));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,novel_id TEXT NOT NULL REFERENCES novels(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),status TEXT NOT NULL,payload TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',
 error TEXT NOT NULL DEFAULT '',attempt TEXT,lease_until REAL,cancel INTEGER NOT NULL DEFAULT 0,pause INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS task_steps(task_id TEXT NOT NULL REFERENCES tasks(id),fingerprint TEXT NOT NULL,
 result TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(task_id,fingerprint));
CREATE TABLE IF NOT EXISTS task_requests(key TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS processes(token TEXT PRIMARY KEY,role TEXT NOT NULL,pid INTEGER NOT NULL,started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,novel_id TEXT NOT NULL REFERENCES novels(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),chapter_id TEXT REFERENCES chapters(id),kind TEXT NOT NULL,
 base_revision INTEGER,payload TEXT NOT NULL,basis TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'candidate',
 task_id TEXT REFERENCES tasks(id),created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY,artifact_id TEXT NOT NULL REFERENCES artifacts(id),result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),
 chapter_id TEXT NOT NULL REFERENCES chapters(id),source_revision INTEGER NOT NULL,subject TEXT NOT NULL,kind TEXT NOT NULL,
 statement TEXT NOT NULL,truth TEXT NOT NULL,story_time TEXT NOT NULL,evidence TEXT NOT NULL,
 evidence_start INTEGER NOT NULL,evidence_end INTEGER NOT NULL,stale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,chapter_id TEXT NOT NULL REFERENCES chapters(id),
 source_revision INTEGER NOT NULL,category TEXT NOT NULL,text TEXT NOT NULL,source_quote TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assessments(id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),
 story_revision INTEGER NOT NULL,category TEXT NOT NULL,result TEXT NOT NULL,note TEXT NOT NULL,minutes TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reading_state(branch_id TEXT PRIMARY KEY REFERENCES branches(id),chapter_id TEXT,
 cursor INTEGER NOT NULL DEFAULT 0,note TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY,title TEXT NOT NULL,source TEXT NOT NULL,file_hash TEXT NOT NULL,
 filename TEXT NOT NULL,format TEXT NOT NULL,status TEXT NOT NULL,coverage TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS source_units(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),
 position INTEGER NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS excerpts(id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES source_units(id),
 start INTEGER NOT NULL,end INTEGER NOT NULL,quote TEXT NOT NULL,note TEXT NOT NULL,novel_id TEXT REFERENCES novels(id),created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_task ON task_events(task_id,id);
CREATE INDEX IF NOT EXISTS chapters_branch ON chapters(branch_id,position);
CREATE INDEX IF NOT EXISTS facts_branch ON facts(branch_id,chapter_id,stale);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            existing = db.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
            if existing and db.execute("SELECT version FROM schema_version").fetchone()[0] > 1:
                raise DomainError("future_schema", "数据库版本高于当前程序，已停止写入。")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            from .reference import SCHEMA as REFERENCE_SCHEMA

            db.executescript(REFERENCE_SCHEMA)
            from .story_graph import SCHEMA as STORY_SCHEMA

            db.executescript(STORY_SCHEMA)
            db.execute(
                """INSERT OR IGNORE INTO revisions(id,chapter_id,revision,content,cause,created)
                SELECT lower(hex(randomblob(16))),id,0,CASE WHEN revision=0 THEN content ELSE '' END,
                '初始稿快照',? FROM chapters WHERE NOT EXISTS
                (SELECT 1 FROM revisions r WHERE r.chapter_id=chapters.id AND r.revision=0)""",
                (time(),),
            )
            try:
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(unit_id UNINDEXED,content,tokenize='trigram')"
                )
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def all(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        if not rows:
            raise DomainError("not_found", "目标不存在或已归档。", 404)
        return rows[0]

    @staticmethod
    def event(db, task_id, kind, payload):
        db.execute(
            "INSERT INTO task_events(task_id,kind,payload,created) VALUES(?,?,?,?)",
            (task_id, kind, dumps(payload), time()),
        )

    @staticmethod
    def check_attempt(db, task_id, attempt):
        row = db.execute(
            "SELECT status,attempt,cancel,lease_until FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if (
            not row
            or row["attempt"] != attempt
            or row["cancel"]
            or row["status"] != "running"
            or row["lease_until"] < time()
        ):
            raise DomainError("cancelled", "任务或租约已停止，旧结果不会继续提交。", 409)

    def emit(self, task_id, kind, payload, attempt=None):
        with self.connect(True) as db:
            if attempt:
                self.check_attempt(db, task_id, attempt)
            self.event(db, task_id, kind, payload)
