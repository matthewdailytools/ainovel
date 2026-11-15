"""Original-only scene fixtures for headless tests; never registered in the daily API."""

from copy import deepcopy
from pathlib import Path
import sys
from time import time
from uuid import uuid4

from fastapi import FastAPI

from ainovel.db import dumps
from ainovel.scene_time import scenes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/tests"))
from test_scene_unknown_reason_correction import unknown  # noqa: E402


def register_unknown_reason_fixture(app: FastAPI, settings):
    if not settings.test_mode:
        raise RuntimeError("Unknown-reason fixture requires an isolated test-mode API.")
    domain = app.state.domain
    fixtures = {}

    @app.post("/test/unknown-scene-reason/fixtures", status_code=201)
    def create():
        novel = domain.create_novel(
            "未知原因更正工程测试 " + uuid4().hex[:6], "仅为隔离工程夹具，不代表文学质量。"
        )
        chapter = domain.create_chapter(novel["branch_id"], "次日记录——保留中文、表情和扩展汉字的原文")
        seed = unknown.__wrapped__(domain, {**novel, "chapter_id": chapter["id"]})
        identifier, second = uuid4().hex, uuid4().hex
        with domain.db.connect(True) as tx:
            # The shared original fixture has a fixed identity; rename only this
            # new isolated fixture before there are receipts, preserving its FK history.
            tx.execute("PRAGMA defer_foreign_keys=ON")
            tx.execute("UPDATE scene_times SET id=? WHERE id=?", (identifier, seed["id"]))
            tx.execute("UPDATE scene_time_history SET scene_id=? WHERE scene_id=?", (identifier, seed["id"]))
            other = deepcopy(seed["payload"])
            other["title"] = "另一条旁记"
            tx.execute(
                "INSERT INTO scene_times VALUES(?,?,?,?,?,?,?)",
                (second, seed["branch"], chapter["id"], 0, dumps(other), 0, time()),
            )
            tx.execute(
                "INSERT INTO scene_time_history VALUES(?,?,?,?,?)", (second, 0, dumps(other), 0, time())
            )
        value = {
            "id": novel["id"],
            "branch_id": seed["branch"],
            "chapter_id": chapter["id"],
            "scene_id": identifier,
            "second_scene_id": second,
            "original_payload": seed["payload"],
            "original_content": seed["chapter"]["content"],
            "original_history": domain.db.one(
                "SELECT payload FROM scene_time_history WHERE scene_id=? AND revision=0", (identifier,)
            )["payload"],
        }
        with app.state.budget.db() as usage:
            value["initial_call_ids"] = [row["id"] for row in usage.execute("SELECT id FROM calls")]
        fixtures[identifier] = value
        return value

    @app.get("/test/unknown-scene-reason/fixtures/{identifier}/invariants")
    def invariants(identifier: str):
        fixture = fixtures[identifier]
        with app.state.budget.db() as tx:
            usage = [
                dict(row)
                for row in tx.execute("SELECT * FROM calls ORDER BY id")
                if row["id"] not in fixture["initial_call_ids"]
            ]
        return {
            "chapter": domain.chapter(fixture["chapter_id"]),
            "scenes": scenes(domain, fixture["branch_id"], fixture["chapter_id"]),
            "history": domain.db.all(
                "SELECT * FROM scene_time_history WHERE scene_id=? ORDER BY revision", (identifier,)
            ),
            "tasks": domain.db.all("SELECT id FROM tasks WHERE novel_id=?", (fixture["id"],)),
            "usage": usage,
        }

    @app.post("/test/unknown-scene-reason/fixtures/{identifier}/advance-story")
    def advance(identifier: str):
        fixture = fixtures[identifier]
        chapter = domain.create_chapter(fixture["branch_id"], "仅改变故事修订的工程空章")
        return {"story_revision": chapter["story_revision"]}
