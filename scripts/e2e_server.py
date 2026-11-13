"""Isolated real API/Worker test server, with a deterministic offline model only."""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import uvicorn

from ainovel.api import create_app
from ainovel.settings import Provider, Settings
from ainovel.worker import Worker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/tests"))
from test_reference_flow import Model as ReferenceModel  # noqa: E402
from test_story_extraction import ExtractionModel, UpdateModel  # noqa: E402


class Model(ReferenceModel):
    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        instruction = value["任务"]
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        if instruction.get("动作") == "按保存稿反向整理长篇结构候选":
            fixture = (
                UpdateModel()
                if any(r["kind"] == "character" for r in instruction["已有记录索引"])
                else ExtractionModel()
            )
            return await fixture.generate(account, task_id, system, prompt, max_output, **kwargs)
        return await super().generate(account, task_id, system, prompt, max_output, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ainovel-browser-tests-") as location:
        settings = Settings(
            Path(location),
            {
                "offline": Provider(
                    "offline",
                    "deepseek",
                    "offline-fixture",
                    "https://example.invalid",
                    "offline-not-real",
                    Decimal("2"),
                    Decimal("8"),
                )
            },
            {"openai": Decimal("20"), "deepseek": Decimal("80")},
            test_mode=True,
            port=args.port,
        )
        app = create_app(settings)
        original = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            async with original(app):
                worker = Worker(settings, Model())

                async def execute():
                    while True:
                        if not await worker.run_once():
                            await asyncio.sleep(0.1)

                runner = asyncio.create_task(execute())
                try:
                    yield
                finally:
                    runner.cancel()
                    try:
                        await runner
                    except asyncio.CancelledError:
                        pass

        app.router.lifespan_context = lifespan
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
