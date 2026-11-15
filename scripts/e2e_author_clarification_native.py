"""Owned API-only native test process; never imported by the production server."""

import argparse
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory, gettempdir
from threading import Event, Thread
from time import monotonic

import uvicorn
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from ainovel.api import create_app  # noqa: E402
from ainovel.settings import Provider, Settings  # noqa: E402
from helpers.author_clarification_fixture import ADDITION, source_unverified  # noqa: E402

VERSION = "author-clarification-native/1"
PREFIX = "ainovel-author-clarification-native-"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def evidence_directory(path):
    path = Path(path)
    if (
        not path.is_absolute()
        or not path.name.startswith("author-clarification-native")
        or path.exists()
        or path.is_symlink()
        or not path.parent.is_dir()
    ):
        raise ValueError("A new, dedicated author-clarification-native evidence directory is required.")
    path.mkdir()
    return path.resolve()


def settings_for(location, port):
    return Settings(
        location,
        {
            "deepseek": Provider(
                "deepseek",
                "deepseek",
                "offline-native",
                "https://example.invalid",
                "offline-not-real",
                Decimal("2"),
                Decimal("8"),
            )
        },
        {"openai": Decimal("20"), "deepseek": Decimal("80")},
        test_mode=True,
        port=port,
    )


def guard(settings, location):
    location = Path(location).resolve()
    if (
        not settings.test_mode
        or settings.data_dir.resolve() != location
        or location.parent != Path(gettempdir()).resolve()
        or not location.name.startswith(PREFIX)
        or not location.is_dir()
        or set(settings.providers) != {"deepseek"}
        or settings.providers["deepseek"].kind != "deepseek"
        or settings.providers["deepseek"].base_url != "https://example.invalid"
        or settings.providers["deepseek"].key != "offline-not-real"
        or settings.port in {8765, 8766, 8877}
    ):
        raise RuntimeError("Native fixture requires owned temporary data and offline-only settings.")


def owned_socket():
    for _ in range(10):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        if listener.getsockname()[1] not in {8765, 8766, 8877}:
            return listener
        listener.close()
    raise RuntimeError("Could not reserve an independent localhost port.")


def snapshot(location):
    answer = {}
    for filename, tables in [
        ("manuscripts.sqlite", ["chapters", "task_steps", "artifacts"]),
        ("usage.sqlite", ["calls"]),
    ]:
        with closing(sqlite3.connect((location / filename).as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            for table in tables:
                answer[table] = [
                    dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                ]
    return answer


def failure_backups(location, evidence):
    for filename in ("manuscripts.sqlite", "usage.sqlite"):
        original = location / filename
        if not original.exists():
            continue
        with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(evidence / ("failed-" + filename))) as target:
                source.backup(target)


def finish_outcome(path, token):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != VERSION
        or data.get("run_token") != token
        or data.get("outcome") not in ("passed", "failed")
    ):
        return None
    return data["outcome"]


def audit(domain, location, parent, initial):
    assert snapshot(location) == initial, "Chapter/step/artifact/usage rows changed after seed."
    tasks = domain.db.all("SELECT id,status FROM tasks WHERE kind='workflow' ORDER BY created")
    assert len(tasks) == 2 and tasks[1]["status"] == "queued", "Expected one queued child only."
    current, child = domain.task(parent["id"]), domain.task(tasks[1]["id"])
    assert current["status"] == "continued" and current["payload"] == parent["payload"]
    assert {k: v for k, v in current["result"].items() if k != "continued_to"} == parent["result"]
    assert current["result"]["continued_to"] == child["id"]
    assert child["payload"]["plots"] == parent["payload"]["plots"]
    assert child["result"]["plan"] == parent["result"]["plan"]
    assert child["result"]["repair_round"] == parent["result"]["repair_round"]
    return {
        "passed": True,
        "mock_model_calls": 2,
        "real_model_calls": 0,
        "background_worker": False,
        "child_status": "queued",
        "source_task_id": parent["id"],
        "child_task_id": child["id"],
        "original_payload_unchanged": True,
        "original_result_only_continued_to_added": True,
        "chapters_steps_artifacts_usage_unchanged": True,
    }


def run(evidence, token, maximum):
    if not 32 <= len(token) <= 128 or not 1 <= maximum <= 300:
        raise ValueError("Invalid native test run token or lifetime.")
    evidence = evidence_directory(evidence)
    with TemporaryDirectory(prefix=PREFIX) as directory, owned_socket() as listener:
        location = Path(directory).resolve()
        settings = settings_for(location, listener.getsockname()[1])
        guard(settings, location)
        write_json(
            evidence / "lifecycle.json", {"version": VERSION, "phase": "seeding", "data_dir": str(location)}
        )
        server = None
        thread = None
        stop = Event()
        outcome = {"reason": "startup", "passed": False}
        deadline = monotonic() + maximum
        try:
            app = create_app(settings)
            domain = app.state.domain
            novel = domain.create_novel("原生作者来源澄清测试", "原创工程样例，不代表文学质量。")
            domain.create_chapter(novel["branch_id"], "原空章")
            with TestClient(app) as client:
                client.headers["X-Ainovel-CSRF"] = client.get("/api/session").json()["csrf"]
                _, parent, frames = source_unverified(settings, domain, novel, client=client)
            initial = snapshot(location)
            assert len(frames) == len(initial["calls"]) == 2 and not initial["artifacts"]
            assert all(
                call["status"] == "settled" and call["charged"] is not None for call in initial["calls"]
            )
            if monotonic() >= deadline:
                outcome.update(reason="deadline", passed=False)
                raise RuntimeError("Native seed exceeded its lifetime.")
            write_json(evidence / "initial.json", initial)
            server = uvicorn.Server(
                uvicorn.Config(
                    app, host="127.0.0.1", port=settings.port, access_log=False, log_level="warning"
                )
            )

            def watch():
                ready = False
                while not stop.wait(0.025):
                    if monotonic() >= deadline:
                        outcome.update(reason="deadline", passed=False)
                        server.should_exit = True
                        return
                    if server.started and not ready:
                        import os

                        write_json(
                            evidence / "ready.json",
                            {
                                "version": VERSION,
                                "run_token": token,
                                "pid": os.getpid(),
                                "parent_pid": os.getppid(),
                                "origin": f"http://127.0.0.1:{settings.port}",
                                "source": parent,
                                "story": {**novel, "branch_id": parent["branch_id"]},
                                "addition": ADDITION,
                                "mock_model_calls": 2,
                                "real_model_calls": 0,
                                "background_worker": False,
                            },
                        )
                        ready = True
                    result = finish_outcome(evidence / "finish.json", token)
                    if result is not None:
                        outcome.update(reason="client", passed=result == "passed")
                        server.should_exit = True
                        return

            def control():
                try:
                    watch()
                except BaseException as error:
                    outcome.update(reason="control_error", passed=False)
                    print("Native control failed: " + str(error), file=sys.stderr, flush=True)
                    server.should_exit = True

            thread = Thread(target=control, daemon=True)
            thread.start()
            server.run(sockets=[listener])
            stop.set()
            thread.join(timeout=2)
            if not outcome["passed"]:
                raise RuntimeError("Native test stopped without a successful client: " + outcome["reason"])
            proof = audit(domain, location, parent, initial)
            write_json(evidence / "final-audit.json", proof)
            write_json(
                evidence / "lifecycle.json",
                {"version": VERSION, "phase": "closed", "passed": True, "stop_reason": outcome["reason"]},
            )
            return 0
        except BaseException as error:
            stop.set()
            write_json(
                evidence / "failure.json",
                {
                    "version": VERSION,
                    "passed": False,
                    "type": type(error).__name__,
                    "message": str(error),
                    "stop_reason": outcome["reason"],
                },
            )
            try:
                failure_backups(location, evidence)
            except (OSError, sqlite3.Error) as backup_error:
                write_json(evidence / "backup-failure.json", {"message": str(backup_error)})
            return 1
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--run-token", required=True)
    parser.add_argument("--max-seconds", type=float, default=90)
    args = parser.parse_args()
    raise SystemExit(run(args.evidence_dir, args.run_token, args.max_seconds))


if __name__ == "__main__":
    main()
