"""Manual pre-adoption explanations; isolated SQLite, no model or production data."""

from copy import deepcopy
import json

import pytest

from ainovel.budget import Budget
from ainovel.db import dumps, uid
from ainovel.scene_time import SceneTime
from ainovel.story_graph import RECORD


@pytest.fixture
def pending_notes(domain, story):
    chapter = domain.save(story["chapter_id"], 0, "😀林舟在渡口记下暗号。")
    task = domain.queue(
        "workflow",
        {"novel_id": story["id"], "adoption": "manual", "account": "openai_1"},
        story["branch_id"],
    )
    revision = domain.branch(story["branch_id"])["story_revision"]
    identifier, scene_id = uid(), uid()
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(chapter["content"].encode("utf-16-le")) // 2,
        "quote": chapter["content"],
    }
    payload = {
        "records": [
            {
                "id": identifier,
                "record": RECORD.validate_python(
                    {"kind": "character", "title": "林舟", "notes": "原说明", "sources": [source]}
                ).model_dump(),
                "reasoning": "匿名候选，仍需作者核对。",
                "dependency_proposals": [],
            }
        ],
        "unknowns": ["暗号来历仍未知"],
        "coverage": "仅此保存稿",
        "source_manifest": [{k: chapter[k] for k in ("id", "revision", "position", "title")}],
        "source_story_revision": revision,
        "scenes": [
            {
                "id": scene_id,
                "chapter_id": chapter["id"],
                "scene": SceneTime(
                    title="渡口",
                    source_revision=chapter["revision"],
                    start=0,
                    end=source["end"],
                    character_id=identifier,
                ).model_dump(),
            }
        ],
        "character_semantic_review": {
            "candidate_revision": 0,
            "status": "needs_review",
            "summary": "历史未知，不因作者编辑冒充模型通过。",
        },
    }
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "story_extraction",
        payload,
        {"story_revision": revision, "maintenance_chapter": {"id": chapter["id"], "revision": 1}},
        task["id"],
    )
    result = {
        "next_chapter": 2,
        "expected_story_revision": revision,
        "waiting_structure": artifact["id"],
        "pending_structure": {"artifact_id": artifact["id"], "chapter_id": chapter["id"], "revision": 1},
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='needs_review',result=? WHERE id=?", (dumps(result), task["id"]))
    return {"task": domain.task(task["id"]), "artifact": artifact, "chapter": chapter, "proposal": identifier}


def path(story, pending):
    return f"/api/branches/{story['branch_id']}/story-extractions/{pending['artifact']['id']}/notes"


def body(pending, **changes):
    return {
        "proposal_id": pending["proposal"],
        "candidate_revision": 0,
        "notes": "作者说明：只记录已发生的事。😀𠮷",
        **changes,
    }


def snapshot(domain):
    return {
        table: domain.db.all(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "artifacts",
            "tasks",
            "task_events",
            "story_records",
            "story_record_history",
            "scene_times",
        )
    }


def test_edit_keeps_sources_identity_scenes_and_history_then_adopts_atomically(
    client, domain, story, pending_notes, settings
):
    row = pending_notes
    budget = Budget(settings)
    call = budget.reserve(settings.providers["openai_1"], row["task"]["id"], 5, 5)
    budget.settle(call)  # Editing is not a model operation or an unknown-fee override.
    fees = budget.report()
    response = client.patch(path(story, row), json=body(row))
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["candidate_revision"] == 1
    expected = deepcopy(row["artifact"]["payload"])
    expected["records"][0]["record"]["notes"] = body(row)["notes"]
    expected["_candidate_revision"] = 1
    assert edited["payload"] == expected
    assert edited["basis"] == row["artifact"]["basis"]
    assert domain.task(row["task"]["id"])["result"] == row["task"]["result"]
    assert domain.chapter(row["chapter"]["id"]) == row["chapter"]
    assert domain.db.all("SELECT * FROM story_records") == []
    assert budget.report() == fees
    event = domain.db.all("SELECT * FROM task_events WHERE kind='candidate_edited'")[-1]
    assert json.loads(event["payload"])["previous_candidate"] == row["artifact"]["payload"]
    assert client.patch(path(story, row), json=body(row)).status_code == 409
    adopt = {
        "base_story_revision": edited["basis"]["story_revision"],
        "candidate_revision": 0,
        "selected_ids": [row["proposal"]],
        "selected_scene_ids": [edited["payload"]["scenes"][0]["id"]],
        "idempotency_key": "manual-edited-structure-notes",
    }
    url = path(story, row).removesuffix("/notes") + "/adopt"
    assert client.post(url, json=adopt).status_code == 409
    assert domain.db.all("SELECT * FROM story_records") == []
    response = client.post(url, json={**adopt, "candidate_revision": 1})
    assert response.status_code == 200, response.text
    record = domain.db.all("SELECT payload FROM story_records")[0]
    assert json.loads(record["payload"])["notes"] == body(row)["notes"]
    progress = domain.task(row["task"]["id"])["result"]
    assert "waiting_structure" not in progress and "pending_structure" not in progress
    assert progress["structure_history"][0]["policy"] == "author_confirmed"
    assert (
        domain.artifact(edited["id"])["payload"]["character_semantic_review"]
        == expected["character_semantic_review"]
    )
    assert domain.chapter(row["chapter"]["id"]) == row["chapter"]
    assert budget.report() == fees


@pytest.mark.parametrize("notes", ["原说明", ""])
def test_noop_and_removing_an_inaccurate_explanation(client, domain, story, pending_notes, notes):
    before = snapshot(domain)
    response = client.patch(path(story, pending_notes), json=body(pending_notes, notes=notes))
    assert response.status_code == 200, response.text
    assert response.json()["candidate_revision"] == (0 if notes == "原说明" else 1)
    if notes == "原说明":
        assert snapshot(domain) == before


@pytest.mark.parametrize(
    "change",
    [
        "accepted",
        "rejected",
        "automatic",
        "running",
        "cancelled",
        "continued",
        "cancel_flag",
        "pointer",
        "pending",
        "story",
        "source_revision",
        "source_stale",
        "source_quote",
        "updated_record",
        "other_task",
        "cancel_pending_task",
    ],
)
def test_invalid_checkpoint_or_source_never_changes_any_rows(client, domain, story, pending_notes, change):
    row = pending_notes
    with domain.db.connect(True) as tx:
        result, policy, payload = (
            deepcopy(row["task"]["result"]),
            deepcopy(row["task"]["payload"]),
            deepcopy(row["artifact"]["payload"]),
        )
        if change in {"accepted", "rejected"}:
            tx.execute("UPDATE artifacts SET status=? WHERE id=?", (change, row["artifact"]["id"]))
        elif change == "automatic":
            policy["adoption"] = "automatic"
        elif change in {"running", "cancelled", "continued"}:
            tx.execute("UPDATE tasks SET status=? WHERE id=?", (change, row["task"]["id"]))
        elif change == "cancel_flag":
            tx.execute("UPDATE tasks SET cancel=1 WHERE id=?", (row["task"]["id"],))
        elif change == "pointer":
            result["waiting_structure"] = "other"
        elif change == "pending":
            result["pending_structure"]["artifact_id"] = "other"
        elif change == "story":
            tx.execute(
                "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (story["branch_id"],)
            )
        elif change == "source_revision":
            tx.execute("UPDATE chapters SET revision=revision+1 WHERE id=?", (row["chapter"]["id"],))
        elif change == "source_stale":
            tx.execute("UPDATE chapters SET stale=1 WHERE id=?", (row["chapter"]["id"],))
        elif change == "source_quote":
            payload["records"][0]["record"]["sources"][0]["quote"] = "不在原文中"
        elif change == "updated_record":
            payload["records"][0]["target_id"] = row["proposal"]
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?",
            (dumps(policy), dumps(result), row["task"]["id"]),
        )
        tx.execute(
            "UPDATE artifacts SET payload=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), row["artifact"]["id"]),
        )
    if change in {"other_task", "cancel_pending_task"}:
        other = domain.queue("workflow", {"novel_id": story["id"]}, story["branch_id"])
        if change == "cancel_pending_task":
            with domain.db.connect(True) as tx:
                tx.execute("UPDATE tasks SET status='running',cancel=1 WHERE id=?", (other["id"],))
    before = snapshot(domain)
    response = client.patch(path(story, row), json=body(row))
    assert response.status_code == 409, response.text
    assert snapshot(domain) == before


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"candidate_revision": 4}, 409),
        ({"proposal_id": "not-in-candidate"}, 409),
        ({"candidate_revision": True}, 422),
        ({"notes": "长" * 4001}, 422),
        ({"title": "不能换身份"}, 422),
        ({"sources": []}, 422),
    ],
)
def test_request_boundary_and_candidate_version(client, domain, story, pending_notes, changes, expected):
    before = snapshot(domain)
    response = client.patch(path(story, pending_notes), json=body(pending_notes, **changes))
    assert response.status_code == expected, response.text
    assert snapshot(domain) == before


def test_foreign_branch_cannot_edit_candidate(client, domain, story, pending_notes):
    other = domain.create_novel("另一本匿名作品")
    before = snapshot(domain)
    response = client.patch(path({"branch_id": other["branch_id"]}, pending_notes), json=body(pending_notes))
    assert response.status_code == 404
    assert snapshot(domain) == before
