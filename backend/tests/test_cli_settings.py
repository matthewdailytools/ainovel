import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from ainovel.__main__ import main
from ainovel.settings import ROOT, Settings


def test_schema_is_read_only_even_with_occupied_port(tmp_path, monkeypatch):
    target = tmp_path / "schema.json"
    monkeypatch.setattr(sys, "argv", ["ainovel", "schema", "--file", str(target)])
    monkeypatch.setattr(Settings, "load", lambda *args: pytest.fail("schema must not load secrets"))
    monkeypatch.setattr(socket, "socket", lambda: pytest.fail("schema must not bind a socket"))
    main()
    assert '"/api/workflows"' in target.read_text("utf-8")
    assert not (ROOT / "runtime").exists()


def test_serve_checks_port_before_starting_worker(tmp_path, monkeypatch):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        settings = Settings(tmp_path, {}, {}, port=occupied.getsockname()[1])
        monkeypatch.setattr(Settings, "load", lambda: settings)
        monkeypatch.setattr(sys, "argv", ["ainovel", "serve"])
        monkeypatch.setattr(Path, "is_file", lambda *args: True)
        monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("orphan worker"))
        with pytest.raises(SystemExit) as failure:
            main()
        assert failure.value.code == 2
        assert not (tmp_path / "runtime").exists()


def test_data_location_is_stable_and_retired_ledger_is_rejected(tmp_path, monkeypatch):
    config = tmp_path / "settings.toml"
    config.write_text('data_dir="~/.ainovel"\n')
    monkeypatch.setenv("AINOVEL_CONFIG", str(config))
    monkeypatch.delenv("AINOVEL_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "virtualized"))
    assert Settings.load().data_dir == (Path.home() / ".ainovel").resolve()
    (tmp_path / "MIGRATED_TO.txt").write_text("moved")
    with pytest.raises(ValueError, match="迁移"):
        Settings.load(tmp_path)


def test_worker_stops_when_owner_marker_exists(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    marker = runtime / "test.stop"
    marker.write_text("stop")
    config = tmp_path / "empty.toml"
    config.write_text("")
    result = subprocess.run(
        [sys.executable, "-m", "ainovel.worker"],
        env={
            **os.environ,
            "AINOVEL_CONFIG": str(config),
            "AINOVEL_DATA_DIR": str(tmp_path),
            "AINOVEL_WORKER_STOP_FILE": str(marker),
            "AINOVEL_PARENT_PID": "0",
        },
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    from ainovel.db import Database

    assert Database(tmp_path / "manuscripts.sqlite").all("SELECT * FROM processes") == []
