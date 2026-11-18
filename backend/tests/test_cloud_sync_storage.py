import json
from pathlib import PureWindowsPath
from struct import pack_into
import subprocess

import pytest

from ainovel import cloud_sync_storage as storage


def test_preflight_covers_database_sidecars_attachments_and_backup_without_writes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    database = data / "sync.sqlite"
    wal = database.with_name("sync.sqlite-wal")
    shm = database.with_name("sync.sqlite-shm")
    checked = []

    def probe(path):
        checked.append(path)
        return "bitlocker"

    monkeypatch.setattr(storage, "_volume_proof", probe)
    assert storage.verify_storage(database, backup) == {
        "storage": {"database_directory": "bitlocker", "backup_directory": "bitlocker"},
        "attachments": "sqlite",
    }
    assert checked == [data, backup]
    assert not database.exists() and not wal.exists() and not shm.exists()
    for item in (database, wal, shm):
        item.write_bytes(b"synthetic fixture")
    checked.clear()
    result = storage.verify_storage(database, backup)
    assert checked == [data, backup, database, wal, shm]
    assert set(result["storage"]) == {"database_directory", "backup_directory", "database", "wal", "shm"}
    assert all(item.read_bytes() == b"synthetic fixture" for item in (database, wal, shm))


def test_refuses_unavailable_backup_or_invalid_sidecar_without_claiming_success(tmp_path, monkeypatch):
    database = tmp_path / "sync.sqlite"
    monkeypatch.setattr(storage, "_volume_proof", lambda _: pytest.fail("invalid path should not be probed"))
    with pytest.raises(FileNotFoundError):
        storage.verify_storage(database, tmp_path / "missing-backups")
    database.with_name("sync.sqlite-wal").mkdir()
    with pytest.raises(storage.StorageProofError, match="wal"):
        storage.verify_storage(database, tmp_path)


@pytest.mark.parametrize(
    ("proof", "valid"),
    [
        ({"encrypted": "FullyEncrypted", "protected": "On", "percentage": 100}, True),
        ({"encrypted": "FullyEncrypted", "protected": "Off", "percentage": 100}, False),
        ({"encrypted": "EncryptionInProgress", "protected": "On", "percentage": 45}, False),
    ],
)
def test_windows_probe_requires_full_encryption_with_live_protection(monkeypatch, proof, valid):
    path = PureWindowsPath("Q:/encrypted/workspace")
    monkeypatch.setattr(storage, "_windows_drive", lambda current: current.drive)

    def command(arguments, *, probe_drive, creationflags):
        assert probe_drive == "Q:"
        assert arguments[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
        assert "Get-BitLockerVolume -MountPoint $env:AINOVEL_STORAGE_PROBE_DRIVE" in arguments[4]
        assert str(path) not in arguments[4]
        return json.dumps(proof)

    monkeypatch.setattr(storage, "_command", command)
    if valid:
        assert storage._windows_proof(path) == "bitlocker"
    else:
        with pytest.raises(storage.StorageProofError, match="BitLocker"):
            storage._windows_proof(path)


@pytest.mark.parametrize(
    ("filesystem", "matched_device", "options", "table", "valid"),
    [
        (
            "ext4",
            "253:7",
            "rw,relatime",
            "0 4096 crypt aes-xts-plain64 x\n4096 4096 crypt aes-xts-plain64 x",
            True,
        ),
        ("ext4", "253:7", "rw,relatime", "0 8192 crypt capi:xts(aes)-plain64 x", True),
        ("ext4", "253:7", "rw", "0 4096 crypt aes-xts-plain64 x\n4096 4096 linear 8:2", False),
        ("ext4", "253:7", "rw", "0 4096 crypt aes-xts-plain64 x\n4097 4096 crypt aes-xts-plain64 x", False),
        ("ext4", "253:7", "rw", "0 4096 linear 8:2", False),
        ("ext4", "253:7", "rw", "0 4096 crypt aes-xts-plain64 x", False),
        ("ext4", "253:7", "rw", "0 8192 crypt cipher_null x", False),
        ("ext4", "253:7", "rw", "", False),
        ("ext4", "8:2", "rw", "0 8192 crypt aes-xts-plain64 x", False),
        ("ext4", "253:7", "rw,journal_dev=262145", "0 8192 crypt aes-xts-plain64 x", False),
        ("xfs", "253:7", "rw,logdev=/dev/other", "0 8192 crypt aes-xts-plain64 x", False),
        ("overlay", "253:7", "rw", "0 8192 crypt aes-xts-plain64 x", False),
    ],
)
def test_linux_requires_matching_mount_and_all_direct_targets_crypt(
    tmp_path, monkeypatch, filesystem, matched_device, options, table, valid
):
    monkeypatch.setattr(storage.os, "major", lambda _: 253, raising=False)
    monkeypatch.setattr(storage.os, "minor", lambda _: 7, raising=False)

    def identity(major, minor):
        assert (major, minor) == (253, 7)
        return "work-crypt", 8192

    monkeypatch.setattr(storage, "_linux_dm_identity", identity)
    checked = []
    monkeypatch.setattr(
        storage, "_linux_check_ext4_device", lambda major, minor: checked.append((major, minor))
    )

    def command(arguments):
        if arguments[0] == "findmnt":
            assert arguments[-2:] == ["--target", str(tmp_path)]
            return json.dumps(
                {"filesystems": [{"fstype": filesystem, "maj:min": matched_device, "options": options}]}
            )
        assert arguments == ["dmsetup", "table", "--", "work-crypt"]
        return table

    monkeypatch.setattr(storage, "_command", command)
    if valid:
        assert storage._linux_proof(tmp_path) == "dm-crypt"
        assert checked == [(253, 7)]
    else:
        with pytest.raises(storage.StorageProofError):
            storage._linux_proof(tmp_path)
        assert not checked


@pytest.mark.parametrize(
    ("features", "incompatible", "uuid", "inode", "device", "valid"),
    [
        (0x4, 0, bytes(16), 8, 0, True),
        (0, 0, bytes(16), 0, 0, True),
        (0x4, 0, bytes(16), 0, 0, False),
        (0x4, 0, b"x" * 16, 0, 2049, False),
        (0x4, 0, b"x" * 16, 0, 0, False),
        (0x4, 0, bytes(16), 8, 2049, False),
        (0, 0x8, bytes(16), 0, 0, False),
    ],
)
def test_ext4_superblock_rejects_external_journal_even_without_mount_option(
    features, incompatible, uuid, inode, device, valid
):
    block = bytearray(1024)
    pack_into("<H", block, 0x38, 0xEF53)
    pack_into("<II", block, 0x5C, features, incompatible)
    block[0xD0:0xE0] = uuid
    pack_into("<II", block, 0xE0, inode, device)
    assert storage._ext4_internal_journal(block) == valid


def test_operator_permission_failure_refuses_proof_without_leaking_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AINOVEL_SYNC_DB", str(tmp_path / "sync.sqlite"))
    monkeypatch.setenv("AINOVEL_SYNC_PROVISION_TOKEN", "synthetic-provisioning-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-application-secret")

    def command(arguments, *, env, **kwargs):
        assert env["AINOVEL_STORAGE_PROBE_DRIVE"] == "Q:"
        assert not {"AINOVEL_SYNC_DB", "AINOVEL_SYNC_PROVISION_TOKEN", "OPENAI_API_KEY"} & env.keys()
        raise subprocess.CalledProcessError(1, arguments, stderr="sensitive system diagnostics")

    monkeypatch.setattr(storage.subprocess, "run", command)
    monkeypatch.setattr(storage, "_windows_drive", lambda _: "Q:")
    monkeypatch.setattr(storage, "_volume_proof", storage._windows_proof)
    with pytest.raises(SystemExit) as stopped:
        storage.main(["--backup-dir", str(tmp_path)])
    assert stopped.value.code == 2
    output = capsys.readouterr()
    assert "数据库" not in output.out and not output.out
    assert "操作员权限" in output.err
    assert "synthetic" not in output.err and "sensitive" not in output.err
