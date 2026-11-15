"""Scene reconfirmation fixtures registered only by the disposable browser server."""

from copy import deepcopy
from pathlib import Path
from time import time

from pydantic import BaseModel, ConfigDict, Field

from ainovel.db import DomainError, dumps, uid
from ainovel.scene_order import VERSION as AUDIT_VERSION, payload_sha
from ainovel.scene_time import SceneTime, scenes
from ainovel.story_graph import SaveRecord, graph, save_record

PREFIX = "/test/scene-reconfirmation"
TEXT = "😀𠮷回铺时天还没黑。\r\n他决定先理，再细看，未抄。\r\n" + "门外的脚步很轻。" * 160


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=128)


def register_scene_reconfirmation_fixture(app, settings, temporary_directory):
    """Never called by create_app; reject anything outside its actual temp directory."""
    location = Path(temporary_directory).resolve()
    if (
        not settings.test_mode
        or settings.data_dir.resolve() != location
        or not location.is_dir()
        or not location.name.startswith("ainovel-browser-tests-")
    ):
        raise RuntimeError("场景测试入口仅允许独立离线浏览器临时目录。")
    domain, budget = app.state.domain, app.state.budget
    fixtures, requests = {}, {}

    def read(identifier):
        if identifier not in fixtures:
            raise DomainError("fixture", "找不到当前隔离测试样例。", 404)
        return fixtures[identifier]

    def record(branch, payload, old=None):
        result = save_record(
            domain,
            branch,
            SaveRecord(
                base_story_revision=domain.branch(branch)["story_revision"],
                base_revision=old["revision"] if old else None,
                record=payload,
            ),
            old["id"] if old else None,
        )
        return (
            next(row for row in result["records"] if row["id"] == old["id"]) if old else result["records"][-1]
        )

    @app.post(PREFIX + "/fixtures", status_code=201)
    def seed(data: FixtureInput):
        if data.client_request_id in requests:
            return read(requests[data.client_request_id])
        novel = domain.create_novel("后台验证 · 提取场景人工重核", "原创工程样例，不是文学评审。")
        branch = novel["branch_id"]
        chapter = domain.create_chapter(branch, "回铺章——保留长中文标题及原文范围")
        chapter = domain.save(chapter["id"], 0, TEXT)
        source = {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": 0,
            "end": len(TEXT.encode("utf-16-le")) // 2,
            "quote": TEXT,
        }
        character = record(branch, {"kind": "character", "title": "林舟", "sources": [source]})
        events = [
            record(
                branch,
                {
                    "kind": "event",
                    "title": f"实际事件{index}",
                    "status": "actual",
                    "story_order": 7,
                    "story_time": "回铺后至傍晚",
                    "participants": [character["id"]],
                    "sources": [source],
                },
            )
            for index in range(2)
        ]
        refs = [
            {
                "ref": f"E{index}",
                "event_id": event["id"],
                "event_revision": event["revision"],
                "story_order": 7,
                "record_sha256": payload_sha(event["payload"]),
            }
            for index, event in enumerate(events)
        ]
        payload = SceneTime(
            title="回铺",
            source_revision=chapter["revision"],
            start=0,
            end=source["end"],
            story_order=7,
            story_time="回铺后至傍晚",
            audience="character",
            character_id=character["id"],
            order_basis={
                "version": AUDIT_VERSION,
                "kind": "event_refs",
                "refs": refs,
                "covered_events": deepcopy(refs),
            },
            note="原提取说明",
        ).model_dump()
        identifier, raw = uid(), dumps(payload)
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO scene_times VALUES(?,?,?,?,?,?,?)",
                (identifier, branch, chapter["id"], 0, raw, 0, time()),
            )
            tx.execute("INSERT INTO scene_time_history VALUES(?,?,?,?,?)", (identifier, 0, raw, 0, time()))
            tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch,))
        empty = domain.create_chapter(branch, "空章——用于切换目标与晚响应隔离")
        event = events[0]
        record(branch, {**event["payload"], "notes": "引用已经补充人工备注，场景需要重核。"}, event)
        fixture = {
            **novel,
            "fixture_id": novel["id"],
            "chapter_id": chapter["id"],
            "empty_chapter_id": empty["id"],
            "scene_id": identifier,
            "event_id": event["id"],
            "original_payload": payload,
            "original_content": TEXT,
        }
        fixtures[novel["id"]] = fixture
        requests[data.client_request_id] = novel["id"]
        return fixture

    @app.post(PREFIX + "/fixtures/{identifier}/advance-event")
    def advance(identifier: str):
        fixture = read(identifier)
        event = next(
            row for row in graph(domain, fixture["branch_id"])["records"] if row["id"] == fixture["event_id"]
        )
        updated = record(
            fixture["branch_id"],
            {**event["payload"], "notes": event["payload"].get("notes", "") + "再次补充。"},
            event,
        )
        return {"revision": updated["revision"]}

    @app.get(PREFIX + "/fixtures/{identifier}/invariants")
    def invariants(identifier: str):
        fixture = read(identifier)
        tasks = domain.db.all("SELECT id,status FROM tasks WHERE novel_id=? ORDER BY id", (fixture["id"],))
        task_ids = {row["id"] for row in tasks}
        with budget.db() as db:
            calls = [dict(row) for row in db.execute("SELECT id,task_id,status FROM calls ORDER BY id")]
        return {
            "chapter": domain.chapter(fixture["chapter_id"]),
            "scenes": scenes(domain, fixture["branch_id"], fixture["chapter_id"]),
            "tasks": tasks,
            "usage": [row for row in calls if row["task_id"] in task_ids],
            "global_task_ids": [row["id"] for row in domain.db.all("SELECT id FROM tasks ORDER BY id")],
            "global_call_ids": [row["id"] for row in calls],
        }
