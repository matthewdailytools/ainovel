import pytest

from ainovel import workflow_inspection
from ainovel.db import uid
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


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_numeric_targets_count_the_same_body_with_each_newline(client, domain, story, newline):
    content = newline.join(("甲", "***", "> - 乙", "1. > 丙。"))
    chapter = domain.save(story["chapter_id"], 0, content)
    route = endpoint(story)
    preview = read(client, route)["preview"]
    assert preview["chapters"][0]["chars"] == 4
    checked = report(client, route, preview, length_targets={"min_chars": 4, "max_chars": 4})
    assert item(checked, "09")["current_status"] == "green"
    assert item(checked, "09")["basis"]["method"].startswith("正文有效字符/2")
    assert read(client, f"/api/chapters/{chapter['id']}")["content"] == content


def test_new_count_method_requires_rechecking_previous_numeric_verdict(client, domain, story, monkeypatch):
    chapter = domain.save(story["chapter_id"], 0, "取回铜铃。")
    route = endpoint(story)
    scope = {"scope": "chapter", "scope_id": chapter["id"]}
    with monkeypatch.context() as legacy:
        legacy.setattr(
            workflow_inspection,
            "COUNT_VERSION",
            "正文有效字符/1，排除 Markdown 标题行、常见排版标记、空白和不可见字符，计入标点",
        )
        previous = report(
            client,
            route,
            read(client, route, **scope)["preview"],
            author_reviews=[author("02", chapter)],
            length_targets={"min_chars": 5, "max_chars": 5},
        )
    current = read(client, route, **scope)
    assert not current["latest"]["current"]
    assert current["preview"]["basis_sha256"] != previous["basis_sha256"]
    history = read(client, f"{route}/{previous['id']}")
    assert item(history, "09")["status"] == "green"
    assert item(history, "09")["current_status"] == "outdated"
    assert item(history, "09")["basis"] == item(previous, "09")["basis"]
    assert item(history, "02")["current_status"] == "green"
    assert item(history, "02")["author_review"]["evidence"] == [source(chapter)]
    current_report = report(client, route, current["preview"], author_reviews=[author("02", chapter)])
    assert item(current_report, "09")["current_status"] == "green"
    assert item(current_report, "09")["basis"]["method"] == workflow_inspection.COUNT_VERSION


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
    revised_chapter = domain.save(chapter["id"], chapter["revision"], "学徒携铃出门。")
    after_prose = read(inspection_client, route)["latest"]
    assert not after_prose["current"]
    assert item(after_prose, "02")["current_status"] == "outdated"
    for check_id in allowed:
        assert item(after_prose, check_id)["current_status"] == "not_applicable"
    assert revised_chapter["revision"] == chapter["revision"] + 1

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


@pytest.mark.parametrize("target_scope", ["chapter", "volume"])
def test_saved_descendant_length_target_blocks_only_its_current_ranges_without_a_report(
    inspection_client, domain, story, target_scope
):
    client = inspection_client
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "## 排版标题\n> **铃响。**")
    second = domain.create_chapter(branch, "卷外章")
    domain.save(second["id"], 0, "师姐寻铃。")
    plan = {
        "summary": "寻找失铃",
        "volumes": [
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
        ],
    }
    put_and_confirm(client, f"/api/branches/{branch}/outline-workspace", {"base_revision": 0, "plan": plan})
    route = endpoint(story)
    book = report(client, route, read(client, route)["preview"], length_targets={"min_chars": 1})
    assert item(book, "09")["status"] == "green"
    volume = {"scope": "volume", "scope_id": "v1"}
    chapter = {"scope": "chapter", "scope_id": first["id"]}
    sibling = read(client, route, scope="volume", scope_id="v2")["preview"]
    snapshot = client.post(f"/api/branches/{branch}/work-snapshots", json={"label": "原篇幅", **volume})
    assert snapshot.status_code == 201, snapshot.text
    checkpoint = f"/api/branches/{branch}/work-snapshots/{snapshot.json()['id']}"

    owner = chapter if target_scope == "chapter" else volume
    adopted = client.put(f"{route}/length-targets", json={**owner, "targets": {"min_chars": 80}})
    assert adopted.status_code == 200, adopted.text
    target = adopted.json()["preview"]["length_target"]
    assert adopted.json()["latest"] is None
    assert item(adopted.json()["preview"], "09")["status"] == "red"
    current = read(client, route)
    failure = item(current["preview"], "09")
    assert failure["status"] == "red"
    assert item(current["latest"], "09")["current_status"] == "outdated"
    assert failure["basis"]["length_target"] == book["length_target"]
    assert failure["basis"]["descendant_length_targets"] == [
        {
            **owner,
            "title": first["title"] if target_scope == "chapter" else "第1卷",
            "chapter_ids": [first["id"]],
            "length_target": target,
            "status": "red",
            "summary": "1 章、3 个有效字符。正文字符数超出冻结目标。",
        }
    ]
    assert item(read(client, route, **volume)["preview"], "09")["status"] == "red"
    assert item(read(client, route, **chapter)["preview"], "09")["status"] == (
        "red" if target_scope == "chapter" else "pending"
    )
    assert read(client, route, scope="volume", scope_id="v2")["preview"] == sibling
    attempted_override = report(client, route, current["preview"], author_reviews=[author("09", first)])
    assert item(attempted_override, "09")["status"] == "red"

    restore = client.post(
        checkpoint + "/restore",
        json={
            "basis_sha256": read(client, checkpoint + "/preview")["basis_sha256"],
            "mode": "in_place",
            "idempotency_key": uid(),
        },
    )
    assert restore.status_code == 201, restore.text
    cleared = item(read(client, route)["preview"], "09")
    assert cleared["status"] == "green"
    assert cleared["basis"]["length_target"] == book["length_target"]
    [receipt] = cleared["basis"]["descendant_length_targets"]
    assert receipt["status"] == "cleared"
    assert receipt["length_target"]["revision"] == target["revision"] + 1
    assert receipt["length_target"]["targets"] is None
    assert item(read(client, route, **owner)["preview"], "09")["status"] == "pending"
    assert item(read(client, route, scope="volume", scope_id="v2")["preview"], "09") == item(sibling, "09")
    recorded = item(read(client, f"{route}/{attempted_override['id']}"), "09")
    assert recorded["status"] == "red" and recorded["current_status"] == "outdated"
    assert recorded["basis"] == failure["basis"]

    readopted = client.put(f"{route}/length-targets", json={**owner, "targets": {"min_chars": 80}})
    assert readopted.status_code == 200, readopted.text
    outline_path = f"/api/branches/{branch}/outline-workspace"
    confirmed = read(client, outline_path)["confirmed"]
    first_volume, second_volume = plan["volumes"]
    combined = {**second_volume, "units": first_volume["units"] + second_volume["units"]}
    put_and_confirm(
        client,
        outline_path,
        {"base_revision": confirmed["revision"], "plan": {**plan, "volumes": [combined]}},
    )
    moved = item(read(client, route)["preview"], "09")
    destination = item(read(client, route, scope="volume", scope_id="v2")["preview"], "09")
    if target_scope == "chapter":
        assert moved["status"] == destination["status"] == "red"
        assert (
            moved["basis"]["descendant_length_targets"] == destination["basis"]["descendant_length_targets"]
        )
    else:
        assert moved["status"] == "green" and destination["status"] == "pending"
        assert "descendant_length_targets" not in moved["basis"]


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


def test_scoped_red_reviews_block_book_completion_until_explicitly_rechecked(client, domain, story):
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "学徒拾铃入门。")
    second = domain.create_chapter(branch, "第二章")
    second = domain.save(second["id"], 0, "师姐接过了铜铃。")
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
                    "chapters": [{**chapter_plan(chapter["id"], "核对铜铃"), "setting_ids": []}],
                }
            ],
        }
        for index, chapter in enumerate((first, second), 1)
    ]
    put_and_confirm(
        client, outline, {"base_revision": 0, "plan": {"summary": "归还铜铃", "volumes": volumes}}
    )
    route = endpoint(story)
    completion = f"/api/branches/{branch}/book-completion"
    decision = {
        "read_note": "已通读当前稿件。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "author_decision",
        },
        "ending": {"note": "铜铃已归还。", "sources": [source(second)]},
        "characters": {"note": "两人找到并交接铜铃。", "sources": [source(second)]},
        "promises": {"note": "作者确认本稿没有单列承诺。"},
        "intro": "学徒在门口拾得旧铜铃。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [first["id"]],
        "materials_reviewed": True,
    }

    def sign_book():
        preview = read(client, route)["preview"]
        return report(
            client,
            route,
            preview,
            author_reviews=[author(check["check_id"], first) for check in preview["items"]],
        )

    def sign_scope(scope, check_id, chapter, status):
        preview = read(client, route, **scope)["preview"]
        reviews = [author(check_id, chapter, status)] if status else []
        return report(client, route, preview, author_reviews=reviews)

    book = sign_book()
    initial = read(client, completion)
    original = client.post(
        completion + "/confirm", json={**decision, "basis_sha256": initial["basis_sha256"]}
    )
    assert original.status_code == 201, original.text
    download = f"{completion}/{original.json()['id']}/export"
    frozen = client.get(download).content
    chapter_scope = {"scope": "chapter", "scope_id": first["id"]}
    first_volume = {"scope": "volume", "scope_id": "v1"}
    second_volume = {"scope": "volume", "scope_id": "v2"}

    chapter_red = sign_scope(chapter_scope, "03", first, "red")
    assert item(chapter_red, "03")["current_status"] == "red"
    first_check = item(read(client, route, **first_volume)["preview"], "03")
    assert first_check["status"] == "red"
    assert first_check["basis"]["scoped_findings"] == [
        {**chapter_scope, "report_id": chapter_red["id"]},
    ]
    assert item(read(client, route, **second_volume)["preview"], "03")["status"] != "red"
    inherited = sign_scope(first_volume, "03", first, "green")
    assert item(inherited, "03")["current_status"] == "red"
    volume_red = sign_scope(second_volume, "04", second, "red")
    sign_scope(chapter_scope, "03", first, None)
    rolled = sign_book()
    assert item(rolled, "03")["basis"]["scoped_findings"] == first_check["basis"]["scoped_findings"]
    assert item(rolled, "04")["basis"]["scoped_findings"] == [
        {**second_volume, "report_id": volume_red["id"]},
    ]
    assert {check["check_id"] for check in rolled["items"] if check["current_status"] == "red"} == {
        "03",
        "04",
    }
    history = read(client, f"{route}/{book['id']}")
    assert not history["current"]
    assert item(history, "03")["current_status"] == "outdated"
    blocked = read(client, completion)
    assert not blocked["latest"]["current"]
    assert blocked["basis_sha256"] != initial["basis_sha256"]
    assert next(check for check in blocked["checks"] if check["id"] == "inspection")["status"] == "red"
    rejected = client.post(
        completion + "/confirm", json={**decision, "basis_sha256": blocked["basis_sha256"]}
    )
    assert rejected.status_code == 409 and rejected.json()["code"] == "completion_blocked"
    assert client.get(download).content == frozen
    archive = read(client, f"/api/branches/{branch}/manuscript-package")["workflow_history"]
    archived_reports = {entry["id"]: entry["report"] for entry in archive["inspections"]}
    assert (
        item(archived_reports[rolled["id"]], "03")["basis"]["scoped_findings"]
        == first_check["basis"]["scoped_findings"]
    )
    assert item(archived_reports[chapter_red["id"]], "03")["author_review"]["evidence"] == [source(first)]

    sign_scope(chapter_scope, "03", first, "green")
    still_blocked = read(client, route)["preview"]
    assert item(still_blocked, "03")["status"] != "red"
    assert item(still_blocked, "04")["status"] == "red"
    sign_scope(second_volume, "04", second, "green")
    passed = sign_book()
    assert all(check["current_status"] == "green" for check in passed["items"])
    confirmed = client.post(
        completion + "/confirm", json={**decision, "basis_sha256": read(client, completion)["basis_sha256"]}
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["manifest"]["inspection_id"] == passed["id"]
    assert client.get(download).content == frozen


def test_same_check_keeps_independent_volume_red_when_chapter_findings_change(client, story):
    branch = f"/api/branches/{story['branch_id']}"
    second = client.post(branch + "/chapters", json={"title": "第二章"})
    assert second.status_code == 201, second.text
    chapters = []
    for identifier, content in (
        (story["chapter_id"], "学徒拾铃入门。"),
        (second.json()["id"], "师姐认出另一枚旧铃。"),
    ):
        saved = client.put(f"/api/chapters/{identifier}", json={"base_revision": 0, "content": content})
        assert saved.status_code == 200, saved.text
        chapters.append(saved.json())
    first, second = chapters
    put_and_confirm(
        client,
        branch + "/outline-workspace",
        {
            "base_revision": 0,
            "plan": {
                "summary": "核对两枚旧铃的来历。",
                "volumes": [
                    {
                        "id": "v1",
                        "title": "归铃卷",
                        "summary": "核对铜铃。",
                        "units": [
                            {
                                "id": "u1",
                                "title": "线索",
                                "summary": "核对持铃人。",
                                "chapters": [
                                    {**chapter_plan(chapter["id"], "核对持铃人"), "setting_ids": []}
                                    for chapter in chapters
                                ],
                            }
                        ],
                    }
                ],
            },
        },
    )
    route = endpoint(story)
    completion = branch + "/book-completion"
    chapter_scope = {"scope": "chapter", "scope_id": first["id"]}
    volume_scope = {"scope": "volume", "scope_id": "v1"}

    def sign(scope, status, chapter, reason):
        preview = read(client, route, **scope)["preview"]
        evidence = {**author("03", chapter, status), "reason": reason}
        return report(client, route, preview, author_reviews=[evidence])

    def sign_book():
        preview = read(client, route)["preview"]
        return report(
            client,
            route,
            preview,
            author_reviews=[author(row["check_id"], first) for row in preview["items"]],
        )

    def findings():
        return item(read(client, route)["preview"], "03")["basis"].get("scoped_findings", [])

    def complete():
        return client.post(
            completion + "/confirm",
            json={
                "basis_sha256": read(client, completion)["basis_sha256"],
                "read_note": "两章已通读。",
                "author_decisions": {
                    "reading": "verified",
                    "ending": "verified",
                    "characters": "verified",
                    "promises": "author_decision",
                },
                "ending": {"note": "铃已交还。", "sources": [source(second)]},
                "characters": {"note": "学徒与师姐完成交接。", "sources": [source(second)]},
                "promises": {"note": "作者确认本稿没有单列伏笔。"},
                "intro": "学徒发现两枚旧铃。",
                "spoiler_scope": "opening",
                "sample_chapter_ids": [first["id"]],
                "materials_reviewed": True,
            },
        )

    sign_book()
    chapter_red = sign(chapter_scope, "red", first, "第一章持铃人称谓冲突。")
    volume_red = sign(volume_scope, "red", second, "第二章另有持铃人身份冲突。")
    volume_finding = {**volume_scope, "report_id": volume_red["id"]}
    assert findings() == [{**chapter_scope, "report_id": chapter_red["id"]}, volume_finding]

    sign(chapter_scope, "green", first, "已核实第一章使用的是同一称谓。")
    assert findings() == [volume_finding]
    old_volume = read(client, f"{route}/{volume_red['id']}")
    assert not old_volume["current"]
    assert item(old_volume, "03")["current_status"] == "red"
    assert item(old_volume, "03")["author_review"]["evidence"] == [source(second)]
    assert item(old_volume, "03")["basis_sha256"] == item(volume_red, "03")["basis_sha256"]
    assert (
        next(check for check in read(client, completion)["checks"] if check["id"] == "inspection")["status"]
        == "red"
    )
    rejected = complete()
    assert rejected.status_code == 409 and rejected.json()["code"] == "completion_blocked"

    next_chapter_red = sign(chapter_scope, "red", first, "第一章另有身份疑点。")
    chapter_finding = {**chapter_scope, "report_id": next_chapter_red["id"]}
    assert findings() == [chapter_finding, volume_finding]
    passed_volume = sign(volume_scope, "green", second, "已核实第二章的人物身份。")
    assert item(passed_volume, "03")["current_status"] == "red"
    assert findings() == [chapter_finding]

    sign(chapter_scope, "green", first, "第一章的身份疑点也已核实。")
    assert findings() == []
    passed_book = sign_book()
    finished = complete()
    assert finished.status_code == 201, finished.text
    assert finished.json()["manifest"]["inspection_id"] == passed_book["id"]
    history = read(client, branch + "/manuscript-package")["workflow_history"]["inspections"]
    frozen = next(entry["report"] for entry in history if entry["id"] == volume_red["id"])
    assert item(frozen, "03")["status"] == "red"
    assert item(frozen, "03")["author_review"]["evidence"] == [source(second)]

    updated_volume_red = sign(volume_scope, "red", second, "第二章有待核对的另一处身份问题。")
    assert findings() == [{**volume_scope, "report_id": updated_volume_red["id"]}]
    updated = client.put(
        f"/api/chapters/{second['id']}",
        json={"base_revision": second["revision"], "content": "师姐认出故人的铜铃，确认了铃主。"},
    )
    assert updated.status_code == 200, updated.text
    assert findings() == []
    changed_volume = read(client, f"{route}/{updated_volume_red['id']}")
    assert item(changed_volume, "03")["current_status"] == "outdated"
    assert complete().status_code == 409


def save_inspection_story_record(client, branch, payload, previous=None):
    revision = read(client, branch + "/story-graph")["story_revision"]
    route = branch + "/story-records"
    data = {"base_story_revision": revision, "record": payload}
    response = (
        client.put(f"{route}/{previous['id']}", json={**data, "base_revision": previous["revision"]})
        if previous
        else client.post(route, json=data)
    )
    assert response.status_code == (200 if previous else 201), response.text
    return next(row for row in response.json()["records"] if row["payload"]["title"] == payload["title"])


@pytest.mark.parametrize("shared_volume", [False, True])
def test_known_character_conflicts_freeze_both_sources_and_block_completion(
    client, domain, story, shared_volume
):
    branch_id = story["branch_id"]
    branch = f"/api/branches/{branch_id}"
    unrelated = domain.save(story["chapter_id"], 0, "船驶离渡口。")
    made = domain.create_chapter(branch_id, "第二章")
    first = domain.save(made["id"], 0, "😀沈砚左手骨折。")
    made = domain.create_chapter(branch_id, "第三章")
    second = domain.save(made["id"], 0, "隔着雾，沈砚左手完好。")
    chapters = (unrelated, first, second)
    volumes = ((unrelated,), (first, second)) if shared_volume else ((unrelated,), (first,), (second,))
    put_and_confirm(
        client,
        branch + "/outline-workspace",
        {
            "base_revision": 0,
            "plan": {
                "summary": "核对渡口交接。",
                "volumes": [
                    {
                        "id": f"v{index}",
                        "title": f"第{index}卷",
                        "summary": "渡口交接。",
                        "units": [
                            {
                                "id": f"u{index}",
                                "title": "线索",
                                "summary": "核对线索。",
                                "chapters": [
                                    {**chapter_plan(chapter["id"], "核对船上见闻"), "setting_ids": []}
                                    for chapter in volume
                                ],
                            }
                        ],
                    }
                    for index, volume in enumerate(volumes, 1)
                ],
            },
        },
    )
    person = save_inspection_story_record(client, branch, {"kind": "character", "title": "沈砚"})
    event = save_inspection_story_record(
        client,
        branch,
        {
            "kind": "event",
            "title": "渡口交接",
            "status": "actual",
            "story_order": 1,
            "participants": [person["id"]],
            "sources": [source(first)],
        },
    )
    state = {
        "kind": "state",
        "character_id": person["id"],
        "event_id": event["id"],
        "category": "injury",
        "key": "左手伤势",
        "audience": "author",
        "truth": "actual",
    }
    first_evidence = {**source(first), "start": 2, "quote": "沈砚左手骨折。"}
    first_state = save_inspection_story_record(
        client, branch, {**state, "title": "左手骨折", "value": "骨折", "sources": [first_evidence]}
    )
    route = endpoint(story)
    completion = branch + "/book-completion"
    publication = {
        "read_note": "作者已经通读。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "author_decision",
        },
        "ending": {"note": "故事以船离开渡口结束。", "sources": [source(first)]},
        "characters": {"note": "沈砚已完成交接。", "sources": [source(first)]},
        "promises": {"note": "作者确认本稿没有另设伏笔。"},
        "intro": "沈砚到达渡口。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [unrelated["id"], first["id"]],
        "materials_reviewed": True,
    }

    def sign_book():
        preview = read(client, route)["preview"]
        return report(
            client,
            route,
            preview,
            author_reviews=[
                author(check["check_id"], first) for check in preview["items"] if check["status"] == "pending"
            ],
            length_targets={"expected_chapters": len(chapters)},
        )

    original = sign_book()
    second_evidence = {**source(second), "start": 4, "quote": "沈砚左手完好。"}
    second_state = save_inspection_story_record(
        client, branch, {**state, "title": "左手完好", "value": "完好", "sources": [second_evidence]}
    )
    ids = {first_state["id"], second_state["id"]}
    context = read(client, branch + "/story-context", story_order=1)
    assert [{row["id"] for row in group} for group in context["conflicts"]] == [ids]
    for chapter in chapters:
        preview = read(client, route, scope="chapter", scope_id=chapter["id"])["preview"]
        assert item(preview, "03")["status"] == "pending"
        assert "state_conflicts" not in item(preview, "03")["basis"]
    for index, volume in enumerate(volumes, 1):
        preview = read(client, route, scope="volume", scope_id=f"v{index}")["preview"]
        check = item(preview, "03")
        if len(volume) == 2:
            assert check["status"] == "red"
            assert {side["record_id"] for side in check["basis"]["state_conflicts"][0]["sides"]} == ids
        else:
            assert check["status"] == "pending" and "state_conflicts" not in check["basis"]

    state_preview = read(client, route)
    preview = state_preview["preview"]
    conflict = item(preview, "03")
    assert conflict["status"] == "red"
    assert item(state_preview["latest"], "03")["current_status"] == "outdated"
    assert item(state_preview["latest"], "09")["current_status"] == "green"
    (group,) = conflict["basis"]["state_conflicts"]
    assert (group["character_id"], group["story_order"], group["event_id"]) == (person["id"], 1, None)
    assert {side["record_id"] for side in group["sides"]} == ids
    assert {side["value"]: side["sources"] for side in group["sides"]} == {
        "骨折": [first_evidence],
        "完好": [second_evidence],
    }
    assert not state_preview["latest"]["current"] and state_preview["latest"]["id"] == original["id"]
    for status in ("green", "yellow", "not_applicable"):
        attempted = client.post(
            route,
            json={"basis_sha256": preview["basis_sha256"], "author_reviews": [author("03", first, status)]},
        )
        assert attempted.status_code == 409 and attempted.json()["code"] == "inspection_state_conflict"
    blocked = sign_book()
    assert item(blocked, "03")["current_status"] == "red"
    completion_basis = read(client, completion)["basis_sha256"]
    refused = client.post(completion + "/confirm", json={**publication, "basis_sha256": completion_basis})
    assert refused.status_code == 409 and refused.json()["code"] == "completion_blocked"
    sources_route = f"{route}/{blocked['id']}/source-context/03"

    def validate_sources():
        entries = read(client, sources_route)["entries"]
        assert len(entries) == 2
        for entry in entries:
            expected = next(
                row for row in (first_evidence, second_evidence) if row["chapter_id"] == entry["chapter_id"]
            )
            exact = read(client, sources_route, source_index=entry["index"])["context"]
            assert {key: entry[key] for key in ("chapter_id", "revision", "start", "end")} == {
                key: expected[key] for key in ("chapter_id", "revision", "start", "end")
            }
            assert exact["quote"] == expected["quote"]

    validate_sources()
    revised = domain.save(second["id"], second["revision"], "隔着雾，沈砚的左手明显骨折。")
    stale = read(client, route)
    assert item(stale["preview"], "03")["status"] == "outdated"
    assert "state_conflicts" not in item(stale["preview"], "03")["basis"]
    saved = item(read(client, f"{route}/{blocked['id']}"), "03")
    assert (saved["status"], saved["current_status"]) == ("red", "outdated")
    validate_sources()

    save_inspection_story_record(
        client,
        branch,
        {**state, "title": "左手伤势已核对", "value": "骨折", "sources": [source(revised)]},
        previous=second_state,
    )
    signed = sign_book()
    assert {row["check_id"]: row["current_status"] for row in signed["items"]} == {
        row["check_id"]: "green" for row in signed["items"]
    }
    basis = read(client, completion)["basis_sha256"]
    delivered = client.post(completion + "/confirm", json={**publication, "basis_sha256": basis})
    assert delivered.status_code == 201, delivered.text
    assert delivered.json()["manifest"]["inspection_id"] == signed["id"]


def test_state_conflict_scope_and_time_are_supported_by_both_source_records(client, domain, story):
    branch = f"/api/branches/{story['branch_id']}"
    first = domain.save(story["chapter_id"], 0, "沈砚左手骨折。沈砚左手完好。")
    person = save_inspection_story_record(client, branch, {"kind": "character", "title": "沈砚"})
    route = endpoint(story)
    events = {}
    for name, order in (("arrival", 1), ("departure", 2), ("anchor_a", None), ("anchor_b", None)):
        events[name] = save_inspection_story_record(
            client,
            branch,
            {
                "kind": "event",
                "title": name,
                "status": "actual",
                "story_order": order,
                "sources": [source(first)],
            },
        )["id"]

    def save_state(key, value, event_id, audience):
        quote = f"沈砚左手{value}。"
        start = first["content"].index(quote)
        return save_inspection_story_record(
            client,
            branch,
            {
                "kind": "state",
                "title": f"{key} {value} {audience}",
                "character_id": person["id"],
                "event_id": events[event_id],
                "category": "injury",
                "key": key,
                "audience": audience,
                "truth": "actual",
                "sources": [{**source(first), "start": start, "end": start + len(quote), "quote": quote}],
                "value": value,
            },
        )

    for key, left, right, other_audience in (
        ("不同时间", "arrival", "departure", "author"),
        ("不同视域", "arrival", "arrival", "reader"),
        ("不明时间的两个锚点", "anchor_a", "anchor_b", "author"),
    ):
        save_state(key, "骨折", left, "author")
        save_state(key, "完好", right, other_audience)
    scope = {"scope": "chapter", "scope_id": first["id"]}
    check = item(read(client, route, **scope)["preview"], "03")
    assert check["status"] == "pending" and "state_conflicts" not in check["basis"]

    left = save_state("相同的确切事件", "骨折", "anchor_a", "author")
    right = save_state("相同的确切事件", "完好", "anchor_a", "author")
    check = item(read(client, route, **scope)["preview"], "03")
    assert check["status"] == "red"
    (group,) = check["basis"]["state_conflicts"]
    assert group["story_order"] is None and group["event_id"] == events["anchor_a"]
    assert {side["record_id"] for side in group["sides"]} == {left["id"], right["id"]}
    signed = report(
        client, route, read(client, route, **scope)["preview"], author_reviews=[author("03", first, "red")]
    )
    assert item(signed, "03")["current_status"] == "red"
    evidence = f"{route}/{signed['id']}/source-context/03"
    entries = [
        entry
        for entry in read(client, evidence)["entries"]
        if left["id"] in entry["label"] or right["id"] in entry["label"]
    ]
    assert len(entries) == 2
    sources = [read(client, evidence, source_index=entry["index"])["context"]["quote"] for entry in entries]
    assert set(sources) == {"沈砚左手骨折。", "沈砚左手完好。"}
