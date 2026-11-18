from hashlib import sha256

from fastapi.testclient import TestClient

from test_outline_workspace import chapter_plan, setting_confirmation
from test_setting_use import TEXT, select, verified
from test_setting_use import author_plan as author_plan


def _report(client, endpoint, scope, scope_id):
    params = {"scope": scope, "scope_id": scope_id}
    preview = client.get(endpoint, params=params).json()["preview"]
    result = client.post(endpoint, json={**params, "basis_sha256": preview["basis_sha256"]})
    assert result.status_code == 201, result.text
    return result.json()


def _outline(client, branch, chapters, promises=(), *, setting_id=None):
    route = f"/api/branches/{branch}/outline-workspace"
    draft = client.put(
        route,
        json={
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": {
                "summary": "记录铜铃的来历和归还。",
                "volumes": [
                    {
                        "id": f"volume-{number}",
                        "title": f"第{number}卷",
                        "summary": "核实疑点。",
                        "units": [
                            {
                                "id": f"unit-{number}",
                                "title": "查清去向",
                                "summary": "寻找师姐。",
                                "chapters": [
                                    {
                                        **chapter_plan(chapter, "解决当前疑问"),
                                        "setting_ids": ["bell"] if setting_id else [],
                                    }
                                ],
                            }
                        ],
                    }
                    for number, chapter in enumerate(chapters, 1)
                ],
                "promises": list(promises),
            },
        },
    )
    assert draft.status_code == 200, draft.text
    confirmed = client.post(
        route + "/confirm",
        json={
            "base_revision": draft.json()["draft"]["revision"],
            "acknowledge_required_promises": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _record(client, domain, branch, **record):
    result = client.post(
        f"/api/branches/{branch}/story-records",
        json={"base_story_revision": domain.branch(branch)["story_revision"], "record": record},
    )
    assert result.status_code == 201, result.text
    return next(row["id"] for row in result.json()["records"] if row["payload"]["title"] == record["title"])


def _keys(record):
    return {row["check_id"] for row in record["items"] if row["current_status"] != "outdated"}


def test_chapter_preview_fixes_utf16_and_exact_affected_scope_then_commits_one_new_revision(
    client, domain, story
):
    branch, first = story["branch_id"], story["chapter_id"]
    second = domain.create_chapter(branch, "归还")["id"]
    domain.save(first, 0, "前卷先记下师姐的去向。")
    old = domain.save(second, 0, "旧稿只记下空铃架。")
    current = domain.save(second, old["revision"], "甲😀门内有铃。乙")
    quote = {"chapter_id": second, "revision": current["revision"], "start": 3, "end": 7, "quote": "门内有铃"}
    event_id = _record(
        client,
        domain,
        branch,
        kind="event",
        title="遇见铜铃",
        status="actual",
        story_order=1,
        sources=[quote],
    )
    promise = _record(
        client,
        domain,
        branch,
        kind="promise",
        title="找出门内铃声来历",
        stages=[{"status": "planned"}, {"status": "planted", "event_id": event_id, "sources": [quote]}],
    )
    window = {key: second for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")}
    setting = setting_confirmation(client, branch)
    _outline(
        client,
        branch,
        [first, second],
        [{"promise_id": promise, **window, "fulfillment": "author_required"}],
        setting_id=setting,
    )
    inspection_endpoint = f"/api/branches/{branch}/workflow-inspections"
    affected = [
        _report(client, inspection_endpoint, "chapter", second),
        _report(client, inspection_endpoint, "volume", "volume-2"),
    ]
    unaffected = _report(client, inspection_endpoint, "chapter", first)

    def history():
        return client.get(f"/api/chapters/{second}/history").json()

    before = history()
    target = next(row for row in before if row["revision"] == old["revision"])
    root = f"/api/chapters/{second}/history/{target['id']}"
    first_preview = client.get(root + "/restore-preview")
    assert first_preview.status_code == 200, first_preview.text
    impact = first_preview.json()
    assert impact["current"]["id"] == before[0]["id"]
    assert impact["current"]["sha256"] == sha256(current["content"].encode()).hexdigest()
    assert impact["target"]["id"] == target["id"] and impact["target"]["content"] == old["content"]
    assert {row["id"] for row in impact["affected_reports"]} == {row["id"] for row in affected}
    assert any(row["kind"] == "outline" for row in impact["references"])
    assert {"kind": "setting", "id": "bell", "title": "铃声规则", "source": None} in impact["references"]
    assert {"kind": "story_record", "id": event_id, "title": "遇见铜铃", "source": quote} in impact[
        "references"
    ]
    assert impact["promises"] == [
        {
            "id": promise,
            "title": "找出门内铃声来历",
            "status": "planted",
            "sources": [quote],
            "window": window,
        }
    ]
    assert history() == before
    assert client.get(root + "/restore-preview").json() == impact
    assert client.get(f"/api/chapters/{first}/history/{target['id']}/restore-preview").status_code == 404

    new = _report(client, inspection_endpoint, "chapter", second)
    stale = client.post(root + "/restore", json={"basis_sha256": impact["basis_sha256"]})
    assert stale.status_code == 409 and stale.json()["code"] == "chapter_restore_changed"
    assert history() == before
    confirmed = client.get(root + "/restore-preview").json()
    assert {entry["id"] for entry in confirmed["affected_reports"]} == {row["id"] for row in [*affected, new]}
    restored = client.post(root + "/restore", json={"basis_sha256": confirmed["basis_sha256"]})
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == current["revision"] + 1
    assert restored.json()["content"] == old["content"]
    assert history()[1:] == before
    assert client.get(f"{inspection_endpoint}/{unaffected['id']}").json()["current"]
    assert client.post(root + "/restore", json={"basis_sha256": confirmed["basis_sha256"]}).status_code == 409
    assert history()[1:] == before


def test_volume_preview_lists_affected_reports_by_exact_scope_and_never_publishes_forecast(
    client, domain, story
):
    branch, first = story["branch_id"], story["chapter_id"]
    second = domain.create_chapter(branch, "归还")["id"]
    domain.save(first, 0, "前卷内容保持。")
    before = domain.save(second, 0, "把铜铃还给师姐。")
    _outline(client, branch, [first, second])
    snapshots = f"/api/branches/{branch}/work-snapshots"
    receipt = client.post(snapshots, json={"scope": "volume", "scope_id": "volume-2", "label": "第二卷终点"})
    assert receipt.status_code == 201, receipt.text
    checkpoint = receipt.json()["id"]
    current = domain.save(second, before["revision"], "把铜铃留在门口。")
    inspection_endpoint = f"/api/branches/{branch}/workflow-inspections"
    reports = [
        _report(client, inspection_endpoint, "volume", "volume-2"),
        _report(client, inspection_endpoint, "chapter", second),
        _report(client, inspection_endpoint, "volume", "volume-1"),
    ]
    by_id = {row["id"]: row for row in reports}
    route = f"{snapshots}/{checkpoint}"
    response = client.get(route + "/preview")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert not preview["in_place_blockers"]
    assert domain.chapter(second) == current
    assert len([row for row in client.get(snapshots).json() if row["purpose"] == "pre_restore"]) == 0
    assert client.get(route + "/preview").json() == preview
    assert reports[0]["id"] in {row["id"] for row in preview["affected_reports"]}
    assert reports[1]["id"] in {row["id"] for row in preview["affected_reports"]}

    restored = client.post(
        route + "/restore",
        json={
            "basis_sha256": preview["basis_sha256"],
            "mode": "in_place",
            "idempotency_key": "restore-second-volume",
        },
    )
    assert restored.status_code == 201, restored.text
    assert domain.chapter(second)["content"] == before["content"]
    expected = {}
    for identifier, baseline in by_id.items():
        latest = client.get(f"{inspection_endpoint}/{identifier}").json()
        lost = _keys(baseline) - _keys(latest)
        if lost:
            expected[identifier] = (baseline["scope"], baseline["scope_id"], lost)
    assert {
        row["id"]: (row["scope"], row["scope_id"], set(row["check_ids"]))
        for row in preview["affected_reports"]
    } == expected
    assert "09" not in expected.get(reports[-1]["id"], (None, None, set()))[2]


def test_chapter_restore_rejects_preview_after_another_session_saves_and_keeps_legacy_path(
    client, domain, story
):
    chapter = story["chapter_id"]
    old = domain.save(chapter, 0, "第一份正文。")
    current = domain.save(chapter, old["revision"], "第二份正文。")
    location = f"/api/chapters/{chapter}"
    rows = client.get(location + "/history").json()
    first = next(row for row in rows if row["revision"] == 1)
    before = f"{location}/history/{first['id']}"
    preview = client.get(before + "/restore-preview").json()
    with TestClient(client.app) as another:
        another.headers["X-Ainovel-CSRF"] = another.get("/api/session").json()["csrf"]
        changed = another.put(
            location, json={"base_revision": current["revision"], "content": "另一窗最新版。"}
        )
        assert changed.status_code == 200, changed.text

    rejected = client.post(before + "/restore", json={"basis_sha256": preview["basis_sha256"]})
    assert rejected.status_code == 409 and rejected.json()["code"] == "chapter_restore_changed"
    assert client.get(location).json() == changed.json()
    fresh = client.get(before + "/restore-preview").json()
    restored = client.post(before + "/restore", json={"basis_sha256": fresh["basis_sha256"]})
    assert restored.status_code == 200 and restored.json()["content"] == old["content"]
    legacy = client.post(
        location + "/restore",
        json={"base_revision": restored.json()["revision"], "revision_id": rows[0]["id"]},
    )
    assert legacy.status_code == 200 and legacy.json()["content"] == current["content"]


def test_chapter_restore_reads_author_setting_use_and_requires_fresh_preview(client, domain, author_plan):
    plan = author_plan
    chapter = plan["second"]
    quote = "守门人"
    chapter_url = f"/api/chapters/{chapter['id']}"
    old = verified(client.get(chapter_url + "/history"))[0]
    current = domain.save(chapter["id"], chapter["revision"], "在😀渡口，守门人留在木门外。")
    history = f"{chapter_url}/history/{old['id']}"

    def preview():
        return verified(client.get(history + "/restore-preview"))

    def restore(value):
        return client.post(history + "/restore", json={"basis_sha256": value["basis_sha256"]})

    def guard_request(window, url, key, parent=None):
        request = select(verified(window.get(url)), key, parent) | {"item_id": "guard"}
        request["evidence"].update(start=6, end=9, quote_sha256=sha256(quote.encode()).hexdigest())
        return request

    baseline = preview()
    assert not any(row["kind"] == "setting_use" for row in baseline["references"])
    with TestClient(client.app) as another:
        another.headers["X-Ainovel-CSRF"] = another.get("/api/session").json()["csrf"]
        other_url = plan["route"]
        other_proof = select(verified(another.get(other_url)), "other-chapter-proof")
        verified(another.post(other_url, json=other_proof), 201)
        assert preview()["basis_sha256"] == baseline["basis_sha256"]

        url = plan["other"]
        request = guard_request(another, url, "current-chapter-proof")
        proof = verified(another.post(url, json=request), 201)["version"]
        expired = restore(baseline)
        assert expired.status_code == 409 and expired.json()["code"] == "chapter_restore_changed"
        assert verified(client.get(chapter_url)) == current
        expected = {
            "kind": "setting_use",
            "id": proof["id"],
            "title": quote,
            "source": {
                "chapter_id": chapter["id"],
                "revision_id": proof["evidence"]["revision_id"],
                "revision": current["revision"],
                "start": proof["evidence"]["start"],
                "end": proof["evidence"]["end"],
                "quote": quote,
            },
        }
        reviewed = preview()
        assert [row for row in reviewed["references"] if row["kind"] == "setting_use"] == [expected]

        withdrawn = verified(
            another.post(
                url + f"/{proof['id']}/withdraw", json={"idempotency_key": "withdraw-chapter-proof"}
            ),
            201,
        )["version"]
        expired = restore(reviewed)
        assert expired.status_code == 409 and expired.json()["code"] == "chapter_restore_changed"
        assert not any(row["kind"] == "setting_use" for row in preview()["references"])
        latest = verified(
            another.post(
                url,
                json=guard_request(another, url, "reauthor-chapter-proof", withdrawn["id"]),
            ),
            201,
        )["version"]

    reviewed_again = preview()
    assert [row for row in reviewed_again["references"] if row["kind"] == "setting_use"] == [
        expected | {"id": latest["id"]}
    ]
    restored = verified(restore(reviewed_again))
    assert (restored["revision"], restored["content"]) == (current["revision"] + 1, old["content"])
    history_record = verified(client.get(plan["other"] + "/" + latest["id"]))
    assert (history_record["status"], history_record["quote"], history_record["version"]) == (
        "outdated",
        quote,
        latest,
    )


def test_chapter_restore_previews_continuity_impact_for_later_setting_proofs(client, domain, author_plan):
    first, second = author_plan["first"], author_plan["second"]
    history = verified(client.get(f"/api/chapters/{first['id']}/history"))
    target = next(row for row in history if row["revision"] == 0)
    route = f"/api/chapters/{first['id']}/history/{target['id']}"
    proof_route = author_plan["other"]
    domain.save(second["id"], second["revision"], TEXT + "守门人留在木门外。")
    # Preserve the current chapter-restore dependencies while confirming a new proof on the later chapter.
    before = verified(client.get(route + "/restore-preview"))
    request = select(verified(client.get(proof_route)), "later-chapter-proof") | {"item_id": "guard"}
    start = len(TEXT.encode("utf-16-le")) // 2
    request["evidence"].update(start=start, end=start + 3, quote_sha256=sha256("守门人".encode()).hexdigest())
    proof = verified(client.post(proof_route, json=request), 201)["version"]
    fresh = verified(client.get(route + "/restore-preview"))
    [impact] = [row for row in fresh["references"] if row["kind"] == "setting_use"]
    assert (impact["id"], impact["source"]["chapter_id"], impact["source"]["quote"]) == (
        proof["id"],
        second["id"],
        "守门人",
    )
    assert client.post(route + "/restore", json={"basis_sha256": before["basis_sha256"]}).status_code == 409
    verified(client.post(route + "/restore", json={"basis_sha256": fresh["basis_sha256"]}))
    assert domain.chapter(second["id"])["revision"] == second["revision"] + 1
    assert verified(client.get(f"{proof_route}/{proof['id']}"))["status"] == "outdated"
