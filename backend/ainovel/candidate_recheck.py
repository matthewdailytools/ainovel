"""Explicitly review an unadopted workflow draft against its pre-prose contract."""

from copy import deepcopy
import hashlib
from time import time
from types import SimpleNamespace

from pydantic import Field

from .db import DomainError, dumps
from .domain import char_count
from .models import Input
from .plot_review import attach_plot_review, review_passed

VERSION = "candidate-recheck/1"


class CandidateRecheckInput(Input):
    candidate_revision: int = Field(ge=0, strict=True)
    account: str = Field(min_length=1, max_length=100)
    client_request_id: str = Field(min_length=8, max_length=128)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)


def sha(value):
    return hashlib.sha256((value if isinstance(value, str) else dumps(value)).encode()).hexdigest()


def source_view(domain, parent):
    return SimpleNamespace(
        task=parent,
        domain=domain,
        author_snapshot=lambda: deepcopy(parent["result"]["author_contract_snapshot"]),
    )


def freeze(domain, request):
    from .plot_audit import VERSION as PLOT_VERSION
    from .workflow_plan_preparation import VERSION as PREPARATION_VERSION, frozen_contract

    source = domain.artifact(request["source_candidate_id"])
    if (
        source["kind"] != "chapter_draft"
        or source["status"] != "candidate"
        or source["candidate_revision"] != request["candidate_revision"]
        or (source["novel_id"], source["branch_id"], source["chapter_id"], source["base_revision"])
        != (request["novel_id"], request["branch_id"], request["chapter_id"], request["base_revision"])
        or not source["task_id"]
    ):
        raise DomainError("candidate_changed", "待复检候选的身份、状态或版本已改变，未派发。", 409)
    parent = domain.task(source["task_id"])
    progress = parent["result"]
    if (
        parent["kind"] != "workflow"
        or (parent["novel_id"], parent["branch_id"]) != (source["novel_id"], source["branch_id"])
        or parent["payload"].get("novel_id") != source["novel_id"]
        or parent["status"] != "needs_review"
        or parent["payload"].get("mode") != "automatic"
        or progress.get("waiting_artifact") != source["id"]
        or progress.get("plot_contract_version") != PLOT_VERSION
        or progress.get("workflow_plan_preparation_version") != PREPARATION_VERSION
        or not isinstance(progress.get("genre_profile_snapshot"), dict)
        or not isinstance(progress.get("author_contract_snapshot"), dict)
    ):
        raise DomainError(
            "candidate_recheck_source",
            "当前只复检原全自动流程中已有完整写前契约的待审正文；旧任务和其他候选不升级。",
            409,
        )
    chapter = domain.chapter(source["chapter_id"], source["branch_id"])
    branch = domain.branch(source["branch_id"], source["novel_id"])
    if (
        chapter["revision"] != source["base_revision"]
        or chapter["archived"]
        or source["basis"].get("story_revision") != branch["story_revision"]
        or progress.get("expected_story_revision") != branch["story_revision"]
    ):
        raise DomainError("story_changed", "复检依据的保存稿或故事版本已改变，未发送旧候选。", 409)
    row = progress.get("plot_preparations", {}).get(str(chapter["position"]))
    plot = next((p for p in progress["plan"]["chapters"] if p["number"] == chapter["position"]), None)
    if (
        row is None
        or row.get("chapter_id") != chapter["id"]
        or progress.get("next_chapter") != chapter["position"]
        or plot is None
        or not isinstance(row.get("prepared_contract"), dict)
    ):
        raise DomainError("candidate_recheck_source", "本章没有完整的写前冻结契约，未重新解释章纲。", 409)
    contract = frozen_contract(
        source_view(domain, parent), row["effective_plot"], plot["number"], row["prepared_contract"]
    )
    fixed = parent["payload"]["fixed"] + [
        item for item in plot["fixed"] if item not in parent["payload"]["fixed"]
    ]
    text = source["payload"].get("text")
    if source["payload"].get("fixed") != fixed or not isinstance(text, str):
        raise DomainError("candidate_recheck_source", "候选的固定条件或正文无法与原流程核对。", 409)
    if not parent["payload"]["min_chars"] <= char_count(text) <= parent["payload"]["max_chars"]:
        raise DomainError("candidate_length", "候选字数超出原流程范围，请先保存符合范围的候选。", 409)
    domain.check_genre_snapshot(source["branch_id"], progress["genre_profile_snapshot"])
    domain.check_author_contract_snapshot(source["branch_id"], progress["author_contract_snapshot"])
    return {
        "version": VERSION,
        "artifact_id": source["id"],
        "candidate_revision": source["candidate_revision"],
        "candidate_payload_sha256": sha(source["payload"]),
        "content_sha256": sha(text),
        "chapter_id": chapter["id"],
        "chapter_revision": chapter["revision"],
        "story_revision": branch["story_revision"],
        "source_workflow_id": parent["id"],
        "source_payload_sha256": sha(parent["payload"]),
        "source_progress_sha256": sha(progress),
        "plot": {**deepcopy(plot), "plot": row["effective_plot"]},
        "prepared_contract": contract,
        "fixed": fixed,
        "genre_profile_snapshot": deepcopy(progress["genre_profile_snapshot"]),
        "author_contract_snapshot": deepcopy(progress["author_contract_snapshot"]),
    }


def validate(worker):
    if worker.task["result"].get("candidate_recheck_version") != VERSION:
        raise DomainError("candidate_recheck_version", "候选复检政策无法确认，未升级旧任务。", 409)
    expected = worker.task["result"].get("candidate_recheck_snapshot")
    if expected is None or freeze(worker.domain, worker.task["payload"]) != expected:
        raise DomainError("candidate_changed", "候选或其冻结来源在复检期间改变，旧结果保留但不发布。", 409)
    return expected


def verified_contract(worker, author_text, number, supplied):
    snapshot = validate(worker)
    if (
        author_text != snapshot["plot"]["plot"]
        or number != snapshot["plot"]["number"]
        or supplied != snapshot["prepared_contract"]
    ):
        raise DomainError("candidate_recheck_source", "复检不能重新解释或替换原写前契约。", 409)
    return deepcopy(snapshot["prepared_contract"])


async def run(worker):
    snapshot = validate(worker)
    request = worker.task["payload"]
    source = worker.domain.artifact(snapshot["artifact_id"])
    parent = worker.domain.task(snapshot["source_workflow_id"])
    text = source["payload"]["text"]
    context = worker.context(request["branch_id"], request["chapter_id"])
    instructions = {
        "动作": "复检已编辑的未采纳候选",
        "本章": snapshot["plot"],
        "固定条件": snapshot["fixed"],
        "全书简报": parent["payload"]["brief"],
        "结局方向": parent["result"]["plan"]["ending"],
        "预设": parent["payload"]["writing_profile"],
        "要求": "只核对本次候选及原写前章纲，不生成正文、不改章纲，不把旧报告当成新稿事实。",
    }
    if parent["payload"].get("completion_scope") == "segment":
        instructions["本次运行范围"] = "阶段批次；全书终局及后续计划不要求在本章提前完成。"
    review = await worker.review(text, snapshot["fixed"], context, snapshot["plot"]["number"], instructions)
    validate(worker)
    review = await attach_plot_review(
        worker,
        review,
        snapshot["plot"]["plot"],
        text,
        snapshot["plot"]["number"],
        "workflow_plan",
        prepared_contract=snapshot["prepared_contract"],
    )
    review["plot_audit"]["candidate_revision"] = snapshot["candidate_revision"]
    passed = review_passed(review)
    validate(worker)
    memory = await worker.memory(text, context) if passed else {}
    validate(worker)
    target = {
        "kind": "candidate",
        "artifact_id": source["id"],
        "revision": snapshot["candidate_revision"],
        "chapter_id": source["chapter_id"],
        "base_revision": source["base_revision"],
        "content_sha256": snapshot["content_sha256"],
    }

    def publish(tx, report_id):
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        validate(worker)
        payload = deepcopy(source["payload"])
        if "review" in payload:
            payload["previous_review"] = payload["review"]
        if "plot_audit" in payload:
            payload["previous_plot_audit"] = payload["plot_audit"]
        payload.update(review=review, plot_audit=review["plot_audit"], review_passed=passed, memory=memory)
        payload.setdefault("candidate_rechecks", []).append(
            {"artifact_id": report_id, "task_id": worker.task["id"], "target": target, "passed": passed}
        )
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), source["id"]))
        tx.execute("UPDATE tasks SET updated=? WHERE id=?", (time(), parent["id"]))
        worker.db.event(
            tx,
            parent["id"],
            "candidate_rechecked",
            {
                "artifact_id": source["id"],
                "review_artifact_id": report_id,
                "target": target,
                "passed": passed,
                "previous_revision": snapshot["candidate_revision"],
                "previous_content_sha256": snapshot["content_sha256"],
                "previous_candidate": source["payload"],
            },
        )
        completed = {
            **worker.task["result"],
            "artifact_id": report_id,
            "target": target,
            "review_passed": passed,
        }
        tx.execute(
            "UPDATE tasks SET result=?,status='succeeded',error='',updated=? WHERE id=? AND attempt=?",
            (dumps(completed), time(), worker.task["id"], worker.attempt),
        )
        worker.db.event(tx, worker.task["id"], "checkpoint", {"status": "succeeded", **completed})

    worker.domain.stage(
        request,
        "candidate_recheck",
        {
            "version": VERSION,
            "target": target,
            "source_workflow_id": parent["id"],
            "review": review,
            "plot_audit": review["plot_audit"],
            "review_passed": passed,
            "memory_generated": bool(memory),
            "previous_candidate": deepcopy(source["payload"]),
            "note": "仅复核此候选版本，未改保存稿、未采纳候选，也未恢复原流程；模型结论仍待作者核对。",
        },
        {"story_revision": snapshot["story_revision"], "candidate_target": target},
        worker.task["id"],
        worker.attempt,
        on_stage=publish,
    )
