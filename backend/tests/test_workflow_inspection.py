import pytest

from ainovel.workflow_inspection import body_chars, latest_book_inspection
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation


@pytest.fixture
def inspection_client(client):
    return client


def endpoint(story):
    return f"/api/branches/{story['branch_id']}/workflow-inspections"


def read(client, route, **scope):
    response = client.get(route, params=scope)
    assert response.status_code == 200, response.text
    return response.json()


def report(client, route, preview, **options):
    data = {key: preview[key] for key in ("scope", "scope_id", "basis_sha256")}
    response = client.post(route, json={**data, **options})
    assert response.status_code == 201, response.text
    return response.json()


def item(report, identifier):
    return next(row for row in report["items"] if row["check_id"] == identifier)


def source(chapter):
    return {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"].encode("utf-16-le")) // 2,
        "quote": chapter["content"],
    }


def author(check_id, chapter, status="green"):
    return {
        "check_id": check_id,
        "status": status,
        "basis_note": "作者已读本范围，逐项比较章纲与保存稿。",
        "reason": "本章保留了角色身份和规则线索。",
        "evidence": [source(chapter)],
    }


def test_body_count_excludes_markdown_formatting_but_keeps_punctuation():
    assert body_chars("## 排版标题\n- **铜铃** [响了](https://example.invalid)\n---\n> 入门。😀\u200b") == 8


def test_only_absent_optional_material_can_be_marked_not_applicable(inspection_client, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "学徒拾铃入门。")
    route = endpoint(story)
    preview = read(inspection_client, route)["preview"]
    allowed = {row["check_id"] for row in preview["items"] if row["can_mark_not_applicable"]}
    assert allowed == {"05", "07"}
    for row in preview["items"]:
        if row["check_id"] in allowed:
            continue
        verdict = author(row["check_id"], chapter, "not_applicable")
        verdict["evidence"] = []
        blocked = inspection_client.post(
            route, json={"basis_sha256": preview["basis_sha256"], "author_reviews": [verdict]}
        )
        assert blocked.status_code == 400
        assert blocked.json()["code"] == "inspection_required"
    authored = [author(check_id, chapter, "not_applicable") for check_id in allowed]
    for verdict in authored:
        verdict["basis_note"] = "逐项核对了本范围采用的资料与正文。"
        verdict["reason"] = "本范围未采用该类设定或伏笔。"
        verdict["evidence"] = []
    created = report(inspection_client, route, preview, author_reviews=authored)
    assert item(created, "05")["current_status"] == item(created, "07")["current_status"] == "not_applicable"

    branch = story["branch_id"]
    conception = f"/api/branches/{branch}/conception"
    workspace = read(inspection_client, conception)
    next(category for category in workspace["categories"] if category["id"] == "world")["items"] = [
        {"id": "bell", "title": "铃声", "detail": "铜铃只在门内响。", "links": []}
    ]
    saved = inspection_client.put(
        conception,
        json={
            "base_revision": workspace["revision"],
            "base_story_revision": workspace["story_revision"],
            "categories": workspace["categories"],
        },
    )
    assert saved.status_code == 200, saved.text
    changed = read(inspection_client, route)
    assert item(changed["preview"], "05")["status"] == "outdated"
    assert not item(changed["preview"], "05")["can_mark_not_applicable"]
    assert item(changed["latest"], "05")["current_status"] == "outdated"
    assert item(changed["latest"], "07")["current_status"] == "not_applicable"


def test_numeric_targets_are_versioned_and_omission_cannot_remove_a_failed_target(
    inspection_client, domain, story
):
    chapter = domain.save(story["chapter_id"], 0, "铃声。")
    route = endpoint(story)
    targets = route + "/length-targets"
    first = inspection_client.put(targets, json={"targets": {"expected_chapters": 2}})
    assert first.status_code == 200, first.text
    preview = first.json()["preview"]
    goal = preview["length_target"]
    assert goal["revision"] == 1
    assert item(preview, "09")["status"] == "red"

    created = report(inspection_client, route, preview, author_reviews=[author("09", chapter)])
    assert item(created, "09")["current_status"] == "red"
    assert item(read(inspection_client, route)["preview"], "09")["status"] == "red"
    changed_inline = inspection_client.post(
        route, json={"basis_sha256": preview["basis_sha256"], "length_targets": {"expected_chapters": 1}}
    )
    assert changed_inline.status_code == 409
    assert inspection_client.put(targets, json={"targets": {"expected_chapters": 1}}).status_code == 409
    assert (
        inspection_client.put(targets, json={"base_target_id": goal["id"], "targets": {}}).status_code == 422
    )

    updated = inspection_client.put(
        targets, json={"base_target_id": goal["id"], "targets": {"expected_chapters": 1}}
    )
    assert updated.status_code == 200, updated.text
    result = updated.json()
    assert result["preview"]["length_target"]["revision"] == 2
    assert result["preview"]["basis_sha256"] != preview["basis_sha256"]
    assert item(result["preview"], "09")["status"] == "green"
    assert item(result["latest"], "09")["current_status"] == "outdated"
    assert all(
        row["current_status"] == row["status"] for row in result["latest"]["items"] if row["check_id"] != "09"
    )
    assert inspection_client.post(route, json={"basis_sha256": preview["basis_sha256"]}).status_code == 409
    historical = read(inspection_client, f"{route}/{created['id']}")
    assert historical["length_target"] == goal
    assert item(historical, "09") == {**item(created, "09"), "current_status": "outdated"}
    assert historical["basis_sha256"] == created["basis_sha256"]


@pytest.mark.parametrize("change", ["revision", "outline_scope"])
def test_length_check_expires_with_changed_chapter_or_volume_membership(
    inspection_client, domain, story, change
):
    client = inspection_client
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "学徒推门。")
    second = domain.create_chapter(branch, "第二章")
    domain.save(second["id"], 0, "师姐寻铃。")
    outline = f"/api/branches/{branch}/outline-workspace"
    volumes = [
        {
            "id": f"v{index}",
            "title": f"第{index}卷",
            "summary": "寻找铜铃",
            "units": [
                {
                    "id": f"u{index}",
                    "title": "线索",
                    "summary": "找铃",
                    "chapters": [{**chapter_plan(chapter["id"], "门内找铃"), "setting_ids": []}],
                }
            ],
        }
        for index, chapter in enumerate((first, second), 1)
    ]
    plan = {"summary": "寻找失铃", "volumes": volumes}
    confirmed = put_and_confirm(client, outline, {"base_revision": 0, "plan": plan})["confirmed"]
    route = endpoint(story)
    scope = {"scope": "volume", "scope_id": "v1"}
    saved = report(client, route, read(client, route, **scope)["preview"], length_targets={"min_chars": 1})
    assert item(saved, "09")["status"] == "green"

    if change == "revision":
        response = client.put(
            f"/api/chapters/{first['id']}", json={"base_revision": first["revision"], "content": "学徒敲门。"}
        )
        assert response.status_code == 200, response.text
    else:
        combined = {**volumes[0], "units": [*volumes[0]["units"], *volumes[1]["units"]]}
        put_and_confirm(
            client, outline, {"base_revision": confirmed["revision"], "plan": {**plan, "volumes": [combined]}}
        )
    active = read(client, route, **scope)["preview"]
    historical = read(client, f"{route}/{saved['id']}")
    assert active["length_target"] == saved["length_target"]
    assert item(active, "09")["status"] == "green"
    assert not historical["current"] and historical["basis_sha256"] == saved["basis_sha256"]
    assert historical["chapters"] == saved["chapters"]
    assert item(historical, "09") == {**item(saved, "09"), "current_status": "outdated"}


def test_inspection_freezes_author_evidence_and_scoped_manifests(inspection_client, domain, story):
    first = domain.save(story["chapter_id"], 0, "😀学徒拾铃。")
    branch = story["branch_id"]
    second = domain.create_chapter(branch, "第二章")
    second = domain.save(second["id"], 0, "他把铜铃归还。")
    route = endpoint(story)
    scope = {"scope": "chapter", "scope_id": first["id"]}
    chapter_preview = read(inspection_client, route, **scope)["preview"]
    assert len(chapter_preview["items"]) == 10
    assert item(chapter_preview, "01")["status"] == "pending"
    assert item(chapter_preview, "09")["status"] == "pending"
    created = report(
        inspection_client,
        route,
        chapter_preview,
        author_reviews=[author("03", first)],
        length_targets={"min_chars": 4, "max_chars": 8, "expected_chapters": 1},
    )
    assert item(created, "03")["current_status"] == item(created, "09")["status"] == "green"
    assert item(created, "03")["author_review"]["evidence"] == [source(first)]
    book = report(inspection_client, route, read(inspection_client, route)["preview"])

    domain.save(second["id"], second["revision"], "师姐接过铜铃。")
    assert read(inspection_client, route, **scope)["latest"]["current"]
    outdated_book = read(inspection_client, f"{route}/{book['id']}")
    assert not outdated_book["current"]
    with domain.db.connect() as tx:
        assert latest_book_inspection(tx, branch) == outdated_book
    domain.save(first["id"], first["revision"], "学徒看见一只铜铃。")
    previous = read(inspection_client, f"{route}/{created['id']}")
    assert item(previous, "03")["status"] == "green"
    assert item(previous, "03")["current_status"] == "outdated"
    assert item(previous, "03")["author_review"]["evidence"] == [source(first)]
    rejected = inspection_client.post(route, json={**scope, "basis_sha256": chapter_preview["basis_sha256"]})
    assert rejected.status_code == 409
    assert read(inspection_client, route, **scope)["history"][0]["id"] == created["id"]


def test_invalid_author_excerpt_and_missing_chapter_target_cannot_pass(inspection_client, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "铃声。")
    route = endpoint(story)
    preview = read(inspection_client, route)["preview"]
    review = author("06", chapter)
    review["evidence"][0]["quote"] = "无关。"
    response = inspection_client.post(
        route,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [review],
            "length_targets": {"expected_chapters": 2},
        },
    )
    assert response.status_code == 409
    rejected = read(inspection_client, route)
    assert rejected["latest"] is None
    assert rejected["preview"]["length_target"] is None
    invalid_scope = inspection_client.get(route, params={"scope": "volume", "scope_id": "missing"})
    assert invalid_scope.status_code == 409
    created = report(inspection_client, route, preview, length_targets={"expected_chapters": 2})
    assert item(created, "09")["status"] == "red"


def test_scoped_promises_use_confirmed_windows_and_actual_cross_scope_sources(
    inspection_client, domain, story
):
    client = inspection_client
    branch = story["branch_id"]
    base = f"/api/branches/{branch}"
    route = endpoint(story)
    first = domain.save(story["chapter_id"], 0, "开卷先做一道记号。")
    later = []
    for title in ("窗边埋设", "追查记号", "约定回收"):
        chapter = domain.create_chapter(branch, title)
        later.append(domain.save(chapter["id"], 0, title + "。"))
    graph_url = base + "/story-records"
    planned = {"kind": "promise", "title": "窗上的记号", "stages": [{"status": "planned"}]}
    saved = client.post(
        graph_url,
        json={"base_story_revision": domain.branch(branch)["story_revision"], "record": planned},
    )
    assert saved.status_code == 201, saved.text
    promise = saved.json()["records"][0]
    chapter_scope = {"scope": "chapter", "scope_id": first["id"]}
    unplaced = report(client, route, read(client, route, **chapter_scope)["preview"])
    assert [row["id"] for row in item(unplaced, "07")["basis"]["records"]] == [promise["id"]]
    assert not item(unplaced, "07")["can_mark_not_applicable"]

    plan = {
        "summary": "两卷各有核对范围",
        "volumes": [
            {
                "id": f"v{number}",
                "title": f"第{number}卷",
                "summary": "检查窗上记号",
                "units": [
                    {
                        "id": f"u{number}",
                        "title": "线索",
                        "summary": "按实际正文核对",
                        "chapters": [
                            {**chapter_plan(chapter["id"], "记录记号"), "setting_ids": []}
                            for chapter in group
                        ],
                    }
                ],
            }
            for number, group in enumerate(([first], later), 1)
        ],
        "promises": [
            {
                "promise_id": promise["id"],
                "plant_chapter_id": later[0]["id"],
                "opens_chapter_id": later[0]["id"],
                "closes_chapter_id": later[2]["id"],
            }
        ],
    }
    put_and_confirm(client, base + "/outline-workspace", {"base_revision": 0, "plan": plan})
    outside = []
    for scope in (chapter_scope, {"scope": "volume", "scope_id": "v1"}):
        preview = read(client, route, **scope)["preview"]
        check = item(preview, "07")
        assert check["basis"]["windows"] == check["basis"]["records"] == []
        assert check["can_mark_not_applicable"]
        verdict = {**author("07", first, "not_applicable"), "evidence": []}
        confirmed = report(client, route, preview, author_reviews=[verdict])
        assert item(confirmed, "07")["current_status"] == "not_applicable"
        outside.append(confirmed)
    older = read(client, f"{route}/{unplaced['id']}")
    assert older["basis_sha256"] == unplaced["basis_sha256"]
    assert item(older, "07") == {**item(unplaced, "07"), "current_status": "outdated"}

    for scope in (
        {"scope": "chapter", "scope_id": later[1]["id"]},
        {"scope": "volume", "scope_id": "v2"},
        {},
    ):
        check = item(read(client, route, **scope)["preview"], "07")
        assert check["status"] == "yellow" and not check["can_mark_not_applicable"]
        assert [row["id"] for row in check["basis"]["records"]] == [promise["id"]]
        assert check["basis"]["windows"][0]["due"] == "due"
    delivery = read(client, base + "/book-completion")
    assert next(row for row in delivery["checks"] if row["id"] == "promises")["status"] == "red"

    citations = [source(first), source(later[0])]
    changed = client.put(
        f"{graph_url}/{promise['id']}",
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "base_revision": promise["revision"],
            "record": {
                **planned,
                "stages": [*planned["stages"], {"status": "planted", "sources": citations}],
            },
        },
    )
    assert changed.status_code == 200, changed.text
    for old in outside:
        history = read(client, f"{route}/{old['id']}")
        assert history["basis_sha256"] == old["basis_sha256"]
        assert item(history, "07") == {**item(old, "07"), "current_status": "outdated"}
    check = item(read(client, route, **chapter_scope)["preview"], "07")
    assert not check["can_mark_not_applicable"] and check["basis"]["windows"] == []
    assert check["basis"]["records"][0]["stages"][-1]["sources"] == citations
    domain.save(first["id"], first["revision"], "开卷改稿，原记号位置已经变化。")
    check = item(read(client, route, **chapter_scope)["preview"], "07")
    assert check["status"] == "outdated" and check["basis"]["records"][0]["invalid_reasons"]
    assert check["basis"]["records"][0]["stages"][-1]["sources"] == citations


def test_inspection_reads_confirmed_outline_due_promise_and_rule_conflict(inspection_client, domain, story):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "铜铃在门外响了。")
    graph_route = f"/api/branches/{branch}/story-records"
    created = inspection_client.post(
        graph_route,
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "record": {"kind": "promise", "title": "门的线索", "stages": [{"status": "planned"}]},
        },
    )
    assert created.status_code == 201, created.text
    promise_id = created.json()["records"][0]["id"]
    setting_id = setting_confirmation(inspection_client, branch)
    route = f"/api/branches/{branch}/outline-workspace"
    outline = {
        "summary": "铃与门",
        "volumes": [
            {
                "id": "vol",
                "title": "遗铃",
                "summary": "门",
                "units": [
                    {
                        "id": "unit",
                        "title": "第一单元",
                        "summary": "开门",
                        "chapters": [
                            chapter_plan(chapter["id"], "追寻铜铃"),
                        ],
                    },
                ],
            }
        ],
        "promises": [
            {
                "promise_id": promise_id,
                "plant_chapter_id": chapter["id"],
                "opens_chapter_id": chapter["id"],
                "closes_chapter_id": chapter["id"],
            }
        ],
    }
    put_and_confirm(
        inspection_client, route, {"base_revision": 0, "confirmation_id": setting_id, "plan": outline}
    )
    actual = source(chapter)
    domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": branch,
            "chapter_id": chapter["id"],
            "base_revision": chapter["revision"],
        },
        "setting_review",
        {
            "result": "fail",
            "reason": "门外铃响。",
            "evidence": {
                "source_id": "SETTING0001",
                "text": actual["quote"],
                "start": 0,
                "end": actual["end"],
            },
        },
        {
            "rule": {
                "confirmation_id": setting_id,
                "id": "bell",
                "title": "铃声规则",
                "detail": "铜铃只在门内响。",
            }
        },
        on_stage=lambda tx, identifier: tx.execute(
            "UPDATE artifacts SET status='accepted' WHERE id=?", (identifier,)
        ),
    )
    scope = {"scope": "volume", "scope_id": "vol"}
    route = endpoint(story)
    preview = read(inspection_client, route, **scope)["preview"]
    assert item(preview, "02")["basis"]["plans"][0]["goal"] == "追寻铜铃"
    assert item(preview, "05")["status"] == "red"
    assert item(preview, "05")["basis"]["reports"][0]["evidence"]["text"] == actual["quote"]
    assert item(preview, "07")["basis"]["windows"][0]["due"] == "due"
    confirmed = report(
        inspection_client, route, preview, author_reviews=[author("05", chapter), author("07", chapter)]
    )
    assert item(confirmed, "05")["status"] == "red"
    assert item(confirmed, "07")["status"] == "yellow"
