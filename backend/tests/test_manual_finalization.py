import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.manual_finalization import (
    ManualFinalizationInput,
    confirm_finalization,
    finalization_status,
)


TEXT = "潮水退下时，林舟将两次量得的读数并排写下，主线至此收束。"
CHECKLIST = {
    "manuscript_read": True,
    "main_conflict_closed": True,
    "character_outcomes_reviewed": True,
    "promises_addressed": True,
}


def request(domain, story, **changes):
    return ManualFinalizationInput.model_validate(
        {
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
            "base_manifest_sha256": state(domain, story)["manifest_sha256"],
            "idempotency_key": "manual-finalization-fixture",
            "checklist": CHECKLIST,
            "note": "作者已通读当前稿，主冲突收束，人物去向与保留事项已核对。",
            **changes,
        }
    )


def state(domain, story):
    branch = domain.branch(story["branch_id"])
    return finalization_status(domain.db, branch["id"], branch["story_revision"])


def test_maintained_promises_block_finalization_until_explicit_decision(domain, story):
    from ainovel.story_graph import SaveRecord, save_record

    chapter = domain.save(story["chapter_id"], 0, TEXT)
    source = {"chapter_id": chapter["id"], "revision": 1, "start": 0, "end": len(TEXT), "quote": TEXT}
    result = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "promise",
                "title": "读数的来源",
                "sources": [source],
                "stages": [{"status": "planned"}],
            },
        ),
    )
    promise = result["records"][0]
    status = state(domain, story)
    assert any(b["id"] == "structured_promises" for b in status["deterministic_blockers"])
    with pytest.raises(DomainError, match="确定性问题"):
        confirm_finalization(domain, story["branch_id"], request(domain, story))
    save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=result["story_revision"],
            base_revision=0,
            record={
                **promise["payload"],
                "stages": [{"status": "retained", "note": "本卷线索保留到下一卷，作者已在交付说明明确。"}],
            },
        ),
        promise["id"],
    )
    assert state(domain, story)["structured_promises"]["closed"]
    confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert state(domain, story)["author_finalized"]
    domain.save(chapter["id"], 1, TEXT + "原文后续改动。")
    current = state(domain, story)
    assert not current["author_finalized"]
    assert any(b["id"] == "structured_promises" for b in current["deterministic_blockers"])


def report(domain, story, chapter=None, **finding):
    chapter = chapter or domain.chapter(story["chapter_id"])
    return domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": chapter["revision"],
        },
        "review",
        {
            "checks": [],
            "summary": "仅用于定稿守卫工程验证。",
            "findings": [
                {
                    "severity": "blocker",
                    "chapter": chapter["position"],
                    "quote": TEXT,
                    "reason": "读数是否对应同一时刻仍有疑问。",
                    "repair": "核对读数时刻。",
                    **finding,
                }
            ],
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )


def test_handwritten_book_can_finalize_without_a_model_or_faking_three_layer_acceptance(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    result = confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert result["author_finalized"] and result["status"] == "finalized"
    assert result["record"]["checklist"] == CHECKLIST
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.db.all("SELECT * FROM tasks") == []
    assert domain.db.all("SELECT category FROM assessments") == [{"category": "author_finalization"}]
    completion = domain.completion(story["branch_id"])
    assert completion["production_complete"] is False and completion["accepted"] is False
    assert completion["author_finalized"] is True
    assert not completion["assessments"]
    repeated = confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert repeated["replayed"] and repeated["record"]["id"] == result["record"]["id"]
    assert len(domain.db.all("SELECT * FROM assessments")) == 1


@pytest.mark.parametrize("content", ["", " \n\t\u3000", "\u200b\ufeff"])
def test_blank_or_invisible_manuscript_cannot_be_finalized(domain, story, content):
    domain.save(story["chapter_id"], 0, content)
    assert "blank_chapters" in {b["id"] for b in state(domain, story)["deterministic_blockers"]}
    with pytest.raises(DomainError, match="确定性问题"):
        confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert not domain.db.all("SELECT * FROM assessments")


def test_missing_directory_stale_and_configured_length_are_not_disposition_overrides(domain, story):
    domain.save(story["chapter_id"], 0, TEXT)
    third = domain.create_chapter(story["branch_id"], "第三章", position=3)
    domain.save(third["id"], 0, TEXT)
    domain.queue(
        "workflow",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapters": 3,
            "min_chars": 200,
            "max_chars": 500,
            "account": "openai_1",
        },
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE chapters SET stale=1 WHERE id=?", (story["chapter_id"],))
    blocked = {b["id"] for b in state(domain, story)["deterministic_blockers"]}
    assert {"chapter_structure", "stale_chapters", "chapter_length"} <= blocked
    with pytest.raises(DomainError) as error:
        confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert error.value.code == "finalization_blocked"


def test_current_serious_report_requires_specific_reason_and_new_reports_revoke_current_confirmation(
    domain, story
):
    domain.save(story["chapter_id"], 0, TEXT)
    original_report = report(domain, story)
    issue = state(domain, story)["known_issues"][0]
    with pytest.raises(DomainError) as error:
        confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert error.value.code == "finalization_issues"
    disposition = {
        "issue_id": issue["id"],
        "decision": "not_an_error",
        "reason": "该段明确写同一轮复量，读数不是异时合并，作者核对后裁定为误读。",
    }
    result = confirm_finalization(
        domain, story["branch_id"], request(domain, story, dispositions=[disposition])
    )
    assert result["author_finalized"]
    assert result["record"]["dispositions"] == [disposition]
    assert domain.artifact(original_report["id"])["status"] == "candidate"
    report(domain, story, reason="另一处物件来历未交代，当前需要作者处理。")
    new = state(domain, story)
    assert new["status"] == "blocked" and not new["author_finalized"]
    assert len(new["known_issues"]) == 2 and len(new["unresolved_issue_ids"]) == 1


def test_fixing_source_obsoletes_old_report_and_alternative_candidate_is_not_current_evidence(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    report(domain, story)
    domain.save(chapter["id"], chapter["revision"], TEXT + "两次均由当值者在同一轮复量记录。")
    current = domain.chapter(chapter["id"])
    domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": current["id"],
            "base_revision": current["revision"],
        },
        "chapter_draft",
        {
            "text": "另一份尚未采纳的候选。",
            "review": {
                "findings": [
                    {"severity": "blocker", "chapter": 1, "quote": "另一份", "reason": "仅候选中的问题。"}
                ]
            },
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    current_state = state(domain, story)
    assert current_state["known_issues"] == []
    assert current_state["historical_reports_not_current"] == 2
    assert confirm_finalization(domain, story["branch_id"], request(domain, story))["author_finalized"]


def test_manually_accepted_failed_draft_is_still_a_known_current_issue(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": chapter["revision"],
        },
        "chapter_draft",
        {
            "text": TEXT + "补充未核实的结论。",
            "review_passed": False,
            "review": {
                "findings": [
                    {
                        "severity": "blocker",
                        "chapter": 1,
                        "quote": "补充未核实的结论。",
                        "reason": "不能采作确定事实。",
                        "repair": "复核依据。",
                    }
                ]
            },
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    domain.accept(artifact["id"], "manual-adopt-failed-fixture")
    assert state(domain, story)["known_issues"][0]["quote"] == "补充未核实的结论。"
    with pytest.raises(DomainError) as error:
        confirm_finalization(domain, story["branch_id"], request(domain, story))
    assert error.value.code == "finalization_issues"


def test_edit_or_title_change_invalidates_and_stale_confirmation_is_rejected(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    old = request(domain, story)
    confirm_finalization(domain, story["branch_id"], old)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE chapters SET title='新标题' WHERE id=?", (chapter["id"],))
    assert state(domain, story)["status"] == "outdated"
    with pytest.raises(DomainError) as error:
        confirm_finalization(
            domain, story["branch_id"], old.model_copy(update={"idempotency_key": "old-title-confirmation"})
        )
    assert error.value.code == "manuscript_changed"
    domain.save(chapter["id"], chapter["revision"], TEXT + "一行新正文。")
    repeated = confirm_finalization(domain, story["branch_id"], old)
    assert repeated["replayed"] and not repeated["author_finalized"]
    assert repeated["status"] == "outdated"
    with pytest.raises(DomainError) as error:
        confirm_finalization(
            domain, story["branch_id"], old.model_copy(update={"idempotency_key": "other-old-confirmation"})
        )
    assert error.value.code == "story_changed"


def test_current_workflow_reports_block_but_old_source_versions_do_not(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    task = domain.queue(
        "workflow",
        {"novel_id": story["id"], "branch_id": story["branch_id"], "account": "openai_1", "chapters": 1},
    )
    result = {
        **task["result"],
        "completion": {
            "source_versions": [{"id": chapter["id"], "revision": chapter["revision"]}],
            "findings": [
                {
                    "severity": "blocker",
                    "chapter": 1,
                    "quote": TEXT,
                    "reason": "终局仍有实质矛盾。",
                    "repair": "核对承诺。",
                }
            ],
        },
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    assert len(state(domain, story)["known_issues"]) == 1
    result["completion"]["source_versions"][0]["revision"] = 0
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    assert not state(domain, story)["known_issues"]


def test_finalization_routes_support_unconfigured_handwriting_and_require_version_checklist(client):
    novel = client.post("/api/novels", json={"title": "手写定稿工程样例"}).json()
    chapter = client.post(f"/api/branches/{novel['branch_id']}/chapters", json={"title": "第一章"}).json()
    client.put(f"/api/chapters/{chapter['id']}", json={"base_revision": 0, "content": TEXT})
    path = f"/api/branches/{novel['branch_id']}/finalization"
    status = client.get(path)
    assert status.status_code == 200
    payload = {
        "base_story_revision": status.json()["base_story_revision"],
        "base_manifest_sha256": status.json()["manifest_sha256"],
        "idempotency_key": "api-author-confirmation",
        "checklist": CHECKLIST,
        "note": "已逐章通读，主要人物与结尾已经核对，保留事项有说明。",
    }
    assert (
        client.post(path, json={**payload, "checklist": {**CHECKLIST, "manuscript_read": False}}).status_code
        == 400
    )
    assert client.post(path, json=payload).json()["author_finalized"] is True
    completion = client.get(f"/api/branches/{novel['branch_id']}/completion").json()
    assert (
        completion["author_finalized"]
        and not completion["production_complete"]
        and not completion["accepted"]
    )
    assert not completion["assessments"]
    assert client.get("/api/config").json()["accounts"]


def test_reusing_confirmation_key_for_different_content_is_not_silently_accepted(domain, story):
    domain.save(story["chapter_id"], 0, TEXT)
    original = request(domain, story)
    confirm_finalization(domain, story["branch_id"], original)
    with pytest.raises(DomainError) as error:
        confirm_finalization(
            domain,
            story["branch_id"],
            original.model_copy(update={"note": "同一个确认标识提出另一份不同的定稿说明。"}),
        )
    assert error.value.code == "idempotency"
    assert (
        json.loads(domain.db.one("SELECT minutes FROM assessments")["minutes"])["request"]
        == original.model_dump()
    )
