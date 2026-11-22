import asyncio
import json
from decimal import Decimal

import httpx

from ainovel.budget import Budget
from ainovel.providers import ModelClient
from ainovel.workspace_chat import SOURCE_LIMIT
from ainovel.worker import Worker


class ChatModel:
    def __init__(self, callback=None):
        self.requests = []
        self.callback = callback

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        self.requests.append({**json.loads(prompt), "max_output": max_output})
        if self.callback:
            self.callback(task_id)
        return {
            "text": f"仅供讨论的回答 {len(self.requests)}。",
            "usage": {"input": 1, "output": 1},
            "reason": "stop",
        }


def path(story):
    return f"/api/branches/{story['branch_id']}/workspace-chat"


def chapter_message(chapter, question, request_id):
    return {
        "chapter_id": chapter["id"],
        "base_revision": chapter["revision"],
        "account": "deepseek",
        "question": question,
        "client_request_id": request_id,
    }


def test_chat_uses_current_read_only_scope_and_recent_turns(client, domain, story, settings):
    chapter = domain.save(story["chapter_id"], 0, "司钥人靠在窗边，等潮退去。")
    other = domain.create_chapter(story["branch_id"], "另一章节")
    domain.save(other["id"], 0, "隔壁章节独有线索")
    context = client.get(f"/api/branches/{story['branch_id']}/context").json()
    result = client.post(
        f"/api/branches/{story['branch_id']}/definitions",
        json={
            "kind": "world",
            "text": "世界设定独有词句",
            "base_story_revision": context["branch"]["story_revision"],
        },
    )
    assert result.status_code == 201, result.text
    model = ChatModel()
    worker = Worker(settings, model)
    first_body = chapter_message(chapter, "第一句如何调整？", "workspace-turn-one")
    first = client.post(path(story), json=first_body)
    assert first.status_code == 202, first.text
    assert client.post(path(story), json=first_body).json()["id"] == first.json()["id"]
    assert (
        client.post(path(story), json={**first_body, "client_request_id": "workspace-other"}).status_code
        == 409
    )
    assert asyncio.run(worker.run_once())
    assert client.post(path(story), json=first_body).json()["reply"] == "仅供讨论的回答 1。"

    second = client.post(
        path(story),
        json={**chapter_message(chapter, "继续谈第一句。", "workspace-turn-two"), "reply_length": "concise"},
    )
    assert second.status_code == 202, second.text
    assert asyncio.run(worker.run_once())
    initial, followup = model.requests
    assert initial["上下文"]["当前保存内容"] == chapter["content"]
    assert "author_contract" not in followup["上下文"] and "genre_profile" not in followup["上下文"]
    assert "隔壁章节独有线索" not in str(followup) and "世界设定独有词句" not in str(followup)
    assert followup["上下文"]["同版本此前对话"] == [{"作者": "第一句如何调整？", "AI": "仅供讨论的回答 1。"}]
    assert followup["max_output"] == 700

    history = client.get(path(story), params={"chapter_id": chapter["id"]}).json()
    assert [turn["id"] for turn in history["turns"]] == [first.json()["id"], second.json()["id"]]
    assert client.get("/api/tasks", params={"branch_id": story["branch_id"]}).json() == []
    assert client.get(path(story), params={"chapter_id": other["id"]}).json()["turns"] == []
    assert client.get(path(story), params={"setting_kind": "world"}).json()["turns"] == []
    assert domain.chapter(chapter["id"]) == chapter
    assert not domain.db.all(
        "SELECT id FROM artifacts WHERE task_id IN (?,?)", (first.json()["id"], second.json()["id"])
    )


def test_chat_blocks_replaced_basis_and_keeps_old_history(client, domain, story, settings):
    model = ChatModel()
    worker = Worker(settings, model)
    before = client.get(path(story), params={"setting_kind": "world"}).json()
    assert before["source"]["artifact_id"] is None

    def write_definition(text):
        result = client.post(
            f"/api/branches/{story['branch_id']}/definitions",
            json={
                "kind": "world",
                "text": text,
                "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
            },
        )
        assert result.status_code == 201, result.text
        return client.get(path(story), params={"setting_kind": "world"}).json()

    source = write_definition("第一版规则。")
    first = {
        "setting_kind": "world",
        "base_artifact_id": source["source"]["artifact_id"],
        "account": "deepseek",
        "client_request_id": "workspace-setting-old",
        "question": "第一个问题",
    }
    assert client.post(path(story), json=first).status_code == 202
    assert asyncio.run(worker.run_once())
    queued = client.post(path(story), json={**first, "client_request_id": "workspace-setting-queued"})
    assert queued.status_code == 202, queued.text
    current = write_definition("第二版规则。")
    assert asyncio.run(worker.run_once())
    assert domain.task(queued.json()["id"])["status"] == "blocked_runtime"
    assert len(model.requests) == 1
    assert (
        client.post(path(story), json={**first, "client_request_id": "workspace-setting-stale"}).status_code
        == 409
    )
    assert (
        client.post(
            path(story),
            json={
                **first,
                "base_artifact_id": current["source"]["artifact_id"],
                "client_request_id": "workspace-setting-fresh",
                "question": "第二个问题",
            },
        ).status_code
        == 202
    )
    assert asyncio.run(worker.run_once())
    latest = model.requests[-1]["上下文"]
    assert latest["当前保存内容"] == "第二版规则。" and latest["同版本此前对话"] == []
    turns = client.get(path(story), params={"setting_kind": "world"}).json()["turns"]
    assert [turn["status"] for turn in turns] == ["succeeded", "blocked_runtime", "succeeded"]

    draft = client.post(
        f"/api/branches/{story['branch_id']}/definitions",
        json={
            "kind": "intent",
            "text": "旧约定中的作者原话不能丢。",
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        },
    )
    assert draft.status_code == 201, draft.text
    intent = client.get(path(story), params={"setting_kind": "intent"}).json()
    assert (
        client.post(
            path(story),
            json={
                **first,
                "setting_kind": "intent",
                "base_artifact_id": intent["source"]["artifact_id"],
                "client_request_id": "workspace-setting-intent",
            },
        ).status_code
        == 202
    )
    assert asyncio.run(worker.run_once())
    assert "旧约定中的作者原话不能丢。" in model.requests[-1]["上下文"]["当前保存内容"]

    saved_reader = client.post(
        f"/api/branches/{story['branch_id']}/definitions",
        json={
            "kind": "reader",
            "text": "希望给读者带来临场感。",
            "author_exceptions": ["不用多线悬疑"],
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        },
    )
    assert saved_reader.status_code == 201, saved_reader.text
    reader = client.get(path(story), params={"setting_kind": "reader"}).json()
    assert (
        client.post(
            path(story),
            json={
                **first,
                "setting_kind": "reader",
                "base_artifact_id": reader["source"]["artifact_id"],
                "client_request_id": "workspace-setting-reader",
            },
        ).status_code
        == 202
    )
    assert asyncio.run(worker.run_once())
    assert json.loads(model.requests[-1]["上下文"]["当前保存内容"])["author_exceptions"] == ["不用多线悬疑"]


def test_chat_scope_and_cancel_in_progress(client, domain, story, settings):
    long_text = "甲" * (SOURCE_LIMIT // 2) + "中间不发送。" + "乙" * (SOURCE_LIMIT // 2)
    chapter = domain.save(story["chapter_id"], 0, long_text)
    different = domain.create_novel("另一作品")
    other = domain.create_chapter(different["branch_id"], "另一作品章节")
    assert client.get(path(story), params={"chapter_id": other["id"]}).status_code == 404
    assert (
        client.post(path(story), json=chapter_message(other, "不可串稿", "workspace-bad-scope")).status_code
        == 403
    )
    assert (
        client.post(
            path(story),
            json={**chapter_message(chapter, "问题", "workspace-mixed-scope"), "setting_kind": "world"},
        ).status_code
        == 422
    )
    queued = client.post(
        path(story), json=chapter_message(chapter, "查看可见范围。", "workspace-cancel-turn")
    )
    assert queued.status_code == 202, queued.text
    model = ChatModel(callback=lambda task_id: client.post(f"/api/tasks/{task_id}/cancel"))
    assert asyncio.run(Worker(settings, model).run_once())
    assert "中间不发送。" not in str(model.requests[0])
    assert model.requests[0]["上下文"]["当前版本"]["sent_chars"] == SOURCE_LIMIT
    turn = client.get(path(story), params={"chapter_id": chapter["id"]}).json()["turns"][0]
    assert turn["status"] == "cancelled" and turn["reply"] is None
    assert domain.chapter(chapter["id"]) == chapter


def test_chat_reserves_shared_budget_before_sending(client, domain, story, settings):
    chapter = domain.save(story["chapter_id"], 0, "当前章的保存稿。")
    queued = client.post(path(story), json=chapter_message(chapter, "可否修改？", "workspace-budget"))
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["id"]

    def unexpected_dispatch(request):
        raise AssertionError("预算不足时不应派发到模型。")

    settings.limits["deepseek"] = Decimal("0")
    model = ModelClient(settings, Budget(settings), transport=httpx.MockTransport(unexpected_dispatch))
    assert asyncio.run(Worker(settings, model).run_once())
    state = domain.task(task_id)
    assert state["status"] == "blocked_runtime" and "共享预算" in state["error"]
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_not_sent'", (task_id,)
    )
    assert [json.loads(event["payload"])["phase"] for event in events] == ["budget_reserve"]
