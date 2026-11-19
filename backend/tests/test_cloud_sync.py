from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import sqlite3
from threading import Event

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


def chapter_at(identifier, title, position):
    return {
        "format": "ainovel-manuscript-sync/1",
        "kind": "chapter",
        "chapter": {
            "id": identifier,
            "branch_id": "bell",
            "position": position,
            "title": title,
            "revision": 1,
            "archived": 0,
            "stale": 0,
        },
        "head": {
            "id": f"{identifier}-manuscript",
            "chapter_id": identifier,
            "revision": 1,
            "parent_id": f"{identifier}-initial",
            "cause": "作者编辑",
            "created": 123.5,
            "content": f"😀{title}。\r\n余音尚未停止。",
            "sha256": sha256(f"😀{title}。\r\n余音尚未停止。".encode()).hexdigest(),
        },
    }


def test_chapter_order_moves_only_selected_unscheduled_chapter_and_replays(service):
    client, _ = service
    desktop, web = grant(client, "desktop"), grant(client, "web")
    ledger = client.get(WORK + "/checkpoint", headers=desktop).json()["ledger_id"]
    desktop, web = ({**headers, cloud_ledger.LEDGER_HEADER: ledger} for headers in (desktop, web))
    left = chapter_at("left", "桌面新章", 2)
    right = chapter_at("right", "Web 新章", 2)
    left_source = push(client, desktop, parent("left-head", object_id="chapter:left", payload=left))
    push(client, web, parent("right-head", object_id="chapter:right", payload=right))
    tail = chapter_at("tail", "目录末章", 6)
    tail_source = push(client, web, parent("longer-tail", object_id="chapter:tail", payload=tail))
    far = chapter_at("archived", "最后归档", 12)
    push(client, desktop, parent("before-archive", object_id="chapter:archived", payload=far))
    archived = push(
        client, desktop, parent("archive", "before-archive", object_id="chapter:archived", tombstone=True)
    )

    path = WORK + "/chapter-order-conflicts"
    mismatch_preview = client.get(path, headers=web, params={"device_id": "desktop"})
    assert mismatch_preview.status_code == 403 and mismatch_preview.json()["code"] == "sync_device_mismatch"
    before = client.get(path, headers=web, params={"device_id": "web"})
    assert before.json() == {
        "conflicts": [
            {
                "position": 2,
                "chapters": [
                    {
                        "chapter_id": "left",
                        "title": "桌面新章",
                        "head_revision": "left-head",
                        "scheduled": False,
                    },
                    {
                        "chapter_id": "right",
                        "title": "Web 新章",
                        "head_revision": "right-head",
                        "scheduled": False,
                    },
                ],
            }
        ]
    }
    command = {
        "change_id": "user-moves-web-chapter",
        "chapter_id": "right",
        "position": 2,
        "heads": {"left": "left-head", "right": "right-head"},
    }
    mismatch = client.post(path + "/resolve", headers=desktop, params={"device_id": "web"}, json=command)
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "sync_device_mismatch"
    assert client.get(WORK + "/checkpoint", headers=web).json()["cursor"] == archived["sequence"]
    # The desktop may choose the Web chapter even though it is pending adoption there.
    result = client.post(path + "/resolve", headers=desktop, params={"device_id": "desktop"}, json=command)
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt == {
        "change_id": command["change_id"],
        "sequence": archived["sequence"] + 1,
        "status": "applied",
        "object_id": "chapter:right",
        "device_id": "desktop",
        "parent_revision": "right-head",
        "head_revision": command["change_id"],
        "conflict_id": None,
        "payload": {**right, "chapter": {**right["chapter"], "position": 7}},
        "tombstone": False,
    }
    assert client.get(path, headers=web).json() == {"conflicts": []}
    retry = {**command, "heads": {"right": "right-head", "left": "left-head"}}
    mismatch_retry = client.post(path + "/resolve", headers=desktop, params={"device_id": "web"}, json=retry)
    assert mismatch_retry.status_code == 403 and mismatch_retry.json()["code"] == "sync_device_mismatch"
    assert (
        client.post(path + "/resolve", headers=desktop, params={"device_id": "desktop"}, json=retry).json()
        == receipt
    )
    assert client.get(WORK + "/receipts/" + command["change_id"], headers=desktop).json() == receipt
    assert client.get(WORK + "/changes/left-head", headers=web).json() == left_source
    assert client.get(WORK + "/changes/longer-tail", headers=web).json() == tail_source
    assert client.get(WORK + "/pull", headers=web, params={"after": archived["sequence"]}).json()[
        "changes"
    ] == [receipt]
    assert client.get(WORK + "/checkpoint", headers=web).json()["cursor"] == receipt["sequence"]
    wrong_replay = client.post(path + "/resolve", headers=desktop, json={**command, "chapter_id": "left"})
    assert wrong_replay.status_code == 409 and wrong_replay.json()["code"] == "sync_replay"


def test_chapter_order_checks_ledger_and_full_collision_heads_under_concurrency(service):
    client, _ = service
    desktop, web = grant(client, "desktop"), grant(client, "web")
    unrelated = grant(client, "other", "/api/sync/works/other")
    checkpoint = client.get(WORK + "/checkpoint", headers=desktop).json()
    bound = {cloud_ledger.LEDGER_HEADER: checkpoint["ledger_id"]}
    path = WORK + "/chapter-order-conflicts"
    for headers in (desktop, {**desktop, cloud_ledger.LEDGER_HEADER: "0" * 32}):
        wrong_ledger = client.get(path, headers=headers)
        assert wrong_ledger.status_code == 409 and wrong_ledger.json()["code"] == "sync_ledger_replaced"
    assert client.get(path, headers={**unrelated, **bound}).status_code == 401
    desktop, web = ({**headers, **bound} for headers in (desktop, web))
    for identifier in ("left", "right", "third"):
        push(
            client,
            desktop,
            parent(
                f"{identifier}-head",
                object_id=f"chapter:{identifier}",
                payload=chapter_at(identifier, identifier, 2),
            ),
        )
    command = {
        "change_id": "move-left",
        "chapter_id": "left",
        "position": 2,
        "heads": {"left": "left-head", "right": "right-head"},
    }
    unbound = client.post(
        path + "/resolve", headers={**desktop, cloud_ledger.LEDGER_HEADER: "0" * 32}, json=command
    )
    assert unbound.status_code == 409 and unbound.json()["code"] == "sync_ledger_replaced"
    assert client.post(path + "/resolve", headers={**unrelated, **bound}, json=command).status_code == 401
    assert (
        client.post(path + "/resolve", headers=desktop, json={**command, "position": "2"}).status_code == 422
    )
    changed = client.post(path + "/resolve", headers=desktop, json=command)
    assert changed.status_code == 409 and changed.json()["code"] == "sync_chapter_order_changed"
    snapshot = client.get(path, headers=desktop).json()["conflicts"][0]
    assert [row["chapter_id"] for row in snapshot["chapters"]] == ["left", "right", "third"]
    push(client, desktop, parent("archive-third", "third-head", object_id="chapter:third", tombstone=True))
    out_of_scope = client.post(path + "/resolve", headers=desktop, json={**command, "chapter_id": "third"})
    assert out_of_scope.status_code == 409 and out_of_scope.json()["code"] == "sync_chapter_order_changed"
    archive_stale = client.post(
        path + "/resolve",
        headers=desktop,
        json={**command, "heads": {**command["heads"], "third": "third-head"}},
    )
    assert archive_stale.status_code == 409 and archive_stale.json()["code"] == "sync_chapter_order_changed"

    rewritten = chapter_at("right", "新标题", 2)
    latest = push(
        client, web, parent("right-new-head", "right-head", object_id="chapter:right", payload=rewritten)
    )
    stale_head = client.post(path + "/resolve", headers=desktop, json=command)
    assert stale_head.status_code == 409 and stale_head.json()["code"] == "sync_chapter_order_changed"
    command["heads"]["right"] = latest["change_id"]
    assert client.get(WORK + "/checkpoint", headers=desktop).json()["cursor"] == latest["sequence"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(client.post, path + "/resolve", headers=desktop, json=command)
        right = pool.submit(
            client.post,
            path + "/resolve",
            headers=web,
            json={**command, "change_id": "move-right", "chapter_id": "right"},
        )
        results = [left.result(), right.result()]
    success = next(response for response in results if response.status_code == 200).json()
    failure = next(response for response in results if response.status_code == 409).json()
    assert failure["code"] == "sync_chapter_order_changed"
    assert success["payload"]["chapter"]["position"] == 3
    assert success["sequence"] == latest["sequence"] + 1
    assert client.get(WORK + "/checkpoint", headers=desktop).json()["cursor"] == success["sequence"]


def test_chapter_order_preserves_chapters_added_to_current_formal_outline(service):
    client, _ = service
    desktop = grant(client, "desktop")
    ledger = client.get(WORK + "/checkpoint", headers=desktop).json()["ledger_id"]
    desktop[cloud_ledger.LEDGER_HEADER] = ledger
    for identifier in ("planned", "free"):
        push(
            client,
            desktop,
            parent(
                f"{identifier}-head",
                object_id=f"chapter:{identifier}",
                payload=chapter_at(identifier, identifier, 2),
            ),
        )
    path = WORK + "/chapter-order-conflicts"
    before = client.get(path, headers=desktop).json()["conflicts"][0]["chapters"]
    assert {item["chapter_id"]: item["scheduled"] for item in before} == {"free": False, "planned": False}
    plan = {
        "summary": "追查铜铃的来历。",
        "volumes": [
            {
                "id": "volume",
                "title": "拾铃卷",
                "summary": "归还铜铃。",
                "units": [
                    {
                        "id": "unit",
                        "title": "找主人",
                        "summary": "追踪刻记。",
                        "chapters": [
                            {
                                "chapter_id": "planned",
                                "goal": "拾铃",
                                "obstacle": "旧门",
                                "turn": "提示",
                                "result": "过门",
                            }
                        ],
                    }
                ],
            }
        ],
        "promises": [],
    }
    outline = {
        "format": "ainovel-manuscript-sync/1",
        "kind": "outline-version",
        "branch_id": "bell",
        "version": {
            "id": "new-plan",
            "parent_id": None,
            "project_version_id": None,
            "settings_version_id": None,
            "plan": plan,
        },
    }
    push(client, desktop, parent("outline-version", object_id="outline-version:new-plan", payload=outline))
    head = {
        "format": outline["format"],
        "kind": "outline-head",
        "branch_id": "bell",
        "revision_id": "new-plan",
    }
    adopted = push(client, desktop, parent("outline-adopted", object_id="outline:bell", payload=head))
    after = client.get(path, headers=desktop).json()["conflicts"][0]["chapters"]
    assert {item["chapter_id"]: item["scheduled"] for item in after} == {"free": False, "planned": True}
    command = {
        "change_id": "move-not-allowed",
        "chapter_id": "planned",
        "position": 2,
        "heads": {"planned": "planned-head", "free": "free-head"},
    }
    rejected = client.post(path + "/resolve", headers=desktop, json=command)
    assert rejected.status_code == 409 and rejected.json()["code"] == "sync_chapter_order_scheduled"
    assert client.get(WORK + "/checkpoint", headers=desktop).json()["cursor"] == adopted["sequence"]
    allowed = client.post(path + "/resolve", headers=desktop, json={**command, "chapter_id": "free"})
    assert allowed.status_code == 200 and allowed.json()["payload"]["chapter"]["position"] == 3
    rearranged = client.post(
        WORK + "/chapter-order-batches",
        headers=desktop,
        json={
            "change_id": "author-reorders-planned-chapter",
            "moves": [
                {"chapter_id": "planned", "position": 2, "next_position": 3, "head_revision": "planned-head"},
                {
                    "chapter_id": "free",
                    "position": 3,
                    "next_position": 2,
                    "head_revision": "move-not-allowed",
                },
            ],
        },
    )
    assert rearranged.status_code == 200, rearranged.text
    assert client.get(WORK + "/changes/outline-adopted", headers=desktop).json() == adopted


@pytest.mark.parametrize("tail", [cloud_sync.MAX_CURSOR, cloud_sync.MAX_CURSOR + 1])
def test_chapter_order_cannot_move_past_supported_positions(service, tail):
    client, _ = service
    desktop = grant(client, "desktop")
    ledger = client.get(WORK + "/checkpoint", headers=desktop).json()["ledger_id"]
    desktop[cloud_ledger.LEDGER_HEADER] = ledger
    for identifier, position in (("first", 1), ("second", 1), ("tail", tail)):
        push(
            client,
            desktop,
            parent(
                f"{identifier}-head",
                object_id=f"chapter:{identifier}",
                payload=chapter_at(identifier, identifier, position),
            ),
        )
    path = WORK + "/chapter-order-conflicts"
    snapshot = client.get(path, headers=desktop)
    assert snapshot.status_code == (200 if tail == cloud_sync.MAX_CURSOR else 409)
    refused = client.post(
        path + "/resolve",
        headers=desktop,
        json={
            "change_id": "move",
            "chapter_id": "first",
            "position": 1,
            "heads": {"first": "first-head", "second": "second-head"},
        },
    )
    assert refused.status_code == 409 and refused.json()["code"] == "sync_chapter_order_limit"
    assert client.get(WORK + "/checkpoint", headers=desktop).json()["cursor"] == 3


def prepare_chapter_batch(client):
    desktop, web = grant(client, "desktop"), grant(client, "web")
    ledger = client.get(WORK + "/checkpoint", headers=desktop).json()["ledger_id"]
    for headers in (desktop, web):
        headers[cloud_ledger.LEDGER_HEADER] = ledger
    chapters = [
        chapter_at(identifier, identifier, index) for index, identifier in enumerate(("a", "b", "c"), 1)
    ]
    sources = [
        push(
            client,
            desktop,
            parent(f"authored-{identifier}", object_id=f"chapter:{identifier}", payload=chapter),
        )
        for identifier, chapter in zip(("a", "b", "c"), chapters, strict=True)
    ]
    return (
        desktop,
        web,
        sources,
        {
            "change_id": "author-swaps-chapters",
            "moves": [
                {"chapter_id": "b", "position": 2, "next_position": 1, "head_revision": "authored-b"},
                {"chapter_id": "a", "position": 1, "next_position": 2, "head_revision": "authored-a"},
            ],
        },
    )


def test_chapter_batch_is_atomic_to_other_devices_and_has_pageable_exact_receipts(service, monkeypatch):
    client, _ = service
    desktop, web, sources, command = prepare_chapter_batch(client)
    remote = WORK + "/chapter-order-batches"
    base_cursor = sources[-1]["sequence"]
    first_written, finish = Event(), Event()
    append = cloud_sync.SyncStore._append

    def pause_after_first_child(store, tx, work, device, data, *args, **kwargs):
        event = append(store, tx, work, device, data, *args, **kwargs)
        if data.change_id.startswith(cloud_sync.CHAPTER_ORDER_PART_PREFIX) and not first_written.is_set():
            first_written.set()
            assert finish.wait(5)
        return event

    with monkeypatch.context() as controls:
        controls.setattr(cloud_sync.SyncStore, "_append", pause_after_first_child)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.post, remote, headers=desktop, json=command, params={"device_id": "desktop"}
            )
            try:
                assert first_written.wait(5)
                assert client.get(WORK + "/checkpoint", headers=web).json()["cursor"] == base_cursor
                assert client.get(WORK + "/pull", headers=web, params={"after": base_cursor}).json() == {
                    "cursor": base_cursor,
                    "has_more": False,
                    "changes": [],
                }
                assert client.get(WORK + "/chapter-order-conflicts", headers=web).json() == {"conflicts": []}
            finally:
                finish.set()
            result = pending.result()
    assert result.status_code == 200, result.text
    batch = result.json()
    assert batch["object_id"] == "chapter-directory:" + command["change_id"]
    assert batch["change_id"] == batch["head_revision"] == command["change_id"]
    assert batch["parent_revision"] is None and batch["status"] == "applied"
    assert batch["sequence"] == base_cursor + 3
    assert batch["payload"]["format"] == "ainovel-manuscript-sync/1"
    assert batch["payload"]["branch_id"] == "bell" and batch["payload"]["kind"] == "chapter-directory"
    cursor = base_cursor
    moved = []
    for index, (previous, expected, entry) in enumerate(
        zip(reversed(sources[:2]), command["moves"], batch["payload"]["moves"], strict=True)
    ):
        part = entry["receipt_id"]
        assert part.startswith(cloud_sync.CHAPTER_ORDER_PART_PREFIX)
        assert entry == {**expected, "receipt_id": part}
        page = client.get(WORK + "/pull", headers=web, params={"after": cursor, "limit": 1}).json()
        assert page["has_more"] and page["cursor"] == base_cursor + index + 1
        (receipt,) = page["changes"]
        assert receipt["change_id"] == receipt["head_revision"] == part
        assert receipt["status"] == "applied" and receipt["parent_revision"] == previous["change_id"]
        assert receipt["object_id"] == previous["object_id"]
        original = previous["payload"]
        assert receipt["payload"] == {
            **original,
            "chapter": {**original["chapter"], "position": entry["next_position"]},
        }
        assert client.get(WORK + "/changes/" + part, headers=web).json() == receipt
        moved.append(receipt)
        cursor = page["cursor"]
    assert client.get(WORK + "/pull", headers=web, params={"after": cursor, "limit": 1}).json() == {
        "cursor": batch["sequence"],
        "has_more": False,
        "changes": [batch],
    }
    assert client.post(remote, headers=desktop, json=command).json() == batch
    assert client.get(WORK + "/receipts/" + command["change_id"], headers=desktop).json() == batch
    assert client.get(WORK + "/checkpoint", headers=web).json()["cursor"] == batch["sequence"]
    assert client.get(WORK + "/chapter-order-conflicts", headers=web).json() == {"conflicts": []}
    previous = moved[0]
    edited = push(
        client,
        web,
        parent(
            "later-edit", previous["change_id"], payload=previous["payload"], object_id=previous["object_id"]
        ),
    )
    assert edited["status"] == "applied"


def test_chapter_batch_refuses_stale_invalid_and_forged_writes_and_rolls_back(service, monkeypatch):
    client, _ = service
    desktop, web, sources, command = prepare_chapter_batch(client)
    base = sources[-1]["sequence"]
    route = WORK + "/chapter-order-batches"
    for values, status, code in (
        ({"change_id": "x" * 111}, 422, "sync_input"),
        ({"moves": command["moves"][:1]}, 422, "sync_input"),
        ({"moves": [command["moves"][0], command["moves"][0]]}, 422, "sync_input"),
        (
            {"moves": [command["moves"][0], {**command["moves"][1], "position": 2, "next_position": 3}]},
            422,
            "sync_input",
        ),
        (
            {"moves": [command["moves"][0], {**command["moves"][1], "next_position": 3}]},
            409,
            "sync_chapter_order_changed",
        ),
        (
            {"moves": [command["moves"][0], {**command["moves"][1], "head_revision": "other-head"}]},
            409,
            "sync_chapter_order_changed",
        ),
        (
            {"moves": [command["moves"][0], {**command["moves"][1], "position": 3}]},
            409,
            "sync_chapter_order_changed",
        ),
    ):
        refused = client.post(route, headers=desktop, json={**command, **values})
        assert refused.status_code == status and refused.json()["code"] == code
    mismatched = client.post(route, headers=desktop, json=command, params={"device_id": "web"})
    assert mismatched.status_code == 403 and mismatched.json()["code"] == "sync_device_mismatch"
    unbound = client.post(route, headers=bearer(desktop["Authorization"].split(" ", 1)[1]), json=command)
    assert unbound.status_code == 409 and unbound.json()["code"] == "sync_ledger_replaced"
    for invalid in (
        parent("forged-manifest", object_id="chapter-directory:forged"),
        parent("forged-child", object_id="chapter:a", payload={"kind": "chapter-directory"}),
        parent("chapter-order-part-forged", object_id="chapter:b"),
    ):
        rejected = client.post(WORK + "/push", headers=desktop, json=invalid)
        assert rejected.status_code == 409 and rejected.json()["code"] == "sync_reserved"

    append = cloud_sync.SyncStore._append
    created_parts = []

    def fail_at_manifest(store, tx, work, device, data, *args, **kwargs):
        if data.object_id.startswith(cloud_sync.CHAPTER_DIRECTORY_PREFIX):
            raise DomainError("sync_test_interrupt", "中断整组提交。", 409)
        receipt = append(store, tx, work, device, data, *args, **kwargs)
        created_parts.append(receipt.change_id)
        return receipt

    with monkeypatch.context() as controls:
        controls.setattr(cloud_sync.SyncStore, "_append", fail_at_manifest)
        interrupted = client.post(route, headers=desktop, json=command)
    assert interrupted.status_code == 409 and interrupted.json()["code"] == "sync_test_interrupt"
    assert len(created_parts) == 2
    for identifier in [*created_parts, command["change_id"]]:
        assert client.get(WORK + "/changes/" + identifier, headers=web).status_code == 404
    assert client.get(WORK + "/checkpoint", headers=desktop).json()["cursor"] == base
    assert client.get(WORK + "/pull", headers=web).json()["changes"] == sources
    retry = client.post(route, headers=desktop, json=command)
    assert retry.status_code == 200, retry.text
    assert [row["receipt_id"] for row in retry.json()["payload"]["moves"]] == created_parts


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
