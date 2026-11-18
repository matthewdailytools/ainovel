"""Create and verify detached cloud ledgers, and restore into a new database."""

import argparse
from contextlib import closing, contextmanager
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from time import monotonic

from . import cloud_ledger
from .cloud_attachments import CHUNK_BYTES, FILE_BYTES
from .cloud_sync import MAX_CURSOR, is_ledger_schema
from .cloud_sync_storage import StorageProofError, verify_storage
from .db import DomainError


class SyncBackupError(ValueError):
    pass


def _source(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise SyncBackupError("来源须为现有普通文件。")
    return path.resolve(strict=True)


def _target(path):
    path = Path(path).expanduser()
    folder = path.parent.resolve(strict=True)
    if not path.name or not folder.is_dir():
        raise SyncBackupError("目标父目录须已存在。")
    target = folder / path.name
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.lexists(target.with_name(target.name + suffix)):
            raise SyncBackupError("目标数据库、备份或其旁路文件已存在，请另选目标。")
    return target


@contextmanager
def _readonly(path, *, standalone=False):
    uri = path.as_uri() + "?mode=ro"
    if standalone:
        uri += "&immutable=1"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as tx:
        tx.execute("PRAGMA temp_store=MEMORY")
        tx.execute("PRAGMA trusted_schema=OFF")
        yield tx


def _check_schema(tx):
    if not is_ledger_schema(tx):
        raise SyncBackupError("文件不是当前格式的独立云同步数据库。")
    try:
        return cloud_ledger.identity(tx)
    except DomainError as error:
        raise SyncBackupError("云同步数据库的账本身份缺失或无效。") from error


def _check_ledger(tx):
    bad_sequence = tx.execute(
        "SELECT 1 FROM sync_works w LEFT JOIN sync_changes c ON w.id=c.work_id "
        "GROUP BY w.id HAVING w.sequence<>count(c.sequence) OR w.sequence>? "
        "OR (count(c.sequence)>0 AND (min(c.sequence)<>1 OR max(c.sequence)<>w.sequence)) LIMIT 1",
        (MAX_CURSOR,),
    ).fetchone()
    bad_head = tx.execute(
        "WITH latest AS (SELECT work_id,object_id,MAX(sequence) AS sequence FROM sync_changes "
        "WHERE status<>'conflict' GROUP BY work_id,object_id) "
        "SELECT 1 FROM latest l LEFT JOIN sync_heads h ON h.work_id=l.work_id AND h.object_id=l.object_id "
        "LEFT JOIN sync_changes c ON c.work_id=h.work_id AND c.change_id=h.revision "
        "WHERE c.sequence IS NULL OR c.sequence<>l.sequence OR c.object_id<>l.object_id "
        "UNION ALL SELECT 1 FROM sync_heads h LEFT JOIN latest l ON h.work_id=l.work_id AND h.object_id=l.object_id "
        "WHERE l.sequence IS NULL LIMIT 1"
    ).fetchone()
    if bad_sequence or bad_head:
        raise SyncBackupError("同步账本的游标或采用头不一致。")


def _check_attachments(tx):
    for work, digest, size, sealed in tx.execute("SELECT work_id,sha256,size,sealed FROM sync_attachments"):
        if (
            not isinstance(size, int)
            or not 1 <= size <= FILE_BYTES
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or sealed not in (0, 1)
        ):
            raise SyncBackupError("附件的冻结清单无效。")
        slots = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
        checksum = sha256()
        received = 0
        for slot, data in tx.execute(
            "SELECT slot,data FROM sync_attachment_parts WHERE work_id=? AND sha256=? ORDER BY slot",
            (work, digest),
        ):
            if (
                not isinstance(slot, int)
                or not 0 <= slot < slots
                or not isinstance(data, bytes)
                or len(data) != min(CHUNK_BYTES, size - slot * CHUNK_BYTES)
                or (sealed and slot != received)
            ):
                raise SyncBackupError("附件分片的顺序、类型或长度无效。")
            checksum.update(data)
            received += 1
        if sealed and (received != slots or checksum.hexdigest() != digest):
            raise SyncBackupError("已封存附件的正文摘要核验失败。")


def _assert_standalone(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    # SQLite file read/write versions of 2 indicate WAL may hold unapplied pages.
    if (
        len(header) != 20
        or header[:16] != b"SQLite format 3\0"
        or header[18:20] != b"\1\1"
        or any(os.path.lexists(path.with_name(path.name + suffix)) for suffix in ("-wal", "-shm", "-journal"))
    ):
        raise SyncBackupError("备份须为完整独立的 SQLite 文件，活动库需先使用 create。")


def verify_backup(path, expected_sha256=None):
    path = _source(path)
    _assert_standalone(path)
    with path.open("rb") as backup:
        digest = file_digest(backup, "sha256").hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise SyncBackupError("备份文件 SHA-256 与指定值不一致。")
    with _readonly(path, standalone=True) as tx:
        _, requires_header = _check_schema(tx)
        if not requires_header or tx.execute("SELECT 1 FROM sync_devices WHERE active=1 LIMIT 1").fetchone():
            raise SyncBackupError("备份须为独立账本，且没有仍可使用的设备凭据。")
        if (
            tx.execute("PRAGMA integrity_check").fetchone() != ("ok",)
            or tx.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise SyncBackupError("SQLite 完整性或外键验证失败。")
        _check_ledger(tx)
        _check_attachments(tx)
        return {"sha256": digest, "works": tx.execute("SELECT count(*) FROM sync_works").fetchone()[0]}


def _detach_identity(path):
    # Keep the stable device IDs used by receipts while invalidating even the old token's self-revoke path.
    with closing(sqlite3.connect(path, timeout=5)) as tx:
        tx.execute("PRAGMA temp_store=MEMORY")
        tx.execute("PRAGMA trusted_schema=OFF")
        tx.execute("PRAGMA secure_delete=ON")
        if tx.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise SyncBackupError("无法将备份转换为独立的 SQLite 文件。")
        with tx:
            cloud_ledger.rotate(tx)
            tx.execute("UPDATE sync_devices SET active=0,token_sha256=lower(hex(randomblob(32)))")


def _copy_live(source, snapshot):
    deadline = monotonic() + 60

    def progress(_status, _remaining, _total):
        if monotonic() > deadline:
            raise SyncBackupError("在线备份等待超过一分钟，请稍后重试。")

    with _readonly(source) as live, closing(sqlite3.connect(snapshot)) as copy:
        live.execute("BEGIN")
        _check_schema(live)
        live.backup(copy, pages=256, progress=progress, sleep=0.05)


def _publish(target, snapshot):
    with snapshot.open("r+b") as sealed:
        os.fsync(sealed.fileno())
    _target(target)
    # A hard link publishes a complete file without replacing a concurrent target.
    os.link(snapshot, target)
    if os.name != "nt":
        folder = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def create_backup(source, target, *, require_encrypted_storage=False):
    source, target = _source(source), _target(target)
    if require_encrypted_storage:
        verify_storage(source, target.parent)
    with TemporaryDirectory(prefix=".ainovel-sync-backup-", dir=target.parent) as scratch:
        snapshot = Path(scratch) / "ledger.sqlite"
        snapshot.touch(mode=0o600, exist_ok=False)
        _copy_live(source, snapshot)
        _detach_identity(snapshot)
        result = verify_backup(snapshot)
        _publish(target, snapshot)
        return result


def restore_backup(archive, target, *, expected_sha256=None, require_encrypted_storage=False):
    archive, target = _source(archive), _target(target)
    _assert_standalone(archive)
    if require_encrypted_storage:
        verify_storage(archive, target.parent)
    with TemporaryDirectory(prefix=".ainovel-sync-restore-", dir=target.parent) as scratch:
        restored = Path(scratch) / "ledger.sqlite"
        restored.touch(mode=0o600, exist_ok=False)
        shutil.copyfile(archive, restored)
        verify_backup(restored, expected_sha256)
        _detach_identity(restored)
        result = verify_backup(restored)
        _publish(target, restored)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="备份、核验或独立恢复云同步账本。")
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("create", "verify", "restore"):
        options = modes.add_parser(mode)
        options.add_argument("--file", required=True, type=Path, help="独立的 SQLite 备份文件。")
        if mode != "verify":
            options.add_argument("--require-encrypted-storage", action="store_true")
        if mode != "create":
            options.add_argument("--sha256", help="预期备份 SHA-256。")
        if mode == "restore":
            options.add_argument("--target", required=True, type=Path, help="全新同步数据库文件。")
    args = parser.parse_args(argv)
    try:
        expected = getattr(args, "sha256", None)
        if expected is not None and not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            raise SyncBackupError("--sha256 须为 64 位十六进制摘要。")
        if args.mode == "verify":
            result = verify_backup(args.file, expected)
        elif args.mode == "create":
            source = os.environ.get("AINOVEL_SYNC_DB")
            if not source:
                raise SyncBackupError("请先配置同步数据库 AINOVEL_SYNC_DB。")
            result = create_backup(
                source, args.file, require_encrypted_storage=args.require_encrypted_storage
            )
        else:
            result = restore_backup(
                args.file,
                args.target,
                expected_sha256=expected,
                require_encrypted_storage=args.require_encrypted_storage,
            )
    except (StorageProofError, SyncBackupError) as error:
        parser.error(str(error))
    except OSError, sqlite3.Error:
        parser.error("同步备份无法完成，请核对 SQLite 文件、目录权限和目标卷。")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
