import asyncio
from copy import deepcopy

import pytest

from ainovel.db import DomainError
from ainovel.backups import create_backup, restore_backup
from ainovel.budget import Budget
from ainovel.ideation import IdeaAdoptInput, adopt_idea
from ainovel.library import Library
from ainovel.models import ModuleInput
from ainovel.worker import Worker
from test_author_contract import FIELDS, WORDS, adopt_intent


IDEA = "修补旧物的学徒能看见失主遗失的记忆。"
PLAN = "学徒想找回师父的记忆，却发现每次修补都会失去自己的一段往事。"


def adoption(domain, story, **changes):
    return IdeaAdoptInput(
        **{
            "text": PLAN,
            "author_idea": IDEA,
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
            "idempotency_key": "adopt-idea-test",
            **changes,
        }
    )


def candidate(domain, story, **changes):
    request = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        action="ideation.explore",
        instruction=IDEA,
        account="openai_1",
        **changes,
    ).model_dump()
    task = domain.queue("module", request)
    return domain.stage(request, "advice", {"text": PLAN}, {}, task["id"])


def test_manual_adoption_preserves_original_and_every_other_commitment(domain, story):
    prior = adopt_intent(domain, story)
    before = domain.author_contract(story["branch_id"])
    result = adopt_idea(domain, story["branch_id"], adoption(domain, story))
    after = domain.author_contract(story["branch_id"])
    expected = deepcopy(FIELDS)
    expected["direction"] = PLAN
    assert after["intent_contract"] == expected
    assert after["author_text"] == WORDS
    assert after["provenance"]["previous_intent_id"] == prior["id"]
    assert (
        after["provenance"]["field_sources"]["prohibitions"]
        == before["provenance"]["field_sources"]["prohibitions"]
    )
    assert domain.artifact(prior["id"])["status"] == "accepted"
    assert domain.artifact(result["artifact_id"])["status"] == "accepted"
    assert domain.chapter(story["chapter_id"])["revision"] == 0
    assert domain.db.all("SELECT * FROM facts") == []


def test_adoption_is_atomic_and_idempotent(domain, story):
    data = adoption(domain, story)
    first = adopt_idea(domain, story["branch_id"], data)
    assert adopt_idea(domain, story["branch_id"], data) == first
    assert domain.branch(story["branch_id"])["story_revision"] == data.base_story_revision + 1
    assert len(domain.context(story["branch_id"])["definitions"]) == 1
    with pytest.raises(DomainError, match="采用标识"):
        adopt_idea(domain, story["branch_id"], data.model_copy(update={"text": "另一个方案"}))


def test_changed_story_does_not_create_partial_adoption(domain, story):
    data = adoption(domain, story)
    adopt_intent(domain, story)
    count = len(domain.db.all("SELECT * FROM artifacts"))
    with pytest.raises(DomainError, match="本书设定已更新"):
        adopt_idea(domain, story["branch_id"], data)
    assert len(domain.db.all("SELECT * FROM artifacts")) == count
    assert domain.author_contract(story["branch_id"])["intent_contract"] == FIELDS


def test_ai_adoption_keeps_source_revision_and_author_edit(domain, story):
    source = candidate(domain, story)
    adopt_idea(
        domain,
        story["branch_id"],
        adoption(domain, story, source_candidate_id=source["id"], text="作者改为寻找母亲的记忆。"),
    )
    contract = domain.author_contract(story["branch_id"])
    assert contract["intent_contract"]["direction"] == "作者改为寻找母亲的记忆。"
    assert contract["provenance"]["source_candidate_id"] == source["id"]
    assert contract["provenance"]["source_candidate_revision"] == 0
    assert domain.artifact(source["id"])["payload"]["text"] == PLAN


@pytest.mark.parametrize("invalid", ["other_branch", "chapter", "rejected", "revision", "missing"])
def test_invalid_source_keeps_current_contract(domain, story, invalid):
    other = domain.create_novel("另一本作品")
    source = candidate(
        domain,
        other if invalid == "other_branch" else story,
        **({"chapter_id": story["chapter_id"], "base_revision": 0} if invalid == "chapter" else {}),
    )
    if invalid == "rejected":
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE artifacts SET status='rejected' WHERE id=?", (source["id"],))
    data = adoption(
        domain,
        story,
        source_candidate_id="missing" if invalid == "missing" else source["id"],
        candidate_revision=1 if invalid == "revision" else 0,
    )
    with pytest.raises(DomainError):
        adopt_idea(domain, story["branch_id"], data)
    assert not domain.author_contract(story["branch_id"])["configured"]
    assert domain.branch(story["branch_id"])["story_revision"] == data.base_story_revision


def test_empty_adoption_rejected(domain, story):
    with pytest.raises(DomainError, match="写下想法"):
        adopt_idea(domain, story["branch_id"], adoption(domain, story, text="  \n"))


def test_api_can_adopt_without_model_or_chapter(client):
    novel = client.post("/api/novels", json={"title": "只有一句想法"}).json()
    response = client.post(
        f"/api/branches/{novel['branch_id']}/ideas/adopt",
        json={
            "text": IDEA,
            "author_idea": IDEA,
            "base_story_revision": 0,
            "idempotency_key": "manual-api-idea",
        },
    )
    assert response.status_code == 200
    contract = client.get(f"/api/branches/{novel['branch_id']}/author-contract").json()
    assert contract["intent_contract"]["direction"] == IDEA
    assert client.get(f"/api/branches/{novel['branch_id']}/chapters").json() == []
    assert client.get(f"/api/tasks?branch_id={novel['branch_id']}").json() == []


def test_generation_is_a_durable_book_candidate(settings, domain):
    story = domain.create_novel("未建章节的构思")
    request = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        action="ideation.explore",
        instruction=IDEA,
        account="openai_1",
    ).model_dump()

    class Model:
        async def generate(self, *args, **kwargs):
            return {"text": PLAN, "usage": {"input": 1, "output": 1}, "call_id": "offline-idea"}

    task = domain.queue("module", request)
    asyncio.run(Worker(settings, Model()).run_once())
    completed = domain.task(task["id"])
    result = domain.artifact(completed["result"]["artifact_id"])
    assert completed["status"] == "succeeded"
    assert result["kind"] == "advice"
    assert result["payload"]["text"] == PLAN
    assert result["chapter_id"] is None
    assert not domain.author_contract(story["branch_id"])["configured"]


def test_queued_idea_can_be_cancelled_without_adoption(client):
    novel = client.post("/api/novels", json={"title": "取消构思"}).json()
    task = client.post(
        "/api/modules",
        json={
            "novel_id": novel["id"],
            "branch_id": novel["branch_id"],
            "action": "ideation.explore",
            "instruction": IDEA,
            "account": "openai_1",
        },
    ).json()
    response = client.post(f"/api/tasks/{task['id']}/cancel")
    assert response.status_code == 200
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "cancelled"
    assert not client.get(f"/api/branches/{novel['branch_id']}/author-contract").json()["configured"]


def test_adopted_idea_and_sources_survive_actual_backup_restore(settings, domain, story):
    Budget(settings)
    Library(domain.db, settings.data_dir)
    source = candidate(domain, story)
    result = adopt_idea(domain, story["branch_id"], adoption(domain, story, source_candidate_id=source["id"]))
    backup = create_backup(settings, domain.db)
    adopt_idea(
        domain,
        story["branch_id"],
        adoption(domain, story, text="备份后的新方向", idempotency_key="after-backup-idea"),
    )
    restore_backup(settings, domain.db, settings.data_dir / "backups" / (backup["id"] + ".zip"))
    contract = domain.author_contract(story["branch_id"])
    assert contract["source_artifact_id"] == result["artifact_id"]
    assert contract["intent_contract"]["direction"] == PLAN
    assert contract["provenance"]["source_candidate_id"] == source["id"]
    assert domain.artifact(source["id"])["payload"]["text"] == PLAN
