from dataclasses import replace

from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.manuscript_exchange import export_manuscript
from test_workflow_inspection import author, endpoint, read, report


def test_archived_report_opens_exact_revision_and_scoped_quote(client, domain, story, settings, tmp_path):
    branch = story["branch_id"]
    rest = "\n另一段正文。见闻再起，见闻再止。"
    chapter = domain.save(story["chapter_id"], 0, "😀东岸见闻。" + rest)
    route = endpoint(story)
    excerpt = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 2,
        "end": 7,
        "quote": "东岸见闻。",
    }
    scene = client.post(
        f"/api/branches/{branch}/chapters/{chapter['id']}/scene-times",
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "scene": {
                "title": "另一段的叙述位置",
                "source_revision": 1,
                "start": 8,
                "end": 14,
                "audience": "author",
            },
        },
    )
    assert scene.status_code == 201, scene.text
    domain.stage(
        {"novel_id": story["id"], "branch_id": branch, "chapter_id": chapter["id"], "base_revision": 1},
        "review",
        {
            "findings": [
                {"severity": "blocker", "quote": "东岸见闻。", "reason": "缺少登船依据。"},
                {"severity": "blocker", "quote": "见闻", "reason": "原意见没有给出唯一位置。"},
            ]
        },
        {"story_revision": domain.branch(branch)["story_revision"]},
    )
    frozen = report(
        client,
        route,
        read(client, route)["preview"],
        author_reviews=[{**author("04", chapter, "red"), "evidence": [excerpt]}],
    )
    source = f"{route}/{frozen['id']}/source-context/04"
    original = read(client, source, source_index=0)["context"]
    assert original["before"] == "😀"
    assert original["quote"] == excerpt["quote"]
    assert original["after"] == rest
    assert original["address"]["revision"] == 1
    scene_source = f"{route}/{frozen['id']}/source-context/06"
    scene_excerpt = read(client, scene_source, source_index=0)["context"]
    assert scene_excerpt["quote"] == "另一段正文。"
    assert scene_excerpt["address"]["label"] == "另一段的叙述位置"
    blocker_source = f"{route}/{frozen['id']}/source-context/08"
    assert read(client, blocker_source)["total"] == 1
    blocker = read(client, blocker_source, source_index=0)["context"]
    assert blocker["address"]["start"] == 2 and blocker["quote"] == "东岸见闻。"

    domain.save(chapter["id"], chapter["revision"], "作者更改了本章正文。")
    package = export_manuscript(domain, branch)
    with TestClient(create_app(replace(settings, data_dir=tmp_path / "restored"))) as restored:
        session = restored.get("/api/session").json()
        restored.headers["X-Ainovel-CSRF"] = session["csrf"]
        imported = restored.post("/api/manuscript-packages/import", json=package.model_dump())
        assert imported.status_code == 201, imported.text
        assert read(restored, source, source_index=0)["context"] == original
        assert read(restored, scene_source, source_index=0)["context"] == scene_excerpt
        assert read(restored, blocker_source, source_index=0)["context"] == blocker
        rejected = restored.get(source, params={"source_index": 2})
        assert rejected.status_code == 404
        assert rejected.json()["code"] == "inspection_source_missing"
