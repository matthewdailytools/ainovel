import asyncio
from copy import deepcopy
import json
from typing import Literal

import pytest

from ainovel import character_review, workflow_structure
from ainovel.character_review import ACTION, VERSION, ensure_character_review
from ainovel.db import DomainError, dumps
from ainovel.story_extraction import AdoptExtraction, adopt
from ainovel.story_graph import RECORD, SaveRecord, graph, save_record
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain


class ReviewModel:
    def __init__(self, verdict="supported", defect=None, callback=None):
        self.verdict, self.defect, self.callback = verdict, defect, callback
        self.calls = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        task = frame["任务"]
        assert task["动作"] == ACTION
        self.calls.append(deepcopy(frame))
        fields = []
        for target in task["人物字段"]:
            verdict = (
                self.verdict.get(target["field"], "unknown")
                if isinstance(self.verdict, dict)
                else self.verdict
            )
            fields.append(
                {
                    "record_id": target["record_id"],
                    "field": target["field"],
                    "result": verdict,
                    "reason": "工程替身：只核对流程，未知也是有效结论。",
                    "source_ids": target["source_ids"][:1] if verdict != "unknown" else [],
                }
            )
        if self.defect == "wrong_item_source":
            fields[0]["source_ids"] = task["人物字段"][-1]["source_ids"][:1]
        elif self.defect == "missing":
            fields.pop()
        elif self.defect == "duplicate":
            fields[-1] = deepcopy(fields[0])
        elif self.defect == "binding":
            task = {**task, "input_sha256": "0" * 64}
        if self.callback:
            self.callback()
        return {
            "text": dumps(
                {
                    "version": task["字段复核契约版本"],
                    "input_sha256": task["input_sha256"],
                    "fields": fields,
                }
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-character-review",
            "model": "observed-test-model",
        }


def pending(
    settings,
    domain,
    story,
    *,
    profile="失踪者的女儿",
    notes="只知道母亲尚未归来",
    count=1,
    model=None,
    adoption="automatic",
    texts=None,
):
    texts = texts or ["宁禾的母亲失踪，尚未归来。", "江舟是渡口掌柜，正在看账。"]
    chapter = domain.save(story["chapter_id"], 0, "\n".join(texts))
    revision = domain.branch(story["branch_id"])["story_revision"]
    task = domain.queue(
        "workflow",
        {
            "novel_id": story["id"],
            "account": "openai_1",
            "adoption": adoption,
            "structured_maintenance": True,
            "brief": "母亲去向仍未知，不能提前确认为死亡。",
            "fixed": ["没有超自然能力"],
        },
        story["branch_id"],
    )
    worker = Worker(settings, model or ReviewModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = {
        **deepcopy(worker.task["result"]),
        "next_chapter": 2,
        "expected_story_revision": revision,
        "pending_structure": {"chapter_id": chapter["id"], "revision": chapter["revision"]},
    }
    worker.checkpoint(result)
    records = []
    start = 0
    for i in range(count):
        quote = texts[i]
        end = start + len(quote.encode("utf-16-le")) // 2
        record = RECORD.validate_python(
            {
                "kind": "character",
                "title": ["宁禾", "江舟"][i],
                "notes": notes,
                "profile": profile,
                "sources": [
                    {"chapter_id": chapter["id"], "revision": 1, "start": start, "end": end, "quote": quote}
                ],
            }
        ).model_dump()
        records.append(
            {
                "id": f"character-{i}",
                "local_id": f"local-{i}",
                "record": record,
                "reasoning": "候选，尚未采用",
            }
        )
        start = end + 1
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "story_extraction",
        {
            "records": records,
            "unknowns": [],
            "coverage": "本章",
            "source_story_revision": revision,
            "source_manifest": [{"id": chapter["id"], "revision": 1, "title": chapter["title"]}],
        },
        {
            "story_revision": revision,
            "maintenance_chapter": {"id": chapter["id"], "revision": 1},
            "maintenance_epoch": "initial",
        },
        task["id"],
        worker.attempt,
    )
    result["pending_structure"]["artifact_id"] = artifact["id"]
    worker.checkpoint(result)
    return worker, result, artifact, chapter


def restart(settings, worker, model):
    with worker.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='queued',lease_until=0 WHERE id=?", (worker.task["id"],))
    restored = Worker(settings, model)
    restored.task = restored.claim()
    restored.attempt = restored.task["attempt"]
    return restored


def test_supported_batch_has_one_review_and_preserves_exact_record_sources(settings, domain, story):
    model = ReviewModel()
    worker, result, artifact, chapter = pending(settings, domain, story, count=2, model=model)
    assert asyncio.run(maintain(worker, result))
    saved = domain.artifact(artifact["id"])
    report = saved["payload"]["character_semantic_review"]
    assert saved["status"] == "accepted" and report["status"] == "supported"
    assert len(model.calls) == 1 and len(report["results"]) == 4
    assert report["candidate_revision"] == 0 and report["version"] == VERSION
    assert report["completed_steps"][0]["model"] == "observed-test-model"
    assert report["persisted_by_attempt"] == worker.attempt
    assert len(graph(domain, story["branch_id"])["records"]) == 2
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.db.all("SELECT * FROM facts") == []
    instruction = model.calls[0]["任务"]
    assert instruction["冻结工作流边界"]["brief"].startswith("母亲去向仍未知")
    assert "definitions" not in model.calls[0]["上下文"]
    assert "records" not in model.calls[0]["上下文"]
    assert "genre_profile" not in model.calls[0]["上下文"]
    assert "author_contract" not in model.calls[0]["上下文"]
    for row in report["results"]:
        assert row["evidence"][0]["quote"] in chapter["content"]


@pytest.mark.parametrize("verdict", ["unknown", "contradicted"])
def test_semantic_non_support_is_valid_no_retry_and_author_can_still_adopt(
    settings, domain, story, client, verdict
):
    model = ReviewModel(verdict)
    worker, result, artifact, chapter = pending(settings, domain, story, model=model)
    assert not asyncio.run(maintain(worker, result))
    assert domain.task(worker.task["id"])["status"] == "needs_review"
    saved = domain.artifact(artifact["id"])
    assert saved["status"] == "candidate"
    assert saved["payload"]["character_semantic_review"]["status"] == "needs_review"
    assert {row["result"] for row in saved["payload"]["character_semantic_review"]["results"]} == {verdict}
    assert len(model.calls) == 1
    assert (
        domain.db.all(
            "SELECT * FROM task_events WHERE task_id=? AND kind='citation_repair'", (worker.task["id"],)
        )
        == []
    )
    assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.chapter(chapter["id"]) == chapter
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt",
        json={
            "base_story_revision": result["expected_story_revision"],
            "candidate_revision": 0,
            "selected_ids": [item["id"] for item in artifact["payload"]["records"]],
            "idempotency_key": "author-chooses-character",
        },
    )
    assert response.status_code == 200, response.text
    assert len(model.calls) == 1 and domain.artifact(artifact["id"])["status"] == "accepted"


@pytest.mark.parametrize("defect", ["wrong_item_source", "missing", "duplicate", "binding"])
def test_invalid_coverage_or_cross_item_source_stops_after_two_repairs(settings, domain, story, defect):
    model = ReviewModel(defect=defect)
    worker, result, artifact, chapter = pending(settings, domain, story, count=2, model=model)
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(maintain(worker, result))
    # There are three validation passes. Identical unsuccessful repair input
    # reuses its paid completed step instead of billing the same prompt again.
    assert len(model.calls) == 2
    assert (
        len(
            domain.db.all(
                "SELECT * FROM task_events WHERE task_id=? AND kind='citation_repair'", (worker.task["id"],)
            )
        )
        == 3
    )
    assert "character_semantic_review" not in domain.artifact(artifact["id"])["payload"]
    assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.chapter(chapter["id"]) == chapter


@pytest.mark.parametrize("adoption,empty", [("manual", False), ("automatic", True)])
def test_manual_or_identity_only_does_not_add_a_review_call(settings, domain, story, adoption, empty):
    model = ReviewModel()
    worker, result, artifact, _ = pending(
        settings,
        domain,
        story,
        model=model,
        adoption=adoption,
        profile="" if empty else "有依据的档案",
        notes="" if empty else "有依据的说明",
    )
    assert asyncio.run(maintain(worker, result)) is empty
    assert model.calls == []
    assert "character_semantic_review" not in domain.artifact(artifact["id"])["payload"]


def test_restart_after_paid_step_before_report_reuses_completed_call(settings, domain, story, monkeypatch):
    model = ReviewModel()
    worker, result, artifact, _ = pending(settings, domain, story, model=model)
    original = character_review.resolved_results
    monkeypatch.setattr(
        character_review,
        "resolved_results",
        lambda *args: (_ for _ in ()).throw(RuntimeError("after completed step")),
    )
    with pytest.raises(RuntimeError, match="after completed step"):
        asyncio.run(maintain(worker, result))
    assert len(model.calls) == 1
    assert "character_semantic_review" not in domain.artifact(artifact["id"])["payload"]
    monkeypatch.setattr(character_review, "resolved_results", original)
    restored = restart(settings, worker, model)
    assert restored.attempt != worker.attempt
    assert asyncio.run(maintain(restored, restored.task["result"]))
    assert len(model.calls) == 1


@pytest.mark.parametrize("verdict", ["supported", "unknown", "contradicted"])
def test_saved_report_recovers_without_call_and_records_are_not_rewritten(settings, domain, story, verdict):
    model = ReviewModel(verdict)
    worker, _, artifact, _ = pending(settings, domain, story, model=model)
    reviewed, token = asyncio.run(ensure_character_review(worker, artifact))
    restored = restart(settings, worker, model)
    assert asyncio.run(maintain(restored, restored.task["result"])) is (verdict == "supported")
    assert len(model.calls) == 1
    assert (
        domain.artifact(artifact["id"])["payload"]["character_semantic_review"]
        == reviewed["payload"]["character_semantic_review"]
    )


@pytest.mark.parametrize("already_adopted", [False, True])
def test_v1_report_and_paid_step_stay_historical_until_explicit_v2_review(
    settings, domain, story, monkeypatch, already_adopted
):
    class V1Review(character_review.CharacterReview):
        version: Literal["character-field-entailment/1"]

    model = ReviewModel()
    worker, result, artifact, chapter = pending(settings, domain, story, model=model)
    with monkeypatch.context() as legacy:
        legacy.setattr(character_review, "VERSION", "character-field-entailment/1")
        legacy.setattr(character_review, "CharacterReview", V1Review)
        old, old_token = asyncio.run(ensure_character_review(worker, artifact))
    old_report = deepcopy(old["payload"]["character_semantic_review"])
    old_steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (worker.task["id"],))
    assert old_report["version"] == "character-field-entailment/1"
    assert len(old_steps) == len(model.calls) == 1
    if already_adopted:
        adopt(
            domain,
            story["branch_id"],
            artifact["id"],
            AdoptExtraction(
                base_story_revision=result["expected_story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in artifact["payload"]["records"]],
                idempotency_key="explicit-author-adopted-v1",
            ),
        )
        original_graph = graph(domain, story["branch_id"])
        restored_progress = domain.task(worker.task["id"])["result"]
        assert asyncio.run(maintain(worker, restored_progress))
        assert graph(domain, story["branch_id"]) == original_graph
        assert domain.artifact(artifact["id"])["payload"]["character_semantic_review"] == old_report
        assert len(model.calls) == 1
    else:
        with pytest.raises(DomainError, match="复核绑定已失效"):
            adopt(
                domain,
                story["branch_id"],
                artifact["id"],
                AdoptExtraction(
                    base_story_revision=result["expected_story_revision"],
                    candidate_revision=0,
                    selected_ids=[r["id"] for r in artifact["payload"]["records"]],
                    idempotency_key="v1-cannot-authorize-v2-automatic",
                ),
                worker.attempt,
                old_token,
            )
        assert domain.artifact(artifact["id"])["payload"]["character_semantic_review"] == old_report
        assert domain.db.all("SELECT * FROM story_records") == []
        fresh, token = asyncio.run(ensure_character_review(worker, old))
        assert token != old_token
        report = fresh["payload"]["character_semantic_review"]
        assert report["version"] == VERSION == "character-field-entailment/2"
        assert fresh["payload"]["character_semantic_review_history"] == [old_report]
        assert report["completed_steps"][0]["fingerprint"] != old_steps[0]["fingerprint"]
        assert len(model.calls) == 2
        assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.chapter(chapter["id"]) == chapter
    for previous in old_steps:
        saved = domain.db.one(
            "SELECT * FROM task_steps WHERE task_id=? AND fingerprint=?",
            (worker.task["id"], previous["fingerprint"]),
        )
        assert saved == previous


@pytest.mark.parametrize("split_surrogate", [False, True])
def test_source_positions_use_utf16_and_reject_split_surrogates_before_call(
    settings, domain, story, split_surrogate
):
    model = ReviewModel()
    worker, result, artifact, chapter = pending(
        settings, domain, story, model=model, texts=["😀宁禾看着𠀀字旧牌，母亲尚未归来。"]
    )
    payload = deepcopy(artifact["payload"])
    source = payload["records"][0]["record"]["sources"][0]
    assert source["end"] == len(chapter["content"].encode("utf-16-le")) // 2
    assert source["end"] > len(chapter["content"])
    if split_surrogate:
        source["start"] = 1
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
        with pytest.raises(DomainError):
            asyncio.run(maintain(worker, result))
        assert model.calls == []
        assert domain.db.all("SELECT * FROM story_records") == []
    else:
        assert asyncio.run(maintain(worker, result))
        reviewed_source = domain.artifact(artifact["id"])["payload"]["character_semantic_review"]["results"][
            0
        ]["evidence"][0]
        assert reviewed_source == source


def test_candidate_edit_invalidates_report_and_preserves_previous_report(settings, domain, story):
    model = ReviewModel()
    worker, _, artifact, _ = pending(settings, domain, story, model=model)
    reviewed, token = asyncio.run(ensure_character_review(worker, artifact))
    payload = deepcopy(reviewed["payload"])
    payload["records"][0]["record"]["notes"] = "已编辑，仍待另行核对"
    payload["_candidate_revision"] = 1
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    fresh, new_token = asyncio.run(ensure_character_review(worker, domain.artifact(artifact["id"])))
    assert new_token != token and len(model.calls) == 2
    assert fresh["payload"]["character_semantic_review"]["candidate_revision"] == 1
    assert fresh["payload"]["character_semantic_review_history"] == [
        reviewed["payload"]["character_semantic_review"]
    ]


@pytest.mark.parametrize("change", ["record", "source", "constraints", "attempt"])
def test_late_review_cannot_bind_changed_record_source_constraint_or_attempt(settings, domain, story, change):
    model = ReviewModel()
    worker, result, artifact, chapter = pending(settings, domain, story, model=model)

    def mutate():
        with domain.db.connect(True) as tx:
            if change == "record":
                payload = deepcopy(artifact["payload"])
                payload["records"][0]["record"]["profile"] = "别处的新内容"
                tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
            elif change == "source":
                tx.execute(
                    "UPDATE chapters SET content=content||'不改引文但正文变了' WHERE id=?", (chapter["id"],)
                )
            elif change == "constraints":
                payload = deepcopy(worker.task["payload"])
                payload["fixed"] = ["新的冻结边界"]
                tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(payload), worker.task["id"]))
            else:
                tx.execute("UPDATE tasks SET attempt='newer-attempt' WHERE id=?", (worker.task["id"],))

    model.callback = mutate
    with pytest.raises(DomainError):
        asyncio.run(maintain(worker, result))
    assert "character_semantic_review" not in domain.artifact(artifact["id"])["payload"]
    assert domain.db.all("SELECT * FROM story_records") == []


def test_adoption_transaction_rechecks_hash_even_without_revision_bump(settings, domain, story):
    model = ReviewModel()
    worker, result, artifact, _ = pending(settings, domain, story, model=model)
    reviewed, token = asyncio.run(ensure_character_review(worker, artifact))
    payload = deepcopy(reviewed["payload"])
    payload["records"][0]["record"]["notes"] = "报告之后发生的变更"
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    with pytest.raises(DomainError, match="复核绑定已失效"):
        adopt(
            domain,
            story["branch_id"],
            artifact["id"],
            AdoptExtraction(
                base_story_revision=result["expected_story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in payload["records"]],
                idempotency_key="automatic-bound-review",
            ),
            worker.attempt,
            token,
        )
    assert domain.db.all("SELECT * FROM story_records") == []


@pytest.mark.parametrize("review_exists", [False, True])
def test_automatic_attempt_cannot_bypass_character_review_by_omitting_token(
    settings, domain, story, review_exists
):
    worker, result, artifact, _ = pending(settings, domain, story)
    if review_exists:
        asyncio.run(ensure_character_review(worker, artifact))
    with pytest.raises(DomainError, match="缺少复核绑定"):
        adopt(
            domain,
            story["branch_id"],
            artifact["id"],
            AdoptExtraction(
                base_story_revision=result["expected_story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in artifact["payload"]["records"]],
                idempotency_key="missing-internal-review-token",
            ),
            worker.attempt,
        )
    assert domain.db.all("SELECT * FROM story_records") == []


def test_explicit_author_adoption_wins_race_with_background_review(settings, domain, story):
    model = ReviewModel()
    worker, result, artifact, chapter = pending(settings, domain, story, model=model)

    def author_adopts():
        adopt(
            domain,
            story["branch_id"],
            artifact["id"],
            AdoptExtraction(
                base_story_revision=result["expected_story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in artifact["payload"]["records"]],
                idempotency_key="author-during-background-review",
            ),
        )

    model.callback = author_adopts
    assert asyncio.run(maintain(worker, result))
    assert domain.artifact(artifact["id"])["status"] == "accepted"
    assert "pending_structure" not in result
    assert len(model.calls) == 1 and domain.chapter(chapter["id"]) == chapter
    assert "character_semantic_review" not in domain.artifact(artifact["id"])["payload"]


@pytest.mark.parametrize("stop", ["needs_review", "paused"])
def test_author_adoption_after_report_persistence_is_not_overwritten_by_wait_checkpoint(
    settings, domain, story, monkeypatch, stop
):
    model = ReviewModel("unknown" if stop == "needs_review" else "supported")
    worker, result, artifact, chapter = pending(settings, domain, story, model=model)
    original = workflow_structure.ensure_character_review

    async def review_then_author_adopts(active_worker, candidate):
        reviewed = await original(active_worker, candidate)
        adopt(
            domain,
            story["branch_id"],
            artifact["id"],
            AdoptExtraction(
                base_story_revision=result["expected_story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in artifact["payload"]["records"]],
                idempotency_key="author-after-review-before-wait",
            ),
        )
        if stop == "paused":
            with domain.db.connect(True) as tx:
                tx.execute("UPDATE tasks SET pause=1 WHERE id=?", (worker.task["id"],))
        return reviewed

    monkeypatch.setattr(workflow_structure, "ensure_character_review", review_then_author_adopts)
    assert asyncio.run(maintain(worker, result))
    saved_task = domain.task(worker.task["id"])
    assert saved_task["status"] == "running"
    assert "pending_structure" not in saved_task["result"]
    assert "waiting_structure" not in saved_task["result"]
    assert result == saved_task["result"]
    assert result["structure_history"][0]["policy"] == "author_confirmed"
    assert result["expected_story_revision"] == domain.branch(story["branch_id"])["story_revision"]
    assert domain.artifact(artifact["id"])["status"] == "accepted"
    assert domain.artifact(artifact["id"])["payload"]["character_semantic_review"]
    assert len(model.calls) == 1 and domain.chapter(chapter["id"]) == chapter


def test_review_before_pause_is_saved_and_resumed_without_another_call(settings, domain, story):
    model = ReviewModel()
    worker, result, artifact, _ = pending(settings, domain, story, model=model)

    def pause():
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET pause=1 WHERE id=?", (worker.task["id"],))

    model.callback = pause
    assert not asyncio.run(maintain(worker, result))
    assert domain.task(worker.task["id"])["status"] == "paused"
    assert domain.artifact(artifact["id"])["payload"]["character_semantic_review"]["status"] == "supported"
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET pause=0 WHERE id=?", (worker.task["id"],))
    model.callback = None
    resumed = restart(settings, worker, model)
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_only_actual_changed_free_fields_are_reviewed_and_original_policy_is_not_relaxed(
    settings, domain, story, changed
):
    old = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "character",
                "title": "宁禾",
                "profile": "OLD_FREE_PROFILE_MUST_NOT_BE_EVIDENCE",
                "notes": "原先说明",
            },
        ),
    )["records"][0]
    model = ReviewModel()
    worker, result, artifact, _ = pending(
        settings,
        domain,
        story,
        model=model,
        profile=old["payload"]["profile"],
        notes="这次改变说明" if changed else old["payload"]["notes"],
    )
    payload = deepcopy(artifact["payload"])
    item = payload["records"][0]
    item.update(
        id=old["id"],
        target_id=old["id"],
        target_revision=old["revision"],
        previous_record={"revision": old["revision"], "record": old["payload"]},
        update_fields=["sources", *(["notes"] if changed else [])],
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    assert asyncio.run(maintain(worker, result)) is (not changed)
    if changed:
        assert len(model.calls) == 1
        assert [f["field"] for f in model.calls[0]["任务"]["人物字段"]] == ["notes"]
        assert "OLD_FREE_PROFILE_MUST_NOT_BE_EVIDENCE" not in dumps(model.calls[0])
        assert graph(domain, story["branch_id"])["records"][0]["payload"] == old["payload"]
    else:
        assert model.calls == []


def test_old_unreviewed_report_is_not_promoted_and_mixed_fields_hold_whole_candidate(settings, domain, story):
    model = ReviewModel({"profile": "supported", "notes": "unknown"})
    worker, result, artifact, _ = pending(settings, domain, story, model=model)
    legacy = {"version": "legacy-unreviewed", "status": "supported", "candidate_revision": 0}
    payload = deepcopy(artifact["payload"])
    payload["character_semantic_review"] = legacy
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    assert not asyncio.run(maintain(worker, result))
    saved = domain.artifact(artifact["id"])["payload"]
    assert saved["character_semantic_review_history"] == [legacy]
    assert {r["result"] for r in saved["character_semantic_review"]["results"]} == {"supported", "unknown"}
    assert len(model.calls) == 1
    assert domain.db.all("SELECT * FROM story_records") == []


def test_unknown_network_result_is_not_automatically_resent(settings, domain, story):
    model = ReviewModel()
    worker, result, artifact, _ = pending(settings, domain, story, model=model)

    def unknown():
        raise DomainError("network_unknown", "已派发但结果未知", 502)

    model.callback = unknown
    with pytest.raises(DomainError) as error:
        asyncio.run(maintain(worker, result))
    assert error.value.code == "network_unknown"
    assert len(model.calls) == 1
    assert "character_semantic_review" not in domain.artifact(artifact["id"])["payload"]
    assert domain.db.all("SELECT * FROM story_records") == []
