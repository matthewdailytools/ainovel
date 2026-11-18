import asyncio
import json

from ainovel.db import DomainError
from ainovel.worker import Worker
from test_conception_workbench import confirm, path, save
from test_reference_flow import SOURCE, reference as reference_fixture, update

reference = reference_fixture


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


def test_new_promise_plans_confirm_atomically_without_evidence(client, domain, story, monkeypatch):
    branch = story["branch_id"]
    first = story["chapter_id"]
    second = domain.create_chapter(branch, "第二章")["id"]
    base = domain.branch(branch)["story_revision"]
    endpoint = f"/api/branches/{branch}/outline-workspace"
    graph_route = f"/api/branches/{branch}/story-graph"
    book_plan = {
        "summary": "找师姐和铃声",
        "volumes": [
            {
                "id": "v",
                "title": "遗铃",
                "summary": "追到门前",
                "units": [
                    {
                        "id": "u",
                        "title": "求证",
                        "summary": "先查刻痕再归铃",
                        "chapters": [
                            {**chapter_plan(chapter, "核对线索"), "setting_ids": []}
                            for chapter in (first, second)
                        ],
                    }
                ],
            }
        ],
    }
    pending = {
        "title": "A 门内的铃声",
        "notes": "到第二章再解释铃声从哪里传来。",
        "plant_chapter_id": first,
        "opens_chapter_id": second,
        "closes_chapter_id": second,
    }

    def save_new(new_promises, revision=0):
        return client.put(
            endpoint, json={"base_revision": revision, "plan": {**book_plan, "new_promises": new_promises}}
        )

    wrong_order = save_new([{**pending, "plant_chapter_id": second, "opens_chapter_id": first}])
    assert wrong_order.status_code == 400 and wrong_order.json()["code"] == "outline_window"
    duplicate = {**pending, "title": "Ａ 门内的铃声"}
    repeated = save_new([pending, duplicate])
    assert repeated.status_code == 400 and repeated.json()["code"] == "outline_promise_duplicate"
    other = {**pending, "title": "门环上的旧刻痕"}
    response = save_new([pending, other])
    assert response.status_code == 200, response.text
    draft = response.json()["draft"]
    assert draft["plan"]["new_promises"] == [pending, other]
    assert client.get(graph_route).json()["records"] == []
    assert domain.branch(branch)["story_revision"] == base

    def fail_after_promise_writes(*args):
        raise DomainError("checkpoint_failure", "模拟写入后检查点失败。", 409)

    confirm_request = {"base_revision": draft["revision"]}
    with monkeypatch.context() as scoped:
        scoped.setattr("ainovel.work_snapshot.checkpoint_tx", fail_after_promise_writes)
        failed = client.post(endpoint + "/confirm", json=confirm_request)
        assert failed.status_code == 409 and failed.json()["code"] == "checkpoint_failure"
    assert domain.branch(branch)["story_revision"] == base
    assert client.get(graph_route).json()["records"] == []
    assert client.get(endpoint).json()["draft"] == draft
    saved = client.post(endpoint + "/confirm", json=confirm_request)
    assert saved.status_code == 200, saved.text
    official = saved.json()
    assert "new_promises" not in official["confirmed"]["plan"]
    ledger = official["promises"]
    assert [row["title"] for row in ledger] == [pending["title"], other["title"]]
    assert all(row["state"] == "planned" and row["stages"][0]["sources"] == [] for row in ledger)
    records = client.get(graph_route).json()["records"]
    assert {row["id"] for row in records} == {row["promise_id"] for row in ledger}
    assert all(row["payload"]["sources"] == [] for row in records)
    retry = client.post(endpoint + "/confirm", json=confirm_request)
    assert retry.status_code == 200 and retry.json()["confirmed"] == official["confirmed"]
    assert domain.branch(branch)["story_revision"] == base + 2
    already_recorded = save_new([duplicate], official["confirmed"]["revision"])
    assert (
        already_recorded.status_code == 400 and already_recorded.json()["code"] == "outline_promise_duplicate"
    )


def test_writing_task_freezes_outline_and_rejects_adoption_after_new_confirmation(
    client, domain, settings, story, reference
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "铜铃一动不动。")
    setting_id = setting_confirmation(client, branch)
    endpoint = f"/api/branches/{branch}/outline-workspace"
    card = {
        "id": "partial-gain",
        "observation": "交涉只兑现部分信息。",
        "interpretation": "角色仍然能继续行动。",
        "technique": "用部分证据明确下一步。",
        "conditions": "仍有其他证据可查。",
        "exceptions": "替换原场景、道具和动作。",
        "evidence": SOURCE,
    }
    incomplete = update(client, domain, reference, cards=[{**card, "exceptions": ""}])
    assert client.get(endpoint + "/reference-options").json() == []
    ready = update(client, domain, incomplete, cards=[card])
    saved_ref = {"session_id": ready["id"], "card_id": card["id"], "revision": ready["revision"]}
    assert client.get(endpoint + "/reference-options").json()[0]["technique"] == card["technique"]
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
                            "chapters": [
                                {**chapter_plan(chapter["id"], "验证铃声"), "reference_cards": [saved_ref]}
                            ],
                        }
                    ],
                }
            ],
        },
    }
    initial = put_and_confirm(client, endpoint, data)["confirmed"]
    update(client, domain, ready, cards=[{**card, "technique": "改成选择导致新的阻碍。"}])
    historical = client.get(
        f"/api/reference-sessions/{ready['id']}/cards/{card['id']}/revisions/{ready['revision']}"
    ).json()
    assert historical["card"]["evidence"] == SOURCE
    assert (
        client.get(endpoint).json()["confirmed"]["setting_basis"]["reference_cards"][0]["technique"]
        == card["technique"]
    )
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
            assert basis["reference_cards"][0]["technique"] == card["technique"]
            assert basis["reference_cards"][0]["revision"] == ready["revision"]
            assert SOURCE not in prompt
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
