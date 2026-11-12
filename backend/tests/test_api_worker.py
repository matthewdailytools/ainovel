import asyncio
import json
from time import time

import pytest

from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.worker import Worker


def create(client):
    story = client.post("/api/novels", json={"title": "API 原创作品"}).json()
    chapter = client.post(f"/api/branches/{story['branch_id']}/chapters", json={"title": "第一章"}).json()
    return story, chapter


def test_boundary_and_secret_redaction(client):
    assert client.get("/api/config", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/config", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.post("/api/novels", json={"title": "x"}, headers={"X-Ainovel-CSRF": "wrong"}).status_code
        == 403
    )
    public = client.get("/api/config").text
    assert "test-not-real" not in public and '"key"' not in public
    assert len(client.get("/api/config").json()["accounts"]) == 3


def test_save_import_history_feedback_and_state(client):
    story, chapter = create(client)
    path = "/api/chapters/" + chapter["id"]
    assert client.put(path, json={"base_revision": 0, "content": "沈砚😀在渡口。"}).status_code == 200
    assert client.put(path, json={"base_revision": 0, "content": "旧稿"}).status_code == 409
    assert (
        client.post(
            "/api/feedback",
            json={
                "chapter_id": chapter["id"],
                "category": "审美偏好",
                "text": "语气稍缓",
                "source_quote": "不存在",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/feedback",
            json={
                "chapter_id": chapter["id"],
                "category": "审美偏好",
                "text": "语气稍缓",
                "source_quote": "沈砚",
            },
        ).status_code
        == 201
    )
    reading = f"/api/branches/{story['branch_id']}/reading-state"
    payload = {"chapter_id": chapter["id"], "cursor": 2, "note": "继续渡口场景", "base_revision": 0}
    assert client.put(reading, json=payload).status_code == 200
    assert client.put(reading, json=payload).status_code == 409
    assert (
        client.post(
            f"/api/branches/{story['branch_id']}/import",
            files={"file": ("新章.md", "导入😀".encode(), "text/plain")},
        ).status_code
        == 201
    )
    assert "导入😀" in client.get(f"/api/branches/{story['branch_id']}/export").text


def test_queue_cancel_and_independent_workflows(client):
    story, chapter = create(client)
    module = {
        "novel_id": story["id"],
        "branch_id": story["branch_id"],
        "chapter_id": chapter["id"],
        "base_revision": 0,
        "action": "writing.advise",
        "instruction": "给方向",
        "account": "openai_1",
    }
    task = client.post("/api/modules", json=module).json()
    assert client.post(f"/api/tasks/{task['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 409
    assert (
        client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    auto = {
        "novel_id": story["id"],
        "mode": "automatic",
        "brief": "原创设定与完整结局方向",
        "account": "openai_2",
        "chapters": 3,
    }
    result = client.post("/api/workflows", json=auto)
    assert result.status_code == 202
    assert result.json()["branch_id"] != story["branch_id"]
    assert (
        client.post(
            "/api/workflows", json={**auto, "plots": [{"number": 1, "title": "一", "plot": "逐章输入"}]}
        ).status_code
        == 400
    )
    assert client.post("/api/workflows", json={**auto, "mode": "assisted"}).status_code == 400


class FakeModel:
    """Deterministic ENGINEERING fixture; it is never a literary acceptance result."""

    def __init__(self):
        self.calls = 0

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        self.calls += 1
        data = json.loads(prompt.split("\n只返回")[0])
        instruction = data["任务"]
        action = instruction.get("动作")
        text = "沈砚用旧尺测量潮水，留下可以回查的记录。" * 15
        if action == "全书滚动规划":
            output = {
                "chapters": [
                    {"number": n, "title": f"第{n}章", "plot": "量潮并记下证据", "fixed": []}
                    for n in range(1, instruction["章数"] + 1)
                ],
                "ending": "公开核验，主冲突闭合",
            }
        elif action == "审查正文":
            output = {
                "checks": [
                    {"condition": item, "result": "pass", "evidence": "沈砚用旧尺测量潮水"}
                    for item in instruction["固定条件"]
                ],
                "findings": [],
                "summary": "工程模拟审查",
            }
        elif action == "从正文提取事实候选与章节摘要":
            output = {
                "facts": [
                    {
                        "subject": "沈砚",
                        "kind": "event",
                        "statement": "完成测量",
                        "evidence": "沈砚用旧尺测量潮水",
                    }
                ],
                "summary": "工程模拟摘要",
            }
        elif action == "整书完结检查":
            output = {
                "closed": True,
                "promises": [
                    {"condition": "测量记录收束", "result": "pass", "evidence": "沈砚用旧尺测量潮水"}
                ],
                "findings": [],
                "summary": "工程模拟收尾，不能替代作者判断",
            }
        else:
            output = text
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": "fake",
            "model": "offline-fixture",
        }


def test_repeated_creation_returns_same_task_even_after_source_changes(client):
    story, chapter = create(client)
    payload = {
        "novel_id": story["id"],
        "branch_id": story["branch_id"],
        "chapter_id": chapter["id"],
        "base_revision": 0,
        "action": "writing.advise",
        "instruction": "卡点诊断",
        "account": "openai_1",
        "client_request_id": "request-once",
    }
    first = client.post("/api/modules", json=payload).json()
    client.put(f"/api/chapters/{chapter['id']}", json={"base_revision": 0, "content": "排队时继续写"})
    second = client.post("/api/modules", json=payload)
    assert second.status_code == 202 and second.json()["id"] == first["id"]
    assert client.post("/api/modules", json={**payload, "instruction": "不同输入"}).status_code == 409
    book = {
        "novel_id": story["id"],
        "mode": "automatic",
        "brief": "独立原创简报",
        "account": "openai_1",
        "client_request_id": "request-book-once",
    }
    first = client.post("/api/workflows", json=book).json()
    second = client.post("/api/workflows", json=book).json()
    assert first["id"] == second["id"] and first["branch_id"] == second["branch_id"]
    assert len(client.get(f"/api/novels/{story['id']}/branches").json()) == 2


def test_queued_workflows_yield_at_chapter_boundaries(settings, domain, story):
    other = domain.create_novel("另一独立作品")
    tasks = []
    for novel_id, branch_id in [(story["id"], story["branch_id"]), (other["id"], other["branch_id"])]:
        spec = WorkflowInput(
            novel_id=novel_id,
            mode="automatic",
            brief="工程调度样本",
            chapters=2,
            min_chars=200,
            max_chars=600,
            account="openai_1",
            adoption="automatic",
        ).model_dump()
        tasks.append(domain.queue("workflow", spec, branch_id))
    worker = Worker(settings, FakeModel())
    asyncio.run(worker.run_once())
    assert domain.task(tasks[0]["id"])["status"] == "queued"
    assert domain.task(tasks[0]["id"])["result"]["next_chapter"] == 2
    for _ in range(8):
        if not asyncio.run(worker.run_once()):
            break
    assert all(domain.task(task["id"])["status"] == "production_complete" for task in tasks)


def test_schema_export_does_not_create_user_data(tmp_path):
    from ainovel.api import create_app
    from ainovel.settings import Settings

    directory = tmp_path / "not-created"
    schema = create_app(Settings(directory, {}, {}), schema_only=True).openapi()
    assert "/api/modules" in schema["paths"]
    assert not directory.exists()


def test_explicit_redo_does_not_reuse_rejected_step(settings, domain, story):
    task = domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "account": "openai_1",
            "action": "writing.advise",
        },
    )
    fake = FakeModel()
    worker = Worker(settings, fake)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    instruction = {"动作": "保持相同输入"}
    first = asyncio.run(worker.call(instruction))
    assert asyncio.run(worker.call(instruction)) == first and fake.calls == 1
    worker.task["result"]["generation_epoch"] = "explicit-redo"
    asyncio.run(worker.call(instruction))
    assert fake.calls == 2
    assert domain.task(task["id"])["status"] == "running"


def test_rejected_candidate_redo_is_reasoned_and_gets_new_epoch(client):
    story, chapter = create(client)
    task = client.post(
        "/api/workflows",
        json={"novel_id": story["id"], "mode": "automatic", "brief": "原创简报", "account": "openai_1"},
    ).json()
    domain = client.app.state.domain
    target = domain.create_chapter(task["branch_id"], "待审章")
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": task["branch_id"],
            "chapter_id": target["id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {"text": "未通过的候选", "review_passed": False},
        {"story_revision": domain.branch(task["branch_id"])["story_revision"]},
        task["id"],
    )
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='needs_review',result=? WHERE id=?",
            (dumps({"waiting_artifact": artifact["id"]}), task["id"]),
        )
    path = f"/api/tasks/{task['id']}/resume"
    assert client.post(path, json={"regenerate_pending": True}).status_code == 409
    response = client.post(path, json={"regenerate_pending": True, "reason": "拒绝人物矛盾并重新生成"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["generation_epoch"] and result["interventions"][-1]["reason"]
    assert domain.artifact(artifact["id"])["status"] == "rejected"


@pytest.mark.parametrize(
    "mode,count", [("automatic", 3), ("assisted", 3), ("automatic", 36), ("assisted", 36)]
)
def test_durable_wholebook_engineering_only(settings, domain, story, mode, count):
    with domain.db.connect(True) as tx:
        tx.execute("DELETE FROM revisions WHERE chapter_id=?", (story["chapter_id"],))
        tx.execute("DELETE FROM chapters WHERE id=?", (story["chapter_id"],))
    payload = WorkflowInput(
        novel_id=story["id"],
        mode=mode,
        brief="工程样本，不作为小说业务验收",
        chapters=count,
        min_chars=200,
        max_chars=600,
        account="openai_1",
        adoption="automatic",
        plots=[{"number": n, "title": f"章{n}", "plot": "测量并记录"} for n in range(1, count + 1)]
        if mode == "assisted"
        else [],
    ).model_dump()
    task = domain.queue("workflow", payload, story["branch_id"])
    worker = Worker(settings, FakeModel())
    assert asyncio.run(worker.run_once())
    state = domain.task(task["id"])
    assert state["status"] == "production_complete", state["error"]
    assert (
        len(domain.db.all("SELECT * FROM chapters WHERE branch_id=? AND content!=''", (story["branch_id"],)))
        == count
    )
    complete = domain.completion(story["branch_id"])
    assert complete["production_complete"] and not complete["accepted"]
    assert all(f["source_revision"] == 1 for f in domain.db.all("SELECT * FROM facts"))


def test_worker_restart_and_late_attempt_cannot_write(settings, domain, story):
    task = domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "action": "writing.advise",
            "account": "openai_1",
        },
    )
    worker = Worker(settings, FakeModel())
    claimed = worker.claim()
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time() - 1, task["id"]))
    assert Worker(settings).claim() is None
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    with pytest.raises(DomainError):
        domain.db.emit(task["id"], "text", {"delta": "late"}, claimed["attempt"])


def test_archive_and_order_keep_text_and_invalidate_state(client):
    story, first = create(client)
    client.put(f"/api/chapters/{first['id']}", json={"base_revision": 0, "content": "不可丢失的原稿😀"})
    second = client.post(f"/api/branches/{story['branch_id']}/chapters", json={"title": "第二章"}).json()
    context = client.get(f"/api/branches/{story['branch_id']}/context").json()
    moved = client.post(
        f"/api/chapters/{second['id']}/move",
        json={"direction": "up", "base_story_revision": context["branch"]["story_revision"]},
    )
    assert moved.status_code == 200 and moved.json()[0]["id"] == second["id"]
    assert (
        client.post(
            f"/api/chapters/{first['id']}/archive", json={"base_revision": 0, "archived": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/chapters/{first['id']}/archive", json={"base_revision": 1, "archived": True}
        ).status_code
        == 200
    )
    assert client.get(f"/api/chapters/{first['id']}").status_code == 404
    assert (
        client.post(
            f"/api/chapters/{first['id']}/archive", json={"base_revision": 1, "archived": False}
        ).status_code
        == 200
    )
    assert client.get(f"/api/chapters/{first['id']}").json()["content"] == "不可丢失的原稿😀"


def test_definition_versions_and_workflow_inherit_only_selected_settings(client):
    story, chapter = create(client)
    for text in ["旧世界", "新世界"]:
        revision = client.get(f"/api/branches/{story['branch_id']}/context").json()["branch"][
            "story_revision"
        ]
        assert (
            client.post(
                f"/api/branches/{story['branch_id']}/definitions",
                json={"kind": "world", "text": text, "base_story_revision": revision},
            ).status_code
            == 201
        )
    context = client.get(f"/api/branches/{story['branch_id']}/context").json()
    assert [d["payload"]["text"] for d in context["definitions"]] == ["新世界"]
    result = client.post(
        "/api/workflows",
        json={
            "novel_id": story["id"],
            "source_branch_id": story["branch_id"],
            "mode": "automatic",
            "brief": "粗纲",
            "account": "openai_1",
        },
    ).json()
    inherited = client.get(f"/api/branches/{result['branch_id']}/context").json()
    assert [d["payload"]["text"] for d in inherited["definitions"]] == ["新世界"]
    assert client.get(f"/api/branches/{result['branch_id']}/chapters").json() == []


def test_changed_queued_chapter_stops_before_paid_request(settings, domain, story):
    task = domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 0,
            "action": "writing.advise",
            "account": "openai_1",
        },
    )
    domain.save(story["chapter_id"], 0, "排队时继续手写")
    fake = FakeModel()
    asyncio.run(Worker(settings, fake).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and fake.calls == 0


def test_manual_adoption_waits_and_resume_does_not_use_other_prose(settings, domain, story):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="automatic",
        brief="独立自动版",
        chapters=1,
        min_chars=200,
        max_chars=600,
        account="openai_1",
    ).model_dump()
    task = domain.queue("workflow", payload, story["branch_id"])
    fake = FakeModel()
    worker = Worker(settings, fake)
    asyncio.run(worker.run_once())
    state = domain.task(task["id"])
    assert state["status"] == "needs_review"
    assert not domain.chapter(story["chapter_id"])["content"]
    domain.accept(state["result"]["waiting_artifact"], "manual-acceptance")
    with domain.db.connect(True) as tx:
        result = state["result"]
        result["expected_story_revision"] = domain.branch(story["branch_id"])["story_revision"]
        tx.execute("UPDATE tasks SET status='queued',result=? WHERE id=?", (dumps(result), task["id"]))
    asyncio.run(worker.run_once())
    assert domain.task(task["id"])["status"] == "production_complete"
