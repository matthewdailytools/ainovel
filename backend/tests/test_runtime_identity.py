"""Restore process identity, legacy PID reuse and disposable migration/restore."""

from contextlib import closing
import ctypes
from ctypes import wintypes
import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from ainovel import backups, runtime
from ainovel.budget import Budget
from ainovel.db import Database, DomainError
from ainovel.library import Library


BIRTH = "windows-filetime/1:134000000000000000"
OTHER_BIRTH = "windows-filetime/1:134000000000000001"


def legacy(**changes):
    return {"pid": 42, "role": "api", "started": 100.0, "birth_id": None, **changes}


@pytest.mark.parametrize(
    "stored,observation,expected",
    [
        (BIRTH, runtime.ProcessIdentity("running", BIRTH), True),
        (BIRTH, runtime.ProcessIdentity("running", OTHER_BIRTH), False),
        (BIRTH, runtime.ProcessIdentity("unknown"), True),
        (BIRTH, runtime.ProcessIdentity("running"), True),
        (BIRTH, runtime.ProcessIdentity("exited"), False),
        ("future/2:123", runtime.ProcessIdentity("running", OTHER_BIRTH), True),
        ("", runtime.ProcessIdentity("running", OTHER_BIRTH), True),
        (BIRTH, runtime.ProcessIdentity("running", "future/2:123"), True),
        (None, runtime.ProcessIdentity("unknown"), True),
        (None, runtime.ProcessIdentity("exited"), False),
    ],
)
def test_native_identity_matching_reuse_and_unknown(monkeypatch, stored, observation, expected):
    monkeypatch.setattr(runtime, "inspect_process", lambda pid: observation)
    assert runtime.restore_process_active(legacy(birth_id=stored)) is expected


@pytest.mark.parametrize(
    "changes,observation,expected",
    [
        ({}, runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Windows\System32\conhost.exe"), False),
        ({}, runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Apps\Edge\MSEDGE.EXE"), False),
        ({}, runtime.ProcessIdentity("running", BIRTH, 105, r"C:\Windows\System32\conhost.exe"), True),
        ({}, runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Python\python.exe"), True),
        ({}, runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Apps\other.exe"), True),
        ({}, runtime.ProcessIdentity("running", BIRTH, 106, "conhost.exe"), True),
        ({}, runtime.ProcessIdentity("running", BIRTH, 106), True),
        ({}, runtime.ProcessIdentity("running", None, 106, r"C:\Windows\conhost.exe"), True),
        ({}, runtime.ProcessIdentity("unknown", BIRTH, 106, r"C:\Windows\conhost.exe"), True),
        (
            {"started": float("nan")},
            runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Windows\conhost.exe"),
            True,
        ),
        ({"started": True}, runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Windows\conhost.exe"), True),
        (
            {"role": "unknown"},
            runtime.ProcessIdentity("running", BIRTH, 106, r"C:\Windows\conhost.exe"),
            True,
        ),
    ],
)
def test_legacy_only_audited_foreign_images_with_clear_later_birth_release(
    monkeypatch, changes, observation, expected
):
    monkeypatch.setattr(runtime, "inspect_process", lambda pid: observation)
    assert runtime.restore_process_active(legacy(**changes)) is expected


def test_registration_identity_is_atomic_and_unregister_cascades(domain, monkeypatch):
    monkeypatch.setattr(runtime, "inspect_process", lambda pid: runtime.ProcessIdentity("running", BIRTH))
    token = runtime.register(domain.db, "api")
    assert (
        domain.db.one("SELECT birth_id FROM process_identities WHERE token=?", (token,))["birth_id"] == BIRTH
    )
    runtime.unregister(domain.db, token)
    assert domain.db.all("SELECT * FROM processes") == []
    assert domain.db.all("SELECT * FROM process_identities") == []
    with domain.db.connect(True) as db:
        db.execute("""CREATE TRIGGER reject_identity BEFORE INSERT ON process_identities
            BEGIN SELECT RAISE(ABORT,'injected identity write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        runtime.register(domain.db, "worker")
    assert domain.db.all("SELECT * FROM processes") == []


def test_unavailable_registration_identity_keeps_a_legacy_blocking_row(domain, monkeypatch):
    monkeypatch.setattr(runtime, "inspect_process", lambda pid: runtime.ProcessIdentity("unknown"))
    runtime.register(domain.db, "worker")
    assert len(domain.db.all("SELECT * FROM processes")) == 1
    assert domain.db.all("SELECT * FROM process_identities") == []
    assert runtime.restore_process_active(legacy())


def dump(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


def remove_extension(database):
    with database.connect(True) as db:
        db.execute("DROP TABLE process_identities")
        db.execute("DELETE FROM extension_migrations WHERE name='process-identities/1'")


def test_old_four_column_schema_upgrades_without_fabricating_identity(domain, story, settings):
    domain.save(story["chapter_id"], 0, "旧库稿件😀")
    ledger = Budget(settings)
    ledger.reserve(settings.providers["openai_1"], "old-unknown", 100, 100)
    remove_extension(domain.db)
    with domain.db.connect(True) as db:
        db.execute("INSERT INTO processes VALUES(?,?,?,?)", ("old", "api", 42, 100))
    before = domain.db.all("SELECT * FROM processes")
    chapters = domain.db.all("SELECT * FROM chapters")
    usage = dump(ledger.path)
    backup = backups.create_backup(settings, domain.db)
    assert backups.verify_backup(Path(backup["path"]))["integrity"] == "ok"
    upgraded = Database(domain.db.path)
    assert upgraded.all("SELECT * FROM processes") == before
    assert upgraded.all("SELECT * FROM chapters") == chapters
    assert upgraded.all("SELECT * FROM process_identities") == []
    assert upgraded.one("SELECT name FROM extension_migrations WHERE name='process-identities/1'")
    with upgraded.connect() as db:
        assert [r["name"] for r in db.execute("PRAGMA table_info(processes)")] == [
            "token",
            "role",
            "pid",
            "started",
        ]
    assert dump(ledger.path) == usage


def test_migration_failure_rolls_back_new_table_marker_and_rows(domain, story, settings, monkeypatch):
    domain.save(story["chapter_id"], 0, "迁移失败保留😀")
    Budget(settings).reserve(settings.providers["deepseek"], "held", 100, 100)
    remove_extension(domain.db)
    before, usage = dump(domain.db.path), dump(settings.data_dir / "usage.sqlite")
    snapshot = backups.create_backup(settings, domain.db)
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "SCHEMA", runtime.SCHEMA + "\nSELECT * FROM injected_missing_identity_table;")
        with pytest.raises(sqlite3.DatabaseError, match="injected_missing"):
            Database(domain.db.path)
    assert dump(domain.db.path) == before
    assert dump(settings.data_dir / "usage.sqlite") == usage
    assert backups.verify_backup(Path(snapshot["path"]))["integrity"] == "ok"


def test_actual_old_backup_restore_migrates_before_copy_and_new_register_works(
    domain, story, settings, monkeypatch
):
    Library(domain.db, settings.data_dir)
    domain.save(story["chapter_id"], 0, "旧备份正文😀")
    ledger = Budget(settings)
    ledger.reserve(settings.providers["openai_1"], "earlier-unknown", 50, 50)
    remove_extension(domain.db)
    snapshot = backups.create_backup(settings, domain.db)
    Database(domain.db.path)
    domain.save(story["chapter_id"], 1, "恢复前现稿𠮷")
    ledger.reserve(settings.providers["openai_2"], "new-unknown", 300, 400)
    ledger.reserve(settings.providers["deepseek"], "new-CNY-unknown", 300, 400)
    usage = dump(ledger.path)
    restored = backups.restore_backup(settings, domain.db, Path(snapshot["path"]))
    assert restored["usage_ledger"] == "kept_current"
    assert domain.chapter(story["chapter_id"])["content"] == "旧备份正文😀"
    assert domain.db.one("SELECT name FROM extension_migrations WHERE name='process-identities/1'")
    token = runtime.register(domain.db, "api")
    observed = runtime.inspect_process(os.getpid())
    if observed.status == "running" and runtime.known_birth(observed.birth_id):
        assert (
            domain.db.one("SELECT birth_id FROM process_identities WHERE token=?", (token,))["birth_id"]
            == observed.birth_id
        )
    assert dump(ledger.path) == usage
    assert backups.verify_backup(Path(snapshot["path"]))["integrity"] == "ok"
    runtime.unregister(domain.db, token)


def test_restore_migration_failure_does_not_touch_target_or_usage(domain, story, settings, monkeypatch):
    domain.save(story["chapter_id"], 0, "旧备份😀")
    remove_extension(domain.db)
    snapshot = backups.create_backup(settings, domain.db)
    Database(domain.db.path)
    domain.save(story["chapter_id"], 1, "当前稿必须保留𠮷")
    Budget(settings).reserve(settings.providers["openai_1"], "unknown-after-backup", 300, 400)
    before, usage = dump(domain.db.path), dump(settings.data_dir / "usage.sqlite")
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "SCHEMA", runtime.SCHEMA + "\nSELECT * FROM injected_restore_migration;")
        with pytest.raises(sqlite3.DatabaseError, match="injected_restore_migration"):
            backups.restore_backup(settings, domain.db, Path(snapshot["path"]))
    assert dump(domain.db.path) == before
    assert dump(settings.data_dir / "usage.sqlite") == usage


def test_restore_still_refuses_current_real_python_process(domain, story, settings):
    snapshot = backups.create_backup(settings, domain.db)
    token = runtime.register(domain.db, "worker")
    before = dump(domain.db.path)
    with pytest.raises(DomainError) as failure:
        backups.restore_backup(settings, domain.db, Path(snapshot["path"]))
    assert failure.value.code == "service_active"
    assert dump(domain.db.path) == before
    runtime.unregister(domain.db, token)


@pytest.mark.skipif(os.name != "nt", reason="Real WinAPI process image/birth probe")
def test_actual_windows_foreign_pid_legacy_restore_in_isolated_database(domain, story, settings):
    Library(domain.db, settings.data_dir)
    script = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "@(Get-Process conhost,msedge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id) "
        "| ConvertTo-Json -Compress"
    )
    found = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    ids = json.loads(found.stdout or "[]")
    ids = ids if isinstance(ids, list) else [ids]
    observed = [(pid, runtime.inspect_process(pid)) for pid in ids]
    picked = next(
        (
            (pid, probe)
            for pid, probe in observed
            if probe.status == "running"
            and probe.created is not None
            and not runtime.restore_process_active(
                {"pid": pid, "role": "api", "started": probe.created - 60, "birth_id": None}
            )
        ),
        None,
    )
    if picked is None:
        pytest.skip("No accessible audited foreign process; native predicate not exercised")
    pid, probe = picked
    snapshot = backups.create_backup(settings, domain.db)
    with domain.db.connect(True) as db:
        db.execute(
            "INSERT INTO processes VALUES(?,?,?,?)", ("isolated-legacy", "api", pid, probe.created - 60)
        )
    assert runtime.alive(pid)  # The previous PID-only guard would reject it.
    assert len(domain.db.all("SELECT * FROM processes")) == 1  # Inspection never cleans history.
    assert (
        backups.restore_backup(settings, domain.db, Path(snapshot["path"]))["usage_ledger"] == "kept_current"
    )


@pytest.mark.parametrize("pid", [0, -1, True, "42", 0x1_0000_0000])
def test_invalid_pid_is_unknown_not_exited(pid):
    assert runtime.inspect_process(pid).status == "unknown"


def test_linux_birth_uses_boot_and_kernel_start_ticks_without_command_line(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "self/stat").write_text("supported", encoding="utf-8")
    folder = tmp_path / "52"
    folder.mkdir()
    (folder / "stat").write_text(
        "52 (name ) with spaces) " + " ".join(["S", *["0"] * 18, "1234"]), encoding="utf-8"
    )
    boot = tmp_path / "sys/kernel/random"
    boot.mkdir(parents=True)
    (boot / "boot_id").write_text("12345678-1234-1234-1234-123456789abc\n", encoding="ascii")
    observed = runtime.linux_identity(52, tmp_path)
    assert observed.birth_id == "linux-start/1:12345678-1234-1234-1234-123456789abc:1234"
    assert observed.status == "running" and runtime.known_birth(observed.birth_id)
    assert runtime.linux_identity(53, tmp_path, exit_probe=lambda pid: True).status == "exited"
    (boot / "boot_id").unlink()
    assert runtime.linux_identity(52, tmp_path).status == "unknown"
    assert runtime.linux_identity(52, tmp_path / "no-proc", exit_probe=lambda pid: False).status == "unknown"


@pytest.mark.parametrize("native_gone", [False, True])
def test_hidden_or_missing_linux_stat_requires_native_exit_evidence(tmp_path, monkeypatch, native_gone):
    (tmp_path / "self").mkdir()
    (tmp_path / "self/stat").write_text("available proc", encoding="utf-8")
    probes = []

    def safe_probe(pid):
        probes.append(pid)
        return native_gone

    observed = runtime.linux_identity(52, tmp_path, exit_probe=safe_probe)
    assert observed.status == ("exited" if native_gone else "unknown")
    assert probes == [52]


def test_unreadable_linux_stat_stays_unknown_without_sending_a_signal(tmp_path, monkeypatch):
    (tmp_path / "self").mkdir()
    (tmp_path / "self/stat").write_text("available proc", encoding="utf-8")
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == tmp_path / "52/stat":
            raise PermissionError("isolated stat permission denial")
        return original(path, *args, **kwargs)

    def no_probe(pid):
        raise AssertionError("stat permission denial must remain unknown")

    monkeypatch.setattr(Path, "read_text", denied)
    assert runtime.linux_identity(52, tmp_path, exit_probe=no_probe).status == "unknown"


@pytest.mark.parametrize("outcome", [None, errno.ESRCH, errno.EPERM, errno.EACCES, errno.EIO])
def test_posix_fallback_only_releases_explicit_native_esrch(monkeypatch, outcome):
    calls = []

    def safe_signal(pid, signal):
        calls.append((pid, signal))
        if outcome is not None:
            raise OSError(outcome, "isolated native probe substitute")

    # Replace the native function before simulating POSIX on a Windows host.
    # No pathlib/pytest assertion runs until all global platform values restore.
    with monkeypatch.context() as patch:
        patch.setattr(runtime.os, "kill", safe_signal)
        patch.setattr(runtime.os, "name", "posix")
        patch.setattr(runtime.sys, "platform", "darwin")
        exited = runtime.posix_exited(52)
        observed = runtime.inspect_process(52)
    assert exited is (outcome == errno.ESRCH)
    assert observed.status == ("exited" if outcome == errno.ESRCH else "unknown")
    assert calls == [(52, 0), (52, 0)]


def test_native_windows_never_uses_posix_kill_even_after_identity_failure(monkeypatch):
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(runtime.os, "kill", lambda *args: calls.append(args))
        patch.setattr(runtime.os, "name", "nt")
        patch.setattr(runtime.sys, "platform", "darwin")

        def unavailable(pid):
            raise OSError("isolated WinAPI read failure")

        patch.setattr(runtime, "windows_identity", unavailable)
        exited = runtime.posix_exited(52)
        observed = runtime.inspect_process(52)
    assert exited is False and observed.status == "unknown" and calls == []


@pytest.mark.parametrize("outcome", [None, errno.EPERM, errno.EIO])
def test_unsupported_posix_restore_refuses_live_or_unknown_without_changes(
    domain, story, settings, monkeypatch, outcome
):
    Library(domain.db, settings.data_dir)
    snapshot = backups.create_backup(settings, domain.db)
    backup_path = Path(snapshot["path"])
    with domain.db.connect(True) as db:
        db.execute("INSERT INTO processes VALUES(?,?,?,?)", ("isolated-posix", "worker", 52, 100))
    Budget(settings).reserve(settings.providers["openai_1"], "unchanged-unknown", 100, 100)
    before, usage = dump(domain.db.path), dump(settings.data_dir / "usage.sqlite")
    calls = []

    def safe_signal(pid, signal):
        calls.append((pid, signal))
        if outcome is not None:
            raise OSError(outcome, "isolated native probe substitute")

    # All native probes are replaced first. Refusal happens before any path or
    # backup operations, and platform globals restore before assertions/cleanup.
    code = None
    with monkeypatch.context() as patch:
        patch.setattr(runtime.os, "kill", safe_signal)
        patch.setattr(runtime.os, "name", "posix")
        patch.setattr(runtime.sys, "platform", "darwin")
        try:
            backups.restore_backup(settings, domain.db, backup_path)
        except DomainError as error:
            code = error.code
    assert code == "service_active" and calls == [(52, 0)]
    assert dump(domain.db.path) == before
    assert dump(settings.data_dir / "usage.sqlite") == usage


@pytest.mark.parametrize("failure", ["denied", "missing", "exit-code", "times", "image", "exited", "none"])
def test_winapi_native_handle_lifetime_access_and_unknown(monkeypatch, failure):
    closed, access = [], []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def open_process(rights, inherit, pid):
        access.append((rights, inherit, pid))
        return 0 if failure in {"denied", "missing"} else 99

    def exit_code(handle, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = (
            0 if failure == "exited" else 259
        )
        return failure != "exit-code"

    def times(handle, creation, *args):
        value = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
        ticks = int(BIRTH.rpartition(":")[2])
        value.dwHighDateTime, value.dwLowDateTime = ticks >> 32, ticks & 0xFFFFFFFF
        return failure != "times"

    def image(handle, flags, buffer, size):
        buffer.value = r"C:\Windows\System32\conhost.exe"
        return failure != "image"

    class Kernel:
        OpenProcess = Function(open_process)
        GetExitCodeProcess = Function(exit_code)
        GetProcessTimes = Function(times)
        QueryFullProcessImageNameW = Function(image)
        CloseHandle = Function(lambda handle: closed.append(handle) or True)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: Kernel(), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87 if failure == "missing" else 5, raising=False)
    result = runtime.windows_identity(42)
    assert access == [(0x1000, False, 42)]
    assert closed == ([] if failure in {"denied", "missing"} else [99])
    expected = (
        "exited"
        if failure in {"missing", "exited"}
        else "unknown"
        if failure in {"denied", "exit-code", "times"}
        else "running"
    )
    assert result.status == expected
    if result.status == "running":
        assert result.birth_id == BIRTH
        assert (result.image is None) is (failure == "image")
