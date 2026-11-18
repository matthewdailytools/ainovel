from fastapi.testclient import TestClient
import pytest

from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve, _headers
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript


def test_two_independent_sqlite_workspaces_sync_and_keep_competing_revisions(client, domain, story, tmp_path):
    chapter_id, branch = story["chapter_id"], story["branch_id"]
    domain.save(chapter_id, 0, "拾起铜铃。")
    other = Domain(Database(tmp_path / "other.sqlite"))
    baseline = export_manuscript(domain, branch)
    import_manuscript(other, baseline)
    cold = Domain(Database(tmp_path / "cold.sqlite"))
    import_manuscript(cold, baseline.model_copy(update={"chapters": [], "revisions": []}))
    cloud = create_sync_app(tmp_path / "cloud.sqlite", "provision-for-tests-only-abcdefgh")
    lost = {"after_push": False}
    after_head = {"save": False}
    origin = "http://127.0.0.1:8888"
    path = f"/api/sync/works/{branch}"
    with TestClient(cloud) as service:

        def grant(device):
            response = service.post(
                path + "/devices",
                json={"device_id": device},
                headers={"Authorization": "Bearer provision-for-tests-only-abcdefgh"},
            )
            assert response.status_code == 201, response.text
            return response.json()["token"]

        def transport(origin, token, method, target, payload, *, ledger_id=None):
            response = service.request(method, target, headers=_headers(token, ledger_id), json=payload)
            if not response.is_success:
                raise DomainError(response.json()["code"], response.json()["message"], response.status_code)
            if (
                target.endswith("/push")
                and payload["object_id"] == f"chapter:{chapter_id}"
                and after_head["save"]
            ):
                after_head["save"] = False
                domain.save(chapter_id, domain.chapter(chapter_id)["revision"], "推送发出后又补写一笔。")
            if target.endswith("/push") and lost["after_push"]:
                lost["after_push"] = False
                raise DomainError("sync_transport", "测试：响应丢失。", 502)
            return response.json()

        left, right = DesktopSync(domain, transport), DesktopSync(other, transport)
        cold_sync = DesktopSync(cold, transport)
        left_token, right_token = grant("desktop-one"), grant("desktop-two")
        cold_token = grant("desktop-cold")
        assert client.get(f"/api/branches/{branch}/desktop-sync").json()["enabled"] is False
        left.enable(branch, origin, left_token)
        right.enable(branch, origin, right_token)
        lost["after_push"] = True
        with pytest.raises(DomainError, match="响应丢失"):
            left.run(branch, origin, left_token)
        assert left._state(branch)["pending"] == 4
        assert left.run(branch, origin, left_token)["cursor"] == 4
        assert right.run(branch, origin, right_token)["cursor"] == 4
        assert left._state(branch)["pending"] == 0
        with pytest.raises(RuntimeError, match="rollback marker"):
            with domain.db.connect(True) as tx:
                tx.execute("UPDATE chapters SET title=? WHERE id=?", ("事务中止的标题", chapter_id))
                assert tx.execute(
                    "SELECT 1 FROM desktop_sync_dirty WHERE branch_id=? AND object_id=?",
                    (branch, f"chapter:{chapter_id}"),
                ).fetchone()
                raise RuntimeError("rollback marker")
        assert left._state(branch)["pending"] == 0
        assert domain.chapter(chapter_id)["title"] != "事务中止的标题"
        assert domain.chapter(chapter_id)["content"] == other.chapter(chapter_id)["content"]
        inspection = f"/api/branches/{branch}/workflow-inspections"
        preview = client.get(inspection).json()["preview"]
        recorded = client.post(inspection, json={"basis_sha256": preview["basis_sha256"]})
        assert recorded.status_code == 201, recorded.text
        assert left.run(branch, origin, left_token)["cursor"] == 6
        assert right.run(branch, origin, right_token)["cursor"] == 6
        assert right.run(branch, origin, right_token)["cursor"] == 6
        copied = export_manuscript(other, branch).evidence
        assert copied[0].id == recorded.json()["id"]
        assert copied[0].chapters[0].revision == 1

        domain.save(chapter_id, 1, "师姐拾起铜铃。")
        other.save(chapter_id, 1, "学徒拾起铜铃。")
        assert left._state(branch)["pending"] == right._state(branch)["pending"] == 2
        left.run(branch, origin, left_token)
        second = export_manuscript(domain, branch)
        cold_sync.enable(branch, origin, cold_token)
        cold_sync.run(branch, origin, cold_token)
        assert export_manuscript(cold, branch).revisions == second.revisions
        conflict = right.run(branch, origin, right_token)["conflicts"][0]
        assert other.chapter(chapter_id)["content"] == "学徒拾起铜铃。"
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["current"]["payload"]["head"]["content"] == "师姐拾起铜铃。"
        assert compared["remote"]["incoming"]["payload"]["head"]["content"] == "学徒拾起铜铃。"
        result = right.resolve(
            branch,
            origin,
            right_token,
            conflict["id"],
            Resolve(
                origin=origin,
                token=right_token,
                choice="current",
                parent_revision=compared["remote"]["head_revision"],
                local_sha256=compared["local_sha256"],
                idempotency_key="old-bell-resolution",
            ),
        )
        assert not result["conflicts"]
        assert other.chapter(chapter_id)["content"] == "师姐拾起铜铃。"
        assert (
            other.db.one("SELECT content FROM revisions WHERE chapter_id=? AND revision=2", (chapter_id,))[
                "content"
            ]
            == "学徒拾起铜铃。"
        )
        assert left.run(branch, origin, left_token)["cursor"] == result["cursor"]
        author_sources = {row.content: row for row in export_manuscript(other, branch).source_revisions}
        assert author_sources["师姐拾起铜铃。"] == second.revisions[-1]
        assert other.chapter(chapter_id)["revision"] == 3
        assert (
            other.db.one("SELECT content FROM revisions WHERE chapter_id=? AND revision=3", (chapter_id,))[
                "content"
            ]
            == "师姐拾起铜铃。"
        )

        route = f"/api/branches/{branch}/workflow-inspections"
        preview = client.get(route).json()["preview"]
        report = client.post(route, json={"basis_sha256": preview["basis_sha256"]})
        assert report.status_code == 201, report.text
        left.run(branch, origin, left_token)
        assert not right.run(branch, origin, right_token)["deferred"]
        received = export_manuscript(other, branch)
        evidence = next(document for document in received.evidence if document.id == report.json()["id"])
        assert evidence.chapters[0].revision == 2
        assert evidence.chapters[0].revision_id == second.revisions[-1].id
        assert evidence.chapters[0].sha256 == second.revisions[-1].sha256
        recovered = Domain(Database(tmp_path / "recovered.sqlite"))
        import_manuscript(recovered, received)
        assert export_manuscript(recovered, branch) == received

        domain.save(chapter_id, domain.chapter(chapter_id)["revision"], "第一次保存后开始推送。")
        after_head["save"] = True
        left.run(branch, origin, left_token)
        assert left._state(branch)["pending"] >= 2
        assert domain.chapter(chapter_id)["content"] == "推送发出后又补写一笔。"
        left.run(branch, origin, left_token)
        right.run(branch, origin, right_token)
        assert other.chapter(chapter_id)["content"] == "推送发出后又补写一笔。"
        assert not left._state(branch)["pending"]
