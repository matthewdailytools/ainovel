"""Archived pre-source-role workflow rows, deliberately not the current creation API.

Only compatibility tests use this fixture. Fresh tests must call Domain.queue.
"""

from time import time

from ainovel.db import dumps, uid


def queue_historical_plan(
    domain, payload, branch_id, *, preparation=True, open_details=True, plot_version="author-plot-audit/2"
):
    initial = {
        "genre_profile_snapshot": domain.genre_profile(branch_id),
        "author_contract_snapshot": domain.author_contract(branch_id),
        "conditional_book_policy_version": "conditional-book-check/1",
        "book_applicability_version": "book-applicability/1",
        "expected_story_revision": domain.branch(branch_id)["story_revision"],
    }
    if payload.get("plot_contract_check"):
        if plot_version is not None:
            initial["plot_contract_version"] = plot_version
        if plot_version == "author-plot-audit/2":
            initial["flow_source_entailment_version"] = "source-flow-entailment/1"
        if preparation:
            initial["workflow_plan_preparation_version"] = "workflow-plan-preparation/1"
        if open_details:
            initial["workflow_plan_open_details_version"] = "workflow-plan-open-details/1"
    identifier = uid()
    now = time()
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,'workflow',?,?,'queued',?,?,?,?)",
            (identifier, payload["novel_id"], branch_id, dumps(payload), dumps(initial), now, now),
        )
        if payload.get("client_request_id"):
            tx.execute("INSERT INTO task_requests VALUES(?,?)", (payload["client_request_id"], identifier))
        domain.db.event(tx, identifier, "queued", {"kind": "workflow"})
    return domain.task(identifier)
