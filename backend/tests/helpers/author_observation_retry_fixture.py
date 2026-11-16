"""Anonymous known failures created by real isolated Worker/MockTransport calls."""

import asyncio

from ainovel import authored_source_scope as authored
from ainovel import authored_source_execution as execution
from ainovel import author_source_recheck as recheck
from ainovel.worker import Worker
from helpers.authored_classification_fixture import native, source_unverified


def wrong_unit(frame, value):
    if frame["任务"]["动作"] == authored.ACTION:
        value["unit_checks"][2].update(status="covered", observation_ids=["O02"])
        value["note"] = frame["任务"].get("前次产物", {}).get("note", "首次核对") + "；仍保留错误引用。"
    return value


def known_returned_failure(
    settings,
    domain,
    story,
    *,
    client=None,
    client_request_id="anonymous-known-observation-failure",
    account="deepseek",
):
    old, first, _ = source_unverified(
        settings, domain, story, client=client, client_request_id=client_request_id, account=account
    )
    old_preview = recheck.preview(domain, old.budget, first)
    request = {
        "version": recheck.VERSION,
        "client_request_id": client_request_id + "-source2",
        "preview_basis_sha256": old_preview["request_basis"]["preview_basis_sha256"],
        "reason": "仅按新版核对同一原话。",
    }
    if client:
        response = client.post(f"/api/tasks/{first['id']}/author-source-rechecks", json=request)
        assert response.status_code == 202, response.text
        child = response.json()
    else:
        child = recheck.recheck(domain, old.budget, first, request)
    worker = Worker(settings)
    frames = native(worker, transform=wrong_unit)
    assert asyncio.run(worker.run_once())
    current = domain.task(child["id"])
    assert current["status"] == "blocked_runtime" and len(frames) == 3
    assert current["result"][authored.RECEIPTS]["chapter:1"]["state"] == "pending"
    assert execution.KEY not in current["result"]
    return worker, current, first, frames
