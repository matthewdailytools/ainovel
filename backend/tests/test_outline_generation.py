import asyncio
import json
from uuid import uuid4

from ainovel.db import dumps
from ainovel.project_brief import ConfirmProject, confirm
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, setting_confirmation


RESOURCE = {"id": "lamps", "title": "油灯库存", "detail": "库房只余两盏旧灯。", "adoption_state": "adopted"}


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
        settings = [item["id"] for item in instruction["其他可引用设定"]]
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
                            "chapters": [
                                {**chapter_plan(c["id"], c["title"]), "setting_ids": ["bell", *settings]}
                                for c in chapters
                            ],
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
    setting_id = setting_confirmation(
        client, branch, [{"id": "resources", "title": "资源台账", "items": [RESOURCE]}]
    )
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
    assert sent["其他可引用设定"] == [
        {
            **{key: RESOURCE[key] for key in ("id", "title", "detail")},
            "category_id": "resources",
            "category_title": "资源台账",
        }
    ]
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
    chosen = result.json()["confirmed"]
    assert chosen["plan"]["volumes"][0]["units"][0]["chapters"][0]["setting_ids"] == ["bell", "lamps"]
    assert chosen["setting_basis"]["confirmation_id"] == prompt["confirmation_id"]
    assert chosen["setting_basis"]["items"][1] == sent["其他可引用设定"][0]
    assert [item["id"] for item in client.get(endpoint + "/setting-options?scope=world").json()["items"]] == [
        "bell"
    ]
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


def test_generated_outline_cannot_declare_or_change_author_required_promise(client, domain, settings, story):
    endpoint, prompt, _, _ = prepare(client, domain, story)
    assert client.post(endpoint + "/generation", json=prompt).status_code == 202

    class Candidate(Model):
        async def generate(self, *args, **kwargs):
            output = await super().generate(*args, **kwargs)
            if len(self.requests) == 1:
                plan = json.loads(output["text"])
                plan["promises"][0]["fulfillment"] = "author_required"
                output["text"] = dumps(plan)
            return {**output, "call_id": f"outline-required-{len(self.requests)}"}

    provider = Candidate()
    assert asyncio.run(Worker(settings, provider).run_once())
    finished = client.get(endpoint + "/generation").json()[0]
    assert finished["status"] == "succeeded", finished
    assert len(provider.requests) == 2
    assert "必达承诺须由作者声明" in provider.requests[1]["修正要求"]
    artifact = client.get(endpoint + "/generation/" + finished["artifact_id"]).json()
    plan = artifact["plan"]
    assert "fulfillment" not in plan["promises"][0]
    plan["promises"][0].update(
        fulfillment="author_required",
        opens_chapter_id=story["chapter_id"],
        closes_chapter_id=story["chapter_id"],
    )
    saved = client.put(
        endpoint,
        json={"base_revision": 0, "confirmation_id": prompt["confirmation_id"], "plan": plan},
    )
    assert saved.status_code == 200, saved.text
    confirmed = client.post(
        endpoint + "/confirm",
        json={"base_revision": saved.json()["draft"]["revision"], "acknowledge_required_promises": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    prompt.update(base_revision=confirmed.json()["confirmed"]["revision"], client_request_id=uuid4().hex)
    queued = client.post(endpoint + "/generation", json=prompt)
    assert queued.status_code == 202, queued.text
    assert asyncio.run(Worker(settings, Model()).run_once())
    generated = next(
        row for row in client.get(endpoint + "/generation").json() if row["id"] == queued.json()["id"]
    )
    assert generated["status"] == "succeeded", generated
    artifact = client.get(endpoint + "/generation/" + generated["artifact_id"]).json()
    assert artifact["plan"]["promises"] == plan["promises"]
