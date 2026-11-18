from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ainovel import story_sync
from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve
from ainovel.domain import Domain
from ainovel.manuscript_exchange import (
    export_manuscript,
    import_manuscript,
    native_revision,
    store_source_revision,
)
from ainovel.story_graph import SaveRecord, graph, save_record
from test_outline_sync import record
from test_settings_sync import receipt


def paired(source, branch, path):
    target = Domain(Database(path))
    import_manuscript(target, export_manuscript(source, branch))
    for domain in (source, target):
        with domain.db.connect() as tx:
            tx.executescript(story_sync.SCHEMA)
    return target


def objects(domain, branch):
    with domain.db.connect() as tx:
        values = story_sync.draft_story_objects(tx, branch)
    versions = [item.version for item in values if isinstance(item, story_sync.StoryVersionObject)]
    heads = {item.record_id: item for item in values if isinstance(item, story_sync.StoryHeadObject)}
    return versions, heads


def visible(domain, branch, identifier):
    return next(item for item in graph(domain, branch)["records"] if item["id"] == identifier)


@contextmanager
def linked_cloud(source, receiver, branch, tmp_path):
    origin = "http://127.0.0.1:8888"
    provision = "isolated-story-sync-access-0123456789"
    endpoint = f"/api/sync/works/{branch}"
    with TestClient(create_sync_app(tmp_path / "story-cloud.sqlite", provision)) as cloud:
        lost = {}

        def provision_device(name):
            response = cloud.post(
                endpoint + "/devices",
                headers={"Authorization": f"Bearer {provision}"},
                json={"device_id": name},
            )
            assert response.status_code == 201, response.text
            return response.json()["token"]

        def transport(_, token, method, path, payload):
            response = cloud.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=payload)
            result = response.json()
            if not response.is_success:
                raise DomainError(result["code"], result["message"], response.status_code)
            if payload and payload.get("object_id") == lost.get("next") and path.endswith("/push"):
                lost["receipt"] = result
                del lost["next"]
                raise DomainError("sync_transport", "模拟云端写入成功后响应中断。", 502)
            return result

        left, right = DesktopSync(source, transport), DesktopSync(receiver, transport)
        left_token, right_token = provision_device("story-left"), provision_device("story-right")
        left.enable(branch, origin, left_token)
        right.enable(branch, origin, right_token)
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert not right.run(branch, origin, right_token)["conflicts"]
        yield left, right, origin, left_token, right_token, cloud, endpoint, lost


def choose_current(sync, branch, origin, token, conflict, compared, key):
    return sync.resolve(
        branch,
        origin,
        token,
        conflict["id"],
        Resolve(
            origin=origin,
            token=token,
            choice="current",
            parent_revision=compared["remote"]["head_revision"],
            local_sha256=compared["local_sha256"],
            idempotency_key=key,
        ),
    )


def revise_notes(domain, branch, identifier, note):
    old = visible(domain, branch, identifier)
    with domain.db.connect(True) as tx:
        record(tx, branch, {**old["payload"], "notes": note}, existing=old)


def test_sync_keeps_real_promise_stage_and_waits_for_exact_native_manuscript(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    target = paired(domain, branch, tmp_path / "story-other.sqlite")
    chapter = domain.save(chapter_id, 0, "门外📯响了一声。")
    evidence = {
        "chapter_id": chapter_id,
        "revision": chapter["revision"],
        "start": 2,
        "end": 4,
        "quote": "📯",
    }
    with domain.db.connect(True) as tx:
        chapter_revision_id = tx.execute(
            "SELECT id FROM revisions WHERE chapter_id=? AND revision=?", (chapter_id, chapter["revision"])
        ).fetchone()[0]
        chapter_source = native_revision(tx, branch, chapter_revision_id)
        person = record(tx, branch, {"kind": "character", "title": "师姐", "profile": "识得铜铃"})
        event = record(
            tx,
            branch,
            {
                "kind": "event",
                "title": "听见号角",
                "status": "actual",
                "participants": [person],
                "sources": [evidence],
            },
        )
        promise = record(
            tx,
            branch,
            {
                "kind": "promise",
                "title": "门外为何会响",
                "stages": [{"status": "planned"}],
            },
        )
    original = visible(domain, branch, promise)
    with domain.db.connect(True) as tx:
        record(
            tx,
            branch,
            {
                **original["payload"],
                "stages": [
                    {"status": "planned"},
                    {"status": "planted", "event_id": event, "sources": [evidence]},
                    {"status": "resolved", "event_id": event, "sources": [evidence]},
                ],
            },
            existing=original,
        )
    versions, heads = objects(domain, branch)
    assert [row.record_id for row in versions] == [person, event, promise, promise]
    actual = versions[-1]
    assert actual.parent_id == versions[-2].id and actual.dependencies == {event: versions[1].id}
    assert actual.evidence.stages[2][0].revision_id == chapter_revision_id
    assert actual.record.stages[-1].status == "resolved" and actual.record.stages[-1].sources == []
    wrapper = story_sync.version_object(branch, actual)
    assert "📯" not in wrapper.model_dump_json()
    object_id = f"story-version:{actual.id}"
    assert story_sync.parse_story_object(branch, object_id, wrapper.model_dump(), False) == wrapper
    modified = wrapper.model_dump()
    modified["version"]["record"]["title"] = "不同于已提交的内容"
    with pytest.raises(DomainError, match="版本哈希"):
        story_sync.parse_story_object(branch, object_id, modified, False)
    with pytest.raises(DomainError, match="同一工作版本"):
        story_sync.parse_story_object("other-branch", object_id, wrapper.model_dump(), False)
    with pytest.raises(DomainError, match="归档须"):
        story_sync.parse_story_object(branch, object_id, wrapper.model_dump(), True)

    with target.db.connect(True) as tx:
        story_sync.observe_story_version(tx, branch, versions[0], receipt("person-source"))
        story_sync.apply_story_head(tx, branch, heads[person], receipt("person-head"))
        with pytest.raises(DomainError, match="原文证据到齐"):
            story_sync.observe_story_version(tx, branch, versions[1], receipt("event-without-source"))
        store_source_revision(tx, branch, chapter_source)
        for index, version in enumerate(versions[1:], 2):
            story_sync.observe_story_version(tx, branch, version, receipt(f"version-{index}", index))
        story_sync.apply_story_head(
            tx, branch, story_sync.head_object(branch, promise, versions[2].id), receipt("planned-head")
        )
        with pytest.raises(DomainError, match="原文证据到齐"):
            story_sync.apply_story_head(tx, branch, heads[event], receipt("event-head"))
        with pytest.raises(DomainError, match="关联版本"):
            story_sync.apply_story_head(tx, branch, heads[promise], receipt("promise-head"))
        assert story_sync.read_story_head(tx, branch, promise) == versions[2].id
        prior = dict(tx.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
        target.revise_tx(
            tx,
            prior,
            chapter_source.content,
            chapter_source.cause,
            revision_id=chapter_source.id,
            created=chapter_source.created,
        )
    target.save(chapter_id, 1, "后来改写，不再出现号角。")
    with target.db.connect(True) as tx:
        story_sync.apply_story_head(tx, branch, heads[event], receipt("event-head"))
        story_sync.apply_story_head(tx, branch, heads[promise], receipt("promise-head"))
        story_sync.apply_story_head(tx, branch, heads[promise], receipt("promise-head"))
        assert story_sync.read_story_head(tx, branch, promise) == actual.id
        mapped = tx.execute(
            "SELECT head_change_id,head_device_id FROM story_sync_applied WHERE record_id=? AND source_id=?",
            (promise, actual.id),
        ).fetchall()
        assert [tuple(row) for row in mapped] == [("promise-head", "writing-laptop")]
    received = visible(target, branch, promise)
    assert received["payload"]["stages"][-1]["status"] == "resolved"
    assert received["payload"]["stages"][-1]["sources"] == [evidence]
    assert "来源正文版本已改变或待复检" in received["invalid_reasons"]
    assert visible(target, branch, event)["payload"]["status"] == "actual"


def test_story_sync_freezes_dependency_history_and_refuses_unselected_dependency(domain, story, tmp_path):
    branch = story["branch_id"]
    target = paired(domain, branch, tmp_path / "story-dependencies.sqlite")
    with domain.db.connect(True) as tx:
        person = record(tx, branch, {"kind": "character", "title": "师姐", "profile": "暂时离山"})
        event = record(
            tx,
            branch,
            {
                "kind": "event",
                "title": "追寻师姐",
                "participants": [person],
            },
        )
    first_person, first_event = objects(domain, branch)[0]
    local_person = visible(domain, branch, person)
    local_event = visible(domain, branch, event)
    with domain.db.connect(True) as tx:
        record(tx, branch, {**local_person["payload"], "profile": "改为留在山中"}, existing=local_person)
        record(tx, branch, {**local_event["payload"], "notes": "不再下山寻找。"}, existing=local_event)
    versions, heads = objects(domain, branch)
    person_v2, event_v2 = versions[-2:]
    assert versions[:2] == [first_person, first_event]
    assert first_event.dependencies == {person: first_person.id}
    assert event_v2.dependencies == {person: person_v2.id}
    with target.db.connect(True) as tx:
        for index, value in enumerate(versions):
            story_sync.observe_story_version(tx, branch, value, receipt(f"version-{index}", index))
        story_sync.apply_story_head(
            tx, branch, story_sync.head_object(branch, person, first_person.id), receipt("base-person")
        )
        story_sync.apply_story_head(
            tx, branch, story_sync.head_object(branch, event, first_event.id), receipt("base-event")
        )
        with pytest.raises(DomainError, match="尚未采用"):
            story_sync.apply_story_head(tx, branch, heads[event], receipt("event-advance"))
        assert story_sync.read_story_head(tx, branch, event) == first_event.id
        story_sync.apply_story_head(tx, branch, heads[person], receipt("person-advance"))
        story_sync.apply_story_head(tx, branch, heads[event], receipt("event-advance"))
        assert story_sync.read_story_head(tx, branch, event) == event_v2.id
    assert visible(target, branch, event)["payload"]["notes"] == "不再下山寻找。"


def test_story_archive_head_is_an_auditable_revision_even_without_a_local_predecessor(
    domain, story, tmp_path
):
    branch = story["branch_id"]
    target = paired(domain, branch, tmp_path / "story-archive.sqlite")
    data = {"kind": "character", "title": "暂离的师姐"}
    with pytest.raises(DomainError, match="不可|不能"):
        save_record(
            domain,
            branch,
            SaveRecord(
                base_story_revision=domain.branch(branch)["story_revision"], record=data, archived=True
            ),
        )
    with domain.db.connect(True) as tx:
        identifier = record(tx, branch, data)
    save_record(
        domain,
        branch,
        SaveRecord(
            base_story_revision=domain.branch(branch)["story_revision"],
            base_revision=0,
            record=data,
            archived=True,
        ),
        identifier,
    )
    (previous, archived), heads = objects(domain, branch)
    assert archived.archived and archived.parent_id == previous.id
    with target.db.connect(True) as tx:
        story_sync.observe_story_version(tx, branch, previous, receipt("before-archive"))
        story_sync.observe_story_version(tx, branch, archived, receipt("archive-record"))
        story_sync.apply_story_head(tx, branch, heads[identifier], receipt("archive-head"))
        story_sync.apply_story_head(tx, branch, heads[identifier], receipt("archive-head"))
        assert story_sync.read_story_head(tx, branch, identifier) == archived.id
        assert not story_sync.read_story_version(tx, branch, previous.id).archived
        assert (
            tx.execute(
                "SELECT COUNT(*) FROM story_record_history WHERE record_id=?", (identifier,)
            ).fetchone()[0]
            == 1
        )
    assert visible(target, branch, identifier)["archived"]


def test_cloud_independent_story_heads_cas_and_lost_receipt(domain, story, tmp_path):
    branch = story["branch_id"]
    target = paired(domain, branch, tmp_path / "cloud-story-recipient.sqlite")
    with linked_cloud(domain, target, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, cloud, endpoint, lost = linked
        with domain.db.connect(True) as tx:
            first = record(tx, branch, {"kind": "event", "title": "敲门"})
            second = record(tx, branch, {"kind": "event", "title": "收铃"})
        left.run(branch, origin, left_token)
        assert not right.run(branch, origin, right_token)["conflicts"]
        revise_notes(domain, branch, first, "左端改变敲门的动机。")
        revise_notes(target, branch, second, "右端改变接铃的位置。")
        lost["next"] = f"story:{first}"
        with pytest.raises(DomainError, match="响应中断"):
            left.run(branch, origin, left_token)
        lost_receipt = lost["receipt"]
        assert lost_receipt["status"] == "applied"
        assert left._state(branch)["pending"]
        assert not left.run(branch, origin, left_token)["pending"]
        assert not right.run(branch, origin, right_token)["conflicts"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert visible(domain, branch, second)["payload"]["notes"] == "右端改变接铃的位置。"
        assert visible(target, branch, first)["payload"]["notes"] == "左端改变敲门的动机。"
        auth = {"Authorization": f"Bearer {left_token}"}
        response = cloud.get(endpoint + "/pull", params={"after": 0, "limit": 50}, headers=auth)
        assert response.status_code == 200, response.text
        receipts = [row for row in response.json()["changes"] if row["object_id"] == f"story:{first}"]
        assert len(receipts) == 2 and receipts[1]["change_id"] == lost_receipt["change_id"]
        base_id = receipts[1]["payload"]["revision_id"]

        revise_notes(domain, branch, first, "云当前留下停顿。")
        revise_notes(target, branch, first, "另一端直接叩门。")
        left.run(branch, origin, left_token)
        pending = right.run(branch, origin, right_token)
        conflict = next(item for item in pending["conflicts"] if item["object_id"] == f"story:{first}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["base"]["payload"]["revision_id"] == base_id
        assert {item["record"]["notes"] for item in compared["story_versions"].values()} == {
            "左端改变敲门的动机。",
            "云当前留下停顿。",
            "另一端直接叩门。",
        }
        kept_id = compared["remote"]["incoming"]["payload"]["revision_id"]
        resolved = choose_current(
            right, branch, origin, right_token, conflict, compared, "choose-story-current"
        )
        assert not resolved["conflicts"] and not resolved["deferred"], resolved
        assert visible(target, branch, first)["payload"]["notes"] == "云当前留下停顿。"
        with target.db.connect() as tx:
            assert story_sync.read_story_version(tx, branch, kept_id).record.notes == "另一端直接叩门。"
            unrelated = story_sync.read_story_head(tx, branch, second)
        forged = story_sync.head_object(branch, first, unrelated).model_dump()
        rejected = cloud.post(
            endpoint + "/push",
            headers=auth,
            json={
                "change_id": "reject-wrong-record-source",
                "object_id": f"story:{first}",
                "parent_revision": None,
                "tombstone": False,
                "payload": forged,
            },
        )
        assert rejected.status_code == 409 and rejected.json()["code"] == "sync_story"


def test_cloud_story_stage_waits_until_conflicting_chapter_source_is_adopted(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    domain.save(chapter_id, 0, "师姐来到门外。")
    target = paired(domain, branch, tmp_path / "story-proof-recipient.sqlite")
    with linked_cloud(domain, target, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        with domain.db.connect(True) as tx:
            promise = record(
                tx, branch, {"kind": "promise", "title": "何处能听见号角", "stages": [{"status": "planned"}]}
            )
        left.run(branch, origin, left_token)
        right.run(branch, origin, right_token)
        new_chapter = domain.save(chapter_id, 1, "门外📯响起，线索就此明白。")
        target.save(chapter_id, 1, "本机更改，写成铜铃自行响起。")
        evidence = {
            "chapter_id": chapter_id,
            "revision": new_chapter["revision"],
            "start": 2,
            "end": 4,
            "quote": "📯",
        }
        prior = visible(domain, branch, promise)
        with domain.db.connect(True) as tx:
            record(
                tx,
                branch,
                {
                    **prior["payload"],
                    "stages": [
                        {"status": "planned"},
                        {"status": "resolved", "sources": [evidence]},
                    ],
                },
                existing=prior,
            )
        left.run(branch, origin, left_token)
        pending = right.run(branch, origin, right_token)
        deferred = {item["object_id"]: item["reason"] for item in pending["deferred"]}
        assert deferred[f"story:{promise}"] == "缺少故事记录引用的原始章稿，待原文证据到齐。"
        assert visible(target, branch, promise)["payload"]["stages"][-1]["status"] == "planned"
        conflict = next(item for item in pending["conflicts"] if item["object_id"] == f"chapter:{chapter_id}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        resolved = choose_current(
            right, branch, origin, right_token, conflict, compared, "choose-proof-chapter"
        )
        assert not resolved["conflicts"] and not resolved["deferred"], resolved
        native = target.chapter(chapter_id)
        adopted = visible(target, branch, promise)
        assert native["revision"] == 3 and native["content"] == new_chapter["content"]
        assert adopted["payload"]["stages"][-1]["status"] == "resolved"
        assert adopted["payload"]["stages"][-1]["sources"] == [{**evidence, "revision": 3}]
        assert not adopted["invalid_reasons"]
        with target.db.connect() as tx:
            version_id = story_sync.read_story_head(tx, branch, promise)
            portable = story_sync.read_story_version(tx, branch, version_id)
            original_id = portable.evidence.stages[-1][0].revision_id
            assert tx.execute(
                "SELECT 1 FROM story_sync_chapter_sources WHERE branch_id=? AND source_id=?",
                (branch, original_id),
            ).fetchone()


def test_oversized_story_record_does_not_block_unrelated_sync(domain, story, tmp_path, monkeypatch):
    branch = story["branch_id"]
    target = paired(domain, branch, tmp_path / "story-size-recipient.sqlite")
    with linked_cloud(domain, target, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        monkeypatch.setattr("ainovel.desktop_sync.MAX_OBJECT_BYTES", 2048)
        with domain.db.connect(True) as tx:
            big = record(tx, branch, {"kind": "event", "title": "超出同步上限", "notes": "长" * 1000})
            small = record(tx, branch, {"kind": "event", "title": "独立短记录"})
        result = left.run(branch, origin, left_token)
        assert any(
            item["object_id"] == f"story:{big}" and "体积限制" in item["reason"]
            for item in result["deferred"]
        )
        assert not right.run(branch, origin, right_token)["conflicts"]
        assert [item["id"] for item in graph(target, branch)["records"]] == [small]
