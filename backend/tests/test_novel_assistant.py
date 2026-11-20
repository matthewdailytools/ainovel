import asyncio
from dataclasses import replace
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ainovel.api import create_app
from ainovel.backups import restore_backup
from ainovel.db import DomainError
from ainovel.novel_assistant import read_card
from ainovel.worker import Worker
from test_assistant import Model
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_work_snapshot import verify


def route(story):
    return f"/api/branches/{story['branch_id']}/novel-assistant"


def request(revision, stage, key, **overrides):
    return {
        "base_revision": revision,
        "stage": stage,
        "scope_id": None,
        "question": "继续核对故事。",
        "account": "deepseek",
        "client_request_id": key,
        **overrides,
    }


def proposed(destination, field, value):
    return {
        "destination": destination,
        "field": field,
        "title": "待用建议",
        "value": value,
        "reason": "保持线索。",
    }


def test_workspace_unifies_scoped_turns_validates_cards_and_keeps_private_material_local(
    client, story, domain, settings
):
    path, branch, chapter = route(story), story["branch_id"], story["chapter_id"]
    setting_id = setting_confirmation(client, branch)
    put_and_confirm(
        client,
        f"/api/branches/{branch}/outline-workspace",
        {
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": {
                "summary": "重返古宅",
                "volumes": [
                    {
                        "id": "volume",
                        "title": "老屋",
                        "summary": "查证",
                        "units": [
                            {
                                "id": "unit",
                                "title": "门槛",
                                "summary": "寻找铜铃",
                                "chapters": [chapter_plan(chapter, "识别铜铃断口")],
                            }
                        ],
                    }
                ],
            },
        },
    )
    domain.save(chapter, 0, "未选择的正式章稿。")
    assert verify(client.get(path)) == {
        "revision": 0,
        "draft": {"text": "", "revision": 0},
        "turns": [],
        "next_before": None,
    }
    first = request(
        0,
        "M3",
        "workspace-first-turn",
        scope_id="bell",
        question="  核对设定和章纲。\n",
        references=[{"stage": "M4", "scope_id": chapter}],
        budget_limit=0.12,
    )
    saved = verify(client.put(path + "/draft", json={"text": first["question"], "base_revision": 0}))
    assert saved == {"text": first["question"], "revision": 1}
    assert client.put(path + "/draft", json={"text": "另一窗口", "base_revision": 0}).json()["code"] == (
        "novel_assistant_draft_changed"
    )
    queued = verify(client.post(path + "/turns", json=first), 202)
    assert queued["revision"] == 1
    assert queued["turn"]["stage"] == "M3"
    assert queued["turn"]["references"][0]["scope_id"] == chapter
    assert verify(client.get(path))["draft"] == {"text": "", "revision": 2}
    assert queued == verify(client.post(path + "/turns", json=first), 202)
    assert (
        client.post(path + "/turns", json={**first, "question": "其他输入"}).json()["code"] == "idempotency"
    )
    assert client.post(path + "/turns", json=request(0, "M4", "workspace-stale-turn")).json()["code"] == (
        "novel_assistant_changed"
    )
    assert client.post(path + "/turns", json=request(1, "M4", "workspace-pending-turn")).json()["code"] == (
        "novel_assistant_pending"
    )
    assert not verify(client.get(f"/api/tasks?branch_id={branch}"))
    model = Model(
        [
            {
                "answer": "已经整理为两份待用建议。",
                "cards": [
                    proposed("D1", "detail", "铜铃依回声鸣响。"),
                    proposed("D2", "goal", "追查铜铃的断口。"),
                ],
            },
            {"answer": "先从线索讨论过渡。", "cards": []},
        ]
    )
    worker = Worker(settings, model)
    assert asyncio.run(worker.run_once())
    assert model.requests[0][:2] == (queued["turn"]["task_id"], 0.12)
    first_page = verify(client.get(path))
    [logged] = first_page["turns"]
    assert logged["status"] == "succeeded"
    assert [(card["stage"], card["scope_id"], card["field"]) for card in logged["result"]["cards"]] == [
        ("M3", "bell", "detail"),
        ("M4", chapter, "goal"),
    ]
    assert len({card["id"] for card in logged["result"]["cards"]}) == 2
    with domain.db.connect() as tx:
        card = read_card(tx, branch, logged["id"], logged["result"]["cards"][0]["id"])
        assert len(card["basis_sha256"]) == 64
    assert "铜铃只在门内响。" in model.calls[0] and "识别铜铃断口" in model.calls[0]
    assert "未选择的正式章稿。" not in model.calls[0]
    assert domain.chapter(chapter)["content"] == "未选择的正式章稿。"
    edited = verify(client.put(path + "/draft", json={"text": "另一窗口的新输入", "base_revision": 2}))
    assert edited["revision"] == 3
    second = request(
        1,
        "M5",
        "workspace-second-turn",
        scope_id=chapter,
        question="讨论过渡。",
        include_current_scope=False,
    )
    verify(client.post(path + "/turns", json=second), 202)
    assert asyncio.run(worker.run_once())
    assert "已经整理为两份待用建议。" in model.calls[1]
    for private in (
        "铜铃依回声鸣响。",
        "识别铜铃断口",
        "未选择的正式章稿。",
        "genre_profile",
        "author_contract",
    ):
        assert private not in model.calls[1]
    semantic = json.loads(model.calls[1].split("\n只返回符合以下 JSON Schema 的对象：\n", 1)[0])
    assert semantic["上下文"]["本轮引用"] == {}
    latest = verify(client.get(path + "?limit=1"))
    assert latest["draft"] == edited
    assert [turn["sequence"] for turn in latest["turns"]] == [2]
    assert latest["next_before"] == 2
    assert verify(client.get(path + "?before=2&limit=1"))["turns"] == [logged]
    other = domain.create_novel("另一部作品", "")
    assert verify(client.get(route(other)))["turns"] == []
    assert (
        client.post(
            route(other) + "/turns",
            json=request(
                0,
                "M1",
                "workspace-cross-branch",
                references=[{"stage": "M5", "scope_id": chapter}],
            ),
        ).status_code
        == 404
    )
    with domain.db.connect() as tx, pytest.raises(DomainError, match="找不到这张待用卡"):
        read_card(tx, other["branch_id"], logged["id"], card["id"])


def test_workspace_checks_explicit_reference_revision_before_dispatch_and_can_cancel(
    client, story, domain, settings
):
    path, chapter = route(story), story["chapter_id"]
    payload = request(
        0,
        "M1",
        "workspace-stale-reference",
        references=[{"stage": "M5", "scope_id": chapter}],
    )
    first = verify(client.post(path + "/turns", json=payload), 202)["turn"]
    domain.save(chapter, 0, "排队后的新稿。")
    model = Model()
    worker = Worker(settings, model)
    assert asyncio.run(worker.run_once())
    assert model.calls == []
    stopped = verify(client.get(path))["turns"][0]
    assert stopped["status"] == "blocked_runtime" and stopped["result"] is None
    assert "引用已改变" in stopped["error"]
    assert (
        client.post(f"/api/tasks/{first['task_id']}/resume", json={}).json()["code"]
        == "novel_assistant_resume"
    )
    next_turn = verify(
        client.post(path + "/turns", json=request(1, "M1", "workspace-cancel-before-call")),
        202,
    )["turn"]
    assert verify(client.post(f"/api/tasks/{next_turn['task_id']}/cancel"))["status"] == "cancelled"
    assert not asyncio.run(worker.run_once())
    assert verify(client.get(path))["turns"][-1]["status"] == "cancelled"


@pytest.mark.parametrize(
    "cards",
    [
        [proposed("D9", "detail", "伪造的去向。")],
        [proposed("D1", "detail", "用别台字段。")],
        [proposed("D1", "position", "甲。"), proposed("D1", "position", "乙。")],
        [proposed("D1", "position", "逾" * 701)],
    ],
)
def test_workspace_rejects_unbound_duplicate_and_oversize_proposals(client, story, settings, cards):
    path = route(story)
    verify(client.post(path + "/turns", json=request(0, "M1", "workspace-bad-card")), 202)
    model = Model([{"answer": "备选", "cards": cards}])
    assert asyncio.run(Worker(settings, model).run_once())
    [stopped] = verify(client.get(path))["turns"]
    assert stopped["status"] == "blocked_runtime" and stopped["result"] is None
    assert len(model.calls) == 1


def test_workspace_draft_and_cross_stage_history_survive_library_backup(client, story, settings, tmp_path):
    path = route(story)
    verify(client.post(path + "/turns", json=request(0, "M1", "workspace-backup-first")), 202)
    model = Model([{"answer": "已保留文案。", "cards": [proposed("D1", "idea", "新的故事设想。")]}])
    worker = Worker(settings, model)
    assert asyncio.run(worker.run_once())
    verify(client.post(path + "/turns", json=request(1, "M7", "workspace-backup-second")), 202)
    model.replies.append({"answer": "确认初步讨论。", "cards": []})
    assert asyncio.run(worker.run_once())
    verify(client.put(path + "/draft", json={"text": "没发送的下一轮。", "base_revision": 0}))
    original = verify(client.get(path))
    archive = verify(client.post("/api/backups"))
    restored = replace(settings, data_dir=tmp_path / "restored-workspace")
    restore_backup(restored, None, Path(archive["path"]))
    with TestClient(create_app(restored)) as recovered:
        assert verify(recovered.get(path)) == original
