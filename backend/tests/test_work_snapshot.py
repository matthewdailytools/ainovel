from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.backups import create_backup, unpack_verified
from ainovel.db import Database, uid
from ainovel.domain import Domain
from ainovel.story_graph import graph
from ainovel.work_snapshot import RestoreInput, register_routes, restore
from test_conception_workbench import confirm, path, save
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF, IDEA


@pytest.fixture
def snapshot_client(settings, domain):
    app = create_app(settings)
    if not any(route.path.endswith("/work-snapshots") for route in app.routes):
        register_routes(app, domain)
    with TestClient(app) as client:
        session = client.get("/api/session").json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        yield client


def verify(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def route(branch):
    return f"/api/branches/{branch}/work-snapshots"


def outline(branch):
    return f"/api/branches/{branch}/outline-workspace"


def save_project(client, branch, revision, ending):
    return verify(
        client.put(
            f"{path(branch)}/project-brief",
            json={
                "base_revision": revision,
                "idempotency_key": uid(),
                "idea": IDEA,
                "brief": {**BRIEF, "ending": ending},
            },
        )
    )


def request_restore(client, branch, snapshot_id, mode="in_place", **fields):
    location = f"{route(branch)}/{snapshot_id}"
    preview = verify(client.get(location + "/preview"))
    return verify(
        client.post(
            location + "/restore",
            json={
                "basis_sha256": preview["basis_sha256"],
                "mode": mode,
                "idempotency_key": uid(),
                **fields,
            },
        ),
        201,
    )


def test_volume_checkpoint_restores_manuscript_setting_outline_and_promise_in_both_modes(
    snapshot_client,
    domain,
    settings,
    story,
    tmp_path,
):
    client = snapshot_client
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    project = save_project(client, branch, 0, "铃归师姐。")
    settled = domain.save(chapter_id, 0, "师姐在门内接过铜铃，铜铃响了。")
    setting_id = setting_confirmation(client, branch)
    record_route = f"/api/branches/{branch}/story-records"
    planned = verify(
        client.post(
            record_route,
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "record": {
                    "kind": "promise",
                    "title": "铃归师姐",
                    "stages": [
                        {"status": "planned"},
                        {
                            "status": "resolved",
                            "sources": [
                                {
                                    "chapter_id": chapter_id,
                                    "revision": settled["revision"],
                                    "start": 0,
                                    "end": len(settled["content"]),
                                    "quote": settled["content"],
                                }
                            ],
                        },
                    ],
                },
            },
        ),
        201,
    )
    record_id = planned["records"][0]["id"]
    authored_plan = {
        "summary": "门内归还",
        "volumes": [
            {
                "id": "volume",
                "title": "遗铃",
                "summary": "追铃",
                "units": [
                    {
                        "id": "unit",
                        "title": "师姐",
                        "summary": "归还",
                        "chapters": [
                            chapter_plan(chapter_id, "门内归铃"),
                        ],
                    }
                ],
            }
        ],
        "promises": [
            {
                "promise_id": record_id,
                "plant_chapter_id": chapter_id,
                "opens_chapter_id": chapter_id,
                "closes_chapter_id": chapter_id,
            }
        ],
    }
    old_outline = put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": 0,
            "confirmation_id": setting_id,
            "plan": authored_plan,
        },
    )["confirmed"]
    checkpoint = verify(
        client.post(
            route(branch),
            json={
                "scope": "volume",
                "scope_id": "volume",
                "label": "卷末",
            },
        ),
        201,
    )
    assert checkpoint["outline_id"] == old_outline["id"]
    assert checkpoint["project_id"] == project["id"]
    before = verify(client.get(f"{route(branch)}/{checkpoint['id']}/preview"))
    changed = domain.save(chapter_id, settled["revision"], "师姐在门外接过铜铃，没有听见声音。")
    changed_project = save_project(client, branch, project["revision"], "铃由学徒保管。")
    second = client.get(path(branch)).json()
    next(category for category in second["categories"] if category["id"] == "world")["items"][0]["detail"] = (
        "铜铃只在门外响。"
    )
    second = confirm(client, path(branch), save(client, path(branch), second), key=uid())
    put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": old_outline["revision"],
            "confirmation_id": second["confirmation"]["id"],
            "plan": {**authored_plan, "summary": "暂存铜铃"},
        },
    )
    stale = client.post(
        f"{route(branch)}/{checkpoint['id']}/restore",
        json={
            "basis_sha256": before["basis_sha256"],
            "mode": "in_place",
            "idempotency_key": uid(),
        },
    )
    assert stale.status_code == 409
    snapshots = verify(client.get(route(branch)))
    assert any(row["id"] == checkpoint["id"] for row in snapshots)
    assert sum(row["purpose"] == "milestone" for row in snapshots) == 2
    assert all(row["purpose"] != "before_restore" for row in snapshots)
    restored = request_restore(client, branch, checkpoint["id"])
    current = domain.chapter(chapter_id)
    assert current["revision"] > changed["revision"]
    assert current["content"] == settled["content"]
    assert verify(client.get(path(branch)))["confirmation"]["id"] == restored["setting_id"]
    assert restored["setting_id"] != setting_id
    returned_project = verify(client.get(f"{path(branch)}/project-brief"))
    assert returned_project["revision"] > changed_project["revision"]
    assert returned_project["brief"]["ending"] == project["brief"]["ending"]
    assert verify(client.get(outline(branch)))["confirmed"]["plan"] == authored_plan
    row = graph(domain, branch)["records"][0]
    assert not row["invalid_reasons"]
    assert row["payload"]["stages"][-1]["sources"][0]["revision"] == current["revision"]
    assert any(row["id"] == restored["recovery_snapshot_id"] for row in verify(client.get(route(branch))))

    fork = request_restore(client, branch, checkpoint["id"], "new_branch", branch_name="卷末复本")
    fork_branch = fork["branch_id"]
    fork_chapter = fork["chapters"][chapter_id]["id"]
    assert fork_chapter != chapter_id
    assert domain.chapter(fork_chapter, fork_branch)["content"] == settled["content"]
    fork_plan = verify(client.get(outline(fork_branch)))["confirmed"]["plan"]
    assert fork_plan["promises"][0]["promise_id"] == fork["record_ids"][record_id] != record_id
    assert fork_plan["promises"][0]["closes_chapter_id"] == fork_chapter
    fork_record = graph(domain, fork_branch)["records"][0]
    assert not fork_record["invalid_reasons"]
    assert fork_record["payload"]["stages"][-1]["sources"][0]["chapter_id"] == fork_chapter
    assert domain.chapter(chapter_id)["revision"] == current["revision"]

    isolated = tmp_path / "isolated-work-snapshot"
    backup = create_backup(settings, domain.db)
    unpack_verified(Path(backup["path"]), isolated)
    recovered = Domain(Database(isolated / "manuscripts.sqlite"))
    assert recovered.artifact(checkpoint["id"])["payload"]["manifest_sha256"] == checkpoint["manifest_sha256"]
    assert (
        recovered.artifact(restored["id"])["payload"]["recovery_snapshot_id"]
        == restored["recovery_snapshot_id"]
    )


def test_failed_restore_is_atomic_and_retry_is_idempotent(snapshot_client, domain, story, monkeypatch):
    client = snapshot_client
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    old = domain.save(chapter_id, 0, "第一稿。")
    checkpoint = verify(client.post(route(branch), json={"label": "回到首稿"}), 201)
    changed = domain.save(chapter_id, old["revision"], "第二稿。")
    location = f"{route(branch)}/{checkpoint['id']}"
    preview = verify(client.get(location + "/preview"))
    params = {
        "basis_sha256": preview["basis_sha256"],
        "mode": "in_place",
        "idempotency_key": uid(),
    }

    def fault(*_):
        raise RuntimeError("simulated failure after restoring chapter and graph")

    with monkeypatch.context() as scoped:
        scoped.setattr("ainovel.work_snapshot._restore_setup", fault)
        with pytest.raises(RuntimeError, match="simulated failure"):
            restore(domain, branch, checkpoint["id"], RestoreInput(**params))
    assert domain.chapter(chapter_id) == changed
    assert len(verify(client.get(route(branch)))) == 1
    completed = verify(client.post(location + "/restore", json=params), 201)
    assert verify(client.post(location + "/restore", json=params), 201) == completed
    assert len(verify(client.get(route(branch)))) == 2


@pytest.mark.parametrize("change_body", [False, True])
def test_partial_volume_leaves_other_plan_and_manuscript_untouched(
    snapshot_client,
    domain,
    story,
    change_body,
):
    client = snapshot_client
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "第一卷旧稿。")
    second = domain.create_chapter(branch, "第二卷第一章")
    second = domain.save(second["id"], 0, "第二卷旧稿。")
    setting = setting_confirmation(client, branch)
    graph_route = f"/api/branches/{branch}/story-records"
    recorded = verify(
        client.post(
            graph_route,
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "record": {
                    "kind": "character",
                    "title": "卷一学徒",
                    "sources": [
                        {
                            "chapter_id": first["id"],
                            "revision": first["revision"],
                            "start": 0,
                            "end": len(first["content"]),
                            "quote": first["content"],
                        }
                    ],
                },
            },
        ),
        201,
    )["records"][0]

    def volume(identifier, chapter_id, goal):
        return {
            "id": identifier,
            "title": identifier,
            "summary": goal,
            "units": [
                {
                    "id": f"{identifier}-unit",
                    "title": goal,
                    "summary": goal,
                    "chapters": [chapter_plan(chapter_id, goal)],
                }
            ],
        }

    original = {
        "summary": "旧总纲",
        "volumes": [volume("v1", first["id"], "旧目标"), volume("v2", second["id"], "旧续写")],
    }
    confirmed = put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": 0,
            "confirmation_id": setting,
            "plan": original,
        },
    )["confirmed"]
    saved = verify(
        client.post(
            route(branch),
            json={
                "label": "第一卷检查点",
                "scope": "volume",
                "scope_id": "v1",
            },
        ),
        201,
    )
    if change_body:
        domain.save(first["id"], first["revision"], "第一卷新稿。")
    newer_second = domain.save(second["id"], second["revision"], "第二卷保留的新稿。")
    edited = {
        "summary": "主线已有发展",
        "volumes": [volume("v1", first["id"], "改过目标"), volume("v2", second["id"], "当下第二卷")],
    }
    put_and_confirm(
        client,
        outline(branch),
        {
            "base_revision": confirmed["revision"],
            "confirmation_id": setting,
            "plan": edited,
        },
    )
    inspection_route = f"/api/branches/{branch}/workflow-inspections"
    scope = {"scope": "volume", "scope_id": "v2"}
    preview = verify(client.get(inspection_route, params=scope))["preview"]
    verify(client.post(inspection_route, json={**scope, "basis_sha256": preview["basis_sha256"]}), 201)
    request_restore(client, branch, saved["id"])
    assert domain.chapter(first["id"])["content"] == first["content"]
    assert domain.chapter(second["id"])["content"] == newer_second["content"]
    assert domain.chapter(second["id"])["revision"] == newer_second["revision"]
    view = verify(client.get(outline(branch)))
    assert view["confirmed"]["plan"]["summary"] == "主线已有发展"
    assert view["confirmed"]["plan"]["volumes"] == [original["volumes"][0], edited["volumes"][1]]
    assert view["confirmed"]["setting_basis"]["confirmation_id"] == setting
    assert verify(client.get(inspection_route, params=scope))["latest"]["current"] is not change_body
    restored = graph(domain, branch)["records"][0]
    assert restored["id"] == recorded["id"]
    assert restored["revision"] > recorded["revision"]
    assert not restored["invalid_reasons"]
    assert restored["payload"]["sources"][0]["revision"] == domain.chapter(first["id"])["revision"]
