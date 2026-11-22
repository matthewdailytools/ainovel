"""Version-scoped saved-draft checks and source ownership."""

import asyncio
import json

from ainovel.worker import Worker


class CheckModel:
    def __init__(self, before_reply=None, invented=False):
        self.calls = []
        self.before_reply = before_reply
        self.invented = invented

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        data = json.loads(prompt.split("\n只返回")[0])
        instruction = data["任务"]
        self.calls.append(instruction["action"])
        assert instruction["action"] == "review.chapter.item"
        assert not data["上下文"]
        if self.before_reply:
            self.before_reply()
        evidence = [
            {"source": scope, "quote": "凭空添加" if self.invented else source["text"].splitlines()[-1]}
            for scope, source in instruction["sources"].items()
            if scope in {"chapter", "previous"} and source
        ]
        verdict = {"result": "pass", "reason": "按选定两章核对。", "evidence": evidence}
        return {
            "text": json.dumps(verdict, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": f"{task_id}-{len(self.calls)}",
        }


def run(settings, domain, task_id, model=None):
    model = model or CheckModel()
    assert asyncio.run(Worker(settings, model).run_once())
    return domain.task(task_id)


def attempt(revision, key, **params):
    return {"base_revision": revision, "account": "openai_1", "client_request_id": key, **params}


def test_scoped_dependencies_and_review_history(client, settings, domain, story):
    first = domain.save(story["chapter_id"], 0, "施术折损了十年寿元。")
    second = domain.create_chapter(story["branch_id"], "第二章")
    second = domain.save(second["id"], 0, "😀宁禾记下昨夜所失。\n她在天亮后开匣。")
    endpoint = f"/api/chapters/{second['id']}/checks"
    two_chapters = attempt(
        1,
        "dependency-check",
        condition="核对跨章代价。",
        include_previous=True,
        previous_base_revision=first["revision"],
        previous_chapter_id=first["id"],
    )
    mismatch = client.post(endpoint, json={**two_chapters, "previous_chapter_id": second["id"]})
    assert mismatch.status_code == 409
    created = client.post(endpoint, json=two_chapters)
    assert created.status_code == 202, created.text
    task_id = created.json()["id"]
    assert client.post(endpoint, json=two_chapters).json()["id"] == task_id
    result = run(settings, domain, task_id)
    assert result["status"] == "succeeded", result["error"]

    local = client.post(endpoint, json=attempt(1, "unrelated-check", condition="本章开匣时点。"))
    local_id = local.json()["id"]
    assert run(settings, domain, local_id)["status"] == "succeeded"

    def items():
        return {item["id"]: item for item in client.get(endpoint).json()["items"]}

    original = items()[task_id]["latest"]
    assert original["evidence"][0]["start"] == len("😀宁禾记下昨夜所失。\n".encode("utf-16-le")) // 2
    rename = client.patch(f"/api/chapters/{first['id']}", json={"title": "新标题"})
    assert rename.status_code == 200, rename.text
    assert items()[task_id]["status"] == "current"
    domain.save(first["id"], 1, "施术折损了十二年寿元。")
    assert items()[task_id]["changed_sources"] == ["previous"]
    assert items()[local_id]["status"] == "current"
    recheck_path = f"{endpoint}/{task_id}/recheck"
    recheck_input = attempt(
        1,
        "explicit-recheck",
        previous_result_id=original["id"],
        previous_base_revision=2,
        previous_chapter_id=first["id"],
    )
    first_recheck = client.post(recheck_path, json=recheck_input)
    assert first_recheck.status_code == 202, first_recheck.text
    assert (
        client.post(recheck_path, json={**recheck_input, "client_request_id": "parallel-recheck"}).status_code
        == 409
    )
    assert run(settings, domain, first_recheck.json()["id"])["status"] == "succeeded"
    refreshed = items()[task_id]
    assert refreshed["status"] == "current"
    assert refreshed["latest"]["sources"]["previous"]["revision"] == 2
    assert refreshed["history"] == [original]
    assert items()[local_id]["history"] == []
    assert domain.chapter(second["id"])["content"] == second["content"]
    old_review = f"/api/artifacts/{original['id']}"
    assert client.post(f"{old_review}/reject").status_code == 409
    assert (
        client.post(recheck_path, json={**recheck_input, "client_request_id": "wrong-result"}).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/chapters/{first['id']}/checks/{task_id}/recheck",
            json=attempt(2, "foreign-check", previous_result_id=refreshed["latest"]["id"]),
        ).status_code
        == 404
    )


def test_changed_during_model_call_preserves_original_check_and_reports(client, settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "宁禾折损了十年寿元。")
    endpoint = f"/api/chapters/{chapter['id']}/checks"
    created = client.post(endpoint, json=attempt(1, "initial-check", condition="本章寿元变化明确。"))
    check_id = created.json()["id"]
    original = run(settings, domain, check_id)
    assert original["status"] == "succeeded"
    result_id = original["result"]["artifact_id"]
    recheck = client.post(
        f"{endpoint}/{check_id}/recheck", json=attempt(1, "racing-check", previous_result_id=result_id)
    )
    model = CheckModel(before_reply=lambda: domain.save(chapter["id"], 1, "宁禾折损了十二年寿元。"))
    task = run(settings, domain, recheck.json()["id"], model)
    assert task["status"] == "blocked_runtime", task["error"]
    assert model.calls == ["review.chapter.item"]
    [item] = client.get(endpoint).json()["items"]
    assert item["latest"]["id"] == result_id
    assert item["changed_sources"] == ["chapter"]
    assert item["history"] == []


def test_recheck_never_substitutes_new_neighbor_or_accepts_unverified_evidence(
    client, settings, domain, story
):
    first = domain.save(story["chapter_id"], 0, "昨夜宁禾关窗。")
    second = domain.create_chapter(story["branch_id"], "第二章")
    second = domain.save(second["id"], 0, "清晨竹窗仍是关的。")
    endpoint = f"/api/chapters/{second['id']}/checks"
    original = client.post(
        endpoint, json=attempt(1, "checked-neighbor", condition="核对竹窗状态。", include_previous=True)
    ).json()
    report_id = run(settings, domain, original["id"])["result"]["artifact_id"]
    extra = domain.create_chapter(story["branch_id"], "插入章")
    domain.save(extra["id"], 0, "另一条剧情。")
    moved = client.post(
        f"/api/chapters/{extra['id']}/move",
        json={"base_story_revision": domain.branch(story["branch_id"])["story_revision"], "direction": "up"},
    )
    assert moved.status_code == 200, moved.text
    [item] = client.get(endpoint).json()["items"]
    assert item["changed_sources"] == ["previous"]
    assert item["previous_chapter_id"] == first["id"]
    url = f"{endpoint}/{original['id']}/recheck"
    rejected = client.post(
        url, json=attempt(1, "wrong-neighbor", previous_result_id=report_id, previous_chapter_id=extra["id"])
    )
    assert rejected.status_code == 409
    recheck = client.post(url, json=attempt(1, "missing-neighbor", previous_result_id=report_id))
    model = CheckModel()
    assert run(settings, domain, recheck.json()["id"], model)["status"] == "succeeded"
    assert model.calls == []
    [item] = client.get(endpoint).json()["items"]
    assert item["latest"]["result"] == "unknown"
    assert item["latest"]["sources"]["previous"] is None
    assert item["history"][0]["id"] == report_id

    invented = client.post(endpoint, json=attempt(1, "invented-quote", condition="核对当前章。"))
    task = run(settings, domain, invented.json()["id"], CheckModel(invented=True))
    assert task["status"] == "blocked_runtime", task["error"]
    assert "无法回到原文" in task["error"]
    assert [value["id"] for value in client.get(endpoint).json()["items"]] == [original["id"]]


def test_attached_setting_keeps_frozen_evidence_when_source_is_invalidated(client, settings, domain, story):
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "每次祭灯扣减十年寿元。")
    second = domain.create_chapter(branch, "第二章")
    second = domain.save(second["id"], 0, "宁禾祭灯后折损了十年寿元。")
    adopted = client.post(
        "/api/facts",
        json={
            "chapter_id": first["id"],
            "source_revision": 1,
            "subject": "祭灯代价",
            "kind": "world",
            "statement": first["content"],
            "evidence": first["content"],
        },
    )
    assert adopted.status_code == 201, adopted.text
    context = client.get(f"/api/branches/{branch}/chapters/{second['id']}/writing-context").json()
    [setting] = context["settings"]
    endpoint = f"/api/chapters/{second['id']}/checks"
    body = attempt(1, "rule-choice", condition="本章遵守祭灯代价。", setting_id=setting["id"])
    stale_choice = client.post(endpoint, json={**body, "expected_setting": {**setting, "text": "过期设定"}})
    assert stale_choice.status_code == 409
    created = client.post(endpoint, json={**body, "expected_setting": setting})
    assert created.status_code == 202, created.text
    check_id = created.json()["id"]
    previous = run(settings, domain, check_id)["result"]["artifact_id"]
    domain.save(first["id"], 1, "每次祭灯扣减五年寿元。")
    [item] = client.get(endpoint).json()["items"]
    assert item["changed_sources"] == ["setting"]
    recheck = client.post(
        f"{endpoint}/{check_id}/recheck", json=attempt(1, "lost-setting", previous_result_id=previous)
    )
    model = CheckModel()
    assert run(settings, domain, recheck.json()["id"], model)["status"] == "succeeded"
    assert model.calls == []
    [updated] = client.get(endpoint).json()["items"]
    assert updated["latest"]["result"] == "unknown"
    assert not updated["latest"]["sources"]["setting"]["source_current"]
    assert updated["history"] == [item["latest"]]
    assert updated["history"][0]["sources"]["setting"]["source"]["quote"] == first["content"]
