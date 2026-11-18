import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.workflow_inspection import body_chars, latest_book_inspection, register_routes
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation


@pytest.fixture
def inspection_client(settings, domain):
    app = create_app(settings)
    if not any(route.path.endswith("/workflow-inspections") for route in app.routes):
        register_routes(app, domain)
    with TestClient(app) as client:
        session = client.get("/api/session").json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        yield client


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
        route, json={"basis_sha256": preview["basis_sha256"], "author_reviews": [review]}
    )
    assert response.status_code == 409
    assert read(inspection_client, route)["latest"] is None
    invalid_scope = inspection_client.get(route, params={"scope": "volume", "scope_id": "missing"})
    assert invalid_scope.status_code == 409
    created = report(inspection_client, route, preview, length_targets={"expected_chapters": 2})
    assert item(created, "09")["status"] == "red"


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
