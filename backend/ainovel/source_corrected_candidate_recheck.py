"""Explicit prose recheck bound to a separately verified source correction.

This is a new policy and task kind; candidate-recheck/1 remains byte-for-byte
unchanged.  Source-only work has already completed before any prose is read.
"""

from copy import deepcopy
from time import time
from typing import Literal

from pydantic import Field

from .candidate_recheck import CandidateRecheckInput, freeze as old_freeze, sha
from .db import DomainError, dumps
from .flow_entailment import KEY as FLOW_KEY, VERSION as FLOW_VERSION
from .plot_review import attach_plot_review, review_passed
from . import source_interpretation_correction as correction

VERSION = "source-corrected-candidate-recheck/1"
KIND = "source_corrected_candidate_recheck"


class RecheckCorrectionInput(CandidateRecheckInput):
    source_identity_context: Literal[False] = False
    preview_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_id: str = Field(min_length=1, max_length=100)


def freeze(domain, request, *, verify_basis=True, flow_entailment_version=FLOW_VERSION):
    from .conditional_review import VERSION as CONDITIONAL_VERSION
    from .book_applicability import VERSION as APPLICABILITY_VERSION

    from . import prepared_source_scope_correction as scope_correction

    if domain.artifact(request["receipt_id"])["kind"] == scope_correction.KIND:
        return scope_correction.freeze_recheck(
            domain, request, verify_basis=verify_basis, flow_entailment_version=flow_entailment_version
        )
    original = old_freeze(
        domain,
        request,
        conditional_policy=CONDITIONAL_VERSION,
        applicability_version=APPLICABILITY_VERSION,
        flow_entailment_version=flow_entailment_version,
    )
    receipt = domain.artifact(request["receipt_id"])
    payload = receipt["payload"]
    if receipt["kind"] != correction.KIND or receipt["status"] != "candidate" or not receipt["task_id"]:
        raise DomainError("source_correction_receipt", "须选择本候选有效的独立来源解释回执。", 409)
    task = domain.task(receipt["task_id"])
    correction.validate_shape(domain, task)
    if (
        payload.get("version") != correction.VERSION
        or payload.get("state") != "ready"
        or task["kind"] != correction.KIND
        or task["status"] != "succeeded"
        or task["result"].get("source_correction_version") != correction.VERSION
        or task["result"].get("artifact_id") != receipt["id"]
        or task["result"].get("source_correction_state") != "ready"
        or payload.get("snapshot") != task["result"].get("source_correction_snapshot")
        or (receipt["novel_id"], receipt["branch_id"], receipt["chapter_id"])
        != (request["novel_id"], request["branch_id"], request["chapter_id"])
    ):
        raise DomainError("source_correction_receipt", "来源解释回执未就绪、目标不符或来源不完整。", 409)
    current = correction.freeze(
        domain,
        {
            **request,
            "item_ids": payload["snapshot"]["item_ids"],
            "preview_basis_sha256": payload["snapshot"]["basis_sha256"],
            **(
                {correction.SHAPE_KEY: payload["snapshot"][correction.SHAPE_KEY]}
                if correction.SHAPE_KEY in payload["snapshot"]
                else {}
            ),
        },
    )
    if current != payload["snapshot"]:
        raise DomainError("source_correction_changed", "回执绑定的候选或原来源已变化，不能套用旧解释。", 409)
    proposed = payload.get("proposal", {})
    checks = payload.get("preservation", {})
    contract = payload.get("corrected_contract", {})
    from .plot_audit import ScopedAuthorPlot

    try:
        contract = ScopedAuthorPlot.model_validate(contract).model_dump()
    except ValueError as error:
        raise DomainError(
            "source_correction_receipt", "核心解释须保持原ScopedAuthorPlot格式，元数据独立保留。", 409
        ) from error
    if not correction.ready(proposed, checks):
        raise DomainError("source_correction_receipt", "回执仍有未知或未通过事项。", 409)
    old_items = current["prepared_contract"]["items"]
    if len(contract.get("items", [])) != len(old_items):
        raise DomainError("source_correction_receipt", "回执缺少原情节事项。", 409)
    for item, old in zip(contract["items"], old_items, strict=True):
        if any(item.get(key) != value for key, value in old.items() if key not in {"actor", "certainty"}):
            raise DomainError("source_correction_receipt", "回执改动了不允许纠偏的事件字段。", 409)
    snapshot = {
        **original,
        "version": VERSION,
        "prepared_contract": deepcopy(contract),
        "receipt_id": receipt["id"],
        "receipt_payload_sha256": sha(payload),
        "original_contract_sha256": current["original_contract_sha256"],
        "source_correction_metadata": {
            "version": correction.VERSION,
            "receipt_id": receipt["id"],
            "items": deepcopy(payload["proposal"]["items"]),
        },
    }
    snapshot["basis_sha256"] = sha(snapshot)
    if verify_basis and request.get("preview_basis_sha256") != snapshot["basis_sha256"]:
        raise DomainError(
            "source_correction_preview_changed", "复检预览依据已改变，请保留输入并重新读取。", 409
        )
    return snapshot


def validate(worker):
    from . import prepared_source_scope_correction as scope_correction

    if worker.task["result"].get("source_corrected_recheck_version") == scope_correction.RECHECK_VERSION:
        return scope_correction.validate_recheck(worker)
    from .conditional_review import KEY as CONDITIONAL_KEY
    from .book_applicability import KEY as APPLICABILITY_KEY

    expected = worker.task["result"].get("source_corrected_recheck_snapshot")
    if (
        worker.task["result"].get("source_corrected_recheck_version") != VERSION
        or expected is None
        or expected.get(FLOW_KEY) != worker.task["result"].get(FLOW_KEY)
        or any(
            expected.get(key) != worker.task["result"].get(key)
            for key in (CONDITIONAL_KEY, APPLICABILITY_KEY)
        )
        or freeze(
            worker.domain,
            worker.task["payload"],
            flow_entailment_version=expected.get(FLOW_KEY),
        )
        != expected
    ):
        raise DomainError(
            "source_correction_changed", "显式来源纠偏复检的依据已变化，保留原结果并停止。", 409
        )
    return expected


def verified_author_contract(worker, author_text, number, supplied):
    snapshot = validate(worker)
    if (
        author_text != snapshot["plot"]["plot"]
        or number != snapshot["plot"]["number"]
        or supplied != snapshot["prepared_contract"]
    ):
        raise DomainError("source_correction_receipt", "本次复检只能使用已核实回执的冻结解释。", 409)
    return deepcopy(snapshot["prepared_contract"])


async def run(worker):
    snapshot = validate(worker)
    source = worker.domain.artifact(snapshot["artifact_id"])
    parent = worker.domain.task(snapshot["source_workflow_id"])
    context = worker.context(source["branch_id"], source["chapter_id"])
    instructions = {
        "动作": "按显式来源纠偏回执复检待审正文",
        "本章": snapshot["plot"],
        "固定条件": snapshot["fixed"],
        "全书简报": parent["payload"]["brief"],
        "预设": parent["payload"]["writing_profile"],
        "要求": "本次读取候选只用于正文核对，不重新解释来源，不生成、采纳或恢复正文；原解释和报告保留。",
    }
    if parent["payload"].get("completion_scope") == "segment":
        instructions["本次运行范围"] = "阶段批次；全书后续承诺仍待核对。"
    text = source["payload"]["text"]
    review = await worker.review(text, snapshot["fixed"], context, snapshot["plot"]["number"], instructions)
    validate(worker)
    review = await attach_plot_review(
        worker,
        review,
        snapshot["plot"]["plot"],
        text,
        snapshot["plot"]["number"],
        snapshot.get("source_kind", "author_plot"),
        prepared_contract=snapshot["prepared_contract"],
    )
    validate(worker)
    review["plot_audit"]["candidate_revision"] = snapshot["candidate_revision"]
    passed = review_passed(review)
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

    def publish(tx, artifact_id):
        validate(worker)
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        payload = deepcopy(source["payload"])
        for key in ("review", "plot_audit"):
            if key in payload:
                payload["previous_" + key] = payload[key]
        payload.update(review=review, plot_audit=review["plot_audit"], review_passed=passed, memory=memory)
        payload.setdefault("source_corrected_rechecks", []).append(
            {
                "artifact_id": artifact_id,
                "task_id": worker.task["id"],
                "receipt_id": snapshot["receipt_id"],
                "target": target,
                "passed": passed,
            }
        )
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), source["id"]))
        worker.db.event(
            tx,
            parent["id"],
            "candidate_rechecked",
            {
                "artifact_id": source["id"],
                "review_artifact_id": artifact_id,
                "target": target,
                "receipt_id": snapshot["receipt_id"],
                "previous_revision": snapshot["candidate_revision"],
                "previous_content_sha256": snapshot["content_sha256"],
                "previous_candidate": source["payload"],
                "passed": passed,
            },
        )
        result = {
            **worker.task["result"],
            "artifact_id": artifact_id,
            "target": target,
            "review_passed": passed,
        }
        tx.execute(
            "UPDATE tasks SET result=?,status='succeeded',updated=? WHERE id=? AND attempt=?",
            (dumps(result), time(), worker.task["id"], worker.attempt),
        )
        worker.db.event(tx, worker.task["id"], "checkpoint", {"status": "succeeded", **result})

    worker.domain.stage(
        worker.task["payload"],
        KIND,
        {
            "version": snapshot["version"],
            "target": target,
            "receipt_id": snapshot["receipt_id"],
            "review": review,
            "plot_audit": review["plot_audit"],
            "review_passed": passed,
            "previous_candidate": deepcopy(source["payload"]),
            "memory_generated": bool(memory),
            "note": "只读显式来源纠偏复检报告；不采纳、不恢复，原稿和原报告保持可查。",
        },
        {"story_revision": snapshot["story_revision"], "candidate_target": target},
        worker.task["id"],
        worker.attempt,
        on_stage=publish,
    )


def register_routes(app, domain, budget, provider_ready):
    @app.get("/api/artifacts/{artifact_id}/recheck-source-correction-preview")
    def recheck_preview(artifact_id: str, receipt_id: str):
        candidate = domain.artifact(artifact_id)
        request = {
            "source_candidate_id": artifact_id,
            "candidate_revision": candidate["candidate_revision"],
            "novel_id": candidate["novel_id"],
            "branch_id": candidate["branch_id"],
            "chapter_id": candidate["chapter_id"],
            "base_revision": candidate["base_revision"],
            "receipt_id": receipt_id,
        }
        snapshot = freeze(domain, request, verify_basis=False)
        return {
            "version": snapshot["version"],
            "target": artifact_id,
            "candidate_revision": candidate["candidate_revision"],
            "receipt_id": receipt_id,
            "receipt_payload_sha256": snapshot["receipt_payload_sha256"],
            "basis_sha256": snapshot["basis_sha256"],
            "story_revision": snapshot["story_revision"],
            "source_text": snapshot["plot"]["plot"],
            "corrected_contract": snapshot["prepared_contract"],
            "notice": "将按此候选版本和来源纠偏回执独立复检，产生模型费用；不生成、采纳或恢复正文。",
        }

    @app.post("/api/artifacts/{artifact_id}/recheck-source-correction", status_code=202)
    def create_recheck(artifact_id: str, data: RecheckCorrectionInput):
        payload = {**correction.request_payload(domain, artifact_id, data), "plot_contract_check": True}
        previous = domain.find_request(KIND, payload)
        if previous:
            return previous
        provider_ready(data.account)
        return domain.queue(KIND, payload)

    @app.get("/api/artifacts/{artifact_id}/recheck-source-correction")
    def recheck_request_query(artifact_id: str, client_request_id: str):
        return correction.query_request(domain, artifact_id, client_request_id, KIND)

    @app.get("/api/artifacts/{artifact_id}/recheck-source-correction/{client_request_id}")
    def recheck_request(artifact_id: str, client_request_id: str):
        return correction.query_request(domain, artifact_id, client_request_id, KIND)
