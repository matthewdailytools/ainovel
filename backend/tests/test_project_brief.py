import asyncio
import json

from ainovel.db import uid
from ainovel.worker import Worker
from test_conception_workbench import confirm, path


IDEA = "学徒靠刻痕找回失散的师姐。"
BRIEF = {
    "position": "以信物查案推动成长",
    "track": "短篇成长悬疑",
    "genre_ids": ["xianxia", "mystery"],
    "audience": "偏好克制成长和证据回收的读者",
    "distinction": "铜铃的每条限制都产生实质取舍",
    "length": "一卷两章",
    "cadence": "",
    "ending": "主角决定把铃留在门外。",
    "final_image": "门外的铜铃不再响。",
}


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.calls.append(frame)
        if frame["任务"]["action"] == "conception.generate.project":
            assert frame["任务"]["作者创作思路"] == IDEA
            assert len(frame["任务"]["可选题材"]) == 36
            output = BRIEF
        else:
            output = {"items": [{"title": "门铃规则", "detail": "铜铃只在山门内响。", "links": []}]}
        return {
            "text": json.dumps(output, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": uid(),
        }


def run(domain, settings, task, model):
    assert asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    return domain.artifact(done["result"]["artifact_id"])


def test_project_brief_edits_confirmation_and_world_handoff(client, domain, settings, story):
    endpoint = path(story["branch_id"])
    project_path = f"{endpoint}/project-brief"
    assert client.get(project_path).json() is None
    current = client.get(endpoint).json()
    queued = client.post(
        project_path + "/generate",
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "account": "openai_1",
            "client_request_id": uid(),
            "idea": IDEA,
        },
    )
    assert queued.status_code == 202, queued.text
    model = Model()
    draft = run(domain, settings, queued.json(), model)
    assert draft["kind"] == "project_brief_draft"
    assert client.get(project_path).json() is None

    authored_ending = "师姐带学徒入门，两人共同修复旧铜铃。"
    first_input = {
        "base_revision": 0,
        "idempotency_key": uid(),
        "candidate_id": draft["id"],
        "idea": IDEA,
        "brief": {**BRIEF, "ending": authored_ending},
    }
    saved = client.put(project_path, json=first_input)
    assert saved.status_code == 200, saved.text
    first = saved.json()
    assert first["revision"] == 1
    assert first["candidate_id"] == draft["id"]
    assert first["brief"]["ending"] == authored_ending
    assert client.put(project_path, json=first_input).json() == first
    assert client.put(project_path, json={**first_input, "idempotency_key": uid()}).status_code == 409

    current = confirm(client, endpoint, client.get(endpoint).json())
    body = {
        "base_revision": current["revision"],
        "base_story_revision": current["story_revision"],
        "account": "openai_1",
        "client_request_id": uid(),
        "category_id": "world",
        "project_confirmation_id": first["id"],
    }
    world = client.post(endpoint + "/generate", json=body)
    assert world.status_code == 202, world.text
    proposed = run(domain, settings, world.json(), model)
    assert proposed["basis"]["project_confirmation_id"] == first["id"]
    assert proposed["basis"]["project_revision"] == 1
    context = model.calls[-1]["上下文"]
    assert context["已确认立项"]["brief"]["ending"] == authored_ending
    assert context["设定框架"]["primary"]["id"] == "xianxia"
    assert context["设定框架"]["secondary"][0]["id"] == "mystery"

    updated = client.put(
        project_path,
        json={
            **first_input,
            "base_revision": 1,
            "idempotency_key": uid(),
            "candidate_id": None,
            "brief": {**BRIEF, "ending": "师姐继承铜铃，学徒回到旧铺。"},
        },
    )
    assert updated.status_code == 200, updated.text
    second = updated.json()
    assert second["revision"] == 2
    current = client.get(endpoint).json()
    assert current["confirmation"]["current"] is False
    assert current["project"] == second
    assert domain.artifact(first["id"])["payload"]["brief"]["ending"] == authored_ending
    stale = client.post(
        endpoint + "/generate",
        json={**body, "base_story_revision": current["story_revision"], "client_request_id": uid()},
    )
    assert stale.status_code == 409
    stale_adoption = client.post(
        endpoint + "/adopt",
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "idempotency_key": uid(),
            "artifact_id": proposed["id"],
            "categories": proposed["payload"]["categories"],
        },
    )
    assert stale_adoption.status_code == 409
    fresh = client.post(
        endpoint + "/generate",
        json={
            **body,
            "base_story_revision": current["story_revision"],
            "project_confirmation_id": second["id"],
            "client_request_id": uid(),
        },
    )
    assert fresh.status_code == 202, fresh.text
    assert run(domain, settings, fresh.json(), model)["basis"]["project_confirmation_id"] == second["id"]
    assert model.calls[-1]["上下文"]["已确认立项"] == second
