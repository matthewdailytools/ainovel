from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import httpx
import pytest

from ainovel.cloud_sync import create_sync_app
from ainovel.cloud_sync_backup import create_backup, restore_backup
from ainovel.db import Database, DomainError, uid
from ainovel.desktop_sync import DesktopSync, ForkChapter, Resolve, _headers, _network
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.outline_workspace import read_outline
from ainovel.story_graph import graph
from test_chapter_conflicts import chapter_conflict
from test_conception_workbench import confirm, path, save
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_story_sync import linked_cloud, paired
from test_work_snapshot import outline, request_restore, route, save_project, verify


def test_dependent_volume_rollback_sync_resumes_from_last_durable_pull_page(client, domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    receiver = paired(domain, branch, tmp_path / "rollback-receiver.sqlite")
    records = f"/api/branches/{branch}/story-records"

    def promise_payload(chapter, notes):
        return {
            "kind": "promise",
            "title": "铜铃交到师姐手里",
            "notes": notes,
            "stages": [
                {"status": "planned"},
                {
                    "status": "resolved",
                    "sources": [
                        {
                            "chapter_id": chapter_id,
                            "revision": chapter["revision"],
                            "start": 0,
                            "end": len(chapter["content"]),
                            "quote": chapter["content"],
                        }
                    ],
                },
            ],
        }

    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, cloud, endpoint, _ = linked
        project = save_project(client, branch, 0, "师姐把铃带回门内。")
        old_chapter = domain.save(chapter_id, 0, "师姐在门内接过铜铃。")
        old_setting = setting_confirmation(client, branch)
        old_record = verify(
            client.post(
                records,
                json={
                    "base_story_revision": domain.branch(branch)["story_revision"],
                    "record": promise_payload(old_chapter, "直接交还"),
                },
            ),
            201,
        )["records"][0]
        promise = old_record["id"]
        old_plan = {
            "summary": "门内归铃",
            "volumes": [
                {
                    "id": "bell-volume",
                    "title": "上卷",
                    "summary": "归还铜铃",
                    "units": [
                        {
                            "id": "return-unit",
                            "title": "第一次归还",
                            "summary": "在门内归还",
                            "chapters": [chapter_plan(chapter_id, "门内归铃")],
                        }
                    ],
                }
            ],
            "promises": [
                {
                    "promise_id": promise,
                    "plant_chapter_id": chapter_id,
                    "opens_chapter_id": chapter_id,
                    "closes_chapter_id": chapter_id,
                }
            ],
        }
        locked = put_and_confirm(
            client, outline(branch), {"base_revision": 0, "confirmation_id": old_setting, "plan": old_plan}
        )["confirmed"]
        checkpoint = verify(
            client.post(
                route(branch),
                json={"scope": "volume", "scope_id": "bell-volume", "label": "旧卷末"},
            ),
            201,
        )
        left.run(branch, origin, left_token)
        first = right.run(branch, origin, right_token)
        assert not first["conflicts"] and not first["deferred"], first
        assert graph(receiver, branch)["records"][0]["payload"]["notes"] == "直接交还"

        save_project(client, branch, project["revision"], "师姐把铃留在门外。")
        changed = domain.save(chapter_id, old_chapter["revision"], "师姐在门外接过铜铃。")
        setup = client.get(path(branch)).json()
        next(category for category in setup["categories"] if category["id"] == "world")["items"][0][
            "detail"
        ] = "本版铜铃可在门外响。"
        new_setting = confirm(client, path(branch), save(client, path(branch), setup), key=uid())[
            "confirmation"
        ]
        verify(
            client.put(
                f"{records}/{promise}",
                json={
                    "base_story_revision": domain.branch(branch)["story_revision"],
                    "base_revision": old_record["revision"],
                    "record": promise_payload(changed, "在门外归还"),
                },
            )
        )
        put_and_confirm(
            client,
            outline(branch),
            {
                "base_revision": locked["revision"],
                "confirmation_id": new_setting["id"],
                "plan": {**old_plan, "summary": "门外归铃"},
            },
        )
        left.run(branch, origin, left_token)
        before = right.run(branch, origin, right_token)
        assert receiver.chapter(chapter_id)["content"] == changed["content"]
        assert read_outline(receiver, branch)["confirmed"]["plan"]["summary"] == "门外归铃"
        assert graph(receiver, branch)["records"][0]["payload"]["notes"] == "在门外归还"

        restored = request_restore(client, branch, checkpoint["id"])
        sent = left.run(branch, origin, left_token)
        assert not sent["deferred"] and not sent["conflicts"], sent
        assert sent["cursor"] > before["cursor"] + 1

        transport = right.transport
        attempts = []

        def disconnect_between_pages(origin, token, method, target, payload, *, ledger_id=None):
            if "/pull?" in target:
                attempts.append(int(parse_qs(urlsplit(target).query)["after"][0]))
                if len(attempts) == 2:
                    raise DomainError("sync_transport", "拉取第二页前断线。", 502)
                target = target.replace("limit=50", "limit=1")
            return transport(origin, token, method, target, payload, ledger_id=ledger_id)

        right.transport = disconnect_between_pages
        with pytest.raises(DomainError, match="拉取第二页前断线"):
            right.run(branch, origin, right_token)
        assert attempts == [before["cursor"], before["cursor"] + 1]
        assert right._state(branch)["cursor"] == attempts[-1]
        # The immutable source has arrived, while the chapter adoption head has not.
        assert receiver.chapter(chapter_id)["content"] == changed["content"]
        right = DesktopSync(receiver, transport)
        final = right.run(branch, origin, right_token)
        additional = verify(
            cloud.get(
                endpoint + "/pull",
                params={"after": sent["cursor"]},
                headers={"Authorization": f"Bearer {right_token}"},
            )
        )["changes"]
        assert final["cursor"] == sent["cursor"] + len(additional)
        assert all(item["object_id"].startswith("evidence:") for item in additional)
        assert not final["conflicts"] and not final["deferred"], final

        manuscript = export_manuscript(receiver, branch)
        authored = export_manuscript(domain, branch)
        assert final["project_source"]["id"] != before["project_source"]["id"]
        assert final["settings_source"]["id"] != before["settings_source"]["id"]
        assert final["outline_source"]["id"] != before["outline_source"]["id"]
        for family in ("project_history", "settings_history", "outline_history"):
            local_history = getattr(manuscript, family)
            source_history = getattr(authored, family)
            assert local_history.adopted_revision_id == source_history.adopted_revision_id
            assert {row.id for row in local_history.versions} == {row.id for row in source_history.versions}
        project_history = manuscript.project_history
        reviewed = next(
            row for row in project_history.versions if row.id == project_history.adopted_revision_id
        )
        assert reviewed.brief.ending == project["brief"]["ending"]
        history = {chapter.content: chapter for chapter in manuscript.revisions}
        assert history[old_chapter["content"]].revision > history[changed["content"]].revision
        saved = next(row for row in graph(receiver, branch)["records"] if row["id"] == promise)
        assert saved["payload"]["notes"] == "直接交还" and not saved["invalid_reasons"]
        assert (
            saved["payload"]["stages"][-1]["sources"][0]["revision"]
            == receiver.chapter(chapter_id)["revision"]
        )
        assert read_outline(receiver, branch)["confirmed"]["plan"] == old_plan
        assert final["cursor"] == right.run(branch, origin, right_token)["cursor"]
        assert restored["outline_id"] == read_outline(domain, branch)["confirmed"]["id"]


def test_stop_and_admin_revoke_keep_local_manuscript_and_resume_after_reprovision(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    domain.save(chapter_id, 0, "共同首稿。")
    receiver = paired(domain, branch, tmp_path / "permission-receiver.sqlite")
    admin = {"Authorization": "Bearer isolated-story-sync-access-0123456789"}
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, token, cloud, endpoint, lost = linked
        author_draft = receiver.save(chapter_id, 1, "作者第一次补写。")
        lost["next"] = f"chapter:{chapter_id}"
        with pytest.raises(DomainError, match="响应中断"):
            right.run(branch, origin, token)
        unknown = lost["receipt"]
        before = right._state(branch)
        assert before["pending"]
        stopped = right.disable(branch)
        assert not stopped["enabled"] and stopped["pending"] == before["pending"]
        transport = right.transport
        right = DesktopSync(receiver, transport)
        with pytest.raises(DomainError) as locally_blocked:
            right.run(branch, origin, token)
        assert locally_blocked.value.code == "sync_binding"
        assert right._state(branch)["cursor"] == stopped["cursor"]

        assert cloud.delete(endpoint + "/devices/story-right", headers=admin).status_code == 204
        old_credential = {"Authorization": f"Bearer {token}"}
        for subpath in ("/pull", "/conflicts", "/attachments", f"/receipts/{unknown['change_id']}"):
            assert cloud.get(endpoint + subpath, headers=old_credential).status_code == 401
        denied = cloud.post(
            endpoint + "/push",
            headers=old_credential,
            json={
                "change_id": "revoked-device-write",
                "object_id": f"chapter:{chapter_id}",
                "parent_revision": unknown["head_revision"],
                "payload": None,
                "tombstone": True,
            },
        )
        assert denied.status_code == 401
        with pytest.raises(DomainError) as revoked:
            right.enable(branch, origin, token)
        assert revoked.value.code == "sync_auth"
        assert not right._state(branch)["enabled"]

        late = receiver.save(chapter_id, author_draft["revision"], "停用期间继续写下一稿。")
        isolated = Domain(Database(tmp_path / "independently-restored.sqlite"))
        import_manuscript(isolated, export_manuscript(receiver, branch))
        assert isolated.chapter(chapter_id)["content"] == late["content"]
        assert not DesktopSync(isolated, transport)._state(branch)["enabled"]

        replacement = verify(
            cloud.post(endpoint + "/devices", headers=admin, json={"device_id": "story-right"}), 201
        )["token"]
        assert replacement != token
        assert right.enable(branch, origin, replacement)["cursor"] == stopped["cursor"]
        assert not right.run(branch, origin, replacement)["conflicts"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert domain.chapter(chapter_id)["content"] == late["content"]
        recovered = verify(
            cloud.get(
                endpoint + f"/receipts/{unknown['change_id']}",
                headers={"Authorization": f"Bearer {replacement}"},
            )
        )
        assert recovered == unknown
        assert not right._state(branch)["pending"]


def test_restored_cloud_cannot_rebind_desktop_at_same_cursor_or_replay_old_conflict(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    provisioning = "isolated-story-sync-access-0123456789"
    archive = tmp_path / "prior-cloud-copy.sqlite"
    restored = tmp_path / "restored-cloud.sqlite"
    with chapter_conflict(domain, story, tmp_path) as (receiver, linked, compared, conflict_id, stamp):
        left, right, origin, left_token, token, old_cloud, endpoint, lost = linked
        comparison_path = f"{endpoint}/conflicts/{conflict_id}"
        backup_source = tmp_path / "story-cloud.sqlite"
        original_checkpoint = verify(old_cloud.get(endpoint + "/checkpoint", headers=_headers(token)))
        create_backup(backup_source, archive)
        clean_copy = export_manuscript(domain, branch)

        newer = domain.create_chapter(branch, "只在原服务提交的章节")
        domain.save(newer["id"], 0, "原服务备份后先推进。")
        left.run(branch, origin, left_token)
        durable = right.run(branch, origin, token)
        assert durable["cursor"] > original_checkpoint["cursor"]
        pending = receiver.create_chapter(branch, "等待回执的本机章节")
        pending_text = receiver.save(pending["id"], 0, "这份本机正文必须保留。")["content"]
        lost["next"] = f"chapter:{pending['id']}"
        with pytest.raises(DomainError, match="响应中断"):
            right.run(branch, origin, token)
        uncertain = lost["receipt"]
        before = right._state(branch)
        assert before["cursor"] == durable["cursor"] and before["pending"] and before["conflicts"]

        def local_sync_rows():
            with receiver.db.connect() as tx:
                return {
                    table: [tuple(row) for row in tx.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in (
                        "desktop_sync",
                        "desktop_sync_ledgers",
                        "desktop_sync_conflicts",
                        "desktop_sync_objects",
                        "desktop_sync_dirty",
                        "desktop_sync_outbox",
                        "desktop_sync_prose_merges",
                    )
                }

        rows_before = local_sync_rows()
        assert rows_before["desktop_sync_outbox"]
        restore_backup(archive, restored)
        with TestClient(create_sync_app(restored, provisioning)) as replacement:
            assert replacement.get(endpoint + "/checkpoint", headers=_headers(token)).status_code == 401

            def grant(name):
                return verify(
                    replacement.post(
                        endpoint + "/devices", headers=_headers(provisioning), json={"device_id": name}
                    ),
                    201,
                )["token"]

            reissued = grant("story-right")
            new_token = grant("recovered-desktop")
            replacement_checkpoint = verify(
                replacement.get(endpoint + "/checkpoint", headers=_headers(reissued))
            )
            assert replacement_checkpoint["cursor"] == original_checkpoint["cursor"] < before["cursor"]
            assert replacement_checkpoint["ledger_id"] != original_checkpoint["ledger_id"]

            requests = []

            def replaced_transport(sync_origin, credential, method, target, payload, *, ledger_id=None):
                requests.append((method, target, ledger_id))
                response = replacement.request(
                    method, target, headers=_headers(credential, ledger_id), json=payload
                )
                if not response.is_success:
                    error = response.json()
                    raise DomainError(error["code"], error["message"], response.status_code)
                return response.json()

            fresh = Domain(Database(tmp_path / "fresh-desktop.sqlite"))
            import_manuscript(fresh, clean_copy)
            unbound = DesktopSync(fresh, replaced_transport)
            assert unbound._state(branch)["ledger_id"] is None
            assert (
                unbound.enable(branch, origin, new_token)["ledger_id"] == replacement_checkpoint["ledger_id"]
            )
            assert unbound.run(branch, origin, new_token)["cursor"] == original_checkpoint["cursor"]
            assert (
                fresh.chapter(chapter)["content"]
                == compared["remote"]["current"]["payload"]["head"]["content"]
            )
            different = fresh.create_chapter(branch, "恢复后另一份独立章节")
            fresh.save(different["id"], 0, "数字相同，提交历史不同。")
            caught_up = unbound.run(branch, origin, new_token)
            assert caught_up["cursor"] == before["cursor"]

            old = DesktopSync(receiver, replaced_transport)
            merge = Resolve(
                **{**stamp, "token": reissued},
                choice="merge",
                content="先前比较编辑出的稿件。",
                idempotency_key="old-comparison-merge",
            )
            fork = ForkChapter(
                **{**stamp, "token": reissued},
                choice="incoming",
                branch_name="试图引用旧账本",
                idempotency_key="old-conflict-fork",
            )
            requests.clear()
            for operation in (
                lambda: old.enable(branch, origin, reissued),
                lambda: old.run(branch, origin, reissued),
                lambda: old.compare(branch, origin, reissued, conflict_id),
                lambda: old.resolve(branch, origin, reissued, conflict_id, merge),
                lambda: old.fork(branch, origin, reissued, conflict_id, fork),
            ):
                with pytest.raises(DomainError) as rejected:
                    operation()
                assert (rejected.value.code, rejected.value.status) == ("sync_ledger_replaced", 409)
            assert requests == [("GET", endpoint + "/checkpoint", None)] * 5

            assert old._state(branch) == before and local_sync_rows() == rows_before
            assert receiver.chapter(chapter)["content"] == compared["local"]["head"]["content"]
            assert receiver.chapter(pending["id"])["content"] == pending_text
            assert {revision.content for revision in export_manuscript(receiver, branch).revisions} >= {
                pending_text,
                compared["local"]["head"]["content"],
            }
            replacement_header = _headers(reissued, replacement_checkpoint["ledger_id"])
            remote = verify(replacement.get(comparison_path, headers=replacement_header))
            assert remote["resolved_by"] is None and remote["head_revision"] == stamp["parent_revision"]
            assert (
                verify(replacement.get(endpoint + "/checkpoint", headers=replacement_header))["cursor"]
                == before["cursor"]
            )
            receipt = endpoint + f"/receipts/{uncertain['change_id']}"
            assert replacement.get(receipt, headers=replacement_header).status_code == 404
            assert verify(old_cloud.get(receipt, headers=_headers(token))) == uncertain

            def replace_after_checkpoint(sync_origin, credential, method, target, payload, *, ledger_id=None):
                if target == endpoint + "/checkpoint":
                    return right.transport(sync_origin, token, method, target, payload, ledger_id=ledger_id)
                return replaced_transport(
                    sync_origin, credential, method, target, payload, ledger_id=ledger_id
                )

            requests.clear()
            guarded = DesktopSync(receiver, replace_after_checkpoint)
            for operation in (
                lambda: guarded.run(branch, origin, reissued),
                lambda: guarded.compare(branch, origin, reissued, conflict_id),
                lambda: guarded.resolve(branch, origin, reissued, conflict_id, merge),
            ):
                with pytest.raises(DomainError) as rejected:
                    operation()
                assert (rejected.value.code, rejected.value.status) == ("sync_ledger_replaced", 409)
            old_id = original_checkpoint["ledger_id"]
            assert requests == [
                ("GET", endpoint + f"/pull?after={before['cursor']}&limit=50", old_id),
                ("GET", comparison_path, old_id),
                ("GET", comparison_path, old_id),
            ]
            assert local_sync_rows() == rows_before


def test_desktop_binding_without_checkpoint_identity_is_not_reused(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    domain.save(chapter, 0, "仍可从本机导出的原文。")
    receiver = paired(domain, branch, tmp_path / "older-desktop.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        _, original, origin, _, token, *_ = linked
        with receiver.db.connect(True) as tx:
            tx.execute("DELETE FROM desktop_sync_ledgers WHERE branch_id=?", (branch,))
        legacy = DesktopSync(receiver, original.transport)
        before = legacy._state(branch)
        assert before["bound"] and before["ledger_id"] is None
        for operation in (legacy.run, legacy.enable):
            with pytest.raises(DomainError) as rejected:
                operation(branch, origin, token)
            assert (rejected.value.code, rejected.value.status) == ("sync_ledger_replaced", 409)
        assert legacy._state(branch) == before
        assert receiver.chapter(chapter)["content"] == "仍可从本机导出的原文。"
        assert not legacy.disable(branch)["enabled"]


@pytest.mark.parametrize(
    ("remote_code", "status", "expected_code", "keep_pending"),
    [
        ("sync_history", 409, "sync_history", False),
        ("sync_history", 502, "sync_remote", True),
        ("sync_auth", 401, "sync_auth", True),
        ("sync_ledger_replaced", 409, "sync_ledger_replaced", True),
    ],
)
def test_native_http_distinguishes_rejected_merge_from_uncertain_receipt(
    domain, story, tmp_path, monkeypatch, remote_code, status, expected_code, keep_pending
):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    with chapter_conflict(domain, story, tmp_path) as (receiver, linked, compared, conflict_id, stamp):
        _, right, origin, _, token, *_ = linked
        transport = right.transport
        client = httpx.Client

        def mock_request(request):
            assert request.url.path.endswith("/resolve")
            assert request.headers["X-Ainovel-Ledger"] == right._state(branch)["ledger_id"]
            return httpx.Response(status, json={"code": remote_code, "message": "private remote text"})

        monkeypatch.setattr(
            "ainovel.desktop_sync.httpx.Client",
            lambda **options: client(transport=httpx.MockTransport(mock_request), **options),
        )

        def reject_resolution(origin, token, method, path, payload, *, ledger_id=None):
            send = _network if path.endswith("/resolve") else transport
            return send(origin, token, method, path, payload, ledger_id=ledger_id)

        right.transport = reject_resolution
        merge = Resolve(**stamp, choice="merge", content="确认合并后的正文。", idempotency_key="http-merge")
        with pytest.raises(DomainError) as failed:
            right.resolve(branch, origin, token, conflict_id, merge)
        assert failed.value.code == expected_code
        assert failed.value.status == status
        assert "private remote text" not in failed.value.message
        assert receiver.chapter(chapter_id)["content"] == compared["local"]["head"]["content"]
        with receiver.db.connect() as tx:
            pending = tx.execute(
                "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?", (branch, "http-merge")
            ).fetchone()
        assert bool(pending) == keep_pending
        right.transport = transport
        assert right.resolve(branch, origin, token, conflict_id, merge)["merge_receipt"]["local_revision_id"]
        assert receiver.chapter(chapter_id)["content"] == merge.content
