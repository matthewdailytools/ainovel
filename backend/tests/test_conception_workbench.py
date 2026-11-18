import asyncio
import json


from ainovel.backups import create_backup, unpack_verified
from ainovel.conception import snapshot
from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.worker import Worker


def path(branch):
    return f"/api/branches/{branch}/conception"


def basis(value):
    return {"base_revision": value["revision"], "base_story_revision": value["story_revision"]}


def item(identifier, title, links=()):
    return {"id": identifier, "title": title, "detail": f"{title}有明确的作者设想。", "links": list(links)}


def save(client, endpoint, current):
    response = client.put(endpoint, json={**basis(current), "categories": current["categories"]})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, endpoint, current, key="confirm-conception"):
    response = client.post(
        f"{endpoint}/confirm",
        json={**basis(current), "idempotency_key": key, "note": "先写开篇，回收节点稍后再补。"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def add_core(client, endpoint, title="盐船换票"):
    state = client.get(endpoint).json()
    state["categories"][0]["items"] = [item("core-1", title)]
    return save(client, endpoint, state)


def test_per_novel_categories_conflicts_and_independent_drafts(client, domain, story):
    endpoint = path(story["branch_id"])
    other = domain.create_novel("不同作品")
    old = client.get(endpoint).json()
    fresh = client.get(endpoint).json()
    custom = {"id": "guilds", "title": "行会", "items": [item("guild-1", "夜渡行会")], "skipped": False}
    fresh["categories"].append(custom)
    saved = save(client, endpoint, fresh)
    assert saved["revision"] == 1
    assert saved["categories"][-1] == custom
    assert client.get(path(other["branch_id"])).json()["revision"] == 0
    assert client.put(endpoint, json={**basis(old), "categories": old["categories"]}).status_code == 409
    bad = saved["categories"][:-1]
    bad[0]["items"] = [item("protagonist", "阿芹", ("guild-1",))]
    assert client.put(endpoint, json={**basis(saved), "categories": bad}).status_code == 400
    assert client.get(endpoint).json()["categories"][-1] == custom


def test_author_can_confirm_with_issues_and_restore_snapshot(client, domain, story, settings):
    endpoint = path(story["branch_id"])
    edited = add_core(client, endpoint)
    event = next(row for row in edited["categories"] if row["id"] == "events")
    event["items"] = [item("e1", "找到票根", ("e2",)), item("e2", "夜船过闸", ("e1",))]
    edited = save(client, endpoint, edited)
    assert any("循环" in issue["message"] for issue in edited["issues"])
    first = confirm(client, endpoint, edited)
    assert confirm(client, endpoint, edited) == first
    chosen = first["confirmation"]
    receipt = domain.artifact(chosen["id"])["payload"]
    assert receipt["issues"] == edited["issues"]
    assert receipt["note"] == chosen["note"]
    assert chosen["current"]
    backup = create_backup(settings, domain.db)
    first["categories"][0]["items"][0]["detail"] = "后来调整的新写法"
    changed = save(client, endpoint, first)
    assert not changed["confirmation"]["current"]
    target = settings.data_dir / "isolated-restore"
    target.mkdir()
    unpack_verified(settings.data_dir / "backups" / (backup["id"] + ".zip"), target)
    restored = snapshot(Domain(Database(target / "manuscripts.sqlite")), story["branch_id"])
    assert restored["confirmation"] == chosen
    assert restored["categories"] == receipt["categories"]


def test_candidate_selection_is_atomic_editable_and_provenanced(client, domain, story):
    branch = story["branch_id"]
    endpoint = path(branch)
    saved = add_core(client, endpoint)
    proposed = item("ch-1", "夜巡人阿芹")
    relation = item("rel-1", "求助夜巡人", ("ch-1",))
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": branch},
        "conception_bundle",
        {
            "categories": [
                {"id": "characters", "items": [proposed]},
                {"id": "relationships", "items": [relation]},
            ]
        },
        {"conception_revision": saved["revision"], "story_revision": saved["story_revision"]},
    )
    data = {**basis(saved), "artifact_id": artifact["id"], "idempotency_key": "sample-choice-001"}
    already = client.post(
        f"/api/artifacts/{artifact['id']}/accept", json={"idempotency_key": "generic-conception"}
    )
    assert already.status_code == 409
    broken = client.post(
        f"{endpoint}/adopt", json={**data, "categories": [{"id": "relationships", "items": [relation]}]}
    )
    assert broken.status_code == 400
    assert client.get(endpoint).json() == saved
    adopted = {
        **data,
        "categories": [{"id": "characters", "items": [{**proposed, "title": "巡夜的阿芹"}]}],
    }
    success = client.post(f"{endpoint}/adopt", json=adopted)
    assert success.status_code == 200, success.text
    assert client.post(f"{endpoint}/adopt", json=adopted).json() == success.json()
    receipt = domain.db.one("SELECT artifact_id FROM receipts WHERE key=?", (data["idempotency_key"],))
    stored = domain.artifact(receipt["artifact_id"])["payload"]
    assert stored["origins"]["ch-1"] == {"origin": "ai", "artifact_id": artifact["id"], "edited": True}
    assert stored["categories"][0]["items"] == saved["categories"][0]["items"]
    assert domain.artifact(artifact["id"])["payload"]["categories"][0]["items"] == [proposed]


def test_confirmed_handoff_queue_freezes_and_isolates_snapshot(client, domain, story, settings):
    branch = story["branch_id"]
    endpoint = path(branch)
    state = confirm(client, endpoint, add_core(client, endpoint))
    confirmation = state["confirmation"]["id"]
    domain.save(story["chapter_id"], 0, "手稿的普通修改不会使没有依赖该正文的构思过期。")
    assert client.get(endpoint).json()["confirmation"]["current"]
    request = {
        "action": "writing.advise",
        "novel_id": story["id"],
        "branch_id": branch,
        "instruction": "如何继续开篇",
        "account": "openai_1",
        "conception_snapshot_id": confirmation,
    }
    writing = client.post("/api/modules", json=request)
    assert writing.status_code == 202, writing.text
    task = writing.json()
    assert task["result"]["conception_snapshot"]["id"] == confirmation

    for mode in ("assisted", "automatic"):
        response = client.post(
            "/api/workflows",
            json={
                "novel_id": story["id"],
                "source_branch_id": branch,
                "mode": mode,
                "brief": "写出盐船换票的决策。",
                "chapters": 1,
                "account": "openai_1",
                "conception_snapshot_id": confirmation,
                "plots": [{"number": 1, "title": "过闸", "plot": "阿芹拿着盐票去过闸。"}]
                if mode == "assisted"
                else [],
            },
        )
        assert response.status_code == 202, response.text
        copied = response.json()
        assert copied["branch_id"] != branch
        assert copied["result"]["conception_snapshot"] == task["result"]["conception_snapshot"]
        assert client.get(f"/api/branches/{copied['branch_id']}/chapters").json() == []

    revised = client.get(endpoint).json()
    revised["categories"][0]["items"][0]["title"] = "改走山路"
    save(client, endpoint, revised)
    assert client.post("/api/modules", json=request).status_code == 409
    other = domain.create_novel("另一部小说")
    assert (
        client.post(
            "/api/modules", json={**request, "novel_id": other["id"], "branch_id": other["branch_id"]}
        ).status_code
        == 409
    )

    class Model:
        async def generate(self, *args, **kwargs):
            prompt = next(
                json.loads(arg) for arg in args if isinstance(arg, str) and arg.startswith('{"任务"')
            )
            plan = prompt["上下文"]["conception_plan"]
            assert plan["id"] == confirmation
            assert plan["categories"][0]["items"][0]["title"] == "盐船换票"
            return {
                "text": "先让阿芹核对票根。",
                "usage": {"input": 1, "output": 1},
                "call_id": "frozen-plan",
            }

    asyncio.run(Worker(settings, Model()).run_once())
    task_after = domain.task(task["id"])
    assert task_after["status"] == "succeeded", task_after["error"]


def test_observed_record_has_source_and_loses_currency_after_source_edit(client, domain, story):
    branch = story["branch_id"]
    endpoint = path(branch)
    manuscript = domain.save(story["chapter_id"], 0, "阿芹在子夜把盐票交给巡夜人。")
    value = client.get(endpoint).json()
    response = client.post(
        f"/api/branches/{branch}/story-records",
        json={
            "base_story_revision": value["story_revision"],
            "record": {
                "kind": "event",
                "title": "交盐票",
                "status": "actual",
                "story_order": 1,
                "sources": [
                    {
                        "chapter_id": story["chapter_id"],
                        "revision": manuscript["revision"],
                        "start": 0,
                        "end": len(manuscript["content"]),
                        "quote": manuscript["content"],
                    }
                ],
            },
        },
    )
    assert response.status_code == 201, response.text
    state = client.get(endpoint).json()
    assert [row["category_id"] for row in state["observed"]] == ["events", "timeline"]
    original = confirm(client, endpoint, state)
    domain.save(story["chapter_id"], manuscript["revision"], "阿芹带着盐票返回码头。")
    invalidated = client.get(endpoint).json()
    assert invalidated["observed"] == []
    assert not invalidated["confirmation"]["current"]
    assert original["confirmation"]["id"] == invalidated["confirmation"]["id"]


def test_adopted_world_source_is_traceable_but_never_a_plan_or_current_after_edit(client, domain, story):
    branch = story["branch_id"]
    endpoint = path(branch)
    quote = "逢潮只能借一次铜牌。"
    chapter = domain.save(story["chapter_id"], 0, quote)
    state = client.get(endpoint).json()
    rule = item("rule-1", "铜牌每潮只准借一次")
    evidence = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(quote),
        "quote": quote,
    }
    sections = [{"id": "world", "items": [rule]}]
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": branch},
        "conception_bundle",
        {"categories": sections, "source_evidence": {rule["id"]: evidence}},
        {"conception_revision": state["revision"], "story_revision": state["story_revision"]},
    )
    adopted = client.post(
        f"{endpoint}/adopt",
        json={
            **basis(state),
            "artifact_id": artifact["id"],
            "categories": sections,
            "idempotency_key": "world-example",
        },
    )
    assert adopted.status_code == 200, adopted.text
    saved = adopted.json()
    assert saved["sources"] == [
        {"item_id": rule["id"], "evidence": evidence, "current": True, "author_edited": False}
    ]
    confirmed = confirm(client, endpoint, saved)
    source_id = confirmed["confirmation"]["id"]
    from ainovel.conception import handoff_tx

    with domain.db.connect() as tx:
        handoff = handoff_tx(tx, branch, source_id)
    assert handoff["categories"] == []
    assert handoff["observed_reference"][0]["title"] == rule["title"]
    assert quote not in str(handoff)
    domain.save(story["chapter_id"], chapter["revision"], "后来改为每月借一次。")
    invalidated = client.get(endpoint).json()
    assert not invalidated["sources"][0]["current"]
    assert not invalidated["confirmation"]["current"]
    assert any("依据已失效" in issue["message"] for issue in invalidated["issues"])
    refreshed = confirm(client, endpoint, invalidated, "confirm-new-state")
    with domain.db.connect() as tx:
        assert handoff_tx(tx, branch, refreshed["confirmation"]["id"])["observed_reference"] == []
