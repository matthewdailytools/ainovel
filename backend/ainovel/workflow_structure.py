"""Durable maintenance after manuscript adoption, shared by assisted/automatic books."""

import json
from time import time

from .db import DomainError, dumps
from .story_extraction import AdoptExtraction, adopt, freeze_sources, run_extraction
from .story_graph import metadata_extension
from .extraction_scenes import can_auto_replace
from .character_review import ensure_character_review


def checkpoint_pending(worker, result, identifier, status, error=""):
    """Stop on the current candidate without undoing concurrent author adoption."""
    with worker.db.connect(True) as tx:
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        row = tx.execute("SELECT result FROM tasks WHERE id=?", (worker.task["id"],)).fetchone()
        fresh = json.loads(row["result"])
        if not fresh.get("pending_structure") and any(
            item.get("artifact_id") == identifier for item in fresh.get("structure_history", [])
        ):
            result.clear()
            result.update(fresh)
            return True
        if fresh.get("pending_structure", {}).get("artifact_id") != identifier:
            raise DomainError("workflow_progress", "待维护结构已改变，旧等待状态未套用。", 409)
        if status == "needs_review":
            fresh["waiting_structure"] = identifier
        tx.execute(
            "UPDATE tasks SET result=?,status=?,error=?,updated=? WHERE id=? AND attempt=?",
            (dumps(fresh), status, error, time(), worker.task["id"], worker.attempt),
        )
        worker.db.event(tx, worker.task["id"], "checkpoint", {"status": status, **fresh})
        result.clear()
        result.update(fresh)
    return False


def safe_automatic(item):
    record = item["record"]
    if record["kind"] == "relationship" and record["from_id"] == record["to_id"]:
        # A missing counterpart must not silently become the same character.
        # Keep explicit author adoption available for intentional self-relations.
        return False
    if not item.get("target_id"):
        return True
    before = item["previous_record"]["record"]
    after = item["record"]
    if metadata_extension(before, after):
        return True
    marker = {"event": "status", "state": "truth", "relationship": "truth"}.get(before["kind"])
    if (
        marker
        and before[marker] == "planned"
        and after[marker] == "actual"
        and not before["sources"]
        and after["sources"]
        and metadata_extension(before, {**after, marker: "planned"})
    ):
        # Only a source-free plan can be realized in place. Retaining earlier
        # citations to a future plan would grant actual knowledge too early.
        return True
    if before["kind"] == "promise":
        # Stage advancement must not silently remove the original evidence.
        evidence_preserved = metadata_extension(before, {**after, "stages": before["stages"]})
        unchanged = {k: v for k, v in before.items() if k not in {"stages", "sources"}} == {
            k: v for k, v in after.items() if k not in {"stages", "sources"}
        }
        old, new = before["stages"], after["stages"]
        return (
            unchanged
            and evidence_preserved
            and new[: len(old)] == old
            and len(new) > len(old)
            and all(s["status"] in {"planted", "reinforced", "resolved"} for s in new[len(old) :])
        )
    return False


async def maintain(worker, result):
    pending = result.get("pending_structure")
    if not pending:
        return True
    if worker.domain.task(worker.task["id"])["pause"]:
        if pending.get("artifact_id"):
            return checkpoint_pending(worker, result, pending["artifact_id"], "paused")
        worker.checkpoint(result, "paused")
        return False
    chapter = worker.domain.chapter(pending["chapter_id"], worker.task["branch_id"])
    if chapter["revision"] != pending["revision"]:
        raise DomainError("revision_conflict", "待维护章节在正文采用后已改变，请先核对。", 409)
    if worker.domain.branch(worker.task["branch_id"])["story_revision"] != result["expected_story_revision"]:
        raise DomainError("story_changed", "结构维护前故事已改变，停止继续派发。", 409)
    identifier = pending.get("artifact_id")
    epoch = pending.get("epoch", "initial")
    if not identifier:
        # Recover a staged result even if the process stopped before its checkpoint.
        lineage = [worker.task["id"]]
        ancestor = worker.task["result"].get("continued_from")
        while ancestor and ancestor not in lineage:
            previous = worker.domain.task(ancestor)
            if previous["branch_id"] != worker.task["branch_id"] or previous["kind"] != "workflow":
                raise DomainError("workflow_progress", "续跑检查点的来源版本不一致。", 409)
            lineage.append(ancestor)
            ancestor = previous["result"].get("continued_from")
        placeholders = ",".join("?" for _ in lineage)
        found = worker.db.all(
            f"SELECT id FROM artifacts WHERE task_id IN ({placeholders}) AND kind='story_extraction' AND json_extract(basis,'$.maintenance_chapter.id')=? AND json_extract(basis,'$.maintenance_chapter.revision')=? AND json_extract(basis,'$.maintenance_epoch')=? ORDER BY created DESC LIMIT 1",
            (*lineage, chapter["id"], chapter["revision"], epoch),
        )
        if found:
            identifier = found[0]["id"]
        else:
            request = {
                "novel_id": worker.task["novel_id"],
                "branch_id": worker.task["branch_id"],
                "story_chapter_ids": [chapter["id"]],
                "max_output_tokens": 8192,
                "include_scene_ranges": worker.task["payload"].get("scene_maintenance", False),
            }
            snapshot = freeze_sources(worker.domain, request)
            context = worker.context(worker.task["branch_id"])
            kind, payload = await run_extraction(worker, request, context, snapshot)
            artifact = worker.domain.stage(
                request,
                kind,
                payload,
                {
                    "story_revision": snapshot["story_revision"],
                    "genre_profile": worker.genre_snapshot(),
                    "author_contract": worker.author_snapshot(),
                    "maintenance_chapter": {"id": chapter["id"], "revision": chapter["revision"]},
                    "maintenance_epoch": epoch,
                },
                worker.task["id"],
                worker.attempt,
            )
            identifier = artifact["id"]
        pending["artifact_id"] = identifier
        worker.checkpoint(result)
    artifact = worker.domain.artifact(identifier)

    def adopted_by_author():
        fresh = worker.domain.task(worker.task["id"])["result"]
        if not fresh.get("pending_structure") and any(
            r.get("artifact_id") == identifier for r in fresh.get("structure_history", [])
        ):
            result.clear()
            result.update(fresh)
            return True
        return False

    if artifact["status"] == "accepted" and adopted_by_author():
        return True
    if worker.domain.task(worker.task["id"])["pause"]:
        return checkpoint_pending(worker, result, identifier, "paused")
    review_token = None
    if artifact["status"] == "candidate" and worker.task["payload"]["adoption"] == "automatic":
        try:
            artifact, review_token = await ensure_character_review(worker, artifact)
        except DomainError as error:
            if (
                error.code in {"candidate_changed", "workflow_progress", "story_changed"}
                and adopted_by_author()
            ):
                return True
            raise
        report = artifact["payload"].get("character_semantic_review")
        if report and report.get("input_sha256") == review_token and report["status"] == "needs_review":
            return checkpoint_pending(
                worker,
                result,
                identifier,
                "needs_review",
                "人物档案来源复核有未知或矛盾，完整候选与报告保留，等待作者核对。",
            )
        if worker.domain.task(worker.task["id"])["pause"]:
            return checkpoint_pending(worker, result, identifier, "paused")
    records = artifact["payload"]["records"]
    unsafe = [r["id"] for r in records if not safe_automatic(r)]
    if (
        artifact["status"] != "candidate"
        or ((records or artifact["payload"].get("scenes")) and worker.task["payload"]["adoption"] == "manual")
        or unsafe
        or not can_auto_replace(worker.domain, artifact["payload"], worker.task)
    ):
        return checkpoint_pending(
            worker,
            result,
            identifier,
            "needs_review",
            "结构候选需作者核对；关系两端相同须确认身份，旧事实纠错、档案改写及保留／放弃承诺不自动采用。",
        )
    data = AdoptExtraction(
        base_story_revision=result["expected_story_revision"],
        candidate_revision=artifact["candidate_revision"],
        selected_ids=[r["id"] for r in records],
        selected_scene_ids=[s["id"] for s in artifact["payload"].get("scenes", [])],
        confirm_scene_replacement=bool(artifact["payload"].get("scene_replacements")),
        idempotency_key="workflow-structure-" + identifier,
    )
    try:
        adopt(worker.domain, worker.task["branch_id"], identifier, data, worker.attempt, review_token)
    except DomainError as error:
        if error.code in {"candidate_changed", "workflow_progress"} and adopted_by_author():
            return True
        if error.code in {"cancelled", "story_changed", "revision_conflict", "candidate_changed"}:
            raise
        return checkpoint_pending(worker, result, identifier, "needs_review", str(error))
    fresh = worker.domain.task(worker.task["id"])["result"]
    result.clear()
    result.update(fresh)
    return True
