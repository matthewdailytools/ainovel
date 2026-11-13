"""Exercise actual process death and a natural lease timeout, without paid calls."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from ainovel.api import create_app
from ainovel.db import DomainError
from ainovel.models import ModuleInput
from ainovel.runtime import alive


def wait_file(process, path, timeout):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"Fixture process exited before checkpoint: {stdout!r} {stderr!r}")
        sleep(0.05)
    pytest.fail("Fixture process did not reach the expected durable checkpoint")


def test_real_worker_kill_natural_lease_restart_and_explicit_recovery(settings, domain, story):
    original = domain.save(story["chapter_id"], 0, "作者原稿仍在渡口等待。😀𠮷")
    task = domain.queue(
        "module",
        ModuleInput(
            action="writing.expand_plot",
            novel_id=story["id"],
            branch_id=story["branch_id"],
            chapter_id=story["chapter_id"],
            base_revision=original["revision"],
            instruction="核对潮尺并记下证据",
            fixed=["记录仍可回查"],
            account="openai_1",
        ).model_dump(),
    )
    helper = Path(__file__).parent / "helpers/worker_crash_fixture.py"
    processes = []
    modes = {}

    def launch(mode):
        process = subprocess.Popen(
            [sys.executable, str(helper), str(settings.data_dir), mode, task["id"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        processes.append(process)
        modes[process.pid] = mode
        return process

    def completed(process, timeout):
        output, error = process.communicate(timeout=timeout)
        assert process.returncode == 0, (output, error)

    try:
        first = launch("crash")
        wait_file(first, settings.data_dir / "review-started.json", 20)
        interrupted = domain.task(task["id"])
        old_attempt = interrupted["attempt"]
        assert interrupted["status"] == "running" and old_attempt
        steps = domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task["id"],))
        assert len(steps) == 1  # Generated prose is committed before the slow review.
        # Windows virtualenv python.exe can be a launcher whose child executes
        # the helper. Kill the helper-reported Worker PID, not just its launcher.
        actual_first = json.loads((settings.data_dir / "process-crash.json").read_text("utf-8"))["pid"]
        os.kill(actual_first, signal.SIGTERM)
        first.communicate(timeout=10)
        assert first.returncode != 0 and first.poll() is not None
        assert not alive(actual_first)
        assert domain.chapter(story["chapter_id"]) == original
        after_kill = domain.task(task["id"])
        assert after_kill["status"] == "running" and after_kill["lease_until"] == interrupted["lease_until"]

        restarted = launch("observe")
        completed(restarted, 120)
        blocked = domain.task(task["id"])
        assert blocked["status"] == "blocked_runtime" and blocked["attempt"] is None
        assert (settings.data_dir / "restart-blocked.json").is_file()
        assert (
            domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task["id"],)) == steps
        )
        with pytest.raises(DomainError):
            domain.db.emit(task["id"], "text", {"delta": "late old process response"}, old_attempt)
        assert domain.chapter(story["chapter_id"]) == original

        with TestClient(create_app(settings)) as client:
            session = client.get("/api/session").json()
            client.headers["X-Ainovel-CSRF"] = session["csrf"]
            unconfirmed = client.post(f"/api/tasks/{task['id']}/resume", json={})
            assert unconfirmed.status_code == 409 and unconfirmed.json()["code"] == "unknown_result"
            resumed = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True})
            assert resumed.status_code == 200
        recovered = launch("recover")
        completed(recovered, 20)
        state = domain.task(task["id"])
        assert state["status"] == "succeeded" and state["attempt"] != old_attempt
        candidate = domain.artifact(state["result"]["artifact_id"])
        assert candidate["status"] == "candidate" and candidate["payload"]["review_passed"]
        assert domain.chapter(story["chapter_id"]) == original
        assert not domain.db.all("SELECT * FROM receipts")
        dispatches = [
            json.loads(line)
            for line in (settings.data_dir / "dispatches.jsonl").read_text("utf-8").splitlines()
        ]
        assert [row["action"] for row in dispatches] == ["writing.expand_plot", "审查正文", "审查正文"]
        actual_restarted = json.loads((settings.data_dir / "process-observe.json").read_text("utf-8"))["pid"]
        actual_recovered = json.loads((settings.data_dir / "process-recover.json").read_text("utf-8"))["pid"]
        assert dispatches[0]["pid"] == dispatches[1]["pid"] == actual_first
        assert dispatches[2]["pid"] == actual_recovered
        assert not any(row["pid"] == actual_restarted for row in dispatches)
        assert len(domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (task["id"],))) == 2
    finally:
        for process in processes:
            if process.poll() is None:
                marker = settings.data_dir / f"process-{modes[process.pid]}.json"
                if marker.is_file():
                    actual_pid = json.loads(marker.read_text("utf-8"))["pid"]
                    if alive(actual_pid):
                        try:
                            os.kill(actual_pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                process.kill()
            process.communicate(timeout=10)
