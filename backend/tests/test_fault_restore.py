"""Interrupted SQLite restore and additive migration failures in disposable data."""

from pathlib import Path
import sqlite3
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from ainovel import backups, story_graph
from ainovel.budget import Budget
from ainovel.db import Database, DomainError
from ainovel.library import Library


def manuscript_dump(database):
    with database.connect() as connection:
        return list(connection.iterdump())


def source_bytes(settings):
    return {path.name: path.read_bytes() for path in (settings.data_dir / "sources").glob("*")}


@pytest.fixture
def restore_case(settings, domain, story):
    Library(domain.db, settings.data_dir).import_file("故障验证.txt", "渡船😀原件".encode())
    domain.save(story["chapter_id"], 0, "备份前原稿😀\n" + "临时工程样本。" * 4000)
    budget = Budget(settings)
    call = budget.reserve(settings.providers["openai_1"], "before-backup", 100, 100)
    budget.settle(call, {"input": 80, "output": 50})
    snapshot = backups.create_backup(settings, domain.db)
    domain.save(story["chapter_id"], 1, "备份后当前稿𠮷\n此稿不能被中断恢复替换。")
    budget.reserve(settings.providers["openai_2"], "after-backup-unknown", 300, 400)
    budget.reserve(settings.providers["deepseek"], "after-backup-deepseek", 200, 300)
    return {
        "path": settings.data_dir / "backups" / (snapshot["id"] + ".zip"),
        "dump": manuscript_dump(domain.db),
        "ledger": budget.report(),
        "sources": source_bytes(settings),
        "backups": set((settings.data_dir / "backups").glob("*.zip")),
    }


def assert_current_unchanged(settings, domain, case):
    assert manuscript_dump(domain.db) == case["dump"]
    assert Budget(settings).report() == case["ledger"]
    assert source_bytes(settings) == case["sources"]
    with domain.db.connect() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sqlite_backup_page_interruption_rolls_back_target_and_preserves_usage(
    settings, domain, restore_case, monkeypatch
):
    """Raise from the native backup progress callback after a page was copied."""
    restore_source = None
    progress_events = []
    real_connect = sqlite3.connect
    real_unpack = backups.unpack_verified

    class InterruptedConnection(sqlite3.Connection):
        def backup(self, target, **kwargs):
            def interrupt(status, remaining, total):
                progress_events.append((status, remaining, total))
                assert 0 < remaining < total
                raise sqlite3.OperationalError("injected restore interruption after first page")

            return super().backup(target, pages=1, progress=interrupt)

    def connect(database, *args, **kwargs):
        if restore_source is not None and Path(database) == restore_source:
            kwargs["factory"] = InterruptedConnection
        return real_connect(database, *args, **kwargs)

    def unpack(path, target):
        nonlocal restore_source
        counts = real_unpack(path, target)
        restore_source = target / "manuscripts.sqlite"
        return counts

    with monkeypatch.context() as patch:
        patch.setattr(backups, "unpack_verified", unpack)
        patch.setattr(backups.sqlite3, "connect", connect)
        with pytest.raises(sqlite3.OperationalError, match="after first page"):
            backups.restore_backup(settings, domain.db, restore_case["path"])

    assert len(progress_events) == 1
    assert_current_unchanged(settings, domain, restore_case)
    retained = set((settings.data_dir / "backups").glob("*.zip")) - restore_case["backups"]
    assert len(retained) == 1
    before = retained.pop()
    assert backups.verify_backup(before)["integrity"] == "ok"
    # Both the snapshot from before the attempt and its newer recovery copy remain readable.
    assert backups.verify_backup(restore_case["path"])["integrity"] == "ok"


def test_isolated_unpack_write_interruption_never_replaces_current_data(
    settings, domain, restore_case, monkeypatch
):
    real_write = Path.write_bytes
    written = []

    def interrupted_write(path, raw):
        if path.parent.name == "sources" and path.parent != settings.data_dir / "sources":
            assert any(item.name == "manuscripts.sqlite" for item in written)
            raise OSError("injected isolated source write interruption")
        result = real_write(path, raw)
        written.append(path)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_bytes", interrupted_write)
        with pytest.raises(DomainError) as failure:
            backups.restore_backup(settings, domain.db, restore_case["path"])
    assert failure.value.code == "backup_invalid"
    assert written and all(path != domain.db.path for path in written)
    assert_current_unchanged(settings, domain, restore_case)
    assert set((settings.data_dir / "backups").glob("*.zip")) == restore_case["backups"]


def test_corrupt_backup_hash_is_rejected_before_current_data_or_usage_changes(
    settings, domain, restore_case, tmp_path
):
    corrupted = tmp_path / "corrupted.zip"
    with ZipFile(restore_case["path"]) as source, ZipFile(corrupted, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            raw = source.read(item.filename)
            if item.filename == "manuscripts.sqlite":
                raw += b"corrupted"
            target.writestr(item.filename, raw)
    with pytest.raises(DomainError) as failure:
        backups.restore_backup(settings, domain.db, corrupted)
    assert failure.value.code == "backup_hash"
    assert_current_unchanged(settings, domain, restore_case)
    assert set((settings.data_dir / "backups").glob("*.zip")) == restore_case["backups"]


@pytest.mark.parametrize("failure_point", ["schema", "initial_snapshot"])
def test_migration_failure_rolls_back_ddl_rows_and_marker_before_retry(
    settings, domain, story, monkeypatch, failure_point
):
    domain.save(story["chapter_id"], 0, "旧结构原稿😀𠮷\n不可因升级失败丢失。")
    Budget(settings).reserve(settings.providers["openai_1"], "migration-unknown", 300, 400)
    ledger = Budget(settings).report()
    with domain.db.connect(True) as connection:
        connection.execute("DROP TABLE story_record_dependencies")
        connection.execute("DROP TABLE story_record_history")
        connection.execute("DROP TABLE story_records")
        connection.execute("DELETE FROM extension_migrations WHERE name='story-graph/1'")
        if failure_point == "initial_snapshot":
            connection.execute(
                "DELETE FROM revisions WHERE chapter_id=? AND revision=0", (story["chapter_id"],)
            )
            connection.execute("""CREATE TRIGGER injected_snapshot_failure BEFORE INSERT ON revisions
                WHEN NEW.revision=0 BEGIN SELECT RAISE(ABORT,'injected snapshot backfill failure'); END""")
    chapters = domain.db.all("SELECT * FROM chapters")
    revisions = domain.db.all("SELECT * FROM revisions")
    before = manuscript_dump(domain.db)
    snapshot = backups.create_backup(settings, domain.db)
    interrupted_schema = (
        story_graph.SCHEMA
        + "\nUPDATE chapters SET title='injected partial migration';"
        + "\nSELECT * FROM injected_missing_migration_table;"
    )
    with monkeypatch.context() as patch:
        if failure_point == "schema":
            patch.setattr(story_graph, "SCHEMA", interrupted_schema)
        with pytest.raises(sqlite3.DatabaseError, match="injected"):
            Database(domain.db.path)
    assert domain.db.all("SELECT * FROM chapters") == chapters
    assert domain.db.all("SELECT * FROM revisions") == revisions
    assert manuscript_dump(domain.db) == before
    assert Budget(settings).report() == ledger
    assert domain.db.all("SELECT name FROM extension_migrations WHERE name='story-graph/1'") == []
    assert domain.db.all("SELECT name FROM sqlite_master WHERE name='story_records'") == []
    assert domain.db.all("SELECT name FROM sqlite_master WHERE name='story_record_history'") == []
    assert (
        backups.verify_backup(settings.data_dir / "backups" / (snapshot["id"] + ".zip"))["integrity"] == "ok"
    )
    if failure_point == "initial_snapshot":
        with domain.db.connect(True) as connection:
            connection.execute("DROP TRIGGER injected_snapshot_failure")
    restarted = Database(domain.db.path)
    assert restarted.all("SELECT * FROM chapters") == chapters
    assert all(row in restarted.all("SELECT * FROM revisions") for row in revisions)
    assert restarted.one("SELECT * FROM revisions WHERE chapter_id=? AND revision=0", (story["chapter_id"],))
    assert restarted.one("SELECT name FROM extension_migrations WHERE name='story-graph/1'")
    assert restarted.all("SELECT * FROM story_record_history") == []
    assert restarted.all("SELECT * FROM story_record_dependencies") == []
    assert Budget(settings).report() == ledger


def test_future_schema_is_rejected_before_manuscript_or_usage_changes(settings, domain, story):
    domain.save(story["chapter_id"], 0, "高版本原稿😀不回写。")
    Budget(settings).reserve(settings.providers["openai_1"], "future-schema-unknown", 300, 400)
    with domain.db.connect(True) as connection:
        connection.execute("UPDATE schema_version SET version=2")
    original = manuscript_dump(domain.db)
    ledger = Budget(settings).report()
    with pytest.raises(DomainError) as failure:
        Database(domain.db.path)
    assert failure.value.code == "future_schema"
    assert manuscript_dump(domain.db) == original
    assert Budget(settings).report() == ledger


@pytest.mark.parametrize("message", ["no such module: fts5", "no such tokenizer: trigram"])
def test_optional_fts_unavailability_does_not_abort_atomic_initialization(
    settings, domain, story, monkeypatch, message
):
    domain.save(story["chapter_id"], 0, "没有FTS时仍保留原稿😀。")
    before = manuscript_dump(domain.db)
    real_connect = sqlite3.connect
    rejected = []

    class WithoutFTSConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("CREATE VIRTUAL TABLE IF NOT EXISTS source_fts"):
                rejected.append(sql)
                raise sqlite3.OperationalError(message)
            return super().execute(sql, *args, **kwargs)

    def connect(database, *args, **kwargs):
        kwargs["factory"] = WithoutFTSConnection
        return real_connect(database, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        initialized = Database(domain.db.path)
    assert rejected and initialized.fts is False
    assert manuscript_dump(domain.db) == before
    assert Database(domain.db.path).fts is True


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (sqlite3.SQLITE_FULL, "database or disk is full"),
        (sqlite3.SQLITE_IOERR_WRITE, "disk I/O error"),
        (sqlite3.SQLITE_ERROR, "unrelated SQL error"),
    ],
)
def test_fatal_fts_initialization_error_is_not_silently_treated_as_missing_capability(
    settings, domain, story, monkeypatch, code, message
):
    domain.save(story["chapter_id"], 0, "FTS初始化失败时旧稿😀保留。")
    Budget(settings).reserve(settings.providers["openai_1"], "fts-error-unknown", 300, 400)
    ledger = Budget(settings).report()
    with domain.db.connect(True) as connection:
        connection.execute("DROP TABLE story_record_dependencies")
        connection.execute("DROP TABLE story_record_history")
        connection.execute("DROP TABLE story_records")
        connection.execute("DELETE FROM extension_migrations WHERE name='story-graph/1'")
    before = manuscript_dump(domain.db)
    real_connect = sqlite3.connect
    failure = sqlite3.OperationalError(message)
    failure.sqlite_errorcode = code
    rolled_back = []

    class FailingFTSConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("CREATE VIRTUAL TABLE IF NOT EXISTS source_fts"):
                assert self.in_transaction
                # SQLite can itself roll back a transaction after FULL/IOERR.
                super().execute("ROLLBACK")
                rolled_back.append(True)
                raise failure
            return super().execute(sql, *args, **kwargs)

    def connect(database, *args, **kwargs):
        kwargs["factory"] = FailingFTSConnection
        return real_connect(database, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with pytest.raises(sqlite3.OperationalError) as observed:
            Database(domain.db.path)
    assert observed.value is failure and rolled_back == [True]
    assert manuscript_dump(domain.db) == before
    assert Budget(settings).report() == ledger
    restarted = Database(domain.db.path)
    assert restarted.fts is True
    assert restarted.one("SELECT name FROM extension_migrations WHERE name='story-graph/1'")
