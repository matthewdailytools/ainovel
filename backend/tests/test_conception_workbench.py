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


def test_setting_inspection_previews_unsaved_invalid_references_without_writing(client, story):
    endpoint = path(story["branch_id"])
    original = client.get(endpoint).json()
    edited = client.get(endpoint).json()
    world = next(row for row in edited["categories"] if row["id"] == "world")
    world["items"] = [
        item("gate", "北城城门", ("missing", "gate", "pass", "pass")),
        item("pass", "通关令牌"),
    ]
    request = {**basis(edited), "categories": edited["categories"]}
    response = client.post(f"{endpoint}/setting-inspection", json=request)
    assert response.status_code == 200, response.text
    findings = {finding["kind"]: finding for finding in response.json()["findings"]}
    assert set(findings) == {"missing_link", "self_link", "duplicate_link"}
    for kind, number, target, target_position in (
        ("missing_link", 1, "missing", None),
        ("self_link", 2, "gate", 1),
        ("duplicate_link", 4, "pass", 2),
    ):
        source, destination = findings[kind]["locations"]
        assert (source["category_id"], source["item_id"], source["item_number"], source["link_number"]) == (
            "world",
            "gate",
            1,
            number,
        )
        assert (destination["item_id"], destination["item_number"]) == (target, target_position)
    discarded = client.put(endpoint, json=request)
    assert discarded.status_code == 400
    assert "gate" in discarded.text and "missing" in discarded.text
    assert client.get(endpoint).json() == original
    add_core(client, endpoint)
    assert client.post(f"{endpoint}/setting-inspection", json=request).status_code == 409


def test_setting_review_keeps_versioned_author_choice_for_recorded_differences(client, domain, story):
    endpoint = path(story["branch_id"])
    edited = client.get(endpoint).json()
    categories = {row["id"]: row for row in edited["categories"]}
    categories["world"]["items"] = [
        {
            **item("gate-east", "１号门", ("gate-west", "pass", "talent")),
            "detail": "凭铜牌开启。",
            "setting_policy": {"strength": "hard", "scope": "东城", "exceptions": "守夜人除外"},
        },
        {
            **item("gate-west", "1号门"),
            "detail": "只识指纹。",
            "setting_policy": {"strength": "hard", "scope": "西城", "exceptions": "城主除外"},
        },
        {
            **item("gate-east-copy", "1号门"),
            "detail": " 凭铜牌开启。 ",
            "setting_policy": {"strength": "hard", "scope": "东城", "exceptions": "守夜人除外"},
        },
        {**item("talent", "待定义天赋"), "detail": ""},
    ]
    categories["events"].update({"skipped": True, "items": [item("pass", "获取铜牌")]})
    saved = save(client, endpoint, edited)
    findings = {finding["kind"]: finding for finding in saved["setting_findings"]}
    assert set(findings) == {"same_name", "policy_link", "skipped_link", "empty_link"}
    same_name = findings["same_name"]
    assert [(side["item_id"], side["item_number"]) for side in same_name["locations"]] == [
        ("gate-east", 1),
        ("gate-west", 2),
        ("gate-east-copy", 3),
    ]
    assert [side["item_id"] for side in findings["policy_link"]["locations"]] == ["gate-east", "gate-west"]
    assert findings["skipped_link"]["locations"][1]["category_id"] == "events"
    keys = [finding["id"] for finding in findings.values()]
    payload = {**basis(saved), "idempotency_key": "confirm-with-findings"}
    for tokens in ([], keys[:-1], [*keys, keys[0]]):
        refused = client.post(f"{endpoint}/confirm", json={**payload, "reviewed_setting_findings": tokens})
        assert refused.status_code == 409 and "conception_setting_review" in refused.text
    payload["reviewed_setting_findings"] = keys
    accepted = client.post(f"{endpoint}/confirm", json=payload)
    assert accepted.status_code == 200, accepted.text
    confirmed = accepted.json()
    assert confirmed["confirmation"]["current"]
    assert all(finding["reviewed"] for finding in confirmed["setting_findings"])
    frozen = domain.artifact(confirmed["confirmation"]["id"])["payload"]
    assert frozen["setting_findings"] == confirmed["setting_findings"]
    assert frozen["reviewed_setting_findings"] == keys
    assert client.post(f"{endpoint}/confirm", json=payload).json() == confirmed
    world = next(row for row in confirmed["categories"] if row["id"] == "world")
    world["items"][1]["setting_policy"]["exceptions"] = "巡河官除外"
    changed = save(client, endpoint, confirmed)
    assert not changed["confirmation"]["current"]
    assert not any(finding["reviewed"] for finding in changed["setting_findings"])
    obsolete = client.post(
        f"{endpoint}/confirm",
        json={**payload, **basis(changed), "idempotency_key": "confirm-previous-findings"},
    )
    assert obsolete.status_code == 409 and "conception_setting_review" in obsolete.text


def test_setting_item_history_pages_and_restores_single_item_with_original_source(client, domain, story):
    branch = story["branch_id"]
    endpoint = path(branch)
    route = f"{endpoint}/items/lock/history"
    original = "潮响三次才能开锁。"
    chapter = domain.save(story["chapter_id"], 0, original)
    state = client.get(endpoint).json()
    contents = item("lock", "潮锁规则")
    evidence = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(original),
        "quote": original,
    }
    sections = [{"id": "world", "items": [contents]}]
    proposal = domain.stage(
        {"novel_id": story["id"], "branch_id": branch},
        "conception_bundle",
        {"categories": sections, "source_evidence": {"lock": evidence}},
        {"conception_revision": state["revision"], "story_revision": state["story_revision"]},
    )
    adopted = client.post(
        f"{endpoint}/adopt",
        json={
            **basis(state),
            "artifact_id": proposal["id"],
            "categories": sections,
            "idempotency_key": "source-rule",
        },
    )
    assert adopted.status_code == 200, adopted.text
    state = adopted.json()

    def world(value):
        return next(category for category in value["categories"] if category["id"] == "world")["items"]

    world(state)[0]["detail"] = "第二版允许手动打开。"
    second = save(client, endpoint, state)
    world(second).append(item("unrelated", "守港人"))
    sibling = save(client, endpoint, second)
    approved = confirm(client, endpoint, sibling)
    approved_id = approved["confirmation"]["id"]
    first_page = client.get(route, params={"limit": 1}).json()
    assert first_page["base_revision"] == sibling["revision"]
    assert first_page["entries"][0]["revision"] == second["revision"]
    assert first_page["current"]["provenance"]["author_edited"]
    older = client.get(route, params={"before_revision": first_page["next_before_revision"]}).json()
    prior = older["entries"][0]
    assert older["next_before_revision"] is None
    assert prior["revision"] == 1
    assert prior["state"]["provenance"]["evidence"] == evidence
    assert prior["state"]["provenance"]["artifact_id"] == proposal["id"]
    assert not prior["state"]["provenance"]["author_edited"]

    restore = f"{endpoint}/items/lock/restore"
    draft = {
        **basis(approved),
        "source_workspace_id": prior["workspace_id"],
        "idempotency_key": "restore-lock",
    }
    assert (
        client.post(restore, json={**draft, "base_revision": second["revision"]}).json()["code"]
        == "conception_changed"
    )
    other = domain.create_novel("另一作品")
    assert client.get(f"{path(other['branch_id'])}/items/lock/history").status_code == 404
    cross = client.post(
        f"{path(other['branch_id'])}/items/lock/restore",
        json={**draft, "base_revision": 0, "base_story_revision": 0},
    )
    assert cross.status_code == 404 and cross.json()["code"] == "conception_item_version"
    updated = client.post(restore, json=draft)
    assert updated.status_code == 200, updated.text
    restored = updated.json()
    assert restored["revision"] == approved["revision"] + 1
    assert restored["confirmation"]["id"] == approved_id and not restored["confirmation"]["current"]
    assert world(restored) == [contents, item("unrelated", "守港人")]
    assert restored["sources"] == [
        {"item_id": "lock", "evidence": evidence, "current": True, "author_edited": False}
    ]
    assert world(domain.artifact(approved_id)["payload"])[0]["detail"] == "第二版允许手动打开。"
    assert client.post(restore, json=draft).json() == restored
    latest = client.get(route).json()["entries"][0]
    assert latest["revision"] == restored["revision"] and latest["restored_from"] == prior["workspace_id"]
    assert confirm(client, endpoint, restored, "confirm-restored-lock")["confirmation"]["current"]


def test_deleted_setting_restore_requires_its_links_and_preserves_current_draft(client, story):
    endpoint = path(story["branch_id"])
    original = client.get(endpoint).json()
    world = next(category for category in original["categories"] if category["id"] == "world")
    original_items = [item("route", "走私路径", ("pass",)), item("pass", "铜牌")]
    world["items"] = original_items
    seeded = save(client, endpoint, original)
    next(category for category in seeded["categories"] if category["id"] == "world")["items"] = []
    removed = save(client, endpoint, seeded)
    history = client.get(f"{endpoint}/items/route/history").json()
    assert history["current"] is None and history["entries"][0]["state"] is None
    source = history["entries"][1]["workspace_id"]
    blocked = client.post(
        f"{endpoint}/items/route/restore",
        json={**basis(removed), "source_workspace_id": source, "idempotency_key": "restore-route"},
    )
    assert blocked.status_code == 400 and blocked.json()["code"] == "conception_links"
    assert "route" in blocked.text and "pass" in blocked.text
    assert client.get(endpoint).json() == removed
    dependency = client.post(
        f"{endpoint}/items/pass/restore",
        json={**basis(removed), "source_workspace_id": source, "idempotency_key": "restore-pass"},
    )
    assert dependency.status_code == 200, dependency.text
    restored = client.post(
        f"{endpoint}/items/route/restore",
        json={**basis(dependency.json()), "source_workspace_id": source, "idempotency_key": "restore-route"},
    )
    assert restored.status_code == 200, restored.text
    assert (
        next(category for category in restored.json()["categories"] if category["id"] == "world")["items"]
        == original_items
    )


def test_restoring_setting_invalidates_current_outline_candidate_and_review(client, domain, story):
    from ainovel.outline_generation import _current as candidate_current
    from ainovel.outline_generation import freeze_tx
    from test_outline_workspace import chapter_plan, put_and_confirm
    from test_workflow_inspection import item as report_item
    from test_workflow_inspection import read, report

    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "在门内轻轻摇铃。")
    endpoint = path(branch)
    state = client.get(endpoint).json()
    index = next(index for index, category in enumerate(state["categories"]) if category["id"] == "world")
    state["categories"][index]["items"] = [item("bell", "铜铃规则")]
    original = save(client, endpoint, state)
    original["categories"][index]["items"][0]["detail"] = "铜铃只在门内响。"
    changed = save(client, endpoint, original)
    confirmed = confirm(client, endpoint, changed)
    confirmation_id = confirmed["confirmation"]["id"]
    outline = f"/api/branches/{branch}/outline-workspace"
    planned = put_and_confirm(
        client,
        outline,
        {
            "base_revision": 0,
            "confirmation_id": confirmation_id,
            "plan": {
                "summary": "铜铃线索",
                "volumes": [
                    {
                        "id": "volume",
                        "title": "铜铃",
                        "summary": "铃声引路",
                        "units": [
                            {
                                "id": "unit",
                                "title": "门前",
                                "summary": "试铃",
                                "chapters": [chapter_plan(chapter["id"], "摇响铃声")],
                            }
                        ],
                    }
                ],
            },
        },
    )
    rule_reports = f"/api/branches/{branch}/workflow-inspections"
    before = report(client, rule_reports, read(client, rule_reports)["preview"])
    assert before["current"]
    with domain.db.connect() as tx:
        frozen = freeze_tx(
            tx,
            branch,
            {"base_revision": planned["draft"]["revision"], "confirmation_id": confirmation_id},
        )
        assert candidate_current(tx, branch, frozen)

    route = f"{endpoint}/items/bell"
    source = client.get(f"{route}/history").json()["entries"][-1]
    restored = client.post(
        f"{route}/restore",
        json={
            **basis(confirmed),
            "source_workspace_id": source["workspace_id"],
            "idempotency_key": "restore-bell",
        },
    )
    assert restored.status_code == 200, restored.text
    with domain.db.connect() as tx:
        assert not candidate_current(tx, branch, frozen)
    current = client.get(outline).json()
    assert current["confirmed"] == planned["confirmed"] and "采用设定已有变化" in current["stale"]
    after = read(client, rule_reports)["latest"]
    assert after["id"] == before["id"] and not after["current"]
    assert report_item(after, "05")["current_status"] == "outdated"


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
