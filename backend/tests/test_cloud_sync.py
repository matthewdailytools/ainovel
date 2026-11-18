from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ainovel import cloud_sync
from ainovel.db import DomainError


WORK = "/api/sync/works/遗铃".replace("遗铃", "bell")
ADMIN = {"Authorization": "Bearer " + "provision-test-only-" * 2}


def bearer(token):
    return {"Authorization": "Bearer " + token}


def grant(client, device, work=WORK, **options):
    answer = client.post(work + "/devices", headers=ADMIN, json={"device_id": device, **options})
    assert answer.status_code == 201, answer.text
    return bearer(answer.json()["token"])


def parent(change_id, parent_revision=None, payload=None, *, tombstone=False, object_id="chapter:first"):
    return {
        "object_id": object_id,
        "change_id": change_id,
        "parent_revision": parent_revision,
        "payload": payload if payload is not None or tombstone else {"text": change_id},
        "tombstone": tombstone,
    }


def push(client, headers, data, work=WORK):
    answer = client.post(work + "/push", headers=headers, json=data)
    assert answer.status_code == 200, answer.text
    return answer.json()


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "sync.sqlite"
    app = cloud_sync.create_sync_app(path, ADMIN["Authorization"].split(" ", 1)[1])
    with TestClient(app) as client:
        yield client, path


def test_provision_opt_in_authentication_scope_rotation_and_revoke(service):
    client, path = service
    response = client.post(WORK + "/devices", json={"device_id": "one"})
    assert response.status_code == 401
    desktop = grant(client, "desktop")
    web = grant(client, "web")
    another = grant(client, "desktop", "/api/sync/works/other")
    assert client.get(WORK + "/pull", headers=another).status_code == 401
    assert client.get("/api/sync/works/other/pull", headers=desktop).status_code == 401
    assert client.get(WORK + "/pull", headers=ADMIN).status_code == 401
    assert client.post(WORK + "/devices", headers=desktop, json={"device_id": "visitor"}).status_code == 401
    duplicate = client.post(WORK + "/devices", headers=ADMIN, json={"device_id": "desktop"})
    assert duplicate.status_code == 409
    rotated = grant(client, "desktop", rotate=True)
    assert client.get(WORK + "/pull", headers=desktop).status_code == 401
    assert client.get(WORK + "/pull", headers=rotated).status_code == 200
    assert client.delete(WORK + "/devices/web", headers=ADMIN).status_code == 204
    assert client.get(WORK + "/pull", headers=web).status_code == 401
    with sqlite3.connect(path) as tx:
        stored = [row[0] for row in tx.execute("SELECT token_sha256 FROM sync_devices")]
    assert all(len(row) == 64 for row in stored)
    assert rotated["Authorization"].split(" ", 1)[1] not in stored


def test_persistent_receipt_idempotent_retry_and_ordered_pull(service):
    client, path = service
    desktop = grant(client, "desktop")
    web = grant(client, "web")
    received = push(client, desktop, parent("a1", payload={"b": 2, "a": "铃"}))
    assert received["sequence"] == 1 and received["head_revision"] == "a1"
    assert received == push(client, desktop, parent("a1", payload={"a": "铃", "b": 2}))
    wrong = client.post(WORK + "/push", headers=desktop, json=parent("a1", payload={"a": "改过"}))
    assert wrong.status_code == 409
    assert client.post(WORK + "/push", headers=web, json=parent("a1")).status_code == 409
    assert client.get(WORK + "/receipts/a1", headers=web).status_code == 404
    other_object = client.post(
        WORK + "/push", headers=desktop, json=parent("a2", "a1", object_id="chapter:other")
    )
    assert other_object.status_code == 409 and other_object.json()["code"] == "sync_parent_unknown"
    second = push(client, web, parent("a2", "a1", {"text": "门内响了"}))
    assert second["sequence"] == 2
    pulled = client.get(WORK + "/pull?after=0&limit=1", headers=web).json()
    assert pulled == {"cursor": 1, "has_more": True, "changes": [received]}
    assert client.get(WORK + "/pull?after=1&limit=1", headers=web).json() == {
        "cursor": 2,
        "has_more": False,
        "changes": [second],
    }
    restarted = cloud_sync.create_sync_app(path, ADMIN["Authorization"].split(" ", 1)[1])
    with TestClient(restarted) as second_client:
        assert second_client.get(WORK + "/receipts/a1", headers=desktop).json() == received
        assert second_client.get(WORK + "/changes/a2", headers=desktop).json() == second
        cursor = second_client.get(WORK + "/pull?after=3", headers=web)
        assert cursor.status_code == 409


def test_two_devices_same_parent_preserve_conflict_and_resolve_with_compare_and_swap(service):
    client, _ = service
    desktop = grant(client, "desktop")
    web = grant(client, "web")
    push(client, desktop, parent("base", payload={"text": "学徒发现铜铃。"}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(push, client, desktop, parent("desktop-draft", "base", {"text": "学徒拾起铜铃。"}))
        right = pool.submit(push, client, web, parent("web-draft", "base", {"text": "师姐拾起铜铃。"}))
        versions = [left.result(), right.result()]
    current = next(row for row in versions if row["status"] == "applied")
    incoming = next(row for row in versions if row["status"] == "conflict")
    conflict = incoming["conflict_id"]
    assert incoming["head_revision"] == current["change_id"]
    pending = client.get(WORK + "/conflicts", headers=desktop).json()
    assert [row["conflict_id"] for row in pending["conflicts"]] == [conflict]
    comparison_path = WORK + f"/conflicts/{conflict}"
    comparison = client.get(comparison_path, headers=desktop).json()
    assert comparison["base"]["payload"]["text"] == "学徒发现铜铃。"
    assert comparison["current"] == current
    assert comparison["incoming"] == incoming

    newer = push(client, web, parent("newer-head", current["change_id"], {"text": "铜铃仍在门口。"}))
    choice = {
        "change_id": "merged-head",
        "choice": "merge",
        "parent_revision": current["change_id"],
        "tombstone": False,
        "payload": {"text": "师姐拾到铜铃，交给学徒。"},
    }
    assert client.post(comparison_path + "/resolve", headers=desktop, json=choice).status_code == 409
    choice["parent_revision"] = newer["change_id"]
    merged = client.post(comparison_path + "/resolve", headers=desktop, json=choice)
    assert merged.status_code == 200, merged.text
    assert merged.json()["status"] == "resolved" and merged.json()["conflict_id"] == conflict
    assert client.post(comparison_path + "/resolve", headers=desktop, json=choice).json() == merged.json()
    assert client.get(WORK + "/conflicts", headers=web).json()["conflicts"] == []
    historical = client.get(comparison_path, headers=web).json()
    assert historical["resolved_by"] == "merged-head"
    assert historical["incoming"] == incoming and historical["current"] == merged.json()


def test_stale_edit_cannot_resurrect_tombstone(service):
    client, _ = service
    desktop = grant(client, "desktop")
    web = grant(client, "web")
    push(client, desktop, parent("base"))
    deleted = push(client, desktop, parent("deleted", "base", tombstone=True))
    lost = push(client, web, parent("old-offline", "base", {"text": "旧端正文仍在。"}))
    assert lost["status"] == "conflict" and lost["head_revision"] == deleted["change_id"]
    path = WORK + "/conflicts/old-offline"
    compare = client.get(path, headers=web).json()
    assert compare["current"]["tombstone"]
    assert compare["incoming"]["payload"]["text"] == "旧端正文仍在。"
    kept = client.post(
        path + "/resolve",
        headers=desktop,
        json={
            "change_id": "deleted-confirmed",
            "parent_revision": "deleted",
            "choice": "current",
        },
    )
    assert kept.status_code == 200
    assert kept.json()["tombstone"] and kept.json()["payload"] is None
    assert client.get(WORK + "/pull?after=3", headers=web).json()["changes"] == [kept.json()]


def test_bounded_payloads_and_sanitized_validation(service, monkeypatch):
    client, _ = service
    desktop = grant(client, "desktop")
    invalid = {**parent("a1"), "payload": {"text": "secret-test-input"}, "tombstone": True}
    response = client.post(WORK + "/push", headers=desktop, json=invalid)
    assert response.status_code == 422 and "secret-test-input" not in response.text
    response = client.post(
        WORK + "/push", headers={"Authorization": "Bearer " + "bogus" * 200}, json=parent("a1")
    )
    assert response.status_code == 422 and "bogus" not in response.text
    monkeypatch.setattr(cloud_sync, "MAX_OBJECT_BYTES", 40)
    oversized = parent("a1", payload={"text": "山" * 50})
    response = client.post(WORK + "/push", headers=desktop, json=oversized)
    assert response.status_code == 413 and response.json()["code"] == "sync_object_size"
    monkeypatch.setattr(cloud_sync, "MAX_REQUEST_BYTES", 45)
    response = client.post(WORK + "/push", headers=desktop, json=oversized)
    assert response.status_code == 413 and response.json()["code"] == "sync_request_size"
    assert client.get(WORK + "/pull", headers=desktop).json() == {
        "cursor": 0,
        "has_more": False,
        "changes": [],
    }


def test_sync_database_separate_from_workbench(domain):
    with pytest.raises(DomainError, match="独立的同步数据库"):
        cloud_sync.SyncStore(domain.db.path)
