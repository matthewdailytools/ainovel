import asyncio
import json
from time import time

import pytest

from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.worker import PROMPT_VERSION, Worker


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
        self.plan_context = None
        self.review_instructions = None
        self.generation_instructions = None

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        self.calls += 1
        data = json.loads(prompt.split("\n只返回")[0])
        instruction = data["任务"]
        action = instruction.get("动作")
        text = "沈砚用旧尺测量潮水，留下可以回查的记录。" * 15
        if action == "全书滚动规划":
            self.plan_context = data["上下文"]
            output = {
                "chapters": [
                    {"number": n, "title": f"第{n}章", "plot": "量潮并记下证据", "fixed": []}
                    for n in range(1, instruction["章数"] + 1)
                ],
                "ending": "公开核验，主冲突闭合",
            }
        elif action == "审查正文":
            self.review_instructions = instruction.get("作者写作要求")
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
            if action == "按章纲写完整正文":
                self.generation_instructions = instruction
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
    dispatched = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
    )
    assert len(dispatched) == 2
    request = json.loads(dispatched[0]["payload"])
    assert request["template_version"] == PROMPT_VERSION
    assert len(request["request"]["prompt_sha256"]) == 64
    assert "prompt" not in request["request"] and "system" not in request["request"]
    assert "test-not-real" not in dispatched[0]["payload"]


def test_native_output_contract_changes_invalidate_completed_step_cache(settings, domain, story):
    from ainovel.models import Memory

    domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "account": "openai_1",
            "action": "memory.extract",
        },
    )
    fake = FakeModel()
    worker = Worker(settings, fake)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    instruction = {"动作": "从正文提取事实候选与章节摘要"}
    asyncio.run(worker.call(instruction, Memory))
    asyncio.run(worker.call(instruction, Memory))
    assert fake.calls == 1
    contract = Memory.model_json_schema()
    contract["properties"]["summary"]["maxLength"] = 350
    asyncio.run(worker.call(instruction, Memory, contract=contract))
    assert fake.calls == 2


def test_changed_model_profile_cannot_reuse_previous_model_output(settings, domain, story):
    from dataclasses import replace

    domain.queue(
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
    asyncio.run(worker.call({"动作": "相同写作输入"}))
    settings.providers["openai_1"] = replace(
        settings.providers["openai_1"], model="different-model", reasoning="low"
    )
    asyncio.run(worker.call({"动作": "相同写作输入"}))
    assert fake.calls == 2


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
    edit = client.patch(
        "/api/artifacts/" + artifact["id"], json={"text": "另一窗口编辑后的待审章", "candidate_revision": 0}
    )
    assert edit.status_code == 200
    assert (
        client.post(
            path, json={"regenerate_pending": True, "reason": "明确重做", "candidate_revision": 0}
        ).status_code
        == 409
    )
    response = client.post(
        path, json={"regenerate_pending": True, "reason": "拒绝人物矛盾并重新生成", "candidate_revision": 1}
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["generation_epoch"] and result["interventions"][-1]["reason"]
    assert domain.artifact(artifact["id"])["status"] == "rejected"


def test_redo_uses_rejected_review_without_treating_old_text_as_story(settings, domain, story):
    spec = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        brief="原创样本",
        plots=[{"number": 1, "title": "量潮", "plot": "测量并留下记录", "fixed": []}],
        chapters=1,
        min_chars=200,
        max_chars=600,
        account="openai_1",
        adoption="automatic",
    ).model_dump()
    task = domain.queue("workflow", spec, story["branch_id"])
    old = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {
            "text": "旧失败稿不得变成已发生事实",
            "review": {"summary": "旧稿动作重复，要求修正"},
            "review_passed": False,
        },
        {},
        task["id"],
    )
    result = {
        "plan": {"chapters": spec["plots"], "ending": "完成核对"},
        "next_chapter": 1,
        "repair_round": 0,
        "generation_epoch": "fresh-redo",
        "interventions": [{"artifact_id": old["id"], "reason": "明确重做"}],
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET status='rejected' WHERE id=?", (old["id"],))
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    fake = FakeModel()
    asyncio.run(Worker(settings, fake).run_once())
    assert (
        fake.generation_instructions["本章前次未采纳的审查意见"]["review"]["summary"]
        == "旧稿动作重复，要求修正"
    )
    assert "旧失败稿" not in domain.chapter(story["chapter_id"])["content"]
    assert domain.artifact(old["id"])["status"] == "rejected"
    assert domain.task(task["id"])["status"] == "production_complete"


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


def test_automatic_planning_reads_adopted_settings_without_other_prose(client, settings):
    story, chapter = create(client)
    client.put(
        f"/api/chapters/{chapter['id']}",
        json={"base_revision": 0, "content": "另一工作版本的正文，禁止复用。"},
    )
    revision = client.get(f"/api/branches/{story['branch_id']}/context").json()["branch"]["story_revision"]
    client.post(
        f"/api/branches/{story['branch_id']}/definitions",
        json={"kind": "world", "text": "所有行动依靠潮尺与人工闸门", "base_story_revision": revision},
    )
    task = client.post(
        "/api/workflows",
        json={
            "novel_id": story["id"],
            "source_branch_id": story["branch_id"],
            "mode": "automatic",
            "brief": "独立原创故事",
            "account": "openai_1",
            "chapters": 1,
            "min_chars": 200,
            "max_chars": 600,
            "adoption": "automatic",
        },
    ).json()
    fake = FakeModel()
    asyncio.run(Worker(settings, fake).run_once())
    assert fake.plan_context["definitions"][0]["payload"]["text"] == "所有行动依靠潮尺与人工闸门"
    assert fake.plan_context["recent"] == [] and fake.plan_context["chapter"] is None
    assert client.get("/api/tasks/" + task["id"]).json()["status"] == "production_complete"


def test_model_context_keeps_facts_and_versions_without_redundant_database_fields(settings, domain, story):
    domain.save(story["chapter_id"], 0, "沈砚只有一枚铜钱。")
    with domain.db.connect(True) as tx:
        domain.add_fact_tx(
            tx,
            story["branch_id"],
            story["chapter_id"],
            1,
            {"subject": "沈砚", "kind": "resource", "statement": "只有一枚铜钱", "evidence": "只有一枚铜钱"},
            "沈砚只有一枚铜钱。",
        )
    raw = domain.context(story["branch_id"])["facts"][0]
    compact = Worker(settings).context(story["branch_id"])["facts"][0]
    assert compact["statement"] == raw["statement"] and compact["id"] == raw["id"]
    assert compact["source_revision"] == 1 and compact["position"] == 1
    assert "evidence" not in compact and "branch_id" not in compact and "chapter_id" not in compact


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


def test_single_plot_expansion_checks_candidate_before_author_adoption(settings, domain, story):
    from ainovel.models import ModuleInput

    spec = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.expand_plot",
        instruction="测量并留下记录",
        fixed=["留下记录"],
        account="openai_1",
    ).model_dump()
    task = domain.queue("module", spec)
    fake = FakeModel()
    asyncio.run(Worker(settings, fake).run_once())
    result = domain.task(task["id"])
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert artifact["payload"]["review_passed"] and fake.calls == 2
    assert [c["condition"] for c in artifact["payload"]["review"]["checks"]] == ["留下记录"]
    assert artifact["status"] == "candidate" and domain.chapter(story["chapter_id"])["content"] == ""


def test_review_module_honors_requested_review_scope(settings, domain, story):
    from ainovel.models import ModuleInput

    request = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="review.chapter",
        instruction="只核对观察位置与证据边界",
        account="openai_1",
    ).model_dump()
    domain.queue("module", request)
    fake = FakeModel()
    asyncio.run(Worker(settings, fake).run_once())
    assert fake.review_instructions == request["instruction"]
    assert domain.chapter(story["chapter_id"])["content"] == ""


def test_candidate_cross_window_edits_and_acceptance_require_seen_version(client):
    story, chapter = create(client)
    domain = client.app.state.domain
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {"text": "初始候选", "review": {"summary": "旧版检查"}, "review_passed": True},
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    path = "/api/artifacts/" + artifact["id"]
    seen = client.get(path).json()
    edited = client.patch(
        path, json={"text": "另一窗口的新候选", "candidate_revision": seen["candidate_revision"]}
    )
    assert edited.status_code == 200 and edited.json()["candidate_revision"] == 1
    assert "review" not in edited.json()["payload"] and edited.json()["payload"]["previous_review"]
    assert client.patch(path, json={"text": "旧窗口不能覆盖", "candidate_revision": 0}).status_code == 409
    assert client.post(path + "/reject", json={"candidate_revision": 0}).status_code == 409
    assert (
        client.post(
            path + "/accept", json={"idempotency_key": "stale-window-accept", "candidate_revision": 0}
        ).status_code
        == 409
    )
    assert client.get("/api/chapters/" + chapter["id"]).json()["content"] == ""
    accepted = client.post(
        path + "/accept", json={"idempotency_key": "current-window-accept", "candidate_revision": 1}
    )
    assert accepted.status_code == 200
    assert client.get("/api/chapters/" + chapter["id"]).json()["content"] == "另一窗口的新候选"
    assert (
        client.post(
            path + "/accept", json={"idempotency_key": "current-window-accept", "candidate_revision": 0}
        ).json()
        == accepted.json()
    )


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


def test_accepted_chapter_and_next_checkpoint_survive_worker_loss_together(settings, domain, story):
    task = domain.queue("workflow", {"novel_id": story["id"], "account": "openai_1"}, story["branch_id"])
    worker = Worker(settings, FakeModel())
    claimed = worker.claim()
    progress = {
        "next_chapter": 1,
        "expected_story_revision": domain.branch(story["branch_id"])["story_revision"],
    }
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {"text": "已采用的原稿😀", "review_passed": True},
        {"story_revision": progress["expected_story_revision"]},
        task["id"],
        claimed["attempt"],
    )
    domain.accept(artifact["id"], "atomic-progress", True, claimed["attempt"], workflow_result=progress)
    # No outer worker checkpoint gets a chance to run before its lease expires.
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time() - 1, task["id"]))
    assert Worker(settings).claim() is None
    recovered = domain.task(task["id"])
    assert recovered["status"] == "blocked_runtime"
    assert recovered["result"]["next_chapter"] == 2
    assert (
        recovered["result"]["expected_story_revision"] == domain.branch(story["branch_id"])["story_revision"]
    )
    assert domain.chapter(story["chapter_id"])["content"] == "已采用的原稿😀"
    assert len(domain.db.all("SELECT * FROM receipts WHERE artifact_id=?", (artifact["id"],))) == 1


def test_author_edit_after_generation_stops_before_review_call(settings, domain, story):
    from ainovel.models import ModuleInput

    class EditingModel(FakeModel):
        async def generate(self, *args, **kwargs):
            output = await super().generate(*args, **kwargs)
            if self.calls == 1:
                domain.save(story["chapter_id"], 0, "作者在等待时继续手写😀")
            return output

    spec = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.expand_plot",
        instruction="量潮并记录",
        account="openai_1",
    ).model_dump()
    task = domain.queue("module", spec)
    fake = EditingModel()
    asyncio.run(Worker(settings, fake).run_once())
    assert fake.calls == 1
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert domain.chapter(story["chapter_id"])["content"] == "作者在等待时继续手写😀"


def test_planned_chapter_creation_cannot_absorb_an_unseen_author_edit(domain, story):
    expected = domain.branch(story["branch_id"])["story_revision"]
    domain.save(story["chapter_id"], 0, "作者刚刚修改前文")
    with pytest.raises(DomainError, match="故事已改变"):
        domain.create_chapter(story["branch_id"], "计划第二章", 2, expected)
    assert len(domain.db.all("SELECT id FROM chapters WHERE branch_id=?", (story["branch_id"],))) == 1


def test_pause_during_last_chapter_stops_before_completion_requests(settings, domain, story):
    class PausingModel(FakeModel):
        async def generate(self, *args, **kwargs):
            output = await super().generate(*args, **kwargs)
            if self.calls == 3:  # Writing, review, then fact extraction for the final chapter.
                with domain.db.connect(True) as tx:
                    tx.execute("UPDATE tasks SET pause=1 WHERE id=?", (task["id"],))
            return output

    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        brief="原创建议",
        chapters=1,
        plots=[{"number": 1, "title": "量潮", "plot": "测量并记录"}],
        min_chars=200,
        max_chars=600,
        account="openai_1",
        adoption="automatic",
    ).model_dump()
    task = domain.queue("workflow", payload, story["branch_id"])
    fake = PausingModel()
    asyncio.run(Worker(settings, fake).run_once())
    state = domain.task(task["id"])
    assert state["status"] == "paused" and state["result"]["next_chapter"] == 2
    assert fake.calls == 3 and domain.chapter(story["chapter_id"])["content"]
    assert "completion" not in state["result"]


def test_resume_manual_adoption_does_not_skip_newly_stale_chapters(client):
    story, first = create(client)
    domain = client.app.state.domain
    domain.save(first["id"], 0, "旧的第一章")
    second = domain.create_chapter(story["branch_id"], "第二章")
    task = domain.queue("workflow", {"novel_id": story["id"], "account": "openai_1"}, story["branch_id"])
    revision = domain.branch(story["branch_id"])["story_revision"]
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": second["id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {"text": "第二章的待审稿"},
        {"story_revision": revision},
        task["id"],
    )
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='needs_review',result=? WHERE id=?",
            (
                dumps(
                    {
                        "waiting_artifact": artifact["id"],
                        "next_chapter": 2,
                        "expected_story_revision": revision,
                    }
                ),
                task["id"],
            ),
        )
    domain.accept(artifact["id"], "manual-second")
    domain.save(first["id"], 1, "作者重新修改了第一章")
    path = f"/api/tasks/{task['id']}/resume"
    assert client.post(path, json={}).status_code == 409
    response = client.post(path, json={"acknowledge_changes": True})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["next_chapter"] == 2 and "waiting_artifact" not in result
    assert domain.chapter(second["id"])["stale"]
