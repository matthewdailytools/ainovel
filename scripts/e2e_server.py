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
from ainovel.db import DomainError
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
        if instruction.get("action") == "writing.advise":
            return {
                "text": "方向一：她先核对公开刻度，再向守闸人借尺复测，暂不修门。\n方向二：改查私藏账簿。\n以上只供作者选择，不代表已经发生。",
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }
        if instruction.get("action") == "writing.expand_plot":
            return {
                "text": "她在闸门前停下，先把公开刻度抄进本子。守闸人把旧尺递给她，她沿原来的标记复测，又记下读数。她只检查，没有修门，也未动用尚未取得的工具。",
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }
        if instruction.get("action") == "writing.rewrite_selection":
            if "后台故障演练：慢速可取消" in instruction.get("作者要求", ""):
                partial = "等待核查的未采纳片段。" * 15
                if kwargs.get("on_text"):
                    await kwargs["on_text"](partial)
                for _ in range(150):
                    await asyncio.sleep(0.1)
                    if kwargs.get("cancelled") and kwargs["cancelled"]():
                        error = DomainError("cancelled", "工程替身收到取消，后续结果不发布。", 409)
                        error.partial_text = partial
                        raise error
            return {
                "text": "她先核对刻度，再记录读数。",
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        if instruction.get("动作") == "仅依据作者原话整理本场景情节":
            return {
                "text": json.dumps(
                    {
                        "items": [
                            {
                                "author_evidence": "AUTHOR0001",
                                "actor": "她",
                                "action": "检查",
                                "object": "闸门",
                                "direction": "",
                                "result": "不修门",
                                "certainty": "explicit",
                            }
                        ],
                        "note": "仅为确定性界面验证",
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }
        if instruction.get("动作") == "逐项对照作者情节解释与本次正文":
            return {
                "text": json.dumps(
                    {
                        "checks": [
                            {
                                "item_id": "P01",
                                "result": "pass",
                                "observed": "保留测量，没有修门",
                                "evidence": "PLOT001P0001",
                                "reason": "工程替身结果，不代表模型语义能力",
                                "repair": "",
                            }
                        ],
                        "summary": "工程替身情节核对",
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }
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
                payload = {"records": records, "unknowns": [], "coverage": "当前章"}
                if "场景范围要求" in instruction:
                    payload["scenes"] = [
                        {
                            "title": "本章场景",
                            "source_ids": [u["source_id"] for u in instruction["可引用原文"]],
                            "story_order": None,
                            "story_time": "未知",
                            "audience": "author",
                            "character_id": None,
                            "note": "工程替身不推断时点",
                        }
                    ]
                return {
                    "text": json.dumps(payload),
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
            result = await fixture.generate(account, task_id, system, prompt, max_output, **kwargs)
            if "场景范围要求" in instruction:
                payload = json.loads(result["text"])
                chapters = dict.fromkeys(u["chapter_id"] for u in instruction["可引用原文"])
                payload["scenes"] = [
                    {
                        "title": "本章场景",
                        "source_ids": [
                            u["source_id"] for u in instruction["可引用原文"] if u["chapter_id"] == chapter
                        ],
                        "story_order": None,
                        "story_time": "未知",
                        "audience": "author",
                        "character_id": None,
                        "note": "工程替身不推断时点",
                    }
                    for chapter in chapters
                ]
                result["text"] = json.dumps(payload)
            return result
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
