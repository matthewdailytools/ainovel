"""SQLite online backup and an exercised, isolated restore. Usage never rolls back."""

import hashlib
from contextlib import closing
import json
import re
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from time import time
from zipfile import ZipFile, ZIP_DEFLATED

from .db import Database, DomainError, uid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_backup(settings, database):
    backup_id = uid()
    folder = settings.data_dir / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / (backup_id + ".zip")
    with TemporaryDirectory(dir=settings.data_dir) as scratch:
        scratch = Path(scratch)
        manuscript = scratch / "manuscripts.sqlite"
        with database.connect() as source, closing(sqlite3.connect(manuscript)) as dest:
            source.backup(dest)
        files = {"manuscripts.sqlite": manuscript}
        with closing(sqlite3.connect(manuscript)) as snapshot:
            for (filename,) in snapshot.execute("SELECT filename FROM books"):
                if Path(filename).name != filename:
                    raise DomainError("backup_path", "资料路径无效，备份停止。")
                files["sources/" + filename] = settings.data_dir / "sources" / filename
        if (settings.data_dir / "usage.sqlite").exists():
            with (
                closing(sqlite3.connect(settings.data_dir / "usage.sqlite")) as source,
                closing(sqlite3.connect(scratch / "usage.sqlite")) as dest,
            ):
                source.backup(dest)
            files["usage-evidence.sqlite"] = scratch / "usage.sqlite"
        manifest = {name: digest(path) for name, path in files.items()}
        with ZipFile(target.with_suffix(".partial"), "x", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({"schema": 1, "created": time(), "files": manifest}))
            for name, path in files.items():
                archive.write(path, name)
        target.with_suffix(".partial").replace(target)
    return {
        "id": backup_id,
        "path": str(target),
        "bytes": target.stat().st_size,
        "note": "包含正文、历史、资料原件和用量证据；恢复稿件不会回退费用账本。",
    }


def unpack_verified(path, target):
    if not path.is_file():
        raise DomainError("backup_missing", "备份文件不存在。", 404)
    try:
        with ZipFile(path) as archive:
            if (
                len(archive.infolist()) > 10_000
                or sum(item.file_size for item in archive.infolist()) > 2_000_000_000
            ):
                raise DomainError("backup_limit", "备份规模超过本版恢复限制。")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest["schema"] != 1 or "manuscripts.sqlite" not in manifest["files"]:
                raise DomainError("backup_schema", "不支持该备份格式。")
            for name, checksum in manifest["files"].items():
                parts = name.split("/")
                if name not in ("manuscripts.sqlite", "usage-evidence.sqlite") and not (
                    len(parts) == 2
                    and parts[0] == "sources"
                    and re.fullmatch(r"[a-f0-9]{32}\.(txt|md|epub)", parts[1])
                ):
                    raise DomainError("backup_path", "备份包含非法路径。")
                raw = archive.read(name)
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise DomainError("backup_hash", "备份内容校验不一致。")
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
    except (ValueError, KeyError, OSError) as error:
        raise DomainError("backup_invalid", "备份无法完整恢复，现有数据未改动。") from error
    with closing(sqlite3.connect(target / "manuscripts.sqlite")) as db:
        if db.execute("SELECT version FROM schema_version").fetchone()[0] != 1:
            raise DomainError("backup_schema", "备份数据库格式与当前版本不一致，未替换数据。")
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise DomainError("backup_integrity", "恢复后的数据库完整性检查失败。")
        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("novels", "chapters", "revisions", "facts", "books")
        }
        for filename, checksum in db.execute("SELECT filename,file_hash FROM books"):
            if not re.fullmatch(r"[a-f0-9]{32}\.(txt|md|epub)", filename):
                raise DomainError("backup_path", "备份数据库含不安全的资料路径。")
            if digest(target / "sources" / filename) != checksum:
                raise DomainError("backup_hash", "恢复后的资料原件与数据库不一致。")
    return counts


def verify_backup(path):
    with TemporaryDirectory() as scratch:
        counts = unpack_verified(path, Path(scratch))
    return {
        "restored_to_isolated_directory": True,
        "integrity": "ok",
        "counts": counts,
        "note": "已实际恢复到隔离目录并校验资料 SHA-256；未替换当前工作数据。",
    }


def restore_backup(settings, database, path):
    from .runtime import restore_process_active

    if any(
        restore_process_active(row)
        for row in database.all(
            "SELECT p.pid,p.role,p.started,i.birth_id FROM processes p "
            "LEFT JOIN process_identities i ON i.token=p.token"
        )
    ):
        raise DomainError(
            "service_active",
            "请先停止本机 API 与 Worker，或核对无法确认的进程身份；可在页面继续进行隔离恢复演练。",
        )
    if database.all("SELECT id FROM tasks WHERE status='running' AND lease_until>?", (time(),)):
        raise DomainError("worker_active", "请先停止本机服务和 Worker，等待租约到期后恢复。")
    with TemporaryDirectory(dir=settings.data_dir) as scratch:
        scratch = Path(scratch)
        counts = unpack_verified(path, scratch)
        # Apply the same additive migrations as startup before touching the
        # target. An older backup must remain registerable after restoration.
        Database(scratch / "manuscripts.sqlite")
        before = create_backup(settings, database)
        # Sources are immutable and UUID-named; only add missing originals.
        for source in (scratch / "sources").glob("*"):
            target = settings.data_dir / "sources" / source.name
            if target.exists() and digest(target) != digest(source):
                raise DomainError("source_conflict", "现有资料与备份同名且内容不同，未覆盖。")
            if not target.exists():
                shutil.copy2(source, target)
        with closing(sqlite3.connect(scratch / "manuscripts.sqlite")) as source, database.connect() as dest:
            source.backup(dest)
        with database.connect(True) as tx:
            tx.execute("DELETE FROM processes")
            tx.execute(
                "UPDATE tasks SET status='blocked_runtime',attempt=NULL,error='从备份恢复，须明确检查后恢复任务。' WHERE status IN ('running','queued')"
            )
            tx.execute(
                "UPDATE library_jobs SET status='failed',error='从备份恢复，请重新获取。' WHERE status IN ('queued','downloading')"
            )
    return {"restored": counts, "before_backup": before["id"], "usage_ledger": "kept_current"}
