import asyncio
import json

import pytest

from ainovel.db import dumps
from ainovel.models import ModuleInput, Plan, WorkflowInput
from ainovel.worker import Worker


TEXT = "林舟用旧尺测量潮水，把两次读数记在纸上。" * 15


def adopt_reader(domain, story, audience="女频", **changes):
    payload = {
        "text": "无CP，靠经营铺子取得主动；交易失败，不战斗。",
        "audience": audience,
        "genres": ["xianxia", "farm"],
        "mechanisms": ["plugin-20"],
        "author_exceptions": ["无CP，不增加恋爱或后宫", "交易失败，不战斗"],
        **changes,
    }
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "reader",
        payload,
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    domain.accept(artifact["id"], "reader-" + artifact["id"])
    return artifact


class CaptureModel:
    def __init__(self, repair=False):
        self.requests = []
        self.repair = repair
        self.failed_review = False

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        data = json.loads(prompt.split("\n只返回")[0])
        self.requests.append({**data, "system": system})
        instruction = data["任务"]
        action = instruction.get("动作", instruction.get("action"))
        if action == "全书滚动规划":
            output = {
                "chapters": [
                    {"number": i, "title": f"第{i}章", "plot": "交易失败后核对证据", "fixed": []}
                    for i in range(1, instruction["章数"] + 1)
                ],
                "ending": "交易仍未完成，当前调查收束。",
            }
        elif action == "审查正文":
            output = {
                "checks": [
                    {"condition": f, "result": "pass", "evidence": instruction["可引用原文"][0]["source_id"]}
                    for f in instruction["固定条件"]
                ],
                "findings": [],
                "summary": "仅用于配置传递工程测试。",
            }
            if self.repair and not self.failed_review:
                self.failed_review = True
                output["findings"] = [
                    {
                        "chapter": instruction["章节号"],
                        "severity": "blocker",
                        "quote": instruction["可引用原文"][0]["source_id"],
                        "reason": "测试定向修订路径。",
                        "repair": "明确尺具。",
                    }
                ]
        elif action == "用局部替换修订正文":
            output = {
                "edits": [
                    {
                        "original": instruction["可替换原文"][0]["source_id"],
                        "replacement": instruction["原稿"].replace("旧尺", "木尺", 1),
                    }
                ],
                "reason": "仅测试有限修订时配置保持。",
            }
        elif action == "从正文提取事实候选与章节摘要":
            output = {"facts": [], "summary": "工程测试摘要。"}
        elif action == "整书完结检查":
            output = {
                "closed": True,
                "promises": [
                    {
                        "condition": "本次主线收束",
                        "result": "pass",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                    }
                ],
                "findings": [],
                "summary": "工程替身，不是创作验收。",
            }
        else:
            output = TEXT
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": "offline",
            "model": "offline-fixture",
        }


def module_spec(story, chapter, action="writing.advise"):
    return ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=chapter["id"],
        base_revision=chapter["revision"],
        action=action,
        instruction="交易失败，不战斗；保留无CP。",
        fixed=["交易失败，不战斗"],
        account="openai_1",
        selection_start=0 if action == "writing.rewrite_selection" else None,
        selection_end=2 if action == "writing.rewrite_selection" else None,
        selection_text=chapter["content"][:2] if action == "writing.rewrite_selection" else None,
    ).model_dump()


def assert_profile(request, source_id=None):
    profile = request["上下文"]["genre_profile"]
    assert profile["audience"] == "女频"
    assert profile["genre_ids"] == ["xianxia", "farm"]
    assert profile["mechanism_ids"] == ["plugin-20"]
    assert "无CP，不增加恋爱或后宫" in profile["author_exceptions"]
    assert profile["author_notes"].startswith("无CP")
    if source_id:
        assert profile["source_artifact_id"] == source_id
    assert "女频不等于必须恋爱" in request["system"]
    return profile


def test_catalog_and_preview_are_read_only_and_keep_author_exceptions(client):
    catalog = client.get("/api/genre-catalog").json()
    assert len(catalog["genres"]) == 36
    story = client.post("/api/novels", json={"title": "题材配置界面样例"}).json()
    path = f"/api/branches/{story['branch_id']}/genre-profile"
    initial = client.get(path).json()
    assert initial["configured"] is False and initial["reader_definition"] is None
    payload = {
        "kind": "reader",
        "text": "无CP经营",
        "audience": "女频",
        "genres": ["xianxia", "farm"],
        "mechanisms": [],
        "author_exceptions": ["无CP"],
        "base_story_revision": initial["story_revision"],
    }
    preview = client.post("/api/genre-profile/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["preview"] is True
    assert preview.json()["author_exceptions"] == ["无CP"]
    assert client.get(path).json() == initial
    saved = client.post(f"/api/branches/{story['branch_id']}/definitions", json=payload)
    assert saved.status_code == 201
    adopted = client.get(path).json()
    assert adopted["source_artifact_id"] and adopted["reader_definition"]["genres"] == ["xianxia", "farm"]
    assert adopted["story_revision"] == initial["story_revision"] + 1
    assert client.post(f"/api/branches/{story['branch_id']}/definitions", json=payload).status_code == 409
    assert client.post("/api/genre-profile/preview", json={**payload, "kind": "world"}).status_code == 400


@pytest.mark.parametrize("had_reader", [False, True])
@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_submission_requires_the_reader_profile_seen_by_the_user(client, domain, story, had_reader, kind):
    if had_reader:
        adopt_reader(domain, story)
    seen = client.get(f"/api/branches/{story['branch_id']}/genre-profile").json()
    basis = {key: seen[key] for key in ("source_artifact_id", "catalog_version")}
    adopt_reader(domain, story, audience="男频", genres=["science"])
    if kind == "module":
        payload = {**module_spec(story, domain.chapter(story["chapter_id"])), "genre_profile_basis": basis}
        response = client.post("/api/modules", json=payload)
    else:
        payload = WorkflowInput(
            novel_id=story["id"],
            source_branch_id=story["branch_id"],
            mode="automatic",
            brief="独立简报",
            account="openai_1",
            genre_profile_basis=basis,
        ).model_dump()
        response = client.post("/api/workflows", json=payload)
    assert response.status_code == 409 and response.json()["code"] == "genre_profile_changed"
    assert domain.db.all("SELECT id FROM tasks") == []
    assert len(client.get(f"/api/novels/{story['id']}/branches").json()) == 1


def test_seen_profile_basis_survives_normal_manuscript_save(client, settings, domain, story):
    adopt_reader(domain, story)
    seen = client.get(f"/api/branches/{story['branch_id']}/genre-profile").json()
    response = client.put(f"/api/chapters/{story['chapter_id']}", json={"base_revision": 0, "content": TEXT})
    assert response.status_code == 200
    chapter = domain.chapter(story["chapter_id"])
    payload = {
        **module_spec(story, chapter),
        "genre_profile_basis": {key: seen[key] for key in ("source_artifact_id", "catalog_version")},
    }
    queued = client.post("/api/modules", json=payload)
    assert queued.status_code == 202
    snapshot = queued.json()["result"]["genre_profile_snapshot"]
    assert snapshot["source_story_revision"] > seen["story_revision"]
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(queued.json()["id"])["status"] == "succeeded"
    assert_profile(model.requests[0], seen["source_artifact_id"])


def test_changed_catalog_basis_stops_before_queuing(client, domain, story):
    seen = client.get(f"/api/branches/{story['branch_id']}/genre-profile").json()
    payload = {
        **module_spec(story, domain.chapter(story["chapter_id"])),
        "genre_profile_basis": {
            "source_artifact_id": None,
            "catalog_version": seen["catalog_version"] + "-old",
        },
    }
    result = client.post("/api/modules", json=payload)
    assert result.status_code == 409 and result.json()["code"] == "genre_profile_changed"
    assert not domain.db.all("SELECT id FROM tasks")


@pytest.mark.parametrize(
    "action",
    [
        value
        for value in ModuleInput.model_fields["action"].annotation.__args__
        if value not in {"intent.compile", "revision.targeted", "story.extract"}
        and not value.startswith("reference.")  # Dedicated source-aware tests cover these.
    ],
)
def test_all_auxiliary_actions_send_frozen_reader_profile(settings, domain, story, action):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    source = adopt_reader(domain, story)
    task = domain.queue("module", module_spec(story, chapter, action))
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    result = domain.task(task["id"])
    assert result["status"] == "succeeded", result["error"]
    for request in model.requests:
        assert_profile(request, source["id"])
        if request["任务"].get("动作") == "审查正文":
            assert request["任务"]["固定条件"] == ["交易失败，不战斗"]
    assert result["result"]["genre_profile_snapshot"] == task["result"]["genre_profile_snapshot"]
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert artifact["basis"]["genre_profile"]["source_artifact_id"] == source["id"]


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
def test_workflows_inherit_profile_through_plan_writing_review_repair_memory_and_completion(
    client, settings, domain, story, mode
):
    source = adopt_reader(domain, story)
    payload = WorkflowInput(
        novel_id=story["id"],
        source_branch_id=story["branch_id"],
        mode=mode,
        brief="无CP经营调查，交易失败。",
        fixed=["交易失败，不战斗"],
        chapters=1,
        min_chars=200,
        max_chars=600,
        plots=[{"number": 1, "title": "核对", "plot": "核对证据，交易失败。", "fixed": []}]
        if mode == "assisted"
        else [],
        account="openai_1",
        adoption="automatic",
        genre_profile_basis={
            key: domain.genre_profile(story["branch_id"])[key]
            for key in ("source_artifact_id", "catalog_version")
        },
    ).model_dump()
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 202
    task = response.json()
    frozen = task["result"]["genre_profile_snapshot"]
    assert frozen["source_artifact_id"] != source["id"]
    # A source-branch edit after inheritance must not alter the independent run.
    adopt_reader(domain, story, audience="男频", genres=["science"], author_exceptions=["不写后宫"])
    model = CaptureModel(repair=True)
    asyncio.run(Worker(settings, model).run_once())
    result = domain.task(task["id"])
    assert result["status"] == "production_complete", result["error"]
    assert result["result"]["genre_profile_snapshot"] == frozen
    actions = {r["任务"].get("动作") for r in model.requests}
    assert {
        "按章纲写完整正文",
        "审查正文",
        "用局部替换修订正文",
        "从正文提取事实候选与章节摘要",
        "整书完结检查",
    } <= actions
    assert ("全书滚动规划" in actions) == (mode == "automatic")
    for request in model.requests:
        assert_profile(request, frozen["source_artifact_id"])
        assert "科幻" not in dumps(request["上下文"]["genre_profile"])
    assert result["result"]["completion"]["genre_profile"]["catalog_version"] == frozen["catalog_version"]


def test_queued_module_stops_before_call_when_adopted_reader_changes(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_reader(domain, story)
    task = domain.queue("module", module_spec(story, chapter))
    adopt_reader(domain, story, audience="男频", genres=["science"])
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    assert not model.requests
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert domain.chapter(chapter["id"])["content"] == TEXT


def test_reader_changes_cannot_be_silently_mixed_by_acknowledged_workflow_resume(
    client, settings, domain, story
):
    adopt_reader(domain, story)
    task = client.post(
        "/api/workflows",
        json=WorkflowInput(
            novel_id=story["id"],
            source_branch_id=story["branch_id"],
            mode="automatic",
            brief="独立题材批次",
            account="openai_1",
            chapters=1,
            min_chars=200,
            max_chars=600,
        ).model_dump(),
    ).json()
    assert client.post(f"/api/tasks/{task['id']}/pause").json()["status"] == "paused"
    adopt_reader(domain, {**story, "branch_id": task["branch_id"]}, audience="男频", genres=["science"])
    response = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_changes": True})
    assert response.status_code == 409 and response.json()["code"] == "genre_profile_changed"
    current = domain.task(task["id"])
    assert current["status"] == "paused"
    assert current["result"]["genre_profile_snapshot"] == task["result"]["genre_profile_snapshot"]
    model = CaptureModel()
    assert asyncio.run(Worker(settings, model).run_once()) is False
    assert not model.requests


def test_same_genre_uses_distinct_channels_without_inventing_relationship_constraints(
    settings, domain, story
):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_reader(domain, story, audience="女频")
    first = domain.queue("module", module_spec(story, chapter))
    female = CaptureModel()
    asyncio.run(Worker(settings, female).run_once())
    adopt_reader(domain, story, audience="男频", author_exceptions=["不写后宫"])
    second = domain.queue("module", module_spec(story, chapter))
    male = CaptureModel()
    asyncio.run(Worker(settings, male).run_once())
    a, b = [m.requests[0]["上下文"]["genre_profile"] for m in (female, male)]
    assert a["audience"] == "女频" and b["audience"] == "男频"
    assert a["focus"] != b["focus"] and a["genre_ids"] == b["genre_ids"]
    assert "不写后宫" in b["author_exceptions"]
    assert first["payload"]["fixed"] == second["payload"]["fixed"] == ["交易失败，不战斗"]
    assert domain.task(first["id"])["result"]["genre_profile_snapshot"]["audience"] == "女频"


def test_catalog_updates_do_not_change_already_queued_snapshot(settings, domain, story, monkeypatch):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_reader(domain, story)
    task = domain.queue("module", module_spec(story, chapter))
    frozen = task["result"]["genre_profile_snapshot"]
    monkeypatch.setattr(
        "ainovel.domain.assemble_profile", lambda reader: {"catalog_version": "future", "configured": False}
    )
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    profile = assert_profile(model.requests[0])
    assert profile["catalog_version"] == frozen["catalog_version"]


def test_old_custom_reader_and_unconfigured_writing_remain_usable(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    first = domain.queue("module", module_spec(story, chapter))
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    assert model.requests[0]["上下文"]["genre_profile"]["configured"] is False
    assert domain.task(first["id"])["status"] == "succeeded"
    adopt_reader(
        domain,
        story,
        audience="男频",
        genres=["自定义潮汐工匠题材"],
        mechanisms=["自定义轮班机制"],
        author_exceptions=[],
    )
    task = domain.queue("module", module_spec(story, chapter))
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    profile = model.requests[0]["上下文"]["genre_profile"]
    assert profile["custom_genres"] == ["自定义潮汐工匠题材"]
    assert profile["custom_mechanisms"] == ["自定义轮班机制"]
    assert domain.task(task["id"])["status"] == "succeeded"


def test_legacy_task_does_not_gain_new_catalog_promises_on_resume(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_reader(domain, story)
    task = domain.queue("module", module_spec(story, chapter))
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result='{}' WHERE id=?", (task["id"],))
    model = CaptureModel()
    asyncio.run(Worker(settings, model).run_once())
    profile = model.requests[0]["上下文"]["genre_profile"]
    assert profile["legacy_unconfigured"] is True and profile["configured"] is False
    assert profile["catalog_version"] == "legacy-unconfigured"
    assert profile["genre_ids"] == []
    for field in ("required_topics", "optional_topics", "checks", "focus", "reading_promises"):
        assert profile[field] == []
    assert model.requests[0]["上下文"]["definitions"][0]["payload"]["audience"] == "女频"


def test_structure_repair_keeps_same_frozen_profile_and_action_scope(settings, domain, story):
    adopt_reader(domain, story)
    domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "action": "outline.plan",
            "account": "openai_1",
        },
    )

    class BrokenOnce(CaptureModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            data = json.loads(prompt.split("\n只返回")[0])
            self.requests.append({**data, "system": system})
            output = (
                {}
                if len(self.requests) == 1
                else {
                    "chapters": [{"number": 1, "title": "核对", "plot": "交易失败", "fixed": []}],
                    "ending": "本次调查收束",
                }
            )
            return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": "offline"}

    model = BrokenOnce()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = asyncio.run(
        worker.call({"动作": "全书滚动规划", "章数": 1}, Plan, worker.context(story["branch_id"]))
    )
    assert result["chapters"][0]["plot"] == "交易失败"
    assert len(model.requests) == 2
    assert_profile(model.requests[0])
    assert model.requests[0]["上下文"]["genre_profile"] == model.requests[1]["上下文"]["genre_profile"]


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_old_payload_nonce_treats_only_missing_and_null_genre_basis_as_equivalent(
    client, domain, story, monkeypatch, kind
):
    if kind == "module":
        body = module_spec(story, domain.chapter(story["chapter_id"]))
        endpoint = "/api/modules"
        changed_field = "instruction"
    else:
        body = WorkflowInput(
            novel_id=story["id"], mode="automatic", brief="旧请求兼容", account="openai_1"
        ).model_dump()
        endpoint = "/api/workflows"
        changed_field = "brief"
    body.pop("genre_profile_basis", None)
    body["client_request_id"] = "legacy-" + kind
    old = client.post(endpoint, json=body)
    assert old.status_code == 202
    task = old.json()
    assert "genre_profile_basis" not in task["payload"]
    with_null = {**task["payload"], "genre_profile_basis": None}
    assert domain.find_request(kind, with_null)["id"] == task["id"]
    assert domain.queue(kind, with_null, task["branch_id"])["id"] == task["id"]
    # Force the second, in-transaction lookup as if another submit won the race.
    with monkeypatch.context() as patch:
        patch.setattr(domain, "find_request", lambda *_: None)
        assert domain.queue(kind, with_null, task["branch_id"])["id"] == task["id"]
    assert client.post(endpoint, json={**body, "genre_profile_basis": None}).json()["id"] == task["id"]
    assert client.post(endpoint, json={**body, changed_field: "确实不同的输入"}).status_code == 409
    seen = domain.genre_profile(task["branch_id"])
    supplied = {key: seen[key] for key in ("source_artifact_id", "catalog_version")}
    assert client.post(endpoint, json={**body, "genre_profile_basis": supplied}).status_code == 409
    assert len(domain.db.all("SELECT id FROM tasks")) == 1
