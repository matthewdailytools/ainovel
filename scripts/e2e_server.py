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
from test_workflow_scope import SegmentModel as BookModel  # noqa: E402
from helpers.character_review_fixture import character_review_output  # noqa: E402
from helpers.plan_execution_fixture import plan_execution_output  # noqa: E402

CHARACTER_REPAIR_FIXTURE = "character-field-repair-e2e"
UNSUPPORTED_CHARACTER_NOTE = "工程替身错误备注：沈砚已经掌握飞行能力。"
GROUNDED_CHARACTER_NOTE = "沈砚用旧尺测量潮水，留下可以回查的记录。"
PLOT_UNKNOWN_FIXTURE = "plot-check-e2e-unknown"
PLAN_PREPARATION_FIXTURE = "plan-preparation-e2e"
PLOT_EXCLUSIVE_FIXTURE = "plot-scope-exclusive-e2e"
EXCLUSIVE_BODY = (
    "沈砚回到修船铺，把记录本摊在师傅面前。他把已经核对过的潮水读数完整告诉师傅，"
    "师傅听完，又逐项复述了一遍，将数字记在自己的本子里。沈砚等墨迹干了才翻过纸页，"
    "没有把前后两次测量混为一谈。桌边的油灯晃了一下，师傅用手掌压住翘起的纸角。\n\n"
    "片刻后，沈砚又把同一份潮水读数完整告诉门边另一个人。那人听过数字，在一张窄纸上记下，"
    "收好纸张才离开。门帘遮住了对方的脸，沈砚没有问姓名，也没有再作解释。\n\n"
    "风从门缝钻进来，吹动墙上的旧船图。沈砚把旧尺擦干，放回钉着铁皮的木匣，"
    "再将原始记录锁进柜中。师傅把灯芯挑短，让他明早仍照原来的标记复测。"
    "他答应下来，检查过门闩，又看了一眼桌上已经抄好的数字，才端起那碗凉了的水。"
)


class Model(ReferenceModel):
    def __init__(self):
        super().__init__()
        self.book_tasks = set()
        self.book_model = BookModel()
        self.character_repair_tasks = {}
        self.exclusive_plot_tasks = set()
        self.plan_preparation_tasks = {}

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        instruction = value["任务"]
        source_instruction = instruction
        while "原始任务" in source_instruction:
            source_instruction = source_instruction["原始任务"]
        if source_instruction.get(
            "动作"
        ) == "按章纲写完整正文" and PLOT_EXCLUSIVE_FIXTURE in source_instruction.get("本章", {}).get(
            "plot", ""
        ):
            self.exclusive_plot_tasks.add(task_id)
            self.book_tasks.add(task_id)
            return {
                "text": EXCLUSIVE_BODY,
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-exclusive-body",
            }
        brief = source_instruction.get("作品简报", "")
        if PLAN_PREPARATION_FIXTURE in brief:
            self.plan_preparation_tasks[task_id] = "unresolved" if "fixture-unresolved" in brief else "ready"
        plan_fixture = self.plan_preparation_tasks.get(task_id)
        if plan_fixture and source_instruction.get("动作") in {"阶段批次规划", "全书滚动规划"}:
            result = await self.book_model.generate(account, task_id, system, prompt, max_output, **kwargs)
            plan = json.loads(result["text"])
            plan["chapters"][0]["plot"] = "测量并记录，保留三种待验原因；登记记录的具体细节尚未指定。"
            result["text"] = json.dumps(plan)
            self.book_tasks.add(task_id)
            return result
        plan_response = plan_execution_output(source_instruction)
        if plan_response is not None:
            if source_instruction.get("动作") == "写前解释AI工作章纲":
                baseline = source_instruction["写前准备契约"].get("原始事项")
                if plan_fixture and (not baseline or plan_fixture == "unresolved"):
                    for item in plan_response["items"]:
                        item["certainty"] = "ambiguous"
            return {
                "text": json.dumps(plan_response),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-plan-preparation",
            }
        if CHARACTER_REPAIR_FIXTURE in brief:
            self.character_repair_tasks[task_id] = "cannot" if "fixture-cannot-repair" in brief else "repair"
        fixture = self.character_repair_tasks.get(task_id)
        if fixture and source_instruction.get("动作") == "修正未采纳人物自由文本字段":
            return {
                "text": json.dumps(
                    {
                        "version": source_instruction["人物修订契约版本"],
                        "input_sha256": source_instruction["input_sha256"],
                        "fields": [
                            {
                                "record_id": target["record_id"],
                                "field": target["field"],
                                "old_sha256": target["old_sha256"],
                                "decision": "cannot_repair" if fixture == "cannot" else "replace",
                                "replacement": None if fixture == "cannot" else GROUNDED_CHARACTER_NOTE,
                                "source_ids": target["source_ids"][:1],
                                "reason": "确定性工程替身：只保留本项来源明确出现的测量与记录，不制造能力。",
                            }
                            for target in source_instruction["人物字段"]
                        ],
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-character-repair",
                "model": "offline-character-repair-fixture",
            }
        character_review = character_review_output(instruction)
        if character_review is not None:
            if fixture:
                for target, field in zip(
                    source_instruction["人物字段"], character_review["fields"], strict=True
                ):
                    if target["text"] == UNSUPPORTED_CHARACTER_NOTE:
                        field.update(
                            result="unknown",
                            source_ids=[],
                            reason="工程替身：本项原文没有飞行能力依据，保持unknown。",
                        )
            return {
                "text": json.dumps(character_review),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-character-review",
            }
        if instruction.get("动作") == "整理作者情绪钩子与方向契约":
            author_text = instruction["作者原话"]
            return {
                "text": json.dumps(
                    {
                        "emotion_target": "从疑虑到取得小进展",
                        "core_hook": "闸门旧账为什么缺页",
                        "direction": "借尺复测闸门，调查维修记录",
                        "ending": "",
                        "fixed_results": [],
                        "prohibitions": [p for p in ("不修门", "不战斗") if p in author_text],
                        "open_details": ["公开刻度、借尺对白和记录动作可由作者选择"],
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-intent",
            }
        designs = {
            "world.generate": "世界提案：临河小城的旧闸有公开刻度，检查与修理分属不同职责。尚未决定的制度保持待作者补充。",
            "character.generate": "人物提案：女修城器师林杳会读尺和记录，须借用守闸人的旧尺；没有修门权限，不新增能力。",
            "outline.plan": "粗纲提案：本章核对公开刻度、借尺复测，只留下读数；下一阶段与终局留待作者决定，不强制填满。",
        }
        if instruction.get("action") in designs:
            return {
                "text": designs[instruction["action"]],
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-design",
            }
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
        plot_unknown = PLOT_UNKNOWN_FIXTURE in instruction.get("作者原话", "")
        scoped = instruction.get("情节核对契约版本") == "author-plot-audit/2"
        exclusive = scoped and PLOT_EXCLUSIVE_FIXTURE in instruction.get("作者原话", "")
        if instruction.get("动作") == "仅依据作者原话整理本场景情节":
            item = {
                "author_evidence": "AUTHOR0001",
                "actor": "沈砚" if plot_unknown else "她",
                "action": "测量并记录" if plot_unknown else "检查",
                "object": "潮水读数" if plot_unknown else "闸门",
                "direction": "",
                "result": "是否公开读数尚未确定" if plot_unknown else "不修门",
                "certainty": "ambiguous" if plot_unknown else "explicit",
            }
            if scoped:
                item.update(recipient_scope="unspecified", scope_condition="")
            if exclusive:
                item.update(
                    actor="沈砚",
                    action="告诉",
                    object="潮水读数",
                    direction="沈砚向师傅告知潮水读数",
                    result="只向师傅交付读数",
                    certainty="explicit",
                    transfer_object="潮水读数",
                    supplier="沈砚",
                    recipient="师傅",
                    transfer_scope="completed",
                    recipient_scope="only",
                    scope_condition="本章中",
                )
            return {
                "text": json.dumps(
                    {
                        "items": [item],
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
                                "result": "unknown" if plot_unknown else "pass",
                                "observed": "沈砚已向师傅交付潮水读数"
                                if exclusive
                                else "只看到量潮记录，公开安排仍未知"
                                if plot_unknown
                                else "保留测量，没有修门",
                                "evidence": instruction["可引用原文"][0]["source_id"],
                                "reason": "作者情节保留公开安排的歧义，不升级为硬条件"
                                if plot_unknown
                                else "工程替身结果，不代表模型语义能力",
                                "repair": "",
                            }
                        ],
                        "summary": "工程替身情节核对",
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline",
            }
        if (
            scoped
            and task_id in self.exclusive_plot_tasks
            and instruction.get("动作") == "只从本次正文独立提取物品与信息流向"
        ):
            entities = instruction["实体别名对照"]
            supplier = next(key for key, name in entities.items() if name == "沈砚")
            recipient = next(key for key, name in entities.items() if name == "师傅")
            object_id = next(key for key, name in instruction["待识别物品名称"].items() if name == "潮水读数")
            units = instruction["可引用原文"]
            return {
                "text": json.dumps(
                    {
                        "flows": [
                            {
                                "object_id": object_id,
                                "supplier_id": supplier,
                                "recipient_id": recipient,
                                "stage": "completed",
                                "certainty": "explicit",
                                "evidence": units[0]["source_id"],
                                "note": "师傅已经听取并记下读数。",
                            },
                            {
                                "object_id": object_id,
                                "supplier_id": supplier,
                                "recipient_id": "unknown",
                                "stage": "completed",
                                "certainty": "explicit",
                                "evidence": units[1]["source_id"],
                                "note": "另一人已听取同一读数，但原文没有可映射的姓名，不能猜测其身份。",
                            },
                        ],
                        "note": "确定性工程观察；保留正向与未知接收者，不判断作者限定是否成立。",
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-exclusive-flows",
            }
        if instruction.get("动作") in {
            "全书滚动规划",
            "阶段批次规划",
            "按章纲写完整正文",
            "从正文提取事实候选与章节摘要",
            "整书完结检查",
            "批次范围检查",
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
                if fixture and records:
                    records[0]["record"]["notes"] = UNSUPPORTED_CHARACTER_NOTE
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
                    if instruction.get("场景证据契约版本") == "scene-event-refs/2":
                        for scene in payload["scenes"]:
                            scene.pop("story_order")
                            scene["order_basis"] = {
                                "kind": "unknown",
                                "reason": "本测试仅有范围依据，未独立确认故事时点。",
                                "refs": [],
                            }
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
                if instruction.get("场景证据契约版本") == "scene-event-refs/2":
                    for scene in payload["scenes"]:
                        scene.pop("story_order")
                        scene["order_basis"] = {
                            "kind": "unknown",
                            "reason": "本测试仅有范围依据，未独立确认故事时点。",
                            "refs": [],
                        }
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
