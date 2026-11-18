import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from ainovel import evaluate, runtime
from ainovel.backups import restore_backup
from ainovel.db import Database, DomainError


def test_evaluation_waits_for_restore_and_unregisters_when_the_worker_fails(settings, monkeypatch):
    blocked = Event()
    attempted = Event()
    real_lock = runtime._try_data_lock

    def observed_lock(file):
        try:
            real_lock(file)
        except OSError:
            blocked.set()
            raise

    async def offline_worker(instance):
        assert instance.db.all("SELECT role FROM processes") == [{"role": "worker"}]
        with pytest.raises(DomainError) as refused:
            restore_backup(settings, instance.db, settings.data_dir / "unused.zip")
        assert refused.value.code == "service_active"
        attempted.set()
        raise RuntimeError("stop before calling any model")

    monkeypatch.setattr(evaluate.Settings, "load", lambda: settings)
    monkeypatch.setattr(evaluate.Worker, "run_once", offline_worker)
    monkeypatch.setattr(runtime, "_try_data_lock", observed_lock)
    args = SimpleNamespace(stage="samples", chapters=1, accounts="deepseek", run_limit=1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with runtime.data_directory_gate(settings.data_dir):
            work = executor.submit(lambda: asyncio.run(evaluate.evaluate(args)))
            assert blocked.wait(5), "evaluation did not encounter the held recovery gate"
            assert not work.done()
            assert not (settings.data_dir / "manuscripts.sqlite").exists()
            assert not (settings.data_dir / "usage.sqlite").exists()
            assert not (settings.data_dir / "acceptance").exists()
        with pytest.raises(RuntimeError, match="stop before calling any model"):
            work.result(timeout=10)

    assert attempted.is_set()
    with runtime.data_directory_gate(settings.data_dir):
        database = Database(settings.data_dir / "manuscripts.sqlite")
        assert database.all("SELECT * FROM processes") == []
