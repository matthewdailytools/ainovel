"""Opt-in character repair stays strict and survives idempotency and continuation."""

import pytest
from pydantic import ValidationError

from ainovel.db import DomainError
from ainovel.domain import same_task_payload
from ainovel.models import WorkflowInput


def workflow_payload(novel_id, **options):
    return WorkflowInput(
        novel_id=novel_id,
        mode="automatic",
        brief="仅用于隔离契约测试，人物去向保持未知。",
        account="openai_1",
        structured_maintenance=True,
        **options,
    ).model_dump()


def test_character_field_repair_is_opt_in_and_accepts_only_boolean_values():
    assert workflow_payload("novel")["character_field_repair"] is False
    for enabled in (False, True):
        assert workflow_payload("novel", character_field_repair=enabled)["character_field_repair"] is enabled


@pytest.mark.parametrize("value", [None, 0, 1, 0.0, 1.0, "false", "true", "0", "1", "", [], {}])
def test_character_field_repair_rejects_coerced_boolean_values(value):
    with pytest.raises(ValidationError) as error:
        workflow_payload("novel", character_field_repair=value)
    assert error.value.errors()[0]["loc"] == ("character_field_repair",)


def test_character_field_repair_legacy_nonce_reuses_explicit_false_and_rejects_true(domain, story):
    payload = workflow_payload(story["id"], client_request_id="legacy-character-repair-nonce")
    legacy = {key: value for key, value in payload.items() if key != "character_field_repair"}
    original = domain.queue("workflow", legacy, story["branch_id"])

    repeated = domain.queue("workflow", payload, story["branch_id"])
    assert repeated["id"] == original["id"]
    assert "character_field_repair" not in repeated["payload"]
    assert same_task_payload(legacy, payload) and same_task_payload(payload, legacy)

    enabled = {**payload, "character_field_repair": True}
    assert not same_task_payload(legacy, enabled)
    assert not same_task_payload(payload, enabled)
    with pytest.raises(DomainError) as error:
        domain.queue("workflow", enabled, story["branch_id"])
    assert error.value.code == "idempotency"
    assert len(domain.db.all("SELECT id FROM tasks")) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_character_field_repair_account_continuation_preserves_boolean(client, domain, story, enabled):
    response = client.post(
        "/api/workflows", json=workflow_payload(story["id"], character_field_repair=enabled)
    )
    assert response.status_code == 202, response.text
    original = response.json()
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (original["id"],))

    request = {
        "client_request_id": "continue-character-repair-" + str(enabled).lower(),
        "account": "openai_2",
        "reason": "保留冻结功能开关，从暂停检查点换账户续跑。",
    }
    response = client.post(f"/api/tasks/{original['id']}/continue", json=request)
    assert response.status_code == 202, response.text
    continued = response.json()
    assert continued["id"] != original["id"]
    assert continued["branch_id"] == original["branch_id"]
    assert continued["payload"]["character_field_repair"] is enabled
    assert continued["result"]["continued_from"] == original["id"]
    assert domain.task(continued["id"])["payload"]["character_field_repair"] is enabled
    assert domain.task(original["id"])["payload"]["character_field_repair"] is enabled
    assert client.post(f"/api/tasks/{original['id']}/continue", json=request).json()["id"] == continued["id"]
