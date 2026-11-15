"""Explicit corrected rechecks freeze the root's new flow policy, not legacy policy."""

import asyncio
from copy import deepcopy

import pytest

from ainovel.db import dumps
from ainovel.flow_entailment import KEY, VERSION
from ainovel.worker import Worker
from test_source_interpretation_correction import (
    CorrectedRecheckModel,
    recheck_basis,
    run_source,
    stopped_source as source_fixture,
)

stopped_source = source_fixture


@pytest.mark.parametrize("damage", [None, "missing", "wrong"])
def test_new_explicit_source_recheck_freezes_flow_policy_for_old_workflow(
    settings, domain, client, stopped_source, damage
):
    parent, candidate = stopped_source
    legacy = deepcopy(parent["result"])
    legacy.pop(KEY, None)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy), parent["id"]))
    task, _ = run_source(settings, domain, client, candidate)
    assert task["status"] == "succeeded", task["error"]
    assert KEY not in task["result"]
    receipt_id = task["result"]["artifact_id"]
    queued = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "explicit-flow-policy-request",
            "receipt_id": receipt_id,
            "preview_basis_sha256": recheck_basis(client, candidate, receipt_id),
        },
    )
    assert queued.status_code == 202, queued.text
    current = queued.json()
    assert current["result"][KEY] == VERSION
    assert current["result"]["source_corrected_recheck_snapshot"][KEY] == VERSION
    assert KEY not in domain.task(parent["id"])["result"]
    if damage:
        changed = deepcopy(current["result"])
        if damage == "missing":
            changed.pop(KEY)
        else:
            changed[KEY] = "unexpected-version"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(changed), current["id"]))
    model = CorrectedRecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(current["id"])
    assert completed["status"] == ("blocked_runtime" if damage else "succeeded"), completed["error"]
    if damage:
        assert model.actions == []
        assert domain.artifact(candidate["id"]) == candidate
