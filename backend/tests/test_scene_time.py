import asyncio
from uuid import uuid4

import pytest

from ainovel.db import DomainError
from ainovel.models import ModuleInput
from ainovel.scene_time import SaveScene, at_position, save_scene, scenes
from ainovel.story_graph import SaveRecord, save_record
from ainovel.worker import Worker


@pytest.fixture
def setup_time(domain, story):
    source = domain.save(story["chapter_id"], 0, "😀宁禾认识暗号，后来学会开锁。")
    target = domain.create_chapter(story["branch_id"], "倒叙与后续")
    target = domain.save(target["id"], 0, "倒叙场景。\n后来场景。")
    evidence = {
        "chapter_id": source["id"],
        "revision": 1,
        "start": 0,
        "end": len(source["content"].encode("utf-16-le")) // 2,
        "quote": source["content"],
    }

    def record(p):
        g = save_record(
            domain,
            story["branch_id"],
            SaveRecord(base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=p),
        )
        return g["records"][-1]

    character = record({"kind": "character", "title": "宁禾"})
    event = record(
        {
            "kind": "event",
            "title": "学会开锁",
            "status": "actual",
            "story_order": 20,
            "participants": [character["id"]],
            "sources": [evidence],
        }
    )
    record(
        {
            "kind": "state",
            "title": "开锁能力",
            "character_id": character["id"],
            "event_id": event["id"],
            "category": "capability",
            "key": "开锁",
            "value": "已学会",
            "audience": "character",
            "truth": "actual",
            "sources": [evidence],
        }
    )
    return target, character


def bind(domain, story, target, character, **changes):
    p = {
        "title": "回忆过去",
        "source_revision": target["revision"],
        "start": 0,
        "end": 6,
        "story_order": 10,
        "audience": "character",
        "character_id": character["id"],
        **changes,
    }
    return save_scene(
        domain,
        story["branch_id"],
        target["id"],
        SaveScene(base_story_revision=domain.branch(story["branch_id"])["story_revision"], scene=p),
    )


def test_two_scene_positions_have_distinct_effective_states(domain, story, setup_time):
    target, character = setup_time
    bind(domain, story, target, character)
    bind(
        domain,
        story,
        target,
        character,
        title="事件之后",
        start=6,
        end=len(target["content"]),
        story_order=21,
    )
    before = domain.context(story["branch_id"], target["id"], 0)
    after = domain.context(story["branch_id"], target["id"], 6)
    assert before["longform"]["states"] == []
    assert after["longform"]["states"][0]["payload"]["value"] == "已学会"
    assert before["recent"] == [] and after["recent"] == []
    assert before["facts"] == [] and before["longform"]["excluded_recent_chapters"] == 1
    assert (
        at_position(domain, story["branch_id"], target["id"], len(target["content"]))["payload"]["title"]
        == "事件之后"
    )


def test_whole_chapter_keeps_scene_entry_and_exit_state_separate(domain, story, setup_time, settings):
    from ainovel.scene_time import chapter_context

    target, character = setup_time
    bind(domain, story, target, character, end=3)
    bind(domain, story, target, character, title="以后", start=6, end=len(target["content"]), story_order=21)
    bundle = chapter_context(domain, story["branch_id"], target["id"])
    assert bundle["unbound_ranges"] == [{"start": 3, "end": 6, "status": "unbound"}]
    assert bundle["scenes"][0]["entry"]["states"] == []
    assert bundle["scenes"][1]["entry"]["states"][0]["payload"]["value"] == "已学会"
    gap = domain.context(story["branch_id"], target["id"], 4)
    assert gap["longform"]["unbound_scene_range"] and gap["recent"] == []
    assembled = Worker(settings).whole_chapter_context(domain.context(story["branch_id"], target["id"]))
    assert assembled["longform"]["states"] == []
    assert len(assembled["chapter_scenes"]["scenes"]) == 2 and assembled["facts"] == []


def test_whole_chapter_rejects_stale_later_scene_even_when_first_is_current(
    domain, story, setup_time, settings
):
    target, character = setup_time
    first = bind(domain, story, target, character)
    bind(domain, story, target, character, title="以后", start=6, end=len(target["content"]), story_order=21)
    latest = domain.save(target["id"], target["revision"], target["content"] + "新稿")
    row = first["scenes"][0]
    save_scene(
        domain,
        story["branch_id"],
        target["id"],
        SaveScene(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            base_revision=row["revision"],
            scene={**row["payload"], "source_revision": latest["revision"]},
        ),
        row["id"],
    )
    context = domain.context(story["branch_id"], target["id"])
    assert context["longform"]["scene_binding"]
    with pytest.raises(DomainError, match="整章动作包含"):
        Worker(settings).whole_chapter_context(context)


def test_edit_invalidates_binding_but_readonly_context_remains_available(domain, story, setup_time, settings):
    target, character = setup_time
    bind(domain, story, target, character)
    domain.save(target["id"], target["revision"], "新的正文位置不同。")
    assert scenes(domain, story["branch_id"], target["id"])["scenes"][0]["stale"]
    context = domain.context(story["branch_id"], target["id"])
    assert context["longform"]["blocked"] == "scene_stale" and context["recent"] == []
    with pytest.raises(DomainError, match="尚待复核"):
        Worker(settings).context(story["branch_id"], target["id"])


def test_overlap_version_source_and_identity_are_checked(domain, story, setup_time):
    target, character = setup_time
    bind(domain, story, target, character)
    with pytest.raises(DomainError, match="重叠"):
        bind(domain, story, target, character, start=5, end=8)
    with pytest.raises(DomainError, match="故事版本"):
        save_scene(
            domain,
            story["branch_id"],
            target["id"],
            SaveScene(
                base_story_revision=0,
                scene={"title": "旧", "source_revision": 1, "start": 0, "end": 0, "audience": "author"},
            ),
        )
    with pytest.raises(DomainError, match="视角人物"):
        bind(domain, story, target, character, start=6, end=7, character_id="other-book")
    with pytest.raises(DomainError):
        domain.context(story["branch_id"], target["id"], 999)


def test_queue_and_worker_use_scene_at_explicit_position(domain, story, setup_time, settings):
    target, character = setup_time
    bind(domain, story, target, character)
    bind(domain, story, target, character, title="以后", start=6, end=len(target["content"]), story_order=21)
    request = ModuleInput(
        action="writing.advise",
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=target["id"],
        base_revision=target["revision"],
        context_position=6,
        instruction="只给建议",
        account="deepseek",
        client_request_id=uuid4().hex,
    ).model_dump()
    queued = domain.queue("module", request)
    assert queued["result"]["scene_snapshot"]["payload"]["story_order"] == 21

    class Model:
        def __init__(self):
            self.context = None

        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            import json

            self.context = json.loads(prompt.split("\n只返回")[0])["上下文"]
            return {
                "text": "仅给建议，不改原稿。",
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-scene",
            }

    model = Model()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    asyncio.run(worker.module())
    assert model.context["longform"]["scene_binding"]["payload"]["story_order"] == 21
    assert model.context["longform"]["states"][0]["payload"]["value"] == "已学会"


def test_binding_backup_restore_and_archive_history(domain, story, setup_time, settings, tmp_path):
    from ainovel.backups import create_backup, unpack_verified
    from ainovel.db import Database
    from ainovel.domain import Domain

    target, character = setup_time
    result = bind(domain, story, target, character)
    row = result["scenes"][0]
    snapshot = create_backup(settings, domain.db)
    folder = tmp_path / "restore"
    folder.mkdir()
    unpack_verified(settings.data_dir / "backups" / (snapshot["id"] + ".zip"), folder)
    restored = Domain(Database(folder / "manuscripts.sqlite"))
    assert scenes(restored, story["branch_id"], target["id"]) == result
    save_scene(
        domain,
        story["branch_id"],
        target["id"],
        SaveScene(
            base_story_revision=result["story_revision"], base_revision=0, scene=row["payload"], archived=True
        ),
        row["id"],
    )
    assert at_position(domain, story["branch_id"], target["id"]) is None
    assert len(domain.db.all("SELECT * FROM scene_time_history WHERE scene_id=?", (row["id"],))) == 2


def test_stale_binding_can_be_archived_without_reusing_old_positions(domain, story, setup_time):
    target, character = setup_time
    result = bind(domain, story, target, character)
    row = result["scenes"][0]
    domain.save(target["id"], target["revision"], "短")
    assert scenes(domain, story["branch_id"], target["id"])["scenes"][0]["stale"]
    save_scene(
        domain,
        story["branch_id"],
        target["id"],
        SaveScene(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            base_revision=0,
            scene=row["payload"],
            archived=True,
        ),
        row["id"],
    )
    assert at_position(domain, story["branch_id"], target["id"]) is None


def test_zero_length_scene_and_unknown_story_time_do_not_invent_state(domain, story, setup_time):
    target, character = setup_time
    result = bind(domain, story, target, character, start=0, end=0, story_order=None)
    value = domain.context(story["branch_id"], target["id"])["longform"]
    assert value["scene_binding"]["id"] == result["scenes"][0]["id"]
    assert value["states"] == [] and value["uncertain"]
