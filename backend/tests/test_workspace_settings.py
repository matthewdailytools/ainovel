import pytest


@pytest.mark.parametrize("kind", ["world", "characters", "outline"])
def test_workspace_setting_reads_only_current_adopted_book_definition(client, domain, story, kind):
    branch_id = story["branch_id"]
    path = f"/api/branches/{branch_id}/definitions"
    summary_path = f"{path}/{kind}"
    empty = client.get(summary_path).json()
    assert empty == {
        "story_revision": domain.branch(branch_id)["story_revision"],
        "artifact_id": None,
        "text": "",
    }

    body = {"kind": kind, "text": "台阶向河面展开。", "base_story_revision": empty["story_revision"]}
    saved = client.post(path, json=body)
    assert saved.status_code == 201, saved.json()
    adopted = next(row for row in saved.json()["definitions"] if row["kind"] == kind)
    other_scope = domain.stage(
        {"novel_id": story["id"], "branch_id": branch_id, "chapter_id": story["chapter_id"]},
        kind,
        {"text": "本章的独立资料。"},
        {},
    )
    domain.accept(other_scope["id"], "workspace-settings-chapter-scope")

    stale = client.post(path, json={**body, "text": "其他窗口的过期草稿。"})
    assert stale.status_code == 409
    assert client.get(summary_path).json() == {
        "story_revision": domain.branch(branch_id)["story_revision"],
        "artifact_id": adopted["id"],
        "text": body["text"],
    }

    other = domain.create_novel("另一部作品")
    assert client.get(f"/api/branches/{other['branch_id']}/definitions/{kind}").json()["artifact_id"] is None
