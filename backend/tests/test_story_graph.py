import pytest

from ainovel.story_graph import graph, query_context


@pytest.fixture
def records(client, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "😀她读到渡口暗号，收起铜牌。")
    next_chapter = domain.create_chapter(story["branch_id"], "第二章")
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 2,
        "end": len(chapter["content"].encode("utf-16-le")) // 2,
        "quote": chapter["content"][1:],
    }

    def save(payload, old=None, expected=201):
        endpoint = f"/api/branches/{story['branch_id']}/story-records"
        body = {"base_story_revision": domain.branch(story["branch_id"])["story_revision"], "record": payload}
        if old:
            endpoint += "/" + old["id"]
            body["base_revision"] = old["revision"]
        result = (client.put if old else client.post)(endpoint, json=body)
        assert result.status_code == (200 if old and expected == 201 else expected), result.text
        if expected >= 400:
            return result.json()
        rows = result.json()["records"]
        return next(r for r in rows if r["id"] == old["id"]) if old else rows[-1]

    character = save({"kind": "character", "title": "宁禾", "aliases": ["小宁"]})
    event = save(
        {
            "kind": "event",
            "title": "读到暗号",
            "status": "actual",
            "story_order": 10,
            "story_time": "初一上午",
            "participants": [character["id"]],
            "sources": [source],
        }
    )
    return save, character, event, source, next_chapter


def test_story_time_knowledge_and_audience_are_separate(records, domain, story):
    save, character, event, source, next_chapter = records
    state = {
        "kind": "state",
        "title": "知道暗号",
        "character_id": character["id"],
        "event_id": event["id"],
        "category": "knowledge",
        "key": "渡口暗号",
        "value": "已知",
        "audience": "character",
        "sources": [source],
    }
    saved = save(state)
    assert query_context(domain, story["branch_id"], next_chapter["id"], 9, "character")["states"] == []
    assert [
        r["id"]
        for r in query_context(domain, story["branch_id"], next_chapter["id"], 10, "character")["states"]
    ] == [saved["id"]]
    assert query_context(domain, story["branch_id"], next_chapter["id"], 10, "reader")["states"] == []
    assert query_context(domain, story["branch_id"], source["chapter_id"], 10, "character")["states"] == []
    assert (
        len(
            query_context(
                domain, story["branch_id"], source["chapter_id"], 10, "character", text_position=source["end"]
            )["states"]
        )
        == 1
    )
    assert (
        len(query_context(domain, story["branch_id"], next_chapter["id"], None, "character")["uncertain"])
        == 1
    )


def test_aliases_identity_and_cross_version_edges_are_guarded(records, domain, story):
    save, character, event, source, _ = records
    assert save({"kind": "character", "title": "小宁"}, expected=409)["code"] == "character_alias"
    other = domain.create_novel("别的书", "")
    from ainovel.story_graph import SaveRecord, save_record
    from ainovel.db import DomainError

    with pytest.raises(DomainError, match="关联须属于"):
        save_record(
            domain,
            other["branch_id"],
            SaveRecord(
                base_story_revision=0,
                record={"kind": "event", "title": "越界", "participants": [character["id"]]},
            ),
        )
    save({**event["payload"], "after": [event["id"]]}, event, expected=400)


def test_upstream_edits_invalidate_records_and_keep_history(records, domain, story, client):
    save, character, event, source, next_chapter = records
    state = save(
        {
            "kind": "state",
            "title": "铜牌数量",
            "character_id": character["id"],
            "event_id": event["id"],
            "category": "resource",
            "key": "铜牌",
            "value": "1枚",
            "audience": "author",
            "sources": [source],
        }
    )
    updated = save({**event["payload"], "story_order": 11}, event)
    rows = {r["id"]: r for r in graph(domain, story["branch_id"])["records"]}
    assert rows[state["id"]]["invalid_reasons"]
    assert query_context(domain, story["branch_id"], next_chapter["id"], 12)["states"] == []
    save(state["payload"], state)
    assert len(query_context(domain, story["branch_id"], next_chapter["id"], 12)["states"]) == 1
    history = client.get(f"/api/branches/{story['branch_id']}/story-records/{updated['id']}/history").json()
    assert [h["payload"]["story_order"] for h in history] == [11, 10]
    domain.save(source["chapter_id"], source["revision"], "原文修改")
    assert all(
        r["invalid_reasons"]
        for r in graph(domain, story["branch_id"])["records"]
        if r["kind"] in {"event", "state"}
    )


def test_alias_extension_preserves_dependents_but_correction_and_revert_do_not(records, domain, story):
    save, character, event, _, _ = records
    extended = save({**character["payload"], "aliases": ["小宁", "宁姑娘"]}, character)
    rows = {r["id"]: r for r in graph(domain, story["branch_id"])["records"]}
    assert not rows[event["id"]]["invalid_reasons"]
    assert rows[event["id"]]["compatible_dependency_updates"]
    corrected = save({**extended["payload"], "profile": "更正身份"}, extended)
    save(extended["payload"], corrected)
    rows = {r["id"]: r for r in graph(domain, story["branch_id"])["records"]}
    assert rows[event["id"]]["invalid_reasons"]


def test_conflicting_states_remain_visible_and_events_do_not_accumulate(records, domain, story):
    save, character, event, source, next_chapter = records
    base = {
        "kind": "state",
        "title": "铜牌数量",
        "character_id": character["id"],
        "event_id": event["id"],
        "category": "resource",
        "key": "铜牌",
        "value": "1枚",
        "audience": "author",
        "sources": [source],
    }
    save(base)
    save({**base, "value": "2枚"})
    context = query_context(domain, story["branch_id"], next_chapter["id"], 11)
    assert len(context["events"]) == 1
    assert len(context["states"]) == 2 and len(context["conflicts"]) == 1


def test_promises_require_stage_evidence_and_record_author_decisions(records):
    save, _, event, source, _ = records
    promise = {
        "kind": "promise",
        "title": "铜牌的来历",
        "stages": [{"status": "planted", "event_id": event["id"]}],
    }
    save(promise, expected=400)
    promise["stages"][0]["sources"] = [source]
    saved = save(promise)
    save({**saved["payload"], "stages": [*promise["stages"], {"status": "abandoned"}]}, saved, expected=400)
    updated = save(
        {
            **saved["payload"],
            "stages": [*promise["stages"], {"status": "retained", "note": "下一卷明确处理"}],
        },
        saved,
    )
    assert updated["payload"]["stages"][-1]["note"] == "下一卷明确处理"


def test_later_state_supersedes_earlier_without_replaying_event(records, domain, story):
    save, character, event, source, next_chapter = records
    state = {
        "kind": "state",
        "title": "铜牌数量",
        "character_id": character["id"],
        "event_id": event["id"],
        "category": "resource",
        "key": "铜牌",
        "value": "1枚",
        "audience": "author",
        "sources": [source],
    }
    save(state)
    later = save({**event["payload"], "title": "交出铜牌", "story_order": 11, "after": [event["id"]]})
    save({**state, "event_id": later["id"], "value": "0枚"})
    assert (
        query_context(domain, story["branch_id"], next_chapter["id"], 10)["states"][0]["payload"]["value"]
        == "1枚"
    )
    current = query_context(domain, story["branch_id"], next_chapter["id"], 11)
    assert len(current["states"]) == 1 and current["states"][0]["payload"]["value"] == "0枚"


def test_completion_reads_maintained_promises_and_changes_invalidate_acceptance(records, domain, story):
    save, _, event, source, _ = records
    promise = save(
        {
            "kind": "promise",
            "title": "暗号的含义",
            "stages": [{"status": "planted", "event_id": event["id"], "sources": [source]}],
        }
    )
    result = domain.completion(story["branch_id"])
    assert not result["promises"]["closed"]
    save(
        {
            **promise["payload"],
            "stages": [
                *promise["payload"]["stages"],
                {"status": "resolved", "event_id": event["id"], "sources": [source]},
            ],
        },
        promise,
    )
    assert domain.completion(story["branch_id"])["promises"]["closed"]


def test_graph_backup_restore_and_old_schema_upgrade(records, domain, settings, tmp_path):
    from ainovel.backups import create_backup, unpack_verified
    from ainovel.db import Database
    from ainovel.domain import Domain

    _, character, event, _, _ = records
    original = graph(domain, event["branch_id"])
    backup = create_backup(settings, domain.db)
    target = tmp_path / "isolated-story-restore"
    target.mkdir()
    unpack_verified(settings.data_dir / "backups" / (backup["id"] + ".zip"), target)
    restored = Domain(Database(target / "manuscripts.sqlite"))
    assert graph(restored, event["branch_id"]) == original
    # Simulate a pre-extension copy: no record data is present before migration.
    old = Database(tmp_path / "old.sqlite")
    with old.connect(True) as tx:
        tx.execute("DROP TABLE story_record_dependencies")
        tx.execute("DROP TABLE story_record_history")
        tx.execute("DROP TABLE story_records")
        tx.execute("DELETE FROM extension_migrations WHERE name='story-graph/1'")
    upgraded = Database(old.path)
    assert upgraded.all("SELECT * FROM story_records") == []
    assert upgraded.one("SELECT name FROM extension_migrations WHERE name='story-graph/1'")
    assert character["id"] in {r["id"] for r in original["records"]}


def test_one_event_can_have_later_presentations_without_leaking_future_quote(records, domain, story):
    save, _, event, source, next_chapter = records
    later = domain.save(next_chapter["id"], 0, "后来她再次讲述那次认出暗号的经历。")
    repeat = {
        "chapter_id": later["id"],
        "revision": later["revision"],
        "start": 0,
        "end": len(later["content"]),
        "quote": later["content"],
    }
    save({**event["payload"], "sources": [source, repeat]}, event)
    early = query_context(domain, story["branch_id"], next_chapter["id"], 10)
    assert len(early["events"]) == 1
    assert early["events"][0]["payload"]["sources"] == [source]
    whole = query_context(domain, story["branch_id"], None, 10)
    assert len(whole["events"]) == 1 and len(whole["events"][0]["payload"]["sources"]) == 2
