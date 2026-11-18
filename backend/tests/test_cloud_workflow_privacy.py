from hashlib import sha256
import json

import httpx

from ainovel.cloud_sync import Push, Resolution, SyncStore, _digest, _encoded, _payload
from ainovel.db import Database
from ainovel.desktop_sync import DesktopSync
from ainovel.domain import Domain
from ainovel.manuscript_exchange import import_manuscript
from ainovel.workflow_archive import workflow_object
from test_workflow_cloud import _grant, loopback as loopback_fixture
from test_workflow_privacy import EXCERPT, legacy as legacy_fixture

legacy = legacy_fixture
loopback = loopback_fixture
FORMAT = "ainovel-manuscript-sync/1"


def request(change_id, object_id, payload, parent_revision=None):
    return {
        "change_id": change_id,
        "object_id": object_id,
        "parent_revision": parent_revision,
        "payload": payload,
        "tombstone": False,
    }


def seed_retained(store, work, data, *, conflicting_head=None, resolution=None):
    """Populate a trusted synthetic change written before privacy checks existed."""
    device = "writer"
    change = Push.model_validate(data)
    head = conflicting_head or change.change_id
    status = "conflict" if conflicting_head else "applied"
    conflict_id = change.change_id if conflicting_head else None
    fingerprint = _digest(_encoded(["push", device, change.model_dump()]))
    if resolution:
        conflict_id, resolve = resolution
        status = "resolved"
        resolved = Resolution.model_validate(resolve)
        frozen = resolved.model_dump(exclude={"content"})
        fingerprint = _digest(_encoded(["resolve", device, conflict_id, frozen]))
    with store.transaction(write=True) as tx:
        written = store._append(
            tx, work, device, change, fingerprint, _payload(change.payload), head, status, conflict_id
        )
        if resolution:
            tx.execute(
                "UPDATE sync_changes SET resolved_by=? WHERE work_id=? AND change_id=?",
                (written.change_id, work, conflict_id),
            )
    return written


def retained_rows(store, work, ids):
    with store.transaction(snapshot=True) as tx:
        return [dict(store._change(tx, work, identifier)) for identifier in ids]


def denied(response, code="sync_workflow_reference_scope"):
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code
    assert EXCERPT not in response.text


def completion_document(legacy):
    completion = legacy.history.completions[0]
    body = json.dumps(
        {
            "manifest": completion.manifest.model_dump(),
            "checks": [check.model_dump() for check in completion.checks],
        },
        ensure_ascii=False,
    )
    return {
        "format": FORMAT,
        "kind": "evidence",
        "document": {
            "id": completion.id,
            "kind": "completion",
            "title": "旧版完本材料",
            "body": body,
            "sha256": sha256(body.encode()).hexdigest(),
            "chapters": legacy.document["chapters"],
        },
    }


def test_withheld_history_slots_keep_paginated_pull_and_desktop_replication_contiguous(
    tmp_path,
    loopback,
    legacy,
):
    origin, secret = loopback
    work = legacy.branch
    base = f"/api/sync/works/{work}"
    tokens = [_grant(loopback, work, device) for device in ("writer", "reader")]
    store = SyncStore(tmp_path / "cloud-workflow.sqlite")
    publisher = Domain(Database(tmp_path / "legacy-publisher-clean.sqlite"))
    import_manuscript(publisher, legacy.baseline)
    send = DesktopSync(publisher)
    send.enable(work, origin, tokens[0])
    start = send.run(work, origin, tokens[0])["cursor"]

    inspection = next(entry for entry in legacy.history.inspections if entry.id == legacy.report_id)
    completion = legacy.history.completions[0]
    input_rows = [
        request("old-report", f"workflow-inspection:{inspection.id}", workflow_object(work, inspection)),
        request("old-delivery", f"workflow-completion:{completion.id}", workflow_object(work, completion)),
        request(
            "old-inspection-doc",
            f"evidence:{inspection.id}",
            {
                "format": FORMAT,
                "kind": "evidence",
                "document": legacy.document,
            },
        ),
        request(
            "old-delivery-doc",
            f"evidence:{completion.id}",
            completion_document(legacy),
        ),
    ]
    original_ids = [row["change_id"] for row in input_rows]
    for data in input_rows:
        seed_retained(store, work, data)
    frozen = retained_rows(store, work, original_ids)
    assert EXCERPT in frozen[0]["payload"] and EXCERPT in frozen[2]["payload"]

    changed = publisher.save(
        legacy.chapter["id"], legacy.chapter["revision"], "归还铜铃后保留了本章独立修改。"
    )
    sent = send.run(work, origin, tokens[0])
    assert sent["cursor"] > start + len(input_rows) and not sent["conflicts"]

    writer, reader = [{"Authorization": f"Bearer {token}"} for token in tokens]
    with httpx.Client(base_url=origin, trust_env=False) as api:
        hidden_only = api.get(base + "/pull", headers=reader, params={"after": start, "limit": 2})
        assert hidden_only.is_success, hidden_only.text
        early = hidden_only.json()
        assert early["cursor"] == start + 2 and early["has_more"]
        assert len(early["changes"]) == 2
        resumed = api.get(base + "/pull", headers=reader, params={"after": early["cursor"], "limit": 50})
        assert resumed.is_success, resumed.text
        assert EXCERPT not in hidden_only.text and EXCERPT not in resumed.text
        later = resumed.json()
        assert later["cursor"] == sent["cursor"] and not later["has_more"]
        feed = early["changes"] + later["changes"]
        assert [row["sequence"] for row in feed] == list(range(start + 1, sent["cursor"] + 1))
        hidden, other = feed[: len(input_rows)], feed[len(input_rows) :]
        assert any(row["object_id"] == f"chapter:{changed['id']}" for row in other)
        for placeholder in hidden:
            assert placeholder["payload"] == {"kind": "withheld", "reason": "reference_scope"}
            assert not placeholder["tombstone"] and placeholder["conflict_id"] is None
            assert all(
                placeholder[key].startswith("withheld:") for key in ("change_id", "object_id", "device_id")
            )
            assert placeholder["head_revision"] == placeholder["change_id"]
            assert placeholder["change_id"] not in original_ids
            assert placeholder["object_id"] not in {data["object_id"] for data in input_rows}
            assert api.get(base + f"/changes/{placeholder['change_id']}", headers=reader).status_code == 404
        assert (
            api.get(base + "/pull", headers=reader, params={"after": start, "limit": 50}).json()["changes"]
            == feed
        )
        for data in input_rows:
            identifier = data["change_id"]
            denied(api.get(base + f"/changes/{identifier}", headers=reader))
            denied(api.get(base + f"/receipts/{identifier}", headers=writer))
            denied(api.post(base + "/push", headers=writer, json=data))
        for data in (
            request(hidden[0]["change_id"], "chapter:unused", {"content": "text"}),
            request("reserved-object", hidden[0]["object_id"], {"content": "text"}),
            request("reserved-kind", "chapter:unused", {"kind": "withheld"}),
        ):
            denied(api.post(base + "/push", headers=writer, json=data), "sync_reserved")
        denied(
            api.post(
                base + "/devices",
                headers={"Authorization": f"Bearer {secret}"},
                json={"device_id": hidden[0]["device_id"]},
            ),
            "sync_reserved",
        )

    replica = Domain(Database(tmp_path / "after-hidden-legacy-receiver.sqlite"))
    import_manuscript(replica, legacy.baseline.model_copy(update={"chapters": [], "revisions": []}))
    receive = DesktopSync(replica)
    receive.enable(work, origin, tokens[1])
    adopted = receive.run(work, origin, tokens[1])
    assert adopted["cursor"] == sent["cursor"] and not adopted["conflicts"]
    assert replica.chapter(changed["id"])["content"] == changed["content"]
    assert len(adopted["deferred"]) == len(input_rows)
    assert all(row["object_id"].startswith("withheld:") for row in adopted["deferred"])
    assert receive.run(work, origin, tokens[1])["cursor"] == sent["cursor"]
    assert retained_rows(store, work, original_ids) == frozen


def test_conflict_reads_and_idempotent_resolve_cannot_return_retained_reference_text(
    tmp_path, loopback, legacy
):
    origin, _ = loopback
    work = legacy.branch
    base = f"/api/sync/works/{work}"
    writer = {"Authorization": f"Bearer {_grant(loopback, work, 'writer')}"}
    store = SyncStore(tmp_path / "cloud-workflow.sqlite")
    object_id = f"evidence:{legacy.report_id}"
    private = {"format": FORMAT, "kind": "evidence", "document": legacy.document}
    clean_body = json.dumps({"scope": "book", "items": []})
    clean = {
        **private,
        "document": {
            **legacy.document,
            "body": clean_body,
            "sha256": sha256(clean_body.encode()).hexdigest(),
        },
    }

    with httpx.Client(base_url=origin, trust_env=False, headers=writer) as api:
        seed_retained(store, work, request("old-private-head", object_id, private))
        unsafe_current = seed_retained(
            store,
            work,
            request("old-conflict-head", object_id, clean),
            conflicting_head="old-private-head",
        )
        denied(api.get(base + f"/conflicts/{unsafe_current.change_id}"))
        seed_retained(
            store,
            work,
            request("old-clean-head", object_id, clean, "old-private-head"),
        )
        delivery = completion_document(legacy)
        retained_delivery = request(
            "projected-only-delivery", f"evidence:{delivery['document']['id']}", delivery
        )
        seed_retained(store, work, retained_delivery)
        # This cloud has only the older report projection and its visible head is now clean.
        assert api.get(base + "/changes/old-clean-head").json()["payload"] == clean
        denied(api.get(base + "/changes/projected-only-delivery"))
        denied(api.post(base + "/push", json=retained_delivery))
        unsafe_base = seed_retained(
            store,
            work,
            request("old-conflict-base", object_id, clean, "old-private-head"),
            conflicting_head="old-clean-head",
        )
        unsafe_incoming = seed_retained(
            store,
            work,
            request("old-conflict-incoming", object_id, private),
            conflicting_head="old-clean-head",
        )
        for entry in (unsafe_current, unsafe_base, unsafe_incoming):
            denied(api.get(base + f"/conflicts/{entry.change_id}"))
            denied(api.get(base + f"/changes/{entry.change_id}"))
        left = api.post(base + "/push", json=request("clean-head", "chapter:separate", {"text": "左"}))
        assert left.is_success, left.text
        right = api.post(base + "/push", json=request("clean-incoming", "chapter:separate", {"text": "右"}))
        assert right.is_success, right.text
        visible = right.json()
        position = 0
        for hidden in (unsafe_current, unsafe_base, unsafe_incoming):
            page = api.get(base + "/conflicts", params={"after": position, "limit": 1}).json()
            assert page == {"conflicts": [], "cursor": hidden.sequence, "has_more": True}
            position = page["cursor"]
        page = api.get(base + "/conflicts", params={"after": position, "limit": 1}).json()
        assert page["cursor"] == visible["sequence"] and not page["has_more"]
        assert [row["conflict_id"] for row in page["conflicts"]] == [visible["change_id"]]
        assert api.get(base + "/conflicts/clean-incoming").json()["incoming"] == visible

        previous_resolution = {
            "change_id": "old-resolved",
            "parent_revision": "old-clean-head",
            "choice": "incoming",
        }
        pointer = base + f"/conflicts/{unsafe_incoming.change_id}/resolve"
        seed_retained(
            store,
            work,
            request("old-resolved", object_id, private, "old-clean-head"),
            resolution=(unsafe_incoming.change_id, previous_resolution),
        )
        # The byte-identical historical request itself contains no excerpt.
        assert EXCERPT not in json.dumps(previous_resolution)
        denied(api.post(pointer, json=previous_resolution))
        denied(api.get(base + "/receipts/old-resolved"))
        denied(api.get(base + "/changes/old-resolved"))
        denied(
            api.post(
                base + "/conflicts/clean-incoming/resolve",
                json={
                    "change_id": "withheld:bogus",
                    "parent_revision": "clean-head",
                    "choice": "current",
                },
            ),
            "sync_reserved",
        )
