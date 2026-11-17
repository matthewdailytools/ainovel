"""New explicit rechecks freeze the flow policy without upgrading their parent."""

import asyncio
from copy import deepcopy

import pytest

from ainovel.db import dumps
from ainovel.flow_entailment import KEY, VERSION, OBJECT_KEY
from ainovel.worker import Worker
from test_candidate_recheck import stopped_candidate as stopped_candidate, queue_recheck, RecheckModel
from test_assisted_candidate_recheck import assisted_candidate as assisted_candidate


@pytest.mark.parametrize("fixture", ["stopped_candidate", "assisted_candidate"])
def test_new_recheck_freezes_source_support_policy_without_rewriting_parent(client, domain, request, fixture):
    parent, candidate, *_ = request.getfixturevalue(fixture)
    before = deepcopy(parent)
    queued = queue_recheck(client, candidate)
    assert queued.status_code == 202, queued.text
    task = queued.json()
    assert task["result"][KEY] == VERSION
    assert task["result"]["candidate_recheck_snapshot"][KEY] == VERSION
    assert domain.task(parent["id"]) == before
    assert domain.artifact(candidate["id"])["payload"] == candidate["payload"]
    assert task["result"]["candidate_recheck_snapshot"]["source_payload_sha256"]


@pytest.mark.parametrize(
    "tamper", ["task_version", "snapshot_version", "task_missing", "flow_removed_with_guidance"]
)
def test_policy_mismatch_stops_before_any_paid_call(client, domain, stopped_candidate, settings, tamper):
    parent, candidate = stopped_candidate
    queued = queue_recheck(client, candidate).json()
    result = deepcopy(queued["result"])
    if tamper == "task_version":
        result[KEY] = "future/8"
    elif tamper == "snapshot_version":
        result["candidate_recheck_snapshot"][KEY] = "future/8"
    elif tamper == "task_missing":
        result.pop(KEY)
    else:
        result.pop(KEY)
        result["candidate_recheck_snapshot"].pop(KEY)
        assert OBJECT_KEY in result["candidate_recheck_snapshot"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), queued["id"]))
    model = RecheckModel()
    assert asyncio.run(Worker(settings, model).run_once())
    assert domain.task(queued["id"])["status"] == "blocked_runtime"
    assert model.actions == [] and model.calls == 0
    assert domain.artifact(candidate["id"])["payload"] == candidate["payload"]
    assert domain.task(parent["id"]) == parent


def test_historical_frozen_recheck_does_not_gain_new_call_or_marker(
    settings, domain, stopped_candidate, client
):
    parent, candidate = stopped_candidate
    task = queue_recheck(client, candidate).json()
    legacy = deepcopy(task["result"])
    legacy.pop(KEY)
    legacy["candidate_recheck_snapshot"].pop(KEY)
    # This historical task predates both flow support and the new object template.
    legacy["candidate_recheck_snapshot"].pop(OBJECT_KEY)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy), task["id"]))
    model = RecheckModel()
    assert asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    assert KEY not in done["result"] and KEY not in done["result"]["candidate_recheck_snapshot"]
    assert OBJECT_KEY not in done["result"] and OBJECT_KEY not in done["result"]["candidate_recheck_snapshot"]
    assert domain.task(parent["id"])["result"] == parent["result"]
    assert "独立核对正文流向观察的来源蕴含" not in model.actions
