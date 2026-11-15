"""Every accepted opaque nonce remains queryable without replaying its POST."""

from copy import deepcopy
from urllib.parse import quote, urlencode
import pytest
from ainovel.worker import Worker
from helpers.author_clarification_fixture import source_unverified, ADDITION
from ainovel import author_source_clarification as bridge

NONCES = [
    "ascii-nonce-123",
    "edge/nonce-123",
    "中文?号#nonce",
    "line\nbreak-nonce-123",
    "leading/and/trailing/",
    "dot/../nonce-123",
    "literal%2Fnonce-123",
]


@pytest.mark.parametrize("nonce", NONCES)
def test_all_accepted_nonce_characters_can_be_queried_by_opaque_query(settings, domain, story, client, nonce):
    _, parent, _ = source_unverified(settings, domain, story)
    prefix = f"/api/tasks/{parent['id']}/author-source-clarifications"
    preview = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview")
    assert preview.status_code == 200, preview.text
    body = {
        "version": bridge.VERSION,
        "client_request_id": nonce,
        "preview_basis_sha256": preview.json()["request_basis"]["preview_basis_sha256"],
        "clarification": ADDITION,
        "actor": "authorized_operator",
        "reason": "",
    }
    response = client.post(prefix, json=body)
    assert response.status_code == 202, response.text
    child = response.json()
    old_tables = {
        table: domain.db.all(f"SELECT * FROM {table} ORDER BY rowid")
        for table in ["tasks", "task_steps", "artifacts", "chapters", "task_events", "task_requests"]
    }
    budget = Worker(settings).budget
    usage = deepcopy(budget.report())
    encoded = quote(nonce, safe="")
    found = client.get(prefix + "?" + urlencode({"client_request_id": nonce}))
    assert found.status_code == 200, {
        "nonce": repr(nonce),
        "encoded": encoded,
        "status": found.status_code,
        "body": found.text,
    }
    assert found.json() == {"source_task_id": parent["id"], "client_request_id": nonce, "task": child}
    unknown = client.get(prefix + "?" + urlencode({"client_request_id": nonce + "-unsubmitted"}))
    assert unknown.status_code == 200 and unknown.json()["task"] is None
    if "/" in nonce:
        # A percent literal is a distinct opaque nonce, not a second decode.
        alias = client.get(prefix + "?" + urlencode({"client_request_id": nonce.replace("/", "%2F")}))
        assert alias.status_code == 200 and alias.json()["task"] is None
    assert {
        table: domain.db.all(f"SELECT * FROM {table} ORDER BY rowid") for table in old_tables
    } == old_tables
    assert budget.report() == usage


@pytest.mark.parametrize("nonce", ["legacy-ascii-123", "中文?号#nonce", "line\nbreak-nonce-123"])
def test_legacy_get_segment_remains_compatible(settings, domain, story, client, nonce):
    _, parent, _ = source_unverified(settings, domain, story)
    prefix = f"/api/tasks/{parent['id']}/author-source-clarifications"
    preview = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview").json()
    response = client.post(
        prefix,
        json={
            "version": bridge.VERSION,
            "client_request_id": nonce,
            "preview_basis_sha256": preview["request_basis"]["preview_basis_sha256"],
            "clarification": ADDITION,
            "actor": "authorized_operator",
            "reason": "",
        },
    )
    assert response.status_code == 202
    found = client.get(prefix + "/" + quote(nonce, safe=""))
    assert found.status_code == 200 and found.json()["task"]["id"] == response.json()["id"]
