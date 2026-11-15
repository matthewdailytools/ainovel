"""Lifecycle safeguards for the independent browser-only test service."""

from dataclasses import replace
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/e2e_author_clarification_native.py"
SPEC = importlib.util.spec_from_file_location("author_clarification_native_driver", SCRIPT)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def test_native_configuration_is_private_temporary_and_offline_only(tmp_path):
    with TemporaryDirectory(prefix=DRIVER.PREFIX) as location:
        directory = Path(location).resolve()
        settings = DRIVER.settings_for(directory, 54321)
        DRIVER.guard(settings, directory)
        for changes in ({"test_mode": False}, {"data_dir": tmp_path}, {"port": 8766}):
            with pytest.raises(RuntimeError):
                DRIVER.guard(replace(settings, **changes), directory)
        for changes in ({"base_url": "https://api.deepseek.com"}, {"kind": "openai"}, {"key": "other"}):
            account = replace(settings.providers["deepseek"], **changes)
            with pytest.raises(RuntimeError):
                DRIVER.guard(replace(settings, providers={"deepseek": account}), directory)
    marker = tmp_path / "manuscripts.sqlite"
    marker.write_text("do not touch", encoding="utf-8")
    with pytest.raises(RuntimeError):
        DRIVER.guard(DRIVER.settings_for(tmp_path, 54321), tmp_path)
    assert marker.read_text(encoding="utf-8") == "do not touch"


def test_existing_evidence_is_not_overwritten_and_partial_control_is_not_authority(tmp_path):
    evidence = tmp_path / "author-clarification-native-existing"
    evidence.mkdir()
    marker = evidence / "preserved.txt"
    marker.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError):
        DRIVER.evidence_directory(evidence)
    assert marker.read_text(encoding="utf-8") == "original"
    control = tmp_path / "finish.json"
    token = "native-test-token-0123456789012345"
    for value in (None, [], {}, {"version": DRIVER.VERSION, "run_token": "foreign", "outcome": "passed"}):
        control.write_text(json.dumps(value), encoding="utf-8")
        assert DRIVER.finish_outcome(control, token) is None
    control.write_text('{"partial":', encoding="utf-8")
    assert DRIVER.finish_outcome(control, token) is None
    for outcome in ("passed", "failed"):
        control.write_text(
            json.dumps({"version": DRIVER.VERSION, "run_token": token, "outcome": outcome}), encoding="utf-8"
        )
        assert DRIVER.finish_outcome(control, token) == outcome


def test_import_does_not_add_any_production_fixture_routes(settings):
    from ainovel.api import create_app

    paths = create_app(settings, schema_only=True).openapi()["paths"]
    assert not any(path.startswith("/api/test/author-source") for path in paths)
    assert not hasattr(DRIVER, "app")


def test_orphan_deadline_stops_owned_server_and_preserves_consistent_failure_evidence(tmp_path):
    evidence = tmp_path / "author-clarification-native-watchdog"
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--evidence-dir",
            str(evidence),
            "--run-token",
            "native-test-token-0123456789012345",
            "--max-seconds",
            "6",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert process.returncode == 1, process.stdout + process.stderr
    failure = json.loads((evidence / "failure.json").read_text(encoding="utf-8"))
    assert failure["stop_reason"] == "deadline"
    ready = json.loads((evidence / "ready.json").read_text(encoding="utf-8"))
    from urllib.parse import urlsplit

    address = urlsplit(ready["origin"])
    with socket.socket() as connection:
        assert connection.connect_ex((address.hostname, address.port)) != 0
    with closing(sqlite3.connect(evidence / "failed-usage.sqlite")) as backup:
        assert backup.execute("SELECT COUNT(*) FROM calls WHERE status='settled'").fetchone()[0] == 2
    with closing(sqlite3.connect(evidence / "failed-manuscripts.sqlite")) as backup:
        assert backup.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
