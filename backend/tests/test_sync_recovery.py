from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ainovel.db import Database, DomainError, uid
from ainovel.desktop_sync import DesktopSync, Resolve, _network
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

        def disconnect_between_pages(origin, token, method, target, payload):
            if "/pull?" in target:
                attempts.append(int(parse_qs(urlsplit(target).query)["after"][0]))
                if len(attempts) == 2:
                    raise DomainError("sync_transport", "拉取第二页前断线。", 502)
                target = target.replace("limit=50", "limit=1")
            return transport(origin, token, method, target, payload)

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


@pytest.mark.parametrize(
    ("remote_code", "status", "expected_code", "keep_pending"),
    [
        ("sync_history", 409, "sync_history", False),
        ("sync_history", 502, "sync_remote", True),
        ("sync_auth", 401, "sync_auth", True),
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
            return httpx.Response(status, json={"code": remote_code, "message": "private remote text"})

        monkeypatch.setattr(
            "ainovel.desktop_sync.httpx.Client",
            lambda **options: client(transport=httpx.MockTransport(mock_request), **options),
        )
        right.transport = lambda origin, token, method, path, payload: (
            _network(origin, token, method, path, payload)
            if path.endswith("/resolve")
            else transport(origin, token, method, path, payload)
        )
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
