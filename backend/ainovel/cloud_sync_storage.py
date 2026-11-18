"""Read-only operator preflight for supported encrypted sync storage."""

import argparse
import ctypes
import json
import os
from pathlib import Path
import stat
from struct import unpack_from
import subprocess
import sys


class StorageProofError(ValueError):
    pass


def _command(arguments, *, probe_drive=None, **kwargs):
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    environment["LC_ALL"] = "C"
    if probe_drive is not None:
        environment["AINOVEL_STORAGE_PROBE_DRIVE"] = probe_drive
    try:
        result = subprocess.run(
            arguments, check=True, timeout=8, capture_output=True, encoding="utf-8", env=environment, **kwargs
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        raise StorageProofError("无法读取系统加密状态。请检查存储查询工具和操作员权限。") from error
    return result.stdout


def _windows_drive(path):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    mount = kernel.GetVolumePathNameW
    mount.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    mount.restype = wintypes.BOOL
    drive_type = kernel.GetDriveTypeW
    drive_type.argtypes = [wintypes.LPCWSTR]
    drive_type.restype = wintypes.UINT
    actual = ctypes.create_unicode_buffer(32_768)
    if not mount(str(path), actual, len(actual)):
        raise StorageProofError("无法确认路径所在的 Windows 卷。")
    if actual.value.casefold() != path.anchor.casefold() or drive_type(actual.value) != 3:
        raise StorageProofError("当前只验证具有直接盘符的 Windows 本地固定卷。")
    return path.drive


def _windows_proof(path):
    drive = _windows_drive(path)
    shell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    command = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$volumes = @(Get-BitLockerVolume -MountPoint $env:AINOVEL_STORAGE_PROBE_DRIVE)
if ($volumes.Count -ne 1) { exit 2 }
$volume = $volumes[0]
@{encrypted = [string] $volume.VolumeStatus; protected = [string] $volume.ProtectionStatus;
  percentage = [int] $volume.EncryptionPercentage} | ConvertTo-Json -Compress
"""
    raw = _command(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command", command],
        probe_drive=drive,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if json.loads(raw) != {"encrypted": "FullyEncrypted", "protected": "On", "percentage": 100}:
        raise StorageProofError("BitLocker 尚未完成加密，或当前卷的保护已暂停。")
    return "bitlocker"


def _linux_dm_identity(major, minor):
    device = Path("/sys/dev/block") / f"{major}:{minor}"
    if not (device / "dm").is_dir():
        raise StorageProofError("文件系统设备未对应本地 device-mapper 映射。")
    name = (device / "dm" / "name").read_text(encoding="utf-8").rstrip("\n")
    if not name or len(name) > 127 or not name.isprintable():
        raise StorageProofError("无法核验设备映射名称。")
    sectors = (device / "size").read_text(encoding="ascii").strip()
    if not sectors.isascii() or not sectors.isdecimal() or len(sectors) > 20 or not int(sectors):
        raise StorageProofError("无法核验设备映射的实际长度。")
    return name, int(sectors)


def _ext4_internal_journal(superblock):
    if len(superblock) != 1024 or unpack_from("<H", superblock, 0x38)[0] != 0xEF53:
        return False
    compatible, incompatible = unpack_from("<II", superblock, 0x5C)
    journal_inode, journal_device = unpack_from("<II", superblock, 0xE0)
    return (
        not incompatible & 0x8  # EXT4_FEATURE_INCOMPAT_JOURNAL_DEV
        and journal_device == 0
        and not any(superblock[0xD0:0xE0])
        and bool(compatible & 0x4) == bool(journal_inode)  # EXT4_FEATURE_COMPAT_HAS_JOURNAL
    )


def _linux_check_ext4_device(major, minor):
    with (Path("/dev/block") / f"{major}:{minor}").open("rb", buffering=0) as device:
        metadata = os.fstat(device.fileno())
        if not stat.S_ISBLK(metadata.st_mode) or (os.major(metadata.st_rdev), os.minor(metadata.st_rdev)) != (
            major,
            minor,
        ):
            raise StorageProofError("无法确认 ext4 超级块来自同一挂载设备。")
        device.seek(1024)
        if not _ext4_internal_journal(device.read(1024)):
            raise StorageProofError("目标 ext4 日志不在同一验证范围内。")


def _linux_proof(path):
    device = path.stat().st_dev
    major, minor = os.major(device), os.minor(device)
    mount = json.loads(
        _command(["findmnt", "--json", "--output", "FSTYPE,MAJ:MIN,OPTIONS", "--target", str(path)])
    )
    filesystems = mount.get("filesystems", []) if isinstance(mount, dict) else []
    if not isinstance(filesystems, list) or len(filesystems) != 1 or not isinstance(filesystems[0], dict):
        raise StorageProofError("无法确认目标只有一个已挂载的 Linux 文件系统。")
    mount = filesystems[0]
    options = mount.get("options")
    if (
        mount.get("fstype") != "ext4"
        or mount.get("maj:min") != f"{major}:{minor}"
        or not isinstance(options, str)
        or any(option.startswith(("journal_dev=", "journal_path=")) for option in options.split(","))
    ):
        raise StorageProofError("当前只验证直接挂载且无外置日志设备的 ext4。")

    # Read the active table for this exact kernel device. Never request mapping keys.
    name, sectors = _linux_dm_identity(major, minor)
    table = _command(["dmsetup", "table", "--", name])
    end = 0
    for line in table.splitlines():
        fields = line.split(None, 3)
        if (
            len(fields) != 4
            or fields[2] != "crypt"
            or fields[3].split(None, 1)[0] not in {"aes-xts-plain64", "capi:xts(aes)-plain64"}
            or not all(part.isascii() and part.isdecimal() and len(part) <= 20 for part in fields[:2])
            or int(fields[0]) != end
            or int(fields[1]) <= 0
        ):
            raise StorageProofError("目标文件系统未直接位于连续的 AES-XTS dm-crypt 映射上。")
        end += int(fields[1])
    if end != sectors:
        raise StorageProofError("dm-crypt 映射未覆盖目标设备的完整长度。")
    _linux_check_ext4_device(major, minor)
    return "dm-crypt"


def _volume_proof(path):
    if sys.platform == "win32":
        return _windows_proof(path)
    if sys.platform.startswith("linux"):
        return _linux_proof(path)
    raise StorageProofError("当前系统没有可验证的存储加密查询方式。")


def verify_storage(database, backup_dir):
    database = Path(database).expanduser()
    folder = database.parent.resolve(strict=True)
    backup = Path(backup_dir).expanduser().resolve(strict=True)
    if not database.name or not folder.is_dir() or not backup.is_dir():
        raise StorageProofError("数据库父目录和备份目标目录须已存在。")
    database = folder / database.name
    paths = {"database_directory": folder, "backup_directory": backup}
    for name, path in (
        ("database", database),
        ("wal", database.with_name(database.name + "-wal")),
        ("shm", database.with_name(database.name + "-shm")),
    ):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise StorageProofError(f"{name} 路径须直接位于数据库目录内，请检查文件链接。")
        if not stat.S_ISREG(mode) or path.resolve(strict=True).parent != folder:
            raise StorageProofError(f"{name} 路径须为数据库目录中的普通文件。")
        paths[name] = path
    proofs = {}
    for name, path in paths.items():
        try:
            proofs[name] = _volume_proof(path)
        except (OSError, ValueError) as error:
            detail = str(error) if isinstance(error, StorageProofError) else "无法取得可核验的系统加密状态。"
            raise StorageProofError(f"{name} · {detail}") from error
    return {"storage": proofs, "attachments": "sqlite"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读核验同步数据库和指定备份目录的静态加密状态。")
    parser.add_argument("--backup-dir", required=True, type=Path, help="配置的现有同步备份目录。")
    args = parser.parse_args(argv)
    database = os.environ.get("AINOVEL_SYNC_DB")
    if not database:
        parser.error("请先为同步工作库配置 AINOVEL_SYNC_DB。")
    try:
        result = verify_storage(database, args.backup_dir)
    except (OSError, ValueError) as error:
        detail = (
            str(error)
            if isinstance(error, StorageProofError)
            else "无法读取数据库或备份目标路径。请检查目录和访问权限。"
        )
        parser.error(detail)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
