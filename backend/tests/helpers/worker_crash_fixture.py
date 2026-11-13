"""Deterministic subprocess used only by the real Worker crash/restart test."""

import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from time import monotonic, time

from ainovel.settings import Provider, Settings
from ainovel.worker import Worker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_api_worker import FakeModel  # noqa: E402


class SlowReviewModel(FakeModel):
    def __init__(self, folder, stop_at_review):
        super().__init__()
        self.folder, self.stop_at_review = folder, stop_at_review

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        action = instruction.get("动作", instruction.get("action"))
        with (self.folder / "dispatches.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(json.dumps({"task_id": task_id, "action": action, "pid": os.getpid()}) + "\n")
        if action == "审查正文" and self.stop_at_review:
            (self.folder / "review-started.json").write_text(
                json.dumps({"task_id": task_id}), encoding="utf-8"
            )
            await asyncio.Event().wait()
        return await super().generate(account, task_id, system, prompt, max_output, **kwargs)


async def run(folder, mode, task_id):
    (folder / f"process-{mode}.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    providers = {
        name: Provider(name, kind, "test-model", url, "test-not-real", Decimal("2"), Decimal("8"))
        for name, kind, url in [
            ("openai_1", "openai", "https://api.openai.com/v1"),
            ("openai_2", "openai", "https://api.openai.com/v1"),
            ("deepseek", "deepseek", "https://api.deepseek.com"),
        ]
    }
    settings = Settings(
        folder, providers, {"openai": Decimal("20"), "deepseek": Decimal("80")}, test_mode=True
    )
    worker = Worker(settings, SlowReviewModel(folder, mode == "crash"))
    if mode == "observe":
        # Wait for the production lease to expire naturally after the first process
        # was actually killed. No SQL expiry edits or test-clock adjustments.
        deadline = monotonic() + 110
        while monotonic() < deadline:
            assert not await worker.run_once(), "Restart must not replay a running/unknown task"
            state = worker.domain.task(task_id)
            if state["status"] == "blocked_runtime":
                (folder / "restart-blocked.json").write_text(
                    json.dumps({"status": state["status"], "time": time()}), encoding="utf-8"
                )
                return
            await asyncio.sleep(0.2)
        raise AssertionError("Interrupted lease never reached blocked_runtime")
    assert await worker.run_once()
    if mode == "recover":
        state = worker.domain.task(task_id)
        assert state["status"] == "succeeded", state["error"]


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
