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
from test_api_worker import FakeModel as BookModel  # noqa: E402


class Model(ReferenceModel):
    def __init__(self):
        super().__init__()
        self.book_tasks = set()
        self.book_model = BookModel()

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        instruction = value["任务"]
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        if instruction.get("动作") in {
            "全书滚动规划",
            "按章纲写完整正文",
            "从正文提取事实候选与章节摘要",
            "整书完结检查",
        }:
            self.book_tasks.add(task_id)
        if task_id in self.book_tasks:
            if instruction.get("动作") == "按保存稿反向整理长篇结构候选":
                unit = instruction["可引用原文"][0]
                source = {k: unit[k] for k in ("chapter_id", "revision", "start", "end")}
                source["quote"] = unit["source_id"]
                records = (
                    []
                    if instruction["已有记录索引"]
                    else [
                        {
                            "local_id": "shen",
                            "record": {"kind": "character", "title": "沈砚", "sources": [source]},
                            "reasoning": "只提取当前章已出现的人物",
                        }
                    ]
                )
                realized_events = set()
                for old in instruction["已有记录索引"]:
                    if (
                        old.get("source_free_plan")
                        and old["kind"] == "event"
                        and old["title"] in unit["text"]
                    ):
                        records.append(
                            {
                                "local_id": "realized-" + old["id"],
                                "target_id": old["id"],
                                "target_revision": old["revision"],
                                "update_fields": ["status"],
                                "reasoning": "当前章明确兑现相同事件",
                                "record": {
                                    "kind": "event",
                                    "title": old["title"],
                                    "status": "actual",
                                    "sources": [source],
                                },
                            }
                        )
                        realized_events.add(old["id"])
                for old in instruction["已有记录索引"]:
                    if (
                        old.get("source_free_plan")
                        and old["kind"] == "state"
                        and old["event_id"] in realized_events
                    ):
                        records.append(
                            {
                                "local_id": "realized-" + old["id"],
                                "target_id": old["id"],
                                "target_revision": old["revision"],
                                "update_fields": ["truth"],
                                "reasoning": "工程替身中测量已发生，状态断言保持",
                                "record": {
                                    "kind": "state",
                                    "title": old["title"],
                                    "truth": "actual",
                                    "sources": [source],
                                    **{
                                        k: old[k]
                                        for k in ("character_id", "event_id", "category", "key", "audience")
                                    },
                                    "value": old["planned_value"],
                                },
                            }
                        )
                return {
                    "text": json.dumps({"records": records, "unknowns": [], "coverage": "当前章"}),
                    "usage": {"input": 1, "output": 1},
                    "call_id": "offline-book-structure",
                }
            return await self.book_model.generate(account, task_id, system, prompt, max_output, **kwargs)
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
