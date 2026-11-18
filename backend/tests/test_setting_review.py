import asyncio
import json
from pathlib import Path

import pytest

from ainovel.backups import create_backup, unpack_verified
from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.worker import Worker
from test_conception_workbench import confirm, path, save


@pytest.mark.parametrize("change", [None, "chapter", "setting"])
def test_setting_report_keeps_frozen_evidence(client, domain, settings, story, tmp_path, change):
    chapter = domain.save(story["chapter_id"], 0, "😀学徒拾起铜铃。\n铜铃在门外响了。")
    endpoint = path(story["branch_id"])
    state = client.get(endpoint).json()
    world = next(category for category in state["categories"] if category["id"] == "world")
    world["items"] = [{"id": "bell", "title": "铃声规则", "detail": "铜铃只在门内响。"}]
    state = confirm(client, endpoint, save(client, endpoint, state))
    request = {
        "novel_id": story["id"],
        "branch_id": story["branch_id"],
        "chapter_id": chapter["id"],
        "base_revision": chapter["revision"],
        "account": "deepseek",
        "action": "review.chapter",
        "instruction": "单条设定对账",
        "conception_snapshot_id": state["confirmation"]["id"],
        "setting_item_id": "bell",
    }
    if change is None:
        rejected = client.post("/api/modules", json={**request, "setting_item_id": "missing"})
        assert rejected.status_code == 409
    response = client.post("/api/modules", json=request)
    assert response.status_code == 202, response.text
    task = response.json()

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            assert frame["任务"]["设定"] == "铜铃只在门内响。"
            assert frame["上下文"] == {}
            first, second = frame["任务"]["可引用原文"]
            assert first["text"] == "😀学徒拾起铜铃。"
            assert second["text"] == "铜铃在门外响了。"
            if change == "chapter":
                domain.save(chapter["id"], chapter["revision"], "铜铃在门内响了。")
            if change == "setting":
                current = client.get(endpoint).json()
                next(c for c in current["categories"] if c["id"] == "world")["items"][0]["detail"] = (
                    "铜铃在门外也可以响。"
                )
                confirm(client, endpoint, save(client, endpoint, current), key="updated-setting")
            return {
                "text": json.dumps(
                    {"result": "fail", "evidence": second["source_id"], "reason": "门外响铃。"}
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-bell-check",
            }

    asyncio.run(Worker(settings, Model()).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    report = client.get(f"/api/artifacts/{done['result']['artifact_id']}").json()
    assert report["basis"]["current"] is (change is None)
    assert report["basis"]["rule"]["confirmation_id"] == state["confirmation"]["id"]
    assert report["basis"]["rule"]["detail"] == "铜铃只在门内响。"
    assert report["base_revision"] == chapter["revision"]
    evidence = report["payload"]["evidence"]
    body = chapter["content"].encode("utf-16-le")
    assert body[evidence["start"] * 2 : evidence["end"] * 2].decode("utf-16-le") == "铜铃在门外响了。"
    saved = domain.chapter(chapter["id"])
    assert saved["content"] == ("铜铃在门内响了。" if change == "chapter" else chapter["content"])
    if change == "chapter":
        history = client.get(f"/api/chapters/{chapter['id']}/history").json()
        assert (
            next(row for row in history if row["revision"] == chapter["revision"])["content"]
            == chapter["content"]
        )
    if change is None:
        restored = tmp_path / "isolated"
        unpack_verified(Path(create_backup(settings, domain.db)["path"]), restored)
        restored_report = Domain(Database(restored / "manuscripts.sqlite")).artifact(report["id"])
        assert restored_report == report
