import asyncio
import json
from uuid import uuid4

from ainovel.db import dumps
from ainovel.project_brief import ConfirmProject, confirm
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, setting_confirmation


class Model:
    def __init__(self, intervene=None):
        self.requests = []
        self.intervene = intervene

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        assert instruction["动作"] == "生成四级大纲候选"
        self.requests.append(instruction)
        chapters = instruction["章节目录"]
        promise = instruction["可用伏笔"][0]
        if self.intervene:
            self.intervene()
        plan = {
            "summary": instruction["已确认立项"]["brief"]["ending"],
            "volumes": [
                {
                    "id": "v1",
                    "title": "遗铃",
                    "summary": "沿着铃声验证规则",
                    "units": [
                        {
                            "id": "u1",
                            "title": "求证",
                            "summary": "先试探，再确认",
                            "chapters": [chapter_plan(c["id"], c["title"]) for c in chapters],
                        }
                    ],
                }
            ],
            "promises": [
                {
                    "promise_id": promise["id"],
                    "plant_chapter_id": chapters[0]["id"],
                    "opens_chapter_id": chapters[-1]["id"],
                    "closes_chapter_id": chapters[-1]["id"],
                }
            ],
        }
        return {"text": dumps(plan), "usage": {"input": 1, "output": 1}, "call_id": "offline-outline"}


def prepare(client, domain, story):
    branch = story["branch_id"]
    second = domain.create_chapter(branch, "门内响铃")
    project = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=0,
            idempotency_key=uuid4().hex,
            idea="找回师姐的铃。",
            brief={
                "position": "短篇规则悬疑",
                "track": "诡异求证",
                "genre_ids": ["xianxia", "mystery"],
                "audience": "喜欢伏笔回收的读者",
                "distinction": "响铃用于查证而非制敌",
                "length": "两章",
                "ending": "找到师姐并归还铜铃",
            },
        ),
    )
    setting_id = setting_confirmation(client, branch)
    record = client.post(
        f"/api/branches/{branch}/story-records",
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "record": {"kind": "promise", "title": "何处能响", "stages": [{"status": "planned"}]},
        },
    )
    assert record.status_code == 201, record.text
    prompt = {
        "account": "deepseek",
        "base_revision": 0,
        "confirmation_id": setting_id,
        "instruction": "保留改过的结局，并安排铃声的伏笔。",
        "client_request_id": uuid4().hex,
    }
    return f"/api/branches/{branch}/outline-workspace", prompt, project, second


def test_outline_ai_candidate_is_reviewable_and_author_save_is_separate(client, domain, settings, story):
    endpoint, prompt, project, second = prepare(client, domain, story)
    queued = client.post(endpoint + "/generation", json=prompt)
    assert queued.status_code == 202, queued.text
    assert client.post(endpoint + "/generation", json=prompt).json()["id"] == queued.json()["id"]
    provider = Model()
    assert asyncio.run(Worker(settings, provider).run_once())
    completed = client.get(endpoint + "/generation").json()[0]
    assert completed["status"] == "succeeded", completed
    assert len(provider.requests) == 1
    sent = provider.requests[0]
    assert sent["已确认立项"]["id"] == project["id"]
    assert sent["已确认世界设定"][0]["detail"] == "铜铃只在门内响。"
    assert [item["id"] for item in sent["章节目录"]] == [story["chapter_id"], second["id"]]
    assert client.get(endpoint).json()["draft"] is None
    artifact = client.get(endpoint + "/generation/" + completed["artifact_id"]).json()
    assert artifact["current"] is True and artifact["project_revision"] == project["revision"]
    assert artifact["plan"]["summary"] == "找到师姐并归还铜铃"
    saved = client.put(
        endpoint,
        json={
            "base_revision": 0,
            "confirmation_id": prompt["confirmation_id"],
            "source_generation_id": artifact["id"],
            "plan": {**artifact["plan"], "summary": "作者确认归还铜铃，再进入写作"},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["confirmed"] is None
    result = client.post(endpoint + "/confirm", json={"base_revision": saved.json()["draft"]["revision"]})
    assert result.status_code == 200, result.text
    assert result.json()["confirmed"]["plan"]["summary"] == "作者确认归还铜铃，再进入写作"
    assert result.json()["promises"][0]["state"] == "planned"


def test_live_directory_change_preserves_stale_candidate_but_blocks_use(client, domain, settings, story):
    endpoint, prompt, _, _ = prepare(client, domain, story)
    assert client.post(endpoint + "/generation", json=prompt).status_code == 202
    provider = Model(lambda: domain.create_chapter(story["branch_id"], "作者新加第三章"))
    assert asyncio.run(Worker(settings, provider).run_once())
    task = client.get(endpoint + "/generation").json()[0]
    assert task["status"] == "succeeded", task
    artifact = client.get(endpoint + "/generation/" + task["artifact_id"]).json()
    assert artifact["current"] is False
    attempted = client.put(
        endpoint,
        json={
            "base_revision": 0,
            "confirmation_id": prompt["confirmation_id"],
            "source_generation_id": artifact["id"],
            "plan": artifact["plan"],
        },
    )
    assert attempted.status_code == 409, attempted.text
    assert client.get(endpoint).json()["draft"] is None
