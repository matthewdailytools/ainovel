import httpx
import pytest

from ainovel.db import DomainError
from ainovel.desktop_sync import _network
from test_story_sync import linked_cloud, paired


def test_desktop_self_revoke_keeps_local_outbox_draft_and_cursor(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    domain.save(chapter, 0, "共同首稿。")
    receiver = paired(domain, branch, tmp_path / "quit-receiver.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, token, cloud, endpoint, lost = linked
        credential = {"Authorization": f"Bearer {token}"}
        stopped = right.disable(branch)
        assert stopped["bound"] and not stopped["enabled"]
        assert cloud.get(endpoint + "/pull", headers=credential).status_code == 200
        right.enable(branch, origin, token)
        second = receiver.save(chapter, 1, "发出后等待回执的稿。")
        lost["next"] = f"chapter:{chapter}"
        with pytest.raises(DomainError, match="响应中断"):
            right.run(branch, origin, token)
        before = right._state(branch)
        assert before["enabled"] and before["pending"]
        draft = receiver.save(chapter, second["revision"], "仍只保存在本机的新稿。")
        transport = right.transport
        attempts = []

        def revoke_transport(origin, token, method, target, payload):
            if method != "DELETE":
                return transport(origin, token, method, target, payload)
            attempts.append(target)
            response = cloud.delete(target, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 204 and response.content == b""
            if len(attempts) == 1:
                raise DomainError("sync_transport", "撤销回执中断。", 502)
            return None

        right.transport = revoke_transport
        with pytest.raises(DomainError) as wrong_service:
            right.revoke_device(branch, "https://other.example", token)
        assert wrong_service.value.code == "sync_binding" and not attempts
        with pytest.raises(DomainError, match="撤销回执中断"):
            right.revoke_device(branch, origin, token)
        unknown = right._state(branch)
        assert unknown["enabled"] and unknown["cursor"] == before["cursor"] and unknown["pending"]
        assert cloud.get(endpoint + "/pull", headers=credential).status_code == 401
        right.disable(branch)
        revoked = right.revoke_device(branch, origin, token)
        assert attempts == [endpoint + "/device-session"] * 2
        assert not revoked["enabled"] and revoked["cursor"] == unknown["cursor"]
        assert revoked["pending"] == unknown["pending"]
        assert receiver.chapter(chapter)["content"] == draft["content"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert domain.chapter(chapter)["content"] == second["content"]
        with receiver.db.connect() as tx:
            assert tx.execute(
                "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                (branch, lost["receipt"]["change_id"]),
            ).fetchone()


@pytest.mark.parametrize(("status", "body"), [(204, b""), (200, b"null")])
def test_desktop_http_accepts_only_empty_success_for_revocation(monkeypatch, status, body):
    client = httpx.Client
    monkeypatch.setattr(
        "ainovel.desktop_sync.httpx.Client",
        lambda **options: client(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)), **options
        ),
    )
    args = ("https://sync.example", "synthetic-token", "DELETE", "/api/sync/works/sample/device-session")
    if status == 204:
        assert _network(*args) is None
    else:
        with pytest.raises(DomainError) as failed:
            _network(*args)
        assert failed.value.code == "sync_response"
