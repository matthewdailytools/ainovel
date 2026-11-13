"""Old clients may omit event fields; only explicit null detaches a legacy basis."""

import json

import pytest

from ainovel.db import DomainError
from ainovel.scene_time import SaveScene, save_scene, scenes
from ainovel.story_graph import SaveRecord, save_record


@pytest.fixture
def legacy_binding(domain, story):
    chapter = domain.save(story["chapter_id"], 0, "😀𠮷看见渡船。")
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"].encode("utf-16-le")) // 2,
        "quote": chapter["content"],
    }
    event = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "event",
                "title": "看见渡船",
                "story_order": 3,
                "status": "actual",
                "sources": [source],
            },
        ),
    )["records"][0]
    saved = save_scene(
        domain,
        story["branch_id"],
        chapter["id"],
        SaveScene(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            scene={
                "title": "渡口",
                "source_revision": chapter["revision"],
                "start": 0,
                "end": source["end"],
                "story_order": 3,
                "event_id": event["id"],
                "event_revision": event["revision"],
                "audience": "author",
            },
        ),
    )["scenes"][0]
    return chapter, event, saved


def edit(domain, story, row, omitted=(), **changes):
    payload = {k: v for k, v in row["payload"].items() if k not in omitted}
    payload.update(title="作者修改标题", note="保留旧事件来源", **changes)
    return SaveScene(
        base_story_revision=domain.branch(story["branch_id"])["story_revision"],
        base_revision=row["revision"],
        scene=payload,
    )


OMITTED = [("event_id", "event_revision"), ("event_id",), ("event_revision",)]


@pytest.mark.parametrize("omitted", OMITTED)
def test_legacy_metadata_edit_inherits_only_omitted_event_fields(legacy_binding, domain, story, omitted):
    chapter, event, old = legacy_binding
    saved = save_scene(
        domain, story["branch_id"], chapter["id"], edit(domain, story, old, omitted), old["id"]
    )["scenes"][0]
    assert saved["payload"]["event_id"] == event["id"]
    assert saved["payload"]["event_revision"] == event["revision"]
    assert saved["payload"]["title"] == "作者修改标题" and not saved["stale"]
    assert "order_basis" not in saved["payload"]
    assert domain.chapter(chapter["id"])["content"] == chapter["content"]
    assert len(domain.db.all("SELECT * FROM scene_time_history WHERE scene_id=?", (old["id"],))) == 2


@pytest.mark.parametrize("omitted", OMITTED)
def test_omission_cannot_clear_a_stale_legacy_event_dependency(legacy_binding, domain, story, omitted):
    chapter, event, old = legacy_binding
    save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            base_revision=event["revision"],
            record={**event["payload"], "notes": "作者已修正事件含义"},
        ),
        event["id"],
    )
    before = domain.branch(story["branch_id"])["story_revision"]
    with pytest.raises(DomainError, match="事件依据已改变"):
        save_scene(domain, story["branch_id"], chapter["id"], edit(domain, story, old, omitted), old["id"])
    assert domain.branch(story["branch_id"])["story_revision"] == before
    row = scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]
    assert row["stale"] and row["revision"] == old["revision"]
    assert row["payload"] == old["payload"]
    assert len(domain.db.all("SELECT * FROM scene_time_history WHERE scene_id=?", (old["id"],))) == 1


def test_both_explicit_nulls_keep_author_detach_and_the_prior_revision(legacy_binding, domain, story):
    chapter, event, old = legacy_binding
    saved = save_scene(
        domain,
        story["branch_id"],
        chapter["id"],
        edit(domain, story, old, event_id=None, event_revision=None),
        old["id"],
    )["scenes"][0]
    assert saved["payload"]["event_id"] is None and saved["payload"]["event_revision"] is None
    assert saved["payload"]["story_order"] == old["payload"]["story_order"]
    previous = domain.db.one(
        "SELECT payload FROM scene_time_history WHERE scene_id=? AND revision=0", (old["id"],)
    )
    assert json.loads(previous["payload"])["event_id"] == event["id"]
    assert "order_basis" not in saved["payload"]


@pytest.mark.parametrize("explicit,omitted", [("event_id", "event_revision"), ("event_revision", "event_id")])
def test_one_explicit_null_and_one_omission_cannot_create_half_a_dependency(
    legacy_binding, domain, story, explicit, omitted
):
    chapter, _, old = legacy_binding
    before = domain.branch(story["branch_id"])["story_revision"]
    with pytest.raises(DomainError, match="事件依据已改变"):
        save_scene(
            domain,
            story["branch_id"],
            chapter["id"],
            edit(domain, story, old, (omitted,), **{explicit: None}),
            old["id"],
        )
    assert domain.branch(story["branch_id"])["story_revision"] == before
    assert scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]["payload"] == old["payload"]
    assert len(domain.db.all("SELECT * FROM scene_time_history WHERE scene_id=?", (old["id"],))) == 1


def test_archive_keeps_original_anchor_even_when_old_client_omits_it(legacy_binding, domain, story):
    chapter, _, old = legacy_binding
    data = edit(domain, story, old, ("event_id", "event_revision")).model_copy(update={"archived": True})
    saved = save_scene(domain, story["branch_id"], chapter["id"], data, old["id"])["scenes"][0]
    assert saved["archived"] and saved["payload"] == old["payload"]
