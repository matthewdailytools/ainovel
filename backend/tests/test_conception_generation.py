import asyncio
import hashlib
import json

import pytest

from ainovel import conception_generation
from ainovel.db import DomainError, uid
from ainovel.domain import utf16_slice
from ainovel.providers import RequestNotSent
from ainovel.worker import Worker


class ProposalProvider:
    def __init__(self, intervene=None):
        self.requests = []
        self.intervene = intervene

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.requests.append(frame)
        if self.intervene:
            self.intervene(frame["任务"], account, task_id)
        if frame["任务"]["action"] == "conception.review.bundle":
            return {"text": '{"issues":[]}', "usage": {"input": 1, "output": 1}, "call_id": uid()}
        category = frame["任务"]["category_title"]
        refs = frame["上下文"]["可关联构思与来源"]
        return {
            "text": json.dumps(
                {
                    "items": [
                        {
                            "title": f"{category}：旧门铃",
                            "detail": "修铃匠只检查铃身刻痕，先用店铺单据判断它的主人。",
                            "links": [refs[-1]["ref"]] if refs else [],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": uid(),
        }


def workspace(client, story):
    path = f"/api/branches/{story['branch_id']}/conception"
    response = client.get(path)
    assert response.status_code == 200, response.text
    return path, response.json()


def select_categories(client, story):
    path, state = workspace(client, story)
    for category in state["categories"]:
        category["skipped"] = category["id"] not in {"core", "characters"}
    state["categories"].append({"id": "objects", "title": "信物", "skipped": False, "items": []})
    state["categories"][0]["items"] = [
        {"id": "author-seed", "title": "铃响有因", "detail": "主角只靠验证线索，不用战斗。", "links": []}
    ]
    next(category for category in state["categories"] if category["id"] == "outline")["items"] = [
        {"id": "paused-item", "title": "暂缓的大纲", "detail": "已跳过类别保密标记。", "links": []}
    ]
    response = client.put(
        path,
        json={
            "base_revision": state["revision"],
            "base_story_revision": state["story_revision"],
            "categories": state["categories"],
        },
    )
    assert response.status_code == 200, response.text
    return path, response.json()


def queue(client, path, state, category_id=None, *, key=None, instruction="根据店铺单据追查旧门铃。"):
    data = {
        "base_revision": state["revision"],
        "base_story_revision": state["story_revision"],
        "category_id": category_id,
        "instruction": instruction,
        "account": "openai_1",
        "client_request_id": key or uid(),
        "budget_limit": 0.5,
    }
    response = client.post(path + "/generate", json=data)
    assert response.status_code == 202, response.text
    return response.json(), data


@pytest.mark.parametrize("category_id", [None, "objects"])
def test_worker_generates_available_sections_and_valid_links(client, settings, domain, story, category_id):
    path, state = select_categories(client, story)
    queued, data = queue(client, path, state, category_id)
    assert client.post(path + "/generate", json=data).json()["id"] == queued["id"]
    model = ProposalProvider()
    assert asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    expected = ["core", "characters", "objects"] if category_id is None else ["objects"]
    assert task["status"] == "succeeded"
    assert task["result"]["completed_categories"] == expected
    assert task["result"]["total_categories"] == len(expected)
    assert [
        frame["任务"]["category_id"] for frame in model.requests if "category_id" in frame["任务"]
    ] == expected
    assert all("已跳过类别保密标记" not in json.dumps(frame, ensure_ascii=False) for frame in model.requests)
    artifact = domain.artifact(task["result"]["artifact_id"])
    assert artifact["id"] == task["result"]["partial_artifact_id"]
    assert artifact["kind"] == "conception_bundle"
    assert artifact["status"] == "candidate"
    assert artifact["payload"]["complete"] is True
    assert ("issues" in artifact["payload"]) is (category_id is None)
    assert ("model_review" in artifact["payload"]) is (category_id is None)
    assert artifact["basis"]["conception_revision"] == state["revision"]
    assert artifact["basis"]["story_revision"] == state["story_revision"]
    prior = "author-seed"
    for category in artifact["payload"]["categories"]:
        item = category["items"][0]
        assert item["links"] == [prior]
        prior = item["id"]
    assert client.get(path).json() == state
    assert domain.chapter(story["chapter_id"])["content"] == ""


def test_full_bundle_reports_cyclic_dependencies_and_missing_cross_category_links(
    client, settings, domain, story
):
    path, state = workspace(client, story)
    active = {"relationships", "events", "timeline", "foreshadowing"}
    for category in state["categories"]:
        category["skipped"] = category["id"] not in active
    saved = client.put(
        path,
        json={
            "base_revision": state["revision"],
            "base_story_revision": state["story_revision"],
            "categories": state["categories"],
        },
    )
    assert saved.status_code == 200, saved.text
    task, _ = queue(client, path, saved.json())

    class CycleProvider(ProposalProvider):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            target = self.requests[-1]["任务"].get("category_id")
            if not target:
                return result
            if target == "events":
                items = [
                    {"title": "提交店铺单据", "detail": "主角出示三份旧单据交叉核验。", "links": ["N2"]},
                    {"title": "取回旧铃", "detail": "主角向街坊交回封存的旧铃。", "links": ["N1"]},
                ]
                return {**result, "text": json.dumps({"items": items}, ensure_ascii=False)}
            item = json.loads(result["text"])["items"][0]
            return {**result, "text": json.dumps({"items": [{**item, "links": []}]}, ensure_ascii=False)}

    assert asyncio.run(Worker(settings, CycleProvider()).run_once())
    result = domain.task(task["id"])
    assert result["status"] == "succeeded"
    payload = domain.artifact(result["result"]["artifact_id"])["payload"]
    for category_id in active:
        assert any(issue["category_id"] == category_id for issue in payload["issues"])
    assert any("循环" in issue["message"] for issue in payload["issues"])


def test_known_not_sent_resumes_from_saved_section(client, settings, domain, story):
    path, state = select_categories(client, story)
    queued, _ = queue(client, path, state)

    def fail_second(instruction, account, task_id):
        if instruction["category_id"] == "characters":
            raise RequestNotSent(DomainError("budget", "预算临时不足。", 409), task_id, account, "budget")

    assert asyncio.run(Worker(settings, ProposalProvider(fail_second)).run_once())
    blocked = domain.task(queued["id"])
    assert blocked["status"] == "blocked_runtime"
    partial = domain.artifact(blocked["result"]["partial_artifact_id"])
    assert partial["payload"]["complete"] is False
    assert blocked["result"]["completed_categories"] == ["core"]
    request = client.post(f"/api/tasks/{queued['id']}/resume", json={"acknowledge_unknown": True})
    assert request.status_code == 200, request.text
    restart = ProposalProvider()
    assert asyncio.run(Worker(settings, restart).run_once())
    assert [frame["任务"].get("category_id") for frame in restart.requests] == ["characters", "objects", None]
    done = domain.task(queued["id"])
    assert done["status"] == "succeeded"
    complete = domain.artifact(done["result"]["artifact_id"])
    assert complete["payload"]["categories"][0] == partial["payload"]["categories"][0]
    assert domain.artifact(partial["id"]) == partial


def test_unknown_step_retains_partial_and_does_not_replay_paid_call(client, settings, domain, story):
    path, state = select_categories(client, story)
    queued, _ = queue(client, path, state)

    def fail_second(instruction, account, task_id):
        if instruction["category_id"] == "characters":
            raise DomainError("timeout", "响应中断，账本结算待确认。", 409)

    assert asyncio.run(Worker(settings, ProposalProvider(fail_second)).run_once())
    blocked = domain.task(queued["id"])
    assert blocked["status"] == "blocked_runtime"
    assert blocked["result"]["completed_categories"] == ["core"]
    request = client.post(f"/api/tasks/{queued['id']}/resume", json={"acknowledge_unknown": True})
    assert request.status_code == 200, request.text
    restart = ProposalProvider()
    assert asyncio.run(Worker(settings, restart).run_once())
    assert restart.requests == []
    after = domain.task(queued["id"])
    assert after["status"] == "blocked_runtime"
    assert after["result"]["partial_artifact_id"] == blocked["result"]["partial_artifact_id"]
    event = domain.db.all(
        "SELECT json_extract(payload, '$.code') AS code FROM task_events "
        "WHERE task_id=? AND kind='stopped' ORDER BY id DESC LIMIT 1",
        (queued["id"],),
    )[0]
    assert event["code"] == "unknown_result"


def test_bundle_review_uses_original_requirement_and_frozen_constraints_without_editing(
    client, settings, domain, story
):
    from test_author_contract import adopt_intent

    intent = adopt_intent(domain, story)
    path, state = select_categories(client, story)
    author_words = "  主角必须找到缺页，不能只确认缺页去向。\n"
    queued, _ = queue(client, path, state, instruction=author_words)

    class ConflictProvider(ProposalProvider):
        async def generate(self, *args, **kwargs):
            response = await super().generate(*args, **kwargs)
            frame = self.requests[-1]
            task = frame["任务"]
            if task.get("category_id") == "core":
                proposal = json.loads(response["text"])
                proposal["items"][0]["title"] = "结局"
                proposal["items"][0]["detail"] = "缺页并未找回，主角只确认其去向。"
                return {**response, "text": json.dumps(proposal, ensure_ascii=False)}
            if task["action"] == "conception.review.bundle":
                assert task["作者本次要求"] == author_words
                context = frame["上下文"]
                assert context["author_contract"]["source_artifact_id"] == intent["id"]
                assert context["author_contract"]["intent_contract"]["prohibitions"] == ["不战斗"]
                assert context["genre_profile"]["source_story_revision"] == state["story_revision"]
                assert any(
                    row["title"] == "结局" and row["detail"] == "缺页并未找回，主角只确认其去向。"
                    for row in context["候选分类"]
                )
                issue = {
                    "category_id": "core",
                    "message": "作者明确要求「必须找到缺页」，草案结局只确认去向，未安排找到缺页。",
                }
                return {**response, "text": json.dumps({"issues": [issue]}, ensure_ascii=False)}
            return response

    model = ConflictProvider()
    assert asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    assert task["status"] == "succeeded"
    assert task["result"]["pending_consistency_review"] is False
    artifact = domain.artifact(task["result"]["artifact_id"])
    (issue,) = artifact["payload"]["model_review"]["issues"]
    assert issue["category_id"] == "core"
    assert "未安排找到缺页" in issue["message"]
    assert artifact["payload"]["categories"][0]["items"][0]["detail"] == "缺页并未找回，主角只确认其去向。"
    assert client.get(path).json() == state
    assert domain.chapter(story["chapter_id"])["content"] == ""


@pytest.mark.parametrize("known_unsent", [False, True])
def test_review_failure_preserves_all_sections_and_resume_does_not_repeat_them(
    client, settings, domain, story, known_unsent
):
    path, state = select_categories(client, story)
    queued, _ = queue(client, path, state)

    def fail_review(instruction, account, task_id):
        if instruction["action"] == "conception.review.bundle":
            if known_unsent:
                raise RequestNotSent(DomainError("budget", "预算暂不足。", 409), task_id, account, "budget")
            raise DomainError("timeout", "复核响应中断。", 409)

    assert asyncio.run(Worker(settings, ProposalProvider(fail_review)).run_once())
    blocked = domain.task(queued["id"])
    assert blocked["status"] == "blocked_runtime"
    assert blocked["result"]["pending_consistency_review"] is True
    partial = domain.artifact(blocked["result"]["partial_artifact_id"])
    assert partial["payload"]["complete"] is False
    assert "issues" in partial["payload"]
    assert len(partial["payload"]["categories"]) == 3

    response = client.post(f"/api/tasks/{queued['id']}/resume", json={"acknowledge_unknown": True})
    assert response.status_code == 200, response.text
    restart = ProposalProvider()
    assert asyncio.run(Worker(settings, restart).run_once())
    after = domain.task(queued["id"])
    if known_unsent:
        assert [frame["任务"]["action"] for frame in restart.requests] == ["conception.review.bundle"]
        assert after["status"] == "succeeded"
        reviewed = domain.artifact(after["result"]["artifact_id"])
        assert reviewed["payload"]["categories"] == partial["payload"]["categories"]
    else:
        assert restart.requests == []
        assert after["status"] == "blocked_runtime"
        assert after["result"]["partial_artifact_id"] == partial["id"]
    assert domain.artifact(partial["id"]) == partial


def test_legacy_bundle_keeps_original_completion_and_request_receipt(
    client, settings, domain, story, monkeypatch
):
    path, state = select_categories(client, story)
    with monkeypatch.context() as patch:
        patch.setattr(conception_generation, "VERSION", conception_generation.LEGACY_VERSION)
        queued, request = queue(client, path, state)
    same = client.post(path + "/generate", json=request)
    assert same.status_code == 202, same.text
    assert same.json()["id"] == queued["id"]
    model = ProposalProvider()
    assert asyncio.run(Worker(settings, model).run_once())
    assert len(model.requests) == 3
    assert all(
        frame["任务"]["构思生成契约版本"] == conception_generation.LEGACY_VERSION for frame in model.requests
    )
    task = domain.task(queued["id"])
    assert task["status"] == "succeeded"
    artifact = domain.artifact(task["result"]["artifact_id"])
    assert artifact["payload"]["version"] == conception_generation.LEGACY_VERSION
    assert "model_review" not in artifact["payload"]


def test_review_rejects_unrelated_category_ids_and_retains_complete_draft(client, settings, domain, story):
    path, state = select_categories(client, story)
    queued, _ = queue(client, path, state)

    class OutscopeReviewProvider(ProposalProvider):
        async def generate(self, *args, **kwargs):
            response = await super().generate(*args, **kwargs)
            frame = self.requests[-1]
            if frame["任务"]["action"] == "conception.review.bundle":
                issue = {"category_id": "outline", "message": "大纲类别本次没有生成。"}
                return {**response, "text": json.dumps({"issues": [issue]}, ensure_ascii=False)}
            return response

    model = OutscopeReviewProvider()
    assert asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    assert task["status"] == "blocked_runtime"
    assert task["result"]["pending_consistency_review"] is True
    reviews = [
        frame["任务"] for frame in model.requests if frame["任务"]["action"] == "conception.review.bundle"
    ]
    assert len(reviews) == 2
    assert "上一轮报告" in reviews[1]
    artifact = domain.artifact(task["result"]["partial_artifact_id"])
    assert "model_review" not in artifact["payload"]
    assert len(artifact["payload"]["categories"]) == 3


def test_complete_provider_response_reused_if_publish_was_interrupted(
    client, settings, domain, story, monkeypatch
):
    path, state = select_categories(client, story)
    queued, _ = queue(client, path, state, "core")
    model = ProposalProvider()

    def interrupt(*args, **kwargs):
        raise DomainError("interrupted", "响应已完成，保存候选前进程意外停止。", 409)

    with monkeypatch.context() as patch:
        patch.setattr(conception_generation, "publish", interrupt)
        assert asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(queued["id"])
    assert stopped["status"] == "blocked_runtime"
    assert "partial_artifact_id" not in stopped["result"]
    assert len(model.requests) == 1
    request = client.post(f"/api/tasks/{queued['id']}/resume", json={"acknowledge_unknown": True})
    assert request.status_code == 200, request.text
    restart = ProposalProvider()
    assert asyncio.run(Worker(settings, restart).run_once())
    assert restart.requests == []
    assert domain.task(queued["id"])["status"] == "succeeded"


def test_proposal_cannot_publish_a_nonexistent_reference(client, settings, domain, story):
    path, state = select_categories(client, story)
    queued, _ = queue(client, path, state, "characters")

    class HallucinatingProvider(ProposalProvider):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            proposal = json.loads(result["text"])
            proposal["items"][0]["links"] = ["S999"]
            return {**result, "text": json.dumps(proposal)}

    model = HallucinatingProvider()
    assert asyncio.run(Worker(settings, model).run_once())
    assert len(model.requests) == 2
    assert model.requests[1]["任务"]["无效关联位置"] == ["items[0].links[0]"]
    task = domain.task(queued["id"])
    assert task["status"] == "blocked_runtime"
    assert "partial_artifact_id" not in task["result"]
    assert domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (queued["id"],)) == []


@pytest.mark.parametrize("when", ["queued", "running", "cancel"])
def test_body_and_workbench_survive_stale_or_cancelled_generation(client, settings, domain, story, when):
    path, state = select_categories(client, story)
    queued, data = queue(client, path, state, "core")

    def save_draft():
        categories = state["categories"]
        categories[0]["items"][0]["detail"] = "作者在另一窗口更新线索。"
        update = client.put(
            path,
            json={
                "base_revision": state["revision"],
                "base_story_revision": state["story_revision"],
                "categories": categories,
            },
        )
        assert update.status_code == 200, update.text

    def intervene(instruction, account, task_id):
        if when == "running":
            save_draft()
        if when == "cancel":
            response = client.post(f"/api/tasks/{task_id}/cancel")
            assert response.status_code == 200, response.text

    if when == "queued":
        save_draft()
    model = ProposalProvider(intervene)
    assert asyncio.run(Worker(settings, model).run_once())
    assert len(model.requests) == (0 if when == "queued" else 1)
    response = domain.task(queued["id"])
    assert response["status"] == ("cancelled" if when == "cancel" else "blocked_runtime")
    assert "partial_artifact_id" not in response["result"]
    assert domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (queued["id"],)) == []
    # A receipt retry after the draft has changed still identifies the original task.
    assert client.post(path + "/generate", json=data).json()["id"] == queued["id"]
    if when != "cancel":
        stale = client.post(path + "/generate", json={**data, "client_request_id": uid()})
        assert stale.status_code == 409
    assert domain.chapter(story["chapter_id"])["content"] == ""


class WorldProvider:
    def __init__(self, quote):
        self.quote = quote
        self.requests = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.requests.append(frame)
        unit = frame["任务"]["可引用原文"][0]
        return {
            "text": json.dumps(
                {
                    "items": (
                        [
                            {
                                "title": "换灯规则",
                                "detail": "据原文，灯阁以缴纳盐券作为换灯条件。",
                                "source_id": unit["source_id"],
                                "quote": self.quote,
                            }
                        ]
                        if self.quote
                        else []
                    )
                },
                ensure_ascii=False,
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": uid(),
        }


def queue_world(client, path, state, chapter_ids, expected=202):
    response = client.post(
        path + "/extract-world",
        json={
            "base_revision": state["revision"],
            "base_story_revision": state["story_revision"],
            "chapter_ids": chapter_ids,
            "account": "openai_1",
            "client_request_id": uid(),
        },
    )
    assert response.status_code == expected, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["evidence", "empty", "invented"])
def test_old_world_extract_uses_only_selected_saved_text_and_publishes_verified_quote(
    client, settings, domain, story, mode
):
    quote = "灯阁换灯须缴盐券。"
    manuscript = "🔔她叩门。\n" + quote
    saved = domain.save(story["chapter_id"], 0, manuscript)
    outside = domain.create_chapter(story["branch_id"], "第二章")
    domain.save(outside["id"], 0, "这句只在未选正文中。")
    path, state = select_categories(client, story)
    task = queue_world(client, path, state, [story["chapter_id"]])
    model = WorldProvider({"evidence": quote, "empty": "", "invented": "灯阁换灯免付盐券。"}[mode])
    assert asyncio.run(Worker(settings, model).run_once())
    assert len(model.requests) == (2 if mode == "invented" else 1)
    first_request = model.requests[0]
    assert first_request["任务"]["action"] == "conception.extract.world"
    assert first_request["任务"]["可引用原文"][0]["text"] == manuscript
    assert "未选正文" not in json.dumps(first_request, ensure_ascii=False)
    assert "author_contract" not in first_request["上下文"]
    assert "genre_profile" not in first_request["上下文"]
    assert "作者种子" not in json.dumps(first_request, ensure_ascii=False)
    result = domain.task(task["id"])
    if mode == "invented":
        assert result["status"] == "blocked_runtime"
        assert "artifact_id" not in result["result"]
        assert "定位错误" in model.requests[1]["任务"]
    else:
        assert result["status"] == "succeeded"
        artifact = domain.artifact(result["result"]["artifact_id"])
        assert artifact["basis"]["chapter_sources"] == [
            {
                "chapter_id": story["chapter_id"],
                "revision": saved["revision"],
                "content_sha256": hashlib.sha256(manuscript.encode()).hexdigest(),
            }
        ]
        category = artifact["payload"]["categories"][0]
        assert category["id"] == "world"
        if mode == "empty":
            assert category["items"] == []
            assert artifact["payload"]["source_evidence"] == {}
        else:
            (item,) = category["items"]
            evidence = artifact["payload"]["source_evidence"][item["id"]]
            assert evidence["chapter_id"] == story["chapter_id"]
            assert evidence["revision"] == saved["revision"]
            assert utf16_slice(manuscript, evidence["start"], evidence["end"])[1] == quote
            assert evidence["quote"] == quote
    assert client.get(path).json() == state
    assert domain.chapter(story["chapter_id"])["content"] == manuscript
    assert domain.db.all("SELECT id FROM facts WHERE branch_id=?", (story["branch_id"],)) == []


def test_old_world_extract_rejects_other_works_and_unavailable_text(client, domain, story):
    path, state = workspace(client, story)
    foreign = domain.create_novel("其他测试作品")
    outside = domain.create_chapter(foreign["branch_id"], "第一章")
    domain.save(outside["id"], 0, "只在另一本书出现的设定。")
    assert queue_world(client, path, state, [outside["id"]], 403)["code"] == "ownership"
    assert queue_world(client, path, state, [story["chapter_id"]], 400)["code"] == "conception_chapters"


def test_generation_labels_current_reverse_items_and_omits_stale_source_data(client, settings, domain, story):
    chapter_id = story["chapter_id"]
    quote = "灯阁换灯须缴盐券。"
    saved = domain.save(chapter_id, 0, quote)
    path, state = workspace(client, story)
    source_task = queue_world(client, path, state, [chapter_id])
    assert asyncio.run(Worker(settings, WorldProvider(quote)).run_once())
    candidate_id = domain.task(source_task["id"])["result"]["artifact_id"]
    candidate = domain.artifact(candidate_id)["payload"]["categories"]
    adopted = client.post(
        path + "/adopt",
        json={
            "base_revision": state["revision"],
            "base_story_revision": state["story_revision"],
            "categories": candidate,
            "artifact_id": candidate_id,
            "idempotency_key": uid(),
        },
    )
    assert adopted.status_code == 200, adopted.text
    queue(client, path, adopted.json(), "core")
    model = ProposalProvider()
    assert asyncio.run(Worker(settings, model).run_once())
    (source,) = model.requests[0]["上下文"]["可关联构思与来源"]
    assert source["title"] == "换灯规则"
    assert source["origin"] == "从当前保存稿提取，作者已采用"
    assert quote not in json.dumps(model.requests[0], ensure_ascii=False)

    domain.save(chapter_id, saved["revision"], "原先那条规则已从正文移除。")
    path, state = workspace(client, story)
    queue(client, path, state, "core")
    assert asyncio.run(Worker(settings, model).run_once())
    assert model.requests[1]["上下文"]["可关联构思与来源"] == []
    assert "换灯" not in json.dumps(model.requests[1], ensure_ascii=False)
