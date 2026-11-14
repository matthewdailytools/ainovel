import pytest
from pydantic import ValidationError

from ainovel.candidate_recheck import CandidateRecheckInput
from ainovel.domain import same_task_payload
from ainovel.models import ModuleInput, WorkflowInput
from ainovel.plot_identity_context import VERSION
from ainovel.reference import ReferenceAction
from test_candidate_recheck import queue_recheck
from test_workflow_scope import workflow_payload
from test_candidate_recheck import stopped_candidate as stopped_candidate
from test_assisted_candidate_recheck import assisted_candidate as assisted_candidate


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_source_context_is_explicit_frozen_and_not_added_to_old_tasks(domain, story, kind):
    if kind == "module":
        request = ModuleInput(
            novel_id=story["id"],
            branch_id=story["branch_id"],
            chapter_id=story["chapter_id"],
            base_revision=0,
            action="writing.expand_plot",
            instruction="向渡口修船匠借尺。",
            account="openai_1",
            plot_contract_check=True,
        ).model_dump()
    else:
        request = workflow_payload(story)
        request["plot_contract_check"] = True
    old = domain.queue(kind, request, story["branch_id"])
    assert "source_identity_context_version" not in old["result"]
    request["source_identity_context"] = True
    new = domain.queue(kind, request, story["branch_id"])
    assert new["result"]["source_identity_context_version"] == VERSION
    assert domain.task(old["id"]) == old
    assert not same_task_payload(old["payload"], new["payload"])
    assert same_task_payload({"action": "example"}, {"action": "example", "source_identity_context": False})


@pytest.mark.parametrize("fixture", ["stopped_candidate", "assisted_candidate"])
def test_recheck_can_request_source_context_without_upgrading_parent(client, domain, request, fixture):
    parent, candidate, *_ = request.getfixturevalue(fixture)
    queued = queue_recheck(client, candidate, source_identity_context=True)
    assert queued.status_code == 202, queued.text
    task = queued.json()
    assert task["result"]["source_identity_context_version"] == VERSION
    assert task["result"]["candidate_recheck_snapshot"]["source_identity_context_version"] == VERSION
    assert domain.task(parent["id"]) == parent
    conflict = queue_recheck(client, candidate, source_identity_context=False)
    assert conflict.status_code == 409


@pytest.mark.parametrize("value", [1, "true", None])
def test_recheck_source_context_boolean_is_strict(value):
    with pytest.raises(ValidationError):
        CandidateRecheckInput(
            candidate_revision=1,
            account="openai_1",
            client_request_id="valid-nonce-123",
            source_identity_context=value,
        )


def test_source_context_requires_supported_plot_action(story):
    module = dict(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        action="writing.expand_plot",
        instruction="借尺",
        account="openai_1",
        source_identity_context=True,
    )
    with pytest.raises(ValidationError):
        ModuleInput(**module)
    workflow = workflow_payload(story)
    workflow["source_identity_context"] = True
    with pytest.raises(ValidationError):
        WorkflowInput(**workflow)
    for action in ("analyze", "plan", "draft"):
        with pytest.raises(ValidationError):
            ReferenceAction(
                action=action,
                base_revision=0,
                account="openai_1",
                client_request_id="reference-source-identity",
                source_identity_context=True,
                plot_contract_check=action != "draft",
            )
