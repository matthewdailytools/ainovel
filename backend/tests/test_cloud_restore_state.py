from hashlib import sha256

from fastapi.testclient import TestClient

from ainovel import restore_state_sync as sync
from ainovel.chapter_conflicts import ChapterObject
from ainovel.cloud_ledger import LEDGER_HEADER
from ainovel.cloud_sync import create_sync_app
from ainovel.cloud_sync_backup import create_backup, restore_backup
from ainovel.manuscript_exchange import PortableChapter, PortableRevision, RevisionObject
from ainovel.outline_portability import OutlineVersion
from ainovel.outline_sync import head_object as outline_head
from ainovel.outline_sync import version_object as outline_version
from test_cloud_sync import ADMIN, WORK, grant, parent, push, service as service
from test_outline_workspace import chapter_plan


BRANCH = "bell"


def request(change_id, model, previous=None):
    return parent(change_id, previous, model.model_dump(), object_id=sync.object_id(model))


def issue(client, headers, change_id, model, previous=None):
    return push(client, headers, request(change_id, model, previous))


def rejected(client, headers, data, code):
    response = client.post(WORK + "/push", headers=headers, json=data)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code


def chapter_revision(identifier, chapter_id, text, prior=None):
    return PortableRevision(
        id=identifier,
        chapter_id=chapter_id,
        revision=prior.revision + 1 if prior else 0,
        parent_id=prior.id if prior else None,
        cause="隔离归档验收",
        content=text,
        sha256=sha256(text.encode()).hexdigest(),
        created=0,
    )


def publish_chapter(client, credential, revision, previous=None):
    source = RevisionObject(format=sync.FORMAT, kind="revision", branch_id=BRANCH, revision=revision)
    push(
        client,
        credential,
        parent("source-" + revision.id, payload=source.model_dump(), object_id=f"revision:{revision.id}"),
    )
    chapter = PortableChapter(
        id=revision.chapter_id,
        branch_id=BRANCH,
        title="归铃",
        position=1,
        revision=revision.revision,
        archived=0,
        stale=0,
    )
    value = ChapterObject(format=sync.FORMAT, kind="chapter", chapter=chapter, head=revision)
    return push(
        client,
        credential,
        parent("chapter-" + revision.id, previous, value.model_dump(), object_id=f"chapter:{chapter.id}"),
    )


def facts(revision, evidence=(), previous=None):
    content = {
        "parent_id": previous,
        "chapter_id": revision.chapter_id,
        "chapter_revision_id": revision.id,
        "facts": list(evidence),
    }
    return sync.FactSetVersion(id=sync.version_id(BRANCH, content), **content)


def test_cloud_facts_wait_for_exact_source_and_keep_the_conflicting_and_archived_versions(service, tmp_path):
    cloud, database = service
    desktop = grant(cloud, "desktop")
    web = grant(cloud, "web")
    first = chapter_revision("original", "chapter-one", "😀\r\n铜铃已归还。")
    quote = "铜铃已归还。"
    fact = {
        "subject": "铜铃",
        "kind": "event",
        "statement": "铃已交还原主。",
        "truth": "fact",
        "story_time": "卷末",
        "evidence": {
            "chapter_id": first.chapter_id,
            "revision_id": first.id,
            "start": 4,
            "end": 4 + len(quote),
            "quote_sha256": sha256(quote.encode()).hexdigest(),
        },
    }
    original = facts(first, [fact])
    first_version = sync.version_object(BRANCH, original)
    first_head = sync.head_object(BRANCH, original)
    rejected(cloud, desktop, request("unpublished", first_head), sync.DEPENDENCY)
    rejected(cloud, desktop, request("no-source", first_version), sync.DEPENDENCY)
    chapter = publish_chapter(cloud, desktop, first)
    wrong_quote = fact | {"evidence": {**fact["evidence"], "start": 3}}
    invalid = sync.version_object(BRANCH, facts(first, [wrong_quote]))
    rejected(cloud, desktop, request("invalid-proof", invalid), sync.INTEGRITY)
    forged = request("invalid-id", first_version)
    forged["payload"]["version"]["facts"][0]["statement"] = "试图修改已封存值。"
    rejected(cloud, desktop, forged, sync.INTEGRITY)
    assert issue(cloud, desktop, "original-source", first_version)["status"] == "applied"
    head = issue(cloud, desktop, "original-head", first_head)
    assert issue(cloud, desktop, "original-head", first_head) == head

    updated = chapter_revision("new-save", first.chapter_id, "旧铃仍留在门外。", first)
    next_chapter = publish_chapter(cloud, desktop, updated, chapter["head_revision"])
    no_facts = facts(updated, previous=original.id)
    issue(cloud, desktop, "empty-source", sync.version_object(BRANCH, no_facts))
    cleared = issue(cloud, desktop, "empty-head", sync.head_object(BRANCH, no_facts), head["head_revision"])
    competitor = facts(first, [fact | {"statement": "师姐将铃交还原主。"}], previous=original.id)
    issue(cloud, web, "divergent-source", sync.version_object(BRANCH, competitor))
    incoming = issue(
        cloud, web, "divergent-head", sync.head_object(BRANCH, competitor), head["head_revision"]
    )
    assert incoming["status"] == "conflict" and incoming["head_revision"] == cleared["head_revision"]
    compare = cloud.get(WORK + "/conflicts/divergent-head", headers=web).json()
    assert compare["incoming"]["payload"] == sync.head_object(BRANCH, competitor).model_dump()
    not_current = cloud.post(
        WORK + "/conflicts/divergent-head/resolve",
        headers=web,
        json={
            "change_id": "try-old-facts",
            "choice": "incoming",
            "parent_revision": cleared["head_revision"],
        },
    )
    assert not_current.status_code == 409 and not_current.json()["code"] == sync.DEPENDENCY
    assert (
        cloud.get(WORK + "/changes/original-source", headers=web).json()["payload"]
        == first_version.model_dump()
    )

    push(
        cloud,
        desktop,
        parent(
            "archive-chapter",
            next_chapter["head_revision"],
            tombstone=True,
            object_id=f"chapter:{first.chapter_id}",
        ),
    )
    archive = issue(
        cloud, desktop, "archived-empty-head", sync.head_object(BRANCH, no_facts), cleared["head_revision"]
    )
    assert archive["status"] == "applied" and not archive["tombstone"]

    limit = target("book", None, values={"min_chars": None, "max_chars": 100, "expected_chapters": None})
    issue(cloud, desktop, "saved-book-length", sync.version_object(BRANCH, limit))
    length = issue(cloud, desktop, "adopted-book-length", sync.head_object(BRANCH, limit))
    old_ledger = cloud.get(WORK + "/checkpoint", headers=desktop).json()
    backup, restored = tmp_path / "sealed-restore-state.sqlite", tmp_path / "new-ledger.sqlite"
    backup_receipt = create_backup(database, backup)
    restore_backup(backup, restored, expected_sha256=backup_receipt["sha256"])
    with TestClient(create_sync_app(restored, ADMIN["Authorization"].split(" ", 1)[1])) as new_cloud:
        assert new_cloud.get(WORK + "/pull", headers=desktop).status_code == 401
        connected = grant(new_cloud, "desktop", rotate=True)
        checkpoint = new_cloud.get(WORK + "/checkpoint", headers=connected).json()
        assert (
            checkpoint["cursor"] == old_ledger["cursor"]
            and checkpoint["ledger_id"] != old_ledger["ledger_id"]
        )
        old_binding = {**connected, LEDGER_HEADER: old_ledger["ledger_id"]}
        retry = new_cloud.post(WORK + "/push", headers=old_binding, json=request("late-retry", first_head))
        assert retry.status_code == 409 and retry.json()["code"] == "sync_ledger_replaced"
        rebound = {**connected, LEDGER_HEADER: checkpoint["ledger_id"]}
        pulled = new_cloud.get(WORK + "/pull", headers=rebound).json()
        assert pulled["cursor"] == checkpoint["cursor"]
        assert {archive["head_revision"], length["head_revision"]} <= {
            row["head_revision"] for row in pulled["changes"]
        }
        assert new_cloud.get(WORK + "/changes/original-source", headers=rebound).json()["payload"] == (
            first_version.model_dump()
        )


def target(scope, scope_id, outline_id=None, values=None, previous=None):
    data = {
        "parent_id": previous,
        "scope": scope,
        "scope_id": scope_id,
        "outline_version_id": outline_id,
        "targets": values,
    }
    return sync.LengthTargetVersion(id=sync.version_id(BRANCH, data), **data)


def test_cloud_length_targets_use_owned_scopes_frozen_outlines_and_explicit_clear(service):
    cloud, _ = service
    desktop = grant(cloud, "desktop")
    volume_id = "v" * 128
    outline = OutlineVersion(
        id="first-outline",
        parent_id=None,
        project_version_id=None,
        settings_version_id=None,
        plan={
            "summary": "铜铃归还。",
            "volumes": [
                {
                    "id": volume_id,
                    "title": "第一卷",
                    "summary": "归铃。",
                    "units": [
                        {
                            "id": "return",
                            "title": "辨认失主",
                            "summary": "确认失主后交还。",
                            "chapters": [{**chapter_plan("chapter-one", "归还铜铃"), "setting_ids": []}],
                        }
                    ],
                }
            ],
        },
    )
    values = {"min_chars": 25, "max_chars": 50, "expected_chapters": 1}
    book = target("book", None, values=values)
    chapter = target("chapter", "chapter-one", values=values)
    volume = target("volume", volume_id, outline.id, values)
    chapter_version = sync.version_object(BRANCH, chapter)
    volume_version = sync.version_object(BRANCH, volume)
    rejected(cloud, desktop, request("missing-chapter", chapter_version), sync.DEPENDENCY)
    rejected(cloud, desktop, request("missing-outline", volume_version), sync.DEPENDENCY)
    publish_chapter(cloud, desktop, chapter_revision("chapter-head", "chapter-one", "铜铃已归还。"))
    issue(cloud, desktop, "chapter-target", chapter_version)
    issue(cloud, desktop, "chapter-target-head", sync.head_object(BRANCH, chapter))
    issue(cloud, desktop, "book-target", sync.version_object(BRANCH, book))
    issue(cloud, desktop, "book-target-head", sync.head_object(BRANCH, book))

    push(
        cloud,
        desktop,
        parent(
            "outline-version",
            payload=outline_version(BRANCH, outline).model_dump(),
            object_id=f"outline-version:{outline.id}",
        ),
    )
    issue(cloud, desktop, "volume-target", volume_version)
    head = sync.head_object(BRANCH, volume)
    assert len(sync.object_id(head)) < 128
    rejected(cloud, desktop, request("outline-not-adopted", head), sync.DEPENDENCY)
    wrong_parent = target("volume", volume_id, outline.id, values, previous=book.id)
    rejected(
        cloud,
        desktop,
        request("book-parent-for-volume", sync.version_object(BRANCH, wrong_parent)),
        sync.INTEGRITY,
    )
    push(
        cloud,
        desktop,
        parent(
            "outline-adopted",
            payload=outline_head(BRANCH, outline.id).model_dump(),
            object_id=f"outline:{BRANCH}",
        ),
    )
    adopted = issue(cloud, desktop, "volume-target-head", head)
    invalid_delete = parent(
        "erase-volume-target", adopted["head_revision"], tombstone=True, object_id=sync.object_id(head)
    )
    rejected(cloud, desktop, invalid_delete, sync.INTEGRITY)
    clear = target("volume", volume_id, outline.id, previous=volume.id)
    stored = issue(cloud, desktop, "cleared-target", sync.version_object(BRANCH, clear))
    assert stored["payload"]["version"]["targets"] is None
    assert (
        issue(
            cloud, desktop, "cleared-target-head", sync.head_object(BRANCH, clear), adopted["head_revision"]
        )["status"]
        == "applied"
    )
    rows = cloud.get(WORK + "/pull", headers=desktop).json()["changes"]
    assert {row["object_id"] for row in rows if row["object_id"].startswith("length-target-head:")} == {
        sync.object_id(sync.head_object(BRANCH, value)) for value in (book, volume, chapter)
    }
