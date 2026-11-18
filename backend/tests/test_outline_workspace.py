import asyncio
import json

from ainovel.worker import Worker
from test_conception_workbench import confirm, path, save


def setting_confirmation(client, branch):
    endpoint = path(branch)
    state = client.get(endpoint).json()
    world = next(category for category in state["categories"] if category["id"] == "world")
    world["items"] = [{"id": "bell", "title": "铃声规则", "detail": "铜铃只在门内响。"}]
    return confirm(client, endpoint, save(client, endpoint, state))["confirmation"]["id"]


def chapter_plan(chapter_id, goal):
    return {
        "chapter_id": chapter_id,
        "goal": goal,
        "obstacle": "无法确认来历",
        "turn": "发现门内刻痕",
        "result": "获得一条新线索",
        "setting_ids": ["bell"],
    }


def put_and_confirm(client, endpoint, data):
    saved = client.put(endpoint, json=data)
    assert saved.status_code == 200, saved.text
    revision = saved.json()["draft"]["revision"]
    confirmed = client.post(endpoint + "/confirm", json={"base_revision": revision})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def test_outline_versions_and_graph_ledger_track_saved_proof(client, domain, story):
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "铜铃在门外没有响。")
    second = domain.create_chapter(branch, "第二章")
    record_endpoint = f"/api/branches/{branch}/story-records"
    created = client.post(
        record_endpoint,
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "record": {"kind": "promise", "title": "铜铃何时响", "stages": [{"status": "planned"}]},
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()["records"][0]
    endpoint = f"/api/branches/{branch}/outline-workspace"
    setting_id = setting_confirmation(client, branch)
    data = {
        "base_revision": 0,
        "confirmation_id": setting_id,
        "plan": {
            "summary": "学徒查出失散师姐和铜铃的联系",
            "volumes": [
                {
                    "id": "volume",
                    "title": "遗铃",
                    "summary": "追到门前",
                    "units": [
                        {
                            "id": "unit",
                            "title": "求证",
                            "summary": "先观察，再入门确认",
                            "chapters": [
                                chapter_plan(first["id"], "识别铜铃"),
                                chapter_plan(second["id"], "解释门内铃声"),
                            ],
                        }
                    ],
                }
            ],
            "promises": [
                {
                    "promise_id": record["id"],
                    "plant_chapter_id": first["id"],
                    "opens_chapter_id": second["id"],
                    "closes_chapter_id": second["id"],
                }
            ],
        },
    }
    view = put_and_confirm(client, endpoint, data)
    assert view["settings"][0]["evidence"] is None
    assert view["promises"][0]["state"] == "planned"
    assert view["promises"][0]["due"] is None
    assert client.put(endpoint, json=data).status_code == 409
    old = view["confirmed"]
    second = domain.save(second["id"], 0, "学徒踏进门内，铜铃响了起来。")
    assert client.get(endpoint).json()["promises"][0]["due"] == "due"
    source = {
        "chapter_id": second["id"],
        "revision": second["revision"],
        "start": 0,
        "end": len(second["content"]),
        "quote": second["content"],
    }
    updated = client.put(
        record_endpoint + "/" + record["id"],
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "base_revision": record["revision"],
            "record": {
                **record["payload"],
                "stages": [{"status": "planned"}, {"status": "resolved", "sources": [source]}],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    current = client.get(endpoint).json()
    assert not current["stale"]
    assert current["promises"][0]["state"] == "resolved"
    assert current["promises"][0]["due"] is None
    assert current["promises"][0]["stages"][-1]["sources"] == [source]

    domain.save(second["id"], second["revision"], "学徒仍然在门外。")
    assert client.get(endpoint).json()["promises"][0]["state"] == "stale"
    data["base_revision"] = old["revision"]
    data["plan"]["summary"] = "先处理眼前的疑问"
    new = client.put(endpoint, json=data).json()
    assert new["draft"]["plan"]["summary"] != old["plan"]["summary"]
    assert new["confirmed"] == old
    assert client.get(f"{endpoint}/revisions/{old['revision']}").json() == old


def test_writing_task_freezes_outline_and_rejects_adoption_after_new_confirmation(
    client, domain, settings, story
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "铜铃一动不动。")
    setting_id = setting_confirmation(client, branch)
    endpoint = f"/api/branches/{branch}/outline-workspace"
    data = {
        "base_revision": 0,
        "confirmation_id": setting_id,
        "plan": {
            "summary": "遗铃",
            "volumes": [
                {
                    "id": "v",
                    "title": "遗铃",
                    "summary": "查找铃声",
                    "units": [
                        {
                            "id": "u",
                            "title": "追索",
                            "summary": "第一步",
                            "chapters": [chapter_plan(chapter["id"], "验证铃声")],
                        }
                    ],
                }
            ],
        },
    }
    initial = put_and_confirm(client, endpoint, data)["confirmed"]
    request = {
        "novel_id": story["id"],
        "branch_id": branch,
        "chapter_id": chapter["id"],
        "base_revision": chapter["revision"],
        "action": "writing.rewrite_selection",
        "instruction": "保留原有行动动机。",
        "selection_start": 0,
        "selection_end": len(chapter["content"]),
        "selection_text": chapter["content"],
        "account": "deepseek",
        "outline_revision_id": initial["id"],
    }
    response = client.post("/api/modules", json=request)
    assert response.status_code == 202, response.text
    task = response.json()
    data["base_revision"] = initial["revision"]
    data["plan"]["volumes"][0]["units"][0]["chapters"][0]["goal"] = "暂停试铃"

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            basis = frame["上下文"]["采用章纲"]
            assert basis["chapter"]["goal"] == "验证铃声"
            assert basis["settings"][0]["detail"] == "铜铃只在门内响。"
            put_and_confirm(client, endpoint, data)
            return {
                "text": "铜铃在风中安静地垂着。",
                "usage": {"input": 1, "output": 1},
                "call_id": "outline",
            }

    asyncio.run(Worker(settings, Model()).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    artifact = domain.artifact(done["result"]["artifact_id"])
    assert artifact["basis"]["outline"]["id"] == initial["id"]
    assert artifact["basis"]["outline"]["chapter"]["goal"] == "验证铃声"
    assert client.post("/api/modules", json=request).status_code == 409
    accepted = client.post(
        f"/api/artifacts/{artifact['id']}/accept",
        json={"idempotency_key": "outline-stale-candidate", "candidate_revision": 0},
    )
    assert accepted.status_code == 409, accepted.text
    assert accepted.json()["code"] == "outline_changed"
    assert domain.chapter(chapter["id"])["content"] == chapter["content"]
