from fastapi.testclient import TestClient
import pytest

from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript


def test_two_independent_sqlite_workspaces_sync_and_keep_competing_revisions(client, domain, story, tmp_path):
    chapter_id, branch = story["chapter_id"], story["branch_id"]
    domain.save(chapter_id, 0, "拾起铜铃。")
    other = Domain(Database(tmp_path / "other.sqlite"))
    import_manuscript(other, export_manuscript(domain, branch))
    cloud = create_sync_app(tmp_path / "cloud.sqlite", "provision-for-tests-only-abcdefgh")
    lost = {"after_push": False}
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

        def transport(origin, token, method, target, payload):
            response = service.request(
                method, target, headers={"Authorization": "Bearer " + token}, json=payload
            )
            if not response.is_success:
                raise DomainError(response.json()["code"], response.json()["message"], response.status_code)
            if target.endswith("/push") and lost["after_push"]:
                lost["after_push"] = False
                raise DomainError("sync_transport", "测试：响应丢失。", 502)
            return response.json()

        left, right = DesktopSync(domain, transport), DesktopSync(other, transport)
        left_token, right_token = grant("desktop-one"), grant("desktop-two")
        assert client.get(f"/api/branches/{branch}/desktop-sync").json()["enabled"] is False
        left.enable(branch, origin, left_token)
        right.enable(branch, origin, right_token)
        lost["after_push"] = True
        with pytest.raises(DomainError, match="响应丢失"):
            left.run(branch, origin, left_token)
        assert left._state(branch)["pending"] == 2
        assert left.run(branch, origin, left_token)["cursor"] == 2
        assert right.run(branch, origin, right_token)["cursor"] == 2
        assert domain.chapter(chapter_id)["content"] == other.chapter(chapter_id)["content"]

        domain.save(chapter_id, 1, "师姐拾起铜铃。")
        other.save(chapter_id, 1, "学徒拾起铜铃。")
        left.run(branch, origin, left_token)
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
