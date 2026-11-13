"""Durable maintenance after manuscript adoption, shared by assisted/automatic books."""

from .db import DomainError
from .story_extraction import AdoptExtraction, adopt, freeze_sources, run_extraction
from .story_graph import metadata_extension


def safe_automatic(item):
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
    if worker.domain.task(worker.task["id"])["pause"]:
        worker.checkpoint(result, "paused")
        return False
    records = artifact["payload"]["records"]
    unsafe = [r["id"] for r in records if not safe_automatic(r)]
    if (
        artifact["status"] != "candidate"
        or (records and worker.task["payload"]["adoption"] == "manual")
        or unsafe
    ):
        result["waiting_structure"] = identifier
        worker.checkpoint(
            result, "needs_review", "结构候选需作者核对；旧事实纠错、档案改写及保留／放弃承诺不自动采用。"
        )
        return False
    data = AdoptExtraction(
        base_story_revision=result["expected_story_revision"],
        candidate_revision=artifact["candidate_revision"],
        selected_ids=[r["id"] for r in records],
        idempotency_key="workflow-structure-" + identifier,
    )
    try:
        adopt(worker.domain, worker.task["branch_id"], identifier, data, worker.attempt)
    except DomainError as error:
        if error.code in {"cancelled", "story_changed", "revision_conflict", "candidate_changed"}:
            raise
        result["waiting_structure"] = identifier
        worker.checkpoint(result, "needs_review", str(error))
        return False
    fresh = worker.domain.task(worker.task["id"])["result"]
    result.clear()
    result.update(fresh)
    return True
