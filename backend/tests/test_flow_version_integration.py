"""New flow policy creation and exact historical nonce recovery stay separate."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from ainovel import flow_policy_continuation as continuation
from ainovel import source_corrected_candidate_recheck as corrected
from ainovel.candidate_recheck import freeze as candidate_freeze, validate as validate_candidate
from ainovel.conditional_review import KEY as CONDITIONAL_KEY
from ainovel.book_applicability import KEY as APPLICABILITY_KEY
from ainovel.db import dumps
from ainovel.flow_entailment import KEY, VERSION, LEGACY_VERSION
from ainovel.worker import Worker
from test_flow_policy_continuation import (
    parent as parent,
    legacy_checkpoint as legacy_checkpoint,
    preview,
    request,
    submit,
    update_task,
)
from test_source_interpretation_correction import (
    CorrectedRecheckModel,
    recheck_basis,
    run_source,
    stopped_source as stopped_source,
)


def test_explicit_v2_child_accepts_v1_checkpoint_without_rewriting_parent(client, domain, parent):
    parent = update_task(domain, parent["id"], result={**parent["result"], KEY: LEGACY_VERSION})
    original = deepcopy(parent)
    details = preview(client, parent)
    assert details.status_code == 200, details.text
    assert details.json()[KEY] == VERSION
    assert details.json()["source_flow_policy"] == LEGACY_VERSION
    assert details.json()["source_policies"][KEY] == LEGACY_VERSION
    child = submit(client, parent, request(client, parent)).json()
    assert child["result"][KEY] == VERSION
    assert child["result"]["plan"] == parent["result"]["plan"]
    assert child["result"]["flow_policy_continuation"]["source_flow_policy"] == LEGACY_VERSION
    saved = domain.task(parent["id"])
    assert saved["result"] == {**original["result"], "continued_to": child["id"]}
    assert saved["payload"] == original["payload"]
    assert preview(client, child).status_code == 409


def test_v1_nonce_replays_by_its_frozen_request_but_cannot_create_a_new_v1_child(
    client, domain, parent, monkeypatch
):
    # Reproduce a historical persisted child using the former creation version.
    with monkeypatch.context() as old:
        old.setattr(continuation, "VERSION", LEGACY_VERSION)
        body = request(client, parent, "historical-v1-child")
        body[KEY] = LEGACY_VERSION
        first = submit(client, parent, body)
        assert first.status_code == 202, first.text
    assert first.json()["result"][KEY] == LEGACY_VERSION
    before = domain.db.all("SELECT * FROM tasks")
    replay = submit(client, parent, body)
    assert replay.status_code == 202, replay.text
    assert replay.json() == first.json()
    found = client.get(f"/api/tasks/{parent['id']}/flow-policy-continuations/{body['client_request_id']}")
    assert found.status_code == 200 and found.json() == first.json()
    assert domain.db.all("SELECT * FROM tasks") == before
    changed = submit(client, parent, {**body, "client_request_id": "new-v1-is-not-authorized"})
    assert changed.status_code == 409 and changed.json()["code"] == "flow_entailment_version"


def test_candidate_recheck_validates_its_existing_v1_snapshot_and_nonce(client, domain, stopped_source):
    parent, candidate = stopped_source
    body = {"candidate_revision": 0, "account": "openai_1", "client_request_id": "old-candidate-v1"}
    response = client.post(f"/api/artifacts/{candidate['id']}/recheck", json=body)
    assert response.status_code == 202, response.text
    current = response.json()
    assert current["result"][KEY] == VERSION
    legacy = candidate_freeze(
        domain,
        current["payload"],
        conditional_policy=current["result"][CONDITIONAL_KEY],
        applicability_version=current["result"][APPLICABILITY_KEY],
        flow_entailment_version=LEGACY_VERSION,
    )
    saved = update_task(
        domain,
        current["id"],
        result={**current["result"], KEY: LEGACY_VERSION, "candidate_recheck_snapshot": legacy},
    )
    assert validate_candidate(SimpleNamespace(task=saved, domain=domain)) == legacy
    replay = client.post(f"/api/artifacts/{candidate['id']}/recheck", json=body)
    assert replay.status_code == 202 and replay.json() == saved
    assert domain.task(parent["id"]) == parent


def test_corrected_recheck_rebuilds_with_its_existing_v1_snapshot_and_nonce(
    settings, client, domain, stopped_source
):
    parent, candidate = stopped_source
    source_task, _ = run_source(settings, domain, client, candidate)
    assert source_task["status"] == "succeeded", source_task["error"]
    receipt_id = source_task["result"]["artifact_id"]
    body = {
        "candidate_revision": 0,
        "account": "openai_1",
        "client_request_id": "old-corrected-recheck-v1",
        "receipt_id": receipt_id,
        "preview_basis_sha256": recheck_basis(client, candidate, receipt_id),
    }
    endpoint = f"/api/artifacts/{candidate['id']}/recheck-source-correction"
    queued = client.post(endpoint, json=body)
    assert queued.status_code == 202, queued.text
    current = queued.json()
    assert current["result"][KEY] == VERSION
    legacy = corrected.freeze(
        domain, current["payload"], verify_basis=False, flow_entailment_version=LEGACY_VERSION
    )
    body["preview_basis_sha256"] = legacy["basis_sha256"]
    saved = update_task(
        domain,
        current["id"],
        payload=dumps({**current["payload"], "preview_basis_sha256": legacy["basis_sha256"]}),
        result={**current["result"], KEY: LEGACY_VERSION, "source_corrected_recheck_snapshot": legacy},
    )
    assert corrected.validate(SimpleNamespace(task=saved, domain=domain)) == legacy
    replay = client.post(endpoint, json=body)
    assert replay.status_code == 202 and replay.json() == saved
    model = CorrectedRecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    completed = domain.task(current["id"])
    assert completed["status"] == "succeeded", completed["error"]
    assert completed["result"][KEY] == LEGACY_VERSION
    assert domain.task(parent["id"]) == parent
