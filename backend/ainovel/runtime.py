"""Record live app processes so a manuscript restore cannot race a running service."""

import os
from dataclasses import dataclass
import errno
import math
from pathlib import Path, PureWindowsPath
import re
import sys
from time import time

from .db import uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS process_identities(
 token TEXT PRIMARY KEY REFERENCES processes(token) ON DELETE CASCADE,birth_id TEXT NOT NULL);
INSERT OR IGNORE INTO extension_migrations VALUES('process-identities/1',unixepoch());
"""


@dataclass(frozen=True)
class ProcessIdentity:
    status: str
    birth_id: str | None = None
    created: float | None = None
    image: str | None = None


def inspect_process(pid):
    """Inspect native birth identity; unsupported or denied inspection is unknown."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return ProcessIdentity("unknown")
    try:
        if os.name == "nt":
            return windows_identity(pid)
        if sys.platform.startswith("linux"):
            return linux_identity(pid)
    except OSError, ValueError, OverflowError, AttributeError:
        pass
    return ProcessIdentity("exited" if posix_exited(pid) else "unknown")


def posix_exited(pid):
    # Windows os.kill(pid, 0) can terminate a process. Never call it there,
    # including when /proc or a platform identity implementation is unavailable.
    if os.name != "posix" or type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return False
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno == errno.ESRCH
    return False


def windows_identity(pid):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        # Invalid positive PID means no such process. Access denied is unknown.
        return ProcessIdentity("exited" if ctypes.get_last_error() == 87 else "unknown")
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return ProcessIdentity("unknown")
        if code.value != 259:
            return ProcessIdentity("exited")
        creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return ProcessIdentity("unknown")
        ticks = creation.dwHighDateTime << 32 | creation.dwLowDateTime
        if not ticks:
            return ProcessIdentity("unknown")
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        found_image = kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))
        return ProcessIdentity(
            "running",
            f"windows-filetime/1:{ticks}",
            ticks / 10_000_000 - 11_644_473_600,
            image.value if found_image else None,
        )
    finally:
        kernel.CloseHandle(handle)


def linux_identity(pid, proc=Path("/proc"), *, exit_probe=None):
    gone = exit_probe if exit_probe is not None else posix_exited
    # Missing /proc support must not be confused with a process exiting.
    if not (proc / "self/stat").is_file():
        return ProcessIdentity("exited" if gone(pid) else "unknown")
    try:
        value = (proc / str(pid) / "stat").read_text("utf-8")
    except FileNotFoundError:
        # hidepid may conceal a live process. Missing stat alone proves nothing.
        return ProcessIdentity("exited" if gone(pid) else "unknown")
    except OSError:
        return ProcessIdentity("unknown")
    tail = value.rpartition(")")[2].split()
    if not value.startswith(f"{pid} (") or len(tail) < 20 or not tail[19].isdigit():
        return ProcessIdentity("unknown")
    if tail[0] in {"Z", "X", "x"}:
        return ProcessIdentity("exited")
    try:
        boot = (proc / "sys/kernel/random/boot_id").read_text("ascii").strip()
    except OSError:
        return ProcessIdentity("unknown")
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot):
        return ProcessIdentity("unknown")
    return ProcessIdentity("running", f"linux-start/1:{boot}:{int(tail[19])}")


def known_birth(value):
    if not isinstance(value, str):
        return False
    if re.fullmatch(r"windows-filetime/1:[1-9][0-9]{0,19}", value):
        return int(value.rpartition(":")[2]) <= 0xFFFFFFFFFFFFFFFF
    return bool(
        re.fullmatch(r"linux-start/1:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}:(?:0|[1-9][0-9]*)", value)
    )


def restore_process_active(row):
    """Only a proved exit or identity mismatch releases a restore guard."""
    observed = inspect_process(row["pid"])
    if observed.status == "exited":
        return False
    if observed.status != "running":
        return True
    stored = row.get("birth_id")
    if stored is not None:
        return not (known_birth(stored) and known_birth(observed.birth_id) and stored != observed.birth_id)
    # Legacy rows have no native identity. The small, audited foreign-image set
    # only releases a clearly later, non-Python Windows process; all other
    # legacy or unreadable observations continue to block restoration.
    started, created, image = row.get("started"), observed.created, observed.image
    if (
        row.get("role") not in {"api", "worker"}
        or not known_birth(observed.birth_id)
        or not observed.birth_id.startswith("windows-filetime/1:")
        or type(started) not in (int, float)
        or not math.isfinite(started)
        or started <= 0
        or type(created) not in (int, float)
        or not math.isfinite(created)
        or created <= started + 5
        or not isinstance(image, str)
        or not PureWindowsPath(image).is_absolute()
    ):
        return True
    name = PureWindowsPath(image).name.casefold()
    backend_names = {
        PureWindowsPath(path).name.casefold()
        for path in (sys.executable, getattr(sys, "_base_executable", sys.executable))
        if path
    }
    return name not in {"conhost.exe", "msedge.exe"} or name in backend_names


def register(database, role):
    token = uid()
    identity = inspect_process(os.getpid())
    with database.connect(True) as db:
        db.execute("INSERT INTO processes VALUES(?,?,?,?)", (token, role, os.getpid(), time()))
        if identity.status == "running" and known_birth(identity.birth_id):
            db.execute("INSERT INTO process_identities VALUES(?,?)", (token, identity.birth_id))
    return token


def unregister(database, token):
    with database.connect(True) as db:
        db.execute("DELETE FROM processes WHERE token=?", (token,))


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
