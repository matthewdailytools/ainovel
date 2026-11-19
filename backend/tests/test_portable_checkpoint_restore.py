from contextlib import contextmanager
from dataclasses import replace

from fastapi.testclient import TestClient

from ainovel import work_snapshot
from ainovel.api import create_app
from ainovel.conception import ConceptionItem, _branch, _persist, _workspace
from ainovel.db import DomainError, uid
from test_manuscript_backup import BACKUP_IMPORT_PATH, BINARY
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_work_snapshot import outline, route, save_project, verify
from test_workflow_inspection import author, report, source


@contextmanager
def imported_work(client, settings, branch, target):
    backup = client.get(f"/api/branches/{branch}/manuscript-backup")
    assert backup.status_code == 200, backup.text
    with TestClient(create_app(replace(settings, data_dir=target))) as independent:
        independent.headers["X-Ainovel-CSRF"] = independent.get("/api/session").json()["csrf"]
        verify(independent.post(BACKUP_IMPORT_PATH, headers=BINARY, content=backup.content), 201)
        yield independent


def archive_sample(client, domain, story, *, cross_volume=False, mixed_story_cycle=False):
    branch = story["branch_id"]
    path = f"/api/branches/{branch}"
    project = save_project(client, branch, 0, "铃归师姐，渡口重开。")
    directory = [story["chapter_id"]] + [
        domain.create_chapter(branch, title)["id"] for title in ("二", "三", "四")
    ]
    first, second, third, last = [
        domain.save(identifier, 0, content)
        for identifier, content in zip(
            directory, ("门内📯响了。", "学徒向师姐点头。", "师姐经过渡口。", "还铃。"), strict=True
        )
    ]
    with domain.db.connect(True) as tx:
        branch_row = _branch(tx, branch)
        workspace = _workspace(tx, branch)
        next(row for row in workspace["categories"] if row["id"] == "world")["items"] = [
            ConceptionItem(id="bell", title="门内铃声", detail="铃只在门内响。").model_dump(exclude_none=True)
        ]
        workspace["categories"].append(
            {
                "id": "notes",
                "title": "回读资料",
                "skipped": False,
                "items": [
                    ConceptionItem(id="proof-note", title="原文线索", detail="记录原稿中的号角。").model_dump(
                        exclude_none=True
                    )
                ],
            }
        )
        _persist(
            tx,
            branch_row,
            workspace,
            workspace["categories"],
            {"proof-note": {"origin": "author", "evidence": source(first)}},
        )
    adopted = verify(
        client.post(
            path + "/conception/confirm",
            json={
                "base_revision": 1,
                "base_story_revision": domain.branch(branch)["story_revision"],
                "idempotency_key": uid(),
            },
        )
    )["confirmation"]
    record_path = path + "/story-records"
    outside = None
    if mixed_story_cycle:
        outside = verify(
            client.post(
                record_path,
                json={
                    "base_story_revision": domain.branch(branch)["story_revision"],
                    "record": {
                        "kind": "event",
                        "title": "卷外目击",
                        "status": "actual",
                        "sources": [source(last)],
                    },
                },
            ),
            201,
        )["records"][0]
    rows = verify(
        client.post(
            record_path,
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "record": {
                    "kind": "event",
                    "title": "听见铃响",
                    "status": "actual",
                    "sources": [source(first)],
                    "depends_on": [outside["id"]] if outside else [],
                },
            },
        ),
        201,
    )["records"]
    actual = next(row for row in rows if row["payload"]["title"] == "听见铃响")
    promised = verify(
        client.post(
            record_path,
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "record": {"kind": "promise", "title": "准时还铃", "stages": [{"status": "planned"}]},
            },
        ),
        201,
    )["records"]
    promise_id = next(row["id"] for row in promised if row["kind"] == "promise")
    return_at = last["id"] if cross_volume else second["id"]
    plan = {
        "summary": "闭合渡口传说",
        "volumes": [
            {
                "id": f"vol-{scope}",
                "title": f"卷 {scope}",
                "summary": "书内和渡口",
                "units": [
                    {
                        "id": f"unit-{scope}",
                        "title": "过渡",
                        "summary": "还铃路线",
                        "chapters": [chapter_plan(chapter, "归还铃铛") for chapter in chapters],
                    }
                ],
            }
            for scope, chapters in enumerate((directory[:2], directory[2:]), 1)
        ],
        "promises": [
            {
                "promise_id": promise_id,
                "plant_chapter_id": first["id"],
                "opens_chapter_id": return_at,
                "closes_chapter_id": return_at,
                "fulfillment": "author_required",
            }
        ],
    }
    saved = verify(
        client.put(outline(branch), json={"base_revision": 0, "confirmation_id": adopted["id"], "plan": plan})
    )
    verify(
        client.post(
            outline(branch) + "/confirm",
            json={"base_revision": saved["draft"]["revision"], "acknowledge_required_promises": True},
        )
    )
    verify(
        client.put(
            path + "/workflow-inspections/length-targets",
            json={"scope": "volume", "scope_id": "vol-1", "targets": {"min_chars": 3}},
        )
    )
    snapshots = {
        scope: verify(
            client.post(route(branch), json={"scope": scope, "scope_id": scope_id, "label": f"旧稿-{scope}"}),
            201,
        )
        for scope, scope_id in (("volume", "vol-1"), ("book", None))
    }
    changed = domain.save(first["id"], first["revision"], "新稿里，铃还没有响。")
    verify(
        client.put(
            f"{record_path}/{actual['id']}",
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "base_revision": actual["revision"],
                "record": {
                    "kind": "event",
                    "title": "后来另起的情节",
                    "status": "actual",
                    "sources": [source(changed)],
                },
            },
        )
    )
    if outside:
        verify(
            client.put(
                f"{record_path}/{outside['id']}",
                json={
                    "base_story_revision": domain.branch(branch)["story_revision"],
                    "base_revision": outside["revision"],
                    "record": {**outside["payload"], "depends_on": [actual["id"]]},
                },
            )
        )
    return snapshots, project, directory, first, actual


def chapter(client, identifier):
    return verify(client.get(f"/api/chapters/{identifier}"))


def test_archive_volume_preserves_the_other_volumes_formal_settings_binding(
    client, domain, story, settings, tmp_path
):
    branch = story["branch_id"]
    first = story["chapter_id"]
    second = domain.create_chapter(branch, "卷外章")["id"]
    plan = {
        "summary": "两卷各保留自己的正式依据。",
        "volumes": [
            {
                "id": f"vol-{position}",
                "title": f"卷 {position}",
                "summary": "保留原来安排。",
                "units": [
                    {
                        "id": f"unit-{position}",
                        "title": "保管失物",
                        "summary": "保留卷内的章纲。",
                        "chapters": [{**chapter_plan(identifier, "原目标"), "setting_ids": []}],
                    }
                ],
            }
            for position, identifier in enumerate((first, second), 1)
        ],
    }
    endpoint = outline(branch)
    initial = put_and_confirm(client, endpoint, {"base_revision": 0, "plan": plan})["confirmed"]
    assert initial["setting_basis"]["confirmation_id"] is None
    settings_id = setting_confirmation(client, branch)
    point = verify(
        client.post(route(branch), json={"scope": "volume", "scope_id": "vol-1", "label": "旧卷纲"}), 201
    )
    plan["volumes"][0]["units"][0]["chapters"][0]["goal"] = "新目标"
    outside = plan["volumes"][1]["units"][0]["chapters"][0]
    outside["setting_ids"] = ["bell"]
    put_and_confirm(
        client,
        endpoint,
        {"base_revision": initial["revision"], "plan": plan, "confirmation_id": settings_id},
    )

    archive = f"/api/branches/{branch}/portable-checkpoints/{point['id']}"
    with imported_work(client, settings, branch, tmp_path / "unchanged-settings-binding") as target:
        current = verify(target.get(endpoint))["confirmed"]
        setting = current["setting_basis"]["confirmation_id"]
        assert setting
        preview = verify(target.get(archive + "/restore-preview"))
        assert preview["in_place_blockers"] == []
        verify(
            target.post(
                archive + "/restore",
                json={"basis_sha256": preview["basis_sha256"], "idempotency_key": uid()},
            ),
            201,
        )
        recovered = verify(target.get(endpoint))["confirmed"]
        assert recovered["setting_basis"]["confirmation_id"] == setting
        assert recovered["plan"]["volumes"][1] == current["plan"]["volumes"][1]
        assert recovered["plan"]["volumes"][0]["units"][0]["chapters"][0]["goal"] == "原目标"


def test_archive_volume_restore_is_local_atomic_and_preserves_exact_history(
    client, domain, story, settings, tmp_path, monkeypatch
):
    branch = story["branch_id"]
    path = f"/api/branches/{branch}"
    snapshots, project, chapters, first, actual = archive_sample(client, domain, story)
    archive = f"{path}/portable-checkpoints/{snapshots['volume']['id']}"
    with imported_work(client, settings, branch, tmp_path / "restored-volume") as target:
        before = chapter(target, first["id"])
        inspection = path + "/workflow-inspections"
        old_target = verify(target.get(inspection, params={"scope": "volume", "scope_id": "vol-1"}))[
            "preview"
        ]["length_target"]
        assert old_target["targets"]["min_chars"] == 3
        verify(
            target.put(
                inspection + "/length-targets",
                json={
                    "scope": "volume",
                    "scope_id": "vol-1",
                    "base_target_id": old_target["id"],
                    "targets": {"min_chars": 20},
                },
            )
        )
        reviewed = verify(target.get(inspection, params={"scope": "chapter", "scope_id": first["id"]}))[
            "preview"
        ]
        signed = report(target, inspection, reviewed, author_reviews=[author("02", before)])
        preview = verify(target.get(archive + "/restore-preview"))
        assert not preview["in_place_blockers"]
        assert [row["chapter_id"] for row in preview["changes"]] == chapters[:2]
        assert any(row["id"] == signed["id"] for row in preview["affected_reports"])
        audit = verify(target.get(route(branch)))

        outside = chapter(target, chapters[-1])
        outside_new = verify(
            target.put(
                f"/api/chapters/{outside['id']}",
                json={"base_revision": outside["revision"], "content": "外卷作者另存。"},
            )
        )
        payload = {"basis_sha256": preview["basis_sha256"], "idempotency_key": uid()}
        assert verify(target.post(archive + "/restore", json=payload), 409)["code"] == "snapshot_changed"
        assert verify(target.get(route(branch))) == audit
        payload["basis_sha256"] = verify(target.get(archive + "/restore-preview"))["basis_sha256"]

        def inject(*args, **kwargs):
            raise DomainError("fixture_write", "模拟正文和故事写入后的故障。", 409)

        with monkeypatch.context() as patch:
            patch.setattr(work_snapshot, "_restore_outline", inject)
            assert verify(target.post(archive + "/restore", json=payload), 409)["code"] == "fixture_write"
        assert chapter(target, first["id"]) == before
        assert verify(target.get(route(branch))) == audit
        outcome = verify(target.post(archive + "/restore", json=payload), 201)
        assert outcome["branch_id"] == branch
        assert verify(target.post(archive + "/restore", json=payload), 201) == outcome
        assert (
            verify(target.post(archive + "/restore", json={**payload, "basis_sha256": "a" * 64}), 409)["code"]
            == "idempotency"
        )
        recovered = chapter(target, first["id"])
        assert recovered["revision"] > before["revision"] and recovered["content"] == first["content"]
        untouched = chapter(target, outside["id"])
        assert (untouched["content"], untouched["revision"]) == (
            outside_new["content"],
            outside_new["revision"],
        )
        assert verify(target.get(path + "/conception/project-brief"))["brief"] == project["brief"]
        reapplied = next(
            row for row in verify(target.get(path + "/story-graph"))["records"] if row["id"] == actual["id"]
        )
        assert reapplied["payload"]["title"] == actual["payload"]["title"]
        assert reapplied["payload"]["sources"][0]["revision"] == recovered["revision"]
        assert (
            verify(target.get(f"{route(branch)}/{outcome['recovery_snapshot_id']}/chapters/{first['id']}"))[
                "snapshot"
            ]["content"]
            == before["content"]
        )
        assert not verify(target.get(f"{inspection}/{signed['id']}"))["current"]
        sizing = verify(target.get(inspection, params={"scope": "volume", "scope_id": "vol-1"}))["preview"]
        assert sizing["length_target"]["targets"]["min_chars"] == 3
        assert target.get(path + "/manuscript-backup").is_success


def test_archive_cannot_restore_a_cross_volume_promise_but_can_restore_the_complete_book(
    client, domain, story, settings, tmp_path
):
    branch = story["branch_id"]
    path = f"/api/branches/{branch}"
    snapshots, project, _, first, _ = archive_sample(client, domain, story, cross_volume=True)
    archives = path + "/portable-checkpoints"
    with imported_work(client, settings, branch, tmp_path / "restored-book") as target:
        vol = f"{archives}/{snapshots['volume']['id']}"
        refusal = verify(target.get(vol + "/restore-preview"))
        assert any("伏笔窗口跨越本卷" in note for note in refusal["in_place_blockers"])
        refused = verify(
            target.post(
                vol + "/restore", json={"basis_sha256": refusal["basis_sha256"], "idempotency_key": uid()}
            ),
            409,
        )
        assert refused["code"] == "snapshot_restore_scope"
        current = verify(target.get(path + "/conception/project-brief"))
        current = save_project(target, branch, current["revision"], "铜铃由渡口故事馆暂管。")
        added = verify(target.post(path + "/chapters", json={"title": "后来添加的章节"}), 201)
        book = f"{archives}/{snapshots['book']['id']}"
        preview = verify(target.get(book + "/restore-preview"))
        assert not preview["in_place_blockers"]
        assert (
            "project_brief" in preview["shared_changes"]["definitions"]
            and preview["shared_changes"]["conception"]
        )
        assert (
            next(row for row in preview["changes"] if row["chapter_id"] == added["id"])["action"] == "archive"
        )
        applied = verify(
            target.post(
                book + "/restore", json={"basis_sha256": preview["basis_sha256"], "idempotency_key": uid()}
            ),
            201,
        )
        assert applied["branch_id"] == branch
        reinstated = verify(target.get(path + "/conception/project-brief"))
        assert reinstated["revision"] > current["revision"]
        assert reinstated["brief"] == project["brief"]
        revised = chapter(target, first["id"])
        assert revised["content"] == first["content"]
        adopted = verify(target.get(path + "/conception"))
        assert adopted["confirmation"]["current"]
        memo = next(row for row in adopted["sources"] if row["item_id"] == "proof-note")
        assert memo["current"] and memo["evidence"]["revision"] == revised["revision"]
        package = verify(target.get(path + "/manuscript-package"))
        assert next(row for row in package["chapters"] if row["id"] == added["id"])["archived"] == 1
        restored_plan = verify(target.get(outline(branch)))["confirmed"]["plan"]
        assert restored_plan["promises"][0]["fulfillment"] == "author_required"
        assert (
            restored_plan["promises"][0]["plant_chapter_id"]
            != restored_plan["promises"][0]["closes_chapter_id"]
        )


def test_mixing_two_individually_valid_graphs_cannot_create_a_cross_volume_cycle(
    client, domain, story, settings, tmp_path
):
    branch = story["branch_id"]
    snapshots, _, _, first, _ = archive_sample(client, domain, story, mixed_story_cycle=True)
    archive = f"/api/branches/{branch}/portable-checkpoints/{snapshots['volume']['id']}"
    with imported_work(client, settings, branch, tmp_path / "cycle-volume") as target:
        before = chapter(target, first["id"])
        preview = verify(target.get(archive + "/restore-preview"))
        assert any("卷外故事记录形成循环" in note for note in preview["in_place_blockers"])
        payload = {"basis_sha256": preview["basis_sha256"], "idempotency_key": uid()}
        result = verify(target.post(archive + "/restore", json=payload), 409)
        assert result["code"] == "snapshot_restore_scope"
        assert chapter(target, first["id"]) == before
