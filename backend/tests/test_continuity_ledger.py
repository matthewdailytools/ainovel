"""Continuity windows, author-declared scope and version-bound evidence."""

import pytest


@pytest.fixture
def ledger(client, domain, story):
    branch_id = story["branch_id"]
    base = f"/api/branches/{branch_id}/continuity-ledger"

    def get():
        response = client.get(base)
        assert response.status_code == 200, response.text
        return response.json()

    return branch_id, base, get


def chapter_span(first, last):
    return {"start_chapter_id": first["id"], "end_chapter_id": last["id"]}


def source(chapter, quote):
    at = chapter["content"].index(quote)
    start = len(chapter["content"][:at].encode("utf-16-le")) // 2
    return {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": start,
        "end": start + len(quote.encode("utf-16-le")) // 2,
        "quote": quote,
    }


def row(snapshot, entry_id):
    return next(entry for entry in snapshot["entries"] if entry["id"] == entry_id)


def stage(entry, stage_name):
    return next(item for item in entry["stages"] if item["stage"] == stage_name)


def make_foreshadow(title, chapters, windows):
    return {
        "kind": "foreshadow",
        "title": title,
        "detail": "只以作者明确安排和保存稿原文为依据。",
        "affected_chapter_ids": [c["id"] for c in chapters],
        "windows": windows,
    }


def create(client, base, plan):
    response = client.post(f"{base}/entries", json=plan)
    assert response.status_code == 201, response.text
    return response.json()


def record_evidence(client, base, entry, stage_name, selected):
    return client.put(
        f"{base}/entries/{entry['id']}/evidence",
        json={
            "base_revision": entry["revision"],
            "stage": stage_name,
            "source": selected,
            "note": "已查正文。",
        },
    )


def test_create_plan_retries_only_replay_the_original_plan(client, ledger):
    _, base, get = ledger
    request = {**make_foreshadow("下一卷的石桥", [], {}), "client_request_id": "same-create-request"}
    initial = create(client, base, request)
    assert create(client, base, request) == initial
    response = client.post(f"{base}/entries", json={**request, "title": "更改名称"})
    assert response.status_code == 409
    assert [item["id"] for item in get()["entries"]] == [initial["id"]]


def test_only_explicit_windows_create_reminders(client, domain, story, ledger):
    branch_id, base, get = ledger
    first = domain.save(story["chapter_id"], 0, "在门外，她看见铜铃缺了一角。")
    second = domain.create_chapter(branch_id, "第二章")
    third = domain.create_chapter(branch_id, "第三章")
    fourth = domain.create_chapter(branch_id, "第四章")

    legacy = client.post(
        f"/api/branches/{branch_id}/story-records",
        json={
            "base_story_revision": get()["story_revision"],
            "record": {
                "kind": "promise",
                "title": "旧伏笔",
                "stages": [{"status": "planned", "note": "想在第三章揭晓"}],
            },
        },
    )
    assert legacy.status_code == 201, legacy.text
    legacy_id = next(r["id"] for r in legacy.json()["records"] if r["payload"]["title"] == "旧伏笔")
    planned = create(
        client,
        base,
        make_foreshadow("铜铃", [first, second, third], {"resolved": chapter_span(second, third)}),
    )

    def assert_status(expected, kind=None, target=None):
        data = get()
        assert stage(row(data, planned["id"]), "resolved")["window_status"] == expected
        assert stage(row(data, legacy_id), "resolved")["window_status"] == "none"
        seen = [(r["kind"], r["entry_id"], r["stage"], r["chapter_id"]) for r in data["reminders"]]
        assert seen == ([(kind, planned["id"], "resolved", target["id"])] if kind else [])

    assert_status("future")
    domain.save(second["id"], 0, "铜铃发出了两声回音。")
    assert_status("open", "window_open", second)
    domain.save(third["id"], 0, "她沿回音找到藏在墙里的门闩。")
    assert_status("due", "window_due", third)
    domain.save(fourth["id"], 0, "她退到门外等同伴。")
    assert_status("overdue", "window_overdue", third)


def test_window_edits_are_optimistic_and_never_substitute_archived_or_reordered_chapters(
    client, domain, story, ledger
):
    branch_id, base, get = ledger
    first = domain.save(story["chapter_id"], 0, "她停在门边。")
    second = domain.create_chapter(branch_id, "第二章")
    third = domain.create_chapter(branch_id, "第三章")
    plan = make_foreshadow("门闩的磨痕", [first, second, third], {"resolved": chapter_span(first, second)})
    entry = create(client, base, plan)
    endpoint = f"{base}/entries/{entry['id']}/plan"
    moved_plan = {
        **plan,
        "windows": {"resolved": chapter_span(second, third)},
        "base_revision": entry["revision"],
    }
    changed = client.put(endpoint, json=moved_plan)
    assert changed.status_code == 200, changed.text
    assert client.put(endpoint, json={**plan, "base_revision": entry["revision"]}).status_code == 409
    assert stage(row(get(), entry["id"]), "resolved")["window"] == chapter_span(second, third)

    foreign = domain.create_novel("另一作品", "")
    foreign_chapter = domain.create_chapter(foreign["branch_id"], "另一章")
    foreign_link = client.put(
        endpoint,
        json={
            **moved_plan,
            "base_revision": changed.json()["revision"],
            "windows": {"resolved": chapter_span(second, foreign_chapter)},
        },
    )
    assert 400 <= foreign_link.status_code < 500, foreign_link.text

    moved = client.post(
        f"/api/chapters/{third['id']}/move",
        json={"base_story_revision": get()["story_revision"], "direction": "up"},
    )
    assert moved.status_code == 200, moved.text
    invalid = stage(row(get(), entry["id"]), "resolved")
    assert invalid["window_status"] == "invalid"
    assert invalid["window"] == chapter_span(second, third)
    assert not get()["reminders"]
    archive = client.post(
        f"/api/chapters/{third['id']}/archive", json={"base_revision": third["revision"], "archived": True}
    )
    assert archive.status_code == 200, archive.text
    assert stage(row(get(), entry["id"]), "resolved")["window_status"] == "invalid"
    assert not get()["reminders"]
    assert stage(row(get(), entry["id"]), "resolved")["window"] == chapter_span(second, third)


def test_stage_proof_keeps_old_saved_revision_until_explicit_recheck(client, domain, story, ledger):
    branch_id, base, get = ledger
    first_quote = "她看见铜铃缺了一角。"
    second_quote = "第一声回音来自梁上。"
    last_quote = "她从梁上取下失踪的铃舌。"
    first = domain.save(story["chapter_id"], 0, f"𠮷\n{first_quote}")
    second = domain.create_chapter(branch_id, "第二章")
    second = domain.save(second["id"], 0, second_quote)
    third = domain.create_chapter(branch_id, "第三章")
    third = domain.save(third["id"], 0, last_quote)
    plan = make_foreshadow(
        "铜铃",
        [first, second, third],
        {"resolved": chapter_span(third, third)},
    )
    entry = create(client, base, plan)
    selected = source(first, first_quote)
    forged = record_evidence(client, base, entry, "planted", {**selected, "start": 0})
    assert forged.status_code == 409
    assert stage(row(get(), entry["id"]), "planted")["review_status"] == "unrecorded"
    planted = record_evidence(client, base, entry, "planted", selected)
    assert planted.status_code == 200, planted.text
    reinforced = record_evidence(client, base, planted.json(), "reinforced", source(second, second_quote))
    assert reinforced.status_code == 200, reinforced.text
    grounded = record_evidence(client, base, reinforced.json(), "resolved", source(third, last_quote))
    assert grounded.status_code == 200, grounded.text
    original = row(get(), entry["id"])
    assert original["closed"]
    assert not get()["reminders"]
    for stage_name in ("planted", "reinforced", "resolved"):
        assert stage(original, stage_name)["review_status"] == "current"

    replacement_quote = "她从铜铃内取下藏住的铃舌。"
    revision = domain.save(third["id"], third["revision"], replacement_quote)
    stale = row(get(), entry["id"])
    assert not stale["closed"]
    assert stage(stale, "planted")["review_status"] == "current"
    assert stage(stale, "reinforced")["review_status"] == "current"
    resolved = stage(stale, "resolved")
    assert resolved["review_status"] == "stale"
    assert resolved["source"]["revision"] == third["revision"]
    assert resolved["source"]["quote"] == last_quote
    assert any(
        r["kind"] == "recheck" and r["entry_id"] == entry["id"] and r["stage"] == "resolved"
        for r in get()["reminders"]
    )

    old_selection = record_evidence(client, base, stale, "resolved", source(third, last_quote))
    assert old_selection.status_code == 409
    assert row(get(), entry["id"]) == stale
    updated = record_evidence(client, base, stale, "resolved", source(revision, replacement_quote))
    assert updated.status_code == 200, updated.text
    assert updated.json()["closed"]
    assert not get()["reminders"]
    history = client.get(f"{base}/entries/{entry['id']}/history")
    assert history.status_code == 200, history.text
    assert {
        stage(snapshot, "resolved")["source"]["quote"]
        for snapshot in history.json()
        if stage(snapshot, "resolved")["source"]
    } == {last_quote, replacement_quote}


def test_effective_setting_cannot_be_retargeted_after_evidence(client, domain, story, ledger):
    branch_id, base, get = ledger
    cost = "点火时会损耗十年寿元。"
    limit = "灯芯熄灭后不能复用。"
    first = domain.save(story["chapter_id"], 0, f"{cost}\n{limit}")
    second = domain.create_chapter(branch_id, "第二章")
    proof = "火焰刚起来，她已折损十年寿元。"
    second = domain.save(second["id"], 0, proof)
    third = domain.create_chapter(branch_id, "第三章")
    fourth = domain.create_chapter(branch_id, "第四章")

    for title, quote in (("点火代价", cost), ("灯芯限制", limit)):
        response = client.post(
            "/api/facts",
            json={
                "chapter_id": first["id"],
                "source_revision": first["revision"],
                "subject": title,
                "kind": "world",
                "statement": quote,
                "evidence": quote,
            },
        )
        assert response.status_code == 201, response.text

    context = client.get(f"/api/branches/{branch_id}/chapters/{second['id']}/writing-context")
    ids = {item["title"]: item["id"] for item in context.json()["settings"]}
    plan = {
        "kind": "setting",
        "title": "点火代价的生效",
        "detail": "她第一次点火时折损寿元。",
        "setting_id": ids["点火代价"],
        "setting_chapter_id": second["id"],
        "affected_chapter_ids": [third["id"]],
        "windows": {"effective": chapter_span(second, second)},
    }
    entry = create(client, base, plan)
    old_basis = {**entry["setting_basis"], "text": "点火不消耗寿元。"}
    rejected = client.put(
        f"{base}/entries/{entry['id']}/evidence",
        json={
            "base_revision": entry["revision"],
            "stage": "effective",
            "source": source(second, proof),
            "expected_setting": old_basis,
        },
    )
    assert rejected.status_code == 409
    assert row(get(), entry["id"]) == entry
    saved = record_evidence(client, base, entry, "effective", source(second, proof))
    assert saved.status_code == 200, saved.text
    current = row(get(), entry["id"])
    endpoint = f"{base}/entries/{entry['id']}/plan"
    retargeted = client.put(
        endpoint, json={**plan, "base_revision": current["revision"], "setting_id": ids["灯芯限制"]}
    )
    assert retargeted.status_code == 409
    assert row(get(), entry["id"]) == current

    plan = {
        **plan,
        "base_revision": current["revision"],
        "windows": {"effective": chapter_span(third, third)},
    }
    moved = client.put(endpoint, json=plan)
    assert moved.status_code == 200, moved.text
    current = row(get(), entry["id"])
    effective = stage(current, "effective")
    assert effective["review_status"] == "outside_window"
    assert effective["source"]["quote"] == proof
    impacted = {chapter["id"] for chapter in current["impacted_chapters"]}
    assert third["id"] in impacted
    assert fourth["id"] not in impacted
    out_of_window = record_evidence(client, base, current, "effective", source(second, proof))
    assert out_of_window.status_code == 409
    assert row(get(), entry["id"]) == current

    new_quote = "这一回，她点火后也折损十年寿元。"
    third = domain.save(third["id"], third["revision"], new_quote)
    checked = record_evidence(client, base, row(get(), entry["id"]), "effective", source(third, new_quote))
    assert checked.status_code == 200, checked.text
    assert stage(checked.json(), "effective")["review_status"] == "current"
    assert not checked.json()["impacted_chapters"]
    archive = client.post(
        f"/api/chapters/{third['id']}/archive", json={"base_revision": third["revision"], "archived": True}
    )
    assert archive.status_code == 200, archive.text
    stale = stage(row(get(), entry["id"]), "effective")
    assert stale["review_status"] != "current"
    assert stale["source"]["chapter_id"] == third["id"]
    assert stale["source"]["quote"] == new_quote
    assert all(r["chapter_id"] != third["id"] for r in get()["reminders"])


def test_existing_full_promise_stage_list_cannot_make_ledger_unreadable(client, domain, story, ledger):
    branch_id, base, get = ledger
    first = domain.save(story["chapter_id"], 0, "她第一次看见那把钥匙。")
    response = client.post(
        f"/api/branches/{branch_id}/story-records",
        json={
            "base_story_revision": get()["story_revision"],
            "record": {
                "kind": "promise",
                "title": "已有百条计划记录",
                "stages": [{"status": "planned", "note": f"计划第 {number} 条"} for number in range(100)],
            },
        },
    )
    assert response.status_code == 201, response.text
    [entry] = get()["entries"]
    blocked = record_evidence(client, base, entry, "planted", source(first, first["content"]))
    assert 400 <= blocked.status_code < 500, blocked.text
    assert get()["entries"] == [entry]
    graph = client.get(f"/api/branches/{branch_id}/story-graph")
    assert graph.status_code == 200, graph.text
    assert len(graph.json()["records"][0]["payload"]["stages"]) == 100


def test_legacy_two_source_payoff_cannot_be_closed_with_one_stale_source(client, domain, story, ledger):
    branch_id, _base, get = ledger
    first = domain.save(story["chapter_id"], 0, "门后留着旧钥匙。")
    second = domain.create_chapter(branch_id, "第二章")
    second = domain.save(second["id"], 0, "旧钥匙能打开梁上的铜匣。")
    response = client.post(
        f"/api/branches/{branch_id}/story-records",
        json={
            "base_story_revision": get()["story_revision"],
            "record": {
                "kind": "promise",
                "title": "钥匙用途",
                "stages": [
                    {
                        "status": "resolved",
                        "sources": [source(first, first["content"]), source(second, second["content"])],
                    }
                ],
            },
        },
    )
    assert response.status_code == 201, response.text
    [original] = get()["entries"]
    assert original["closed"]
    domain.save(second["id"], second["revision"], "铜匣依然锁着。")
    [changed] = get()["entries"]
    assert not changed["closed"]
    assert stage(changed, "resolved")["review_status"] == "stale"
    assert second["id"] in {item["id"] for item in changed["impacted_chapters"]}
