from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ainovel import cloud_ledger, cloud_sync
from ainovel.cloud_attachments import CHUNK_BYTES
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


def test_checkpoint_is_authenticated_work_scoped_stable_and_available_through_allowed_cors(tmp_path):
    path = tmp_path / "sync.sqlite"
    provisioning = ADMIN["Authorization"].split(" ", 1)[1]
    with TestClient(cloud_sync.create_sync_app(path, provisioning, ("https://writer.example",))) as client:
        desktop = grant(client, "desktop")
        unrelated = grant(client, "desktop", "/api/sync/works/other")
        route = WORK + "/checkpoint"
        for unauthorized in ({}, ADMIN, unrelated):
            assert client.get(route, headers=unauthorized).status_code == 401
        initial = client.get(route, headers=desktop).json()
        assert initial["cursor"] == 0
        assert len(initial["ledger_id"]) == 32 and set(initial["ledger_id"]) <= set("0123456789abcdef")
        wrong = {**desktop, cloud_ledger.LEDGER_HEADER: initial["ledger_id"].upper() + "X"}
        assert client.get(WORK + "/pull", headers=wrong).json()["code"] == "sync_ledger_replaced"
        assert client.get(WORK + "/pull", headers={cloud_ledger.LEDGER_HEADER: "foreign"}).status_code == 401
        first = push(client, desktop, parent("first"))
        bound = {**desktop, cloud_ledger.LEDGER_HEADER: initial["ledger_id"]}
        assert client.get(WORK + "/pull", headers=bound).json()["changes"] == [first]
        preflight = client.options(
            WORK + "/pull",
            headers={
                "Origin": "https://writer.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": f"authorization,{cloud_ledger.LEDGER_HEADER.lower()}",
            },
        )
        assert preflight.status_code == 200
        assert cloud_ledger.LEDGER_HEADER.lower() in preflight.headers["Access-Control-Allow-Headers"].lower()

    with TestClient(cloud_sync.create_sync_app(path, provisioning)) as reopened:
        assert reopened.get(route, headers=desktop).json() == {**initial, "cursor": 1}


def test_pre_generation_service_db_preserves_history_and_corrupt_current_identity_fails_closed(tmp_path):
    path = tmp_path / "sync.sqlite"
    provisioning = ADMIN["Authorization"].split(" ", 1)[1]
    with TestClient(cloud_sync.create_sync_app(path, provisioning)) as client:
        desktop = grant(client, "desktop")
        first = push(client, desktop, parent("original"))
        old_ledger = client.get(WORK + "/checkpoint", headers=desktop).json()["ledger_id"]

    # Reproduce the pre-generation layout while keeping the same real authenticated history.
    with closing(sqlite3.connect(path)) as tx, tx:
        tx.execute("DROP TABLE sync_ledger")
        tx.execute("PRAGMA user_version=1")

    with TestClient(cloud_sync.create_sync_app(path, provisioning)) as upgraded:
        frozen = upgraded.get(WORK + "/checkpoint", headers=desktop).json()
        assert frozen["ledger_id"] != old_ledger and frozen["cursor"] == 1
        current = {**desktop, cloud_ledger.LEDGER_HEADER: frozen["ledger_id"]}
        assert upgraded.get(WORK + "/pull", headers=desktop).json()["code"] == "sync_ledger_replaced"
        assert upgraded.get(WORK + "/pull", headers=current).json()["changes"] == [first]

    with closing(sqlite3.connect(path)) as tx, tx:
        tx.execute("DELETE FROM sync_ledger")
    with pytest.raises(DomainError, match="账本身份"):
        cloud_sync.SyncStore(path)
    with closing(sqlite3.connect(path)) as tx:
        assert tx.execute("SELECT count(*) FROM sync_ledger").fetchone() == (0,)
        assert tx.execute("SELECT change_id FROM sync_changes").fetchone() == ("original",)


def test_device_can_revoke_only_its_own_work_session_and_repeat_lost_receipt(service):
    client, _ = service
    desktop = grant(client, "device")
    peer = grant(client, "peer")
    other_work = "/api/sync/works/same-device-name-different-work"
    other = grant(client, "device", other_work)
    event = push(client, desktop, parent("published"))
    material = b"existing selected document"
    digest = sha256(material).hexdigest()
    attachments = WORK + "/attachments"
    assert (
        client.post(
            attachments,
            headers=desktop,
            json={"name": "selected.txt", "sha256": digest, "size": len(material)},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{attachments}/{digest}/parts/0",
            headers={**desktop, "Content-Type": "application/octet-stream"},
            content=material,
        ).status_code
        == 204
    )
    assert client.post(f"{attachments}/{digest}/seal", headers=desktop).status_code == 200

    session = WORK + "/device-session"
    denied = client.delete(session, headers=desktop, params={"device_id": "peer"})
    assert denied.status_code == 403 and denied.json()["code"] == "sync_device_mismatch"
    assert client.get(WORK + "/pull", headers=desktop).status_code == 200
    assert client.delete(other_work + "/device-session", headers=desktop).status_code == 401
    assert client.delete(session, headers=other).status_code == 401
    assert client.delete(session, headers=ADMIN).status_code == 401
    assert client.delete(WORK + "/devices/peer", headers=desktop).status_code == 401

    for _ in range(2):
        revoked = client.delete(session, headers=desktop, params={"device_id": "device"})
        assert revoked.status_code == 204 and revoked.content == b""
    assert client.get(WORK + "/pull", headers=desktop).status_code == 401
    assert client.post(WORK + "/push", headers=desktop, json=parent("not-written")).status_code == 401
    assert client.get(WORK + "/pull", headers=peer).json()["changes"] == [event]
    assert client.get(f"{attachments}/{digest}/content", headers=peer).content == material
    assert client.get(other_work + "/pull", headers=other).status_code == 200

    replacement = grant(client, "device")
    assert client.delete(session, headers=desktop).status_code == 401
    assert client.get(WORK + "/pull", headers=replacement).json()["changes"] == [event]


def test_scoped_attachment_resume_validates_full_content(service):
    client, _ = service
    desktop, web = grant(client, "desktop"), grant(client, "web")
    other = grant(client, "other", "/api/sync/works/another")
    bundle = WORK + "/attachments"
    data = b"a" * CHUNK_BYTES + "铜铃资料".encode()
    digest = sha256(data).hexdigest()
    manifest = {"name": "铜铃.txt", "size": len(data), "sha256": digest}
    assert client.post(bundle, headers=desktop, json=manifest).json()["received"] == []
    file = bundle + "/" + digest
    assert client.get(file + "/content", headers=web).status_code == 404
    assert client.get(bundle, headers=web).json()["files"] == []
    binary = {**desktop, "Content-Type": "application/octet-stream"}
    unsupported = {**desktop, "Content-Type": "text/plain"}
    assert client.post(file + "/parts/0", headers=unsupported, content=data[:CHUNK_BYTES]).status_code == 415
    assert client.post(file + "/parts/1", headers=binary, content=b"bad").status_code == 400
    assert client.post(file + "/parts/0", headers=binary, content=data[:CHUNK_BYTES]).status_code == 204
    assert client.post(bundle, headers=desktop, json=manifest).json()["received"] == [0]
    assert client.post(file + "/seal", headers=desktop).status_code == 409
    assert client.post(file + "/parts/0", headers=binary, content=data[:CHUNK_BYTES]).status_code == 204
    assert client.post(file + "/parts/1", headers=binary, content=data[CHUNK_BYTES:]).status_code == 204
    assert client.post(file + "/seal", headers=desktop).json()["sealed"] == 1
    assert client.get(bundle, headers=web).json()["files"][0]["sha256"] == digest
    assert client.get(file + "/content", headers=other).status_code == 401
    assert client.get(file + "/content", headers=web).content == data


def test_attachment_wrong_digest_is_not_published(service):
    client, _ = service
    writer = grant(client, "writer")
    bundle = WORK + "/attachments"
    valid, invalid = b"valid", b"wrong"
    digest = sha256(valid).hexdigest()
    address = bundle + "/" + digest
    assert client.post(
        bundle, headers=writer, json={"sha256": digest, "size": len(valid), "name": "file.txt"}
    ).is_success
    binary = {**writer, "Content-Type": "application/octet-stream"}
    assert client.post(address + "/parts/0", headers=binary, content=invalid).status_code == 204
    rejected = client.post(address + "/seal", headers=writer)
    assert rejected.status_code == 409 and rejected.json()["code"] == "sync_attachment_integrity"
    assert client.get(address + "/content", headers=writer).status_code == 404
    assert client.get(bundle, headers=writer).json()["files"] == []


def test_source_revision_validates_parent_sha_and_never_accepts_mutation(service):
    client, _ = service
    desktop, web = grant(client, "desktop"), grant(client, "web")

    def source(change, identifier, revision, ancestor, content):
        return parent(
            change,
            object_id=f"revision:{identifier}",
            payload={
                "format": "ainovel-manuscript-sync/1",
                "kind": "revision",
                "branch_id": "bell",
                "revision": {
                    "id": identifier,
                    "chapter_id": "chapter-one",
                    "revision": revision,
                    "parent_id": ancestor,
                    "content": content,
                    "sha256": sha256(content.encode()).hexdigest(),
                    "cause": "作者原稿",
                    "created": 8,
                },
            },
        )

    base = source("base-upload", "base", 0, None, "")
    next_revision = source("next-upload", "next", 1, "base", "师姐拾铃。")
    assert client.post(WORK + "/push", headers=desktop, json=next_revision).status_code == 409
    assert push(client, desktop, base)["sequence"] == 1
    assert push(client, desktop, next_revision) == push(client, desktop, next_revision)
    assert push(client, web, {**base, "change_id": "peer-had-same-base"})["status"] == "applied"

    changed = source("changed-upload", "next", 1, "base", "学徒拾铃。")
    changed["parent_revision"] = "next-upload"
    broken_hash = source("bad-hash-upload", "other", 1, "base", "找到铜铃。")
    broken_hash["payload"]["revision"]["sha256"] = "0" * 64
    for rejected in (
        changed,
        broken_hash,
        parent("deleted", "next-upload", tombstone=True, object_id="revision:next"),
    ):
        response = client.post(WORK + "/push", headers=web, json=rejected)
        assert response.status_code == 409, response.text
    recorded = client.get(WORK + "/pull", headers=web).json()
    assert [(row["object_id"], row["status"]) for row in recorded["changes"]] == [
        ("revision:base", "applied"),
        ("revision:next", "applied"),
        ("revision:base", "applied"),
    ]


def test_configured_browser_origin_can_call_isolated_cloud(tmp_path):
    origin = "http://127.0.0.1:8877"
    cloud = cloud_sync.create_sync_app(
        tmp_path / "browser.sqlite", "local-test-token-1234567890abcdef", (origin,)
    )
    with TestClient(cloud) as client:
        assert (
            client.options(
                WORK + "/pull",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            ).headers["access-control-allow-origin"]
            == origin
        )
        assert (
            client.options(
                WORK + "/pull",
                headers={"Origin": "https://other.invalid", "Access-Control-Request-Method": "GET"},
            ).status_code
            == 400
        )


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


def test_conflict_without_recorded_parent_remains_distinct_from_tombstone(service):
    client, _ = service
    desktop, web = grant(client, "desktop"), grant(client, "web")
    push(client, desktop, parent("desktop-first"))
    submitted = push(client, web, parent("independent"))
    compared = client.get(WORK + "/conflicts/independent", headers=web).json()
    assert compared["base"] is None and compared["parent_revision"] is None
    assert compared["incoming"] == submitted
    assert not compared["current"]["tombstone"]


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
