"""Anonymous source semantics/budget/replay boundaries for scope-only receipts."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel import prepared_source_scope_correction as correction
from ainovel import workflow_plan_recipient_scope as scope
from ainovel.budget import Budget
from ainovel.candidate_recheck import sha
from ainovel.db import DomainError, dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.prepared_scope_fixture import old_contract, SOURCE, inventory_output, preservation_output
from test_openai_schema import completed
from test_prepared_source_scope_correction import submit, execute, SourceModel
from helpers.prepared_scope_fixture import stopped


@pytest.fixture
def old_auto(settings, domain, story):
    return stopped(settings, domain, story)


def mapping_snapshot(text=SOURCE):
    return {
        "plot": {"plot": text},
        "prepared_contract": old_contract(text),
        "item_ids": ["P01"],
        "fixed": ["原件不得带走。"],
    }


def source_receipt(snapshot, *, kind="named_destination", mutate=None):
    text = snapshot["plot"]["plot"]
    units = scope.units(text)
    i = {"input_sha256": sha({"version": correction.VERSION, "source": text}), "可引用原文": units}
    value = inventory_output(i, kind=kind)
    if mutate:
        mutate(value)
    return {
        "version": correction.VERSION,
        "input_sha256": i["input_sha256"],
        "sources": units,
        "inventory": value,
        "status": "supported" if scope.inventory_supported(value) else "needs_review",
    }


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("named_destination", "unspecified"),
        ("recipient_exclusivity", "only"),
        ("explicitly_unrestricted", "unrestricted"),
    ],
)
def test_server_proposal_only_maps_scope_fields_not_direction_or_stage(kind, expected):
    text = (
        "林舟只把测绘记录抄本寄给青石测量所，不得转交其他人。" if kind == "recipient_exclusivity" else SOURCE
    )
    snapshot = mapping_snapshot(text)
    source = source_receipt(snapshot, kind=kind)
    proposal, contract = correction.propose(snapshot, source)
    assert (
        proposal["items"][0]["status"] == "supported" and contract["items"][0]["recipient_scope"] == expected
    )
    assert all(
        contract["items"][0][key] == value
        for key, value in snapshot["prepared_contract"]["items"][0].items()
        if key not in correction.FIELDS
    )


def test_true_conditional_only_keeps_continuous_condition_and_original_copy():
    snapshot = mapping_snapshot("天亮后林舟只把测绘记录抄本寄给青石测量所，不得转交其他人；原件留在桌上。")

    def change(v):
        v["observations"][0]["condition"] = "天亮后"

    source = source_receipt(snapshot, kind="recipient_exclusivity", mutate=change)
    proposal, contract = correction.propose(snapshot, source)
    assert (
        proposal["items"][0]["status"] == "supported" and contract["items"][0]["scope_condition"] == "天亮后"
    )
    assert contract["items"][0]["transfer_object"] == "测绘记录抄本"


@pytest.mark.parametrize(
    "field,value",
    [("supplier", "师傅"), ("recipient", "顾平"), ("transfer_object", "原件"), ("transfer_scope", "planned")],
)
def test_wrong_transfer_or_stage_cannot_clear_only(field, value):
    snapshot = mapping_snapshot()
    source = source_receipt(snapshot, mutate=lambda v: v["observations"][0].update({field: value}))
    proposal, contract = correction.propose(snapshot, source)
    assert proposal["items"][0]["status"] == "unknown" and contract == snapshot["prepared_contract"]


def test_separate_permission_or_single_person_ban_not_global_delivery_only():
    snapshot = mapping_snapshot()
    source = source_receipt(snapshot)
    for kind, stage, supplier, recipient, obj, evidence in [
        ("action_permission", "none", "", "", "", "SCOPE0002"),
        ("specific_recipient_exclusion", "planned", "林舟", "顾平", "此事", "SCOPE0003"),
    ]:
        v = source["inventory"]
        identifier = f"O{len(v['observations']) + 1:02}"
        v["observations"].append(
            {
                "observation_id": identifier,
                "kind": kind,
                "supplier": supplier,
                "recipient": recipient,
                "transfer_object": obj,
                "transfer_scope": stage,
                "evidence": evidence,
                "restriction_evidence": evidence,
                "condition": "",
                "reason": "许可/特定排除仅关联自身原句，不挪给已寄抄本。",
            }
        )
        unit = next(r for r in v["unit_checks"] if r["source_id"] == evidence)
        unit.update(status="covered", observation_ids=[identifier])
    proposal, contract = correction.propose(snapshot, source)
    assert proposal["items"][0]["observation_ids"] == ["O01"]
    assert contract["items"][0]["recipient_scope"] == "unspecified"


def test_ambiguous_source_never_deletes_old_scope_even_when_named_recipient_exists():
    snapshot = mapping_snapshot()
    source = source_receipt(snapshot)
    source["inventory"]["unit_checks"][-1].update(status="unknown", observation_ids=[])
    source["status"] = "needs_review"
    proposal, contract = correction.propose(snapshot, source)
    assert proposal["items"][0]["status"] == "unknown" and contract == snapshot["prepared_contract"]


def test_saved_preservation_rejects_wrong_id_quote_range_and_extra_fields():
    snapshot = mapping_snapshot()
    i = {
        "原全部P提案": [{"item_id": "P01"}],
        "可引用原文": [{"source_id": "AUTHOR0001"}],
        "原固定条件": [{"condition_id": "FIX001"}],
    }
    result = correction.resolve_preservation(snapshot, preservation_output(i))
    correction.verify_saved_preservation(snapshot, result)
    for mutate in [
        lambda x: x["item_checks"][0]["evidence_source"].update(start=2),
        lambda x: x["item_checks"][0].update(evidence="伪造"),
        lambda x: x["item_checks"].clear(),
        lambda x: x["fixed_checks"].clear(),
    ]:
        invalid = deepcopy(result)
        mutate(invalid)
        with pytest.raises((DomainError, ValueError, KeyError)):
            correction.verify_saved_preservation(snapshot, invalid)


@pytest.mark.parametrize("where", ["payload", "result", "snapshot", "event"])
def test_new_marker_deletion_cannot_downgrade_to_old_assisted_source(
    client, settings, domain, old_auto, where
):
    _, candidate = old_auto
    task = submit(client, candidate).json()
    with domain.db.connect(True) as tx:
        if where == "event":
            tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (task["id"], correction.EVENT))
        else:
            target = task[where] if where != "snapshot" else task["result"][correction.SNAPSHOT]
            target.pop(correction.KEY if where != "snapshot" else "version")
            field = "payload" if where == "payload" else "result"
            tx.execute(f"UPDATE tasks SET {field}=? WHERE id=?", (dumps(task[field]), task["id"]))
    model = SourceModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and model.frames == []


@pytest.mark.parametrize("stage", [1, 2])
def test_unknown_actual_wire_never_resends_after_explicit_resume(client, domain, settings, old_auto, stage):
    _, candidate = old_auto
    task = submit(client, candidate).json()
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        i = json.loads(calls[-1]["input"][-1]["content"].split("\n只返回")[0])["任务"]
        if len(calls) == stage:
            raise httpx.ReadTimeout("unknown remote outcome")
        return completed(dumps(inventory_output(i)))

    ledger = Budget(settings)
    model = ModelClient(settings, ledger, httpx.MockTransport(handler))
    asyncio.run(Worker(settings, model).run_once())
    assert len(calls) == stage
    before = ledger.report()
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert (
        client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    asyncio.run(Worker(settings, model).run_once())
    assert len(calls) == stage and ledger.report() == before
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


@pytest.mark.parametrize("change", ["candidate", "story", "receipt", "cross_branch"])
def test_stale_or_forged_source_receipt_does_not_authorize_body(
    client, domain, settings, story, old_auto, change
):
    _, candidate = old_auto
    receipt, _ = execute(settings, domain, submit(client, candidate).json())
    other = domain.create_novel("隔离另一作品", "")
    with domain.db.connect(True) as tx:
        if change == "candidate":
            tx.execute(
                "UPDATE artifacts SET payload=? WHERE id=?",
                (dumps({**candidate["payload"], "_candidate_revision": 1}), candidate["id"]),
            )
        elif change == "story":
            tx.execute(
                "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (story["branch_id"],)
            )
        elif change == "cross_branch":
            tx.execute("UPDATE artifacts SET branch_id=? WHERE id=?", (other["branch_id"], receipt["id"]))
        else:
            p = receipt["payload"]
            p["corrected_contract"]["items"][0]["action"] = "借此删除原寄出事件"
            tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(p), receipt["id"]))
    response = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": receipt["id"]},
    )
    assert response.status_code == 409, response.text


def test_new_scope_auxiliary_costs_share_parent_envelope_once(client, domain, settings, old_auto):
    # This original stopped family belongs to openai_1; both OpenAI keys share it.
    from ainovel.source_interpretation_correction import bounded_run_limit

    parent, candidate = old_auto
    first = submit(client, candidate).json()
    execute(settings, domain, first)
    second = submit(client, candidate, client_request_id="second-source-fee-123", budget_limit=10).json()
    worker = Worker(settings, SourceModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    ledger = worker.budget
    provider = settings.providers["openai_1"]
    parent_call = ledger.reserve(provider, parent["id"], 1, 1)
    ledger.settle(parent_call, {"input": 10, "output": 10})
    first_call = ledger.reserve(provider, first["id"], 1, 1)
    ledger.settle(first_call, {"input": 10, "output": 10})
    current_call = ledger.reserve(provider, second["id"], 1, 1)
    ledger.settle(current_call, {"input": 1, "output": 1})
    before = ledger.report()
    assert bounded_run_limit(worker) == pytest.approx(5 - 0.0002)
    assert ledger.report() == before
    hold = ledger.reserve(provider, first["id"], 1, 1)
    with pytest.raises(DomainError, match="未决费用"):
        bounded_run_limit(worker)
    assert hold and ledger.report() != before


@pytest.mark.parametrize("nonce", ["edge/nonce-123", "中文?号#nonce", "opaque\nnonce-123"])
def test_both_new_scope_and_body_lookup_preserve_all_accepted_nonce_characters(
    client, domain, settings, old_auto, nonce
):
    _, candidate = old_auto
    task = submit(client, candidate, client_request_id=nonce).json()
    receipt, _ = execute(settings, domain, task)
    lookup = client.get(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-corrections",
        params={"client_request_id": nonce},
    )
    assert lookup.status_code == 200 and lookup.json()["task"]["id"] == task["id"]
    preview = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": receipt["id"]},
    ).json()
    body = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "receipt_id": receipt["id"],
            "preview_basis_sha256": preview["basis_sha256"],
            "account": "openai_1",
            "client_request_id": nonce + "-body",
        },
    )
    assert body.status_code == 202, body.text
    queried = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        params={"client_request_id": nonce + "-body"},
    )
    assert queried.status_code == 200 and queried.json()["id"] == body.json()["id"]
    same = client.post(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-corrections",
        json={k: v for k, v in task["payload"].items() if k in correction.ScopeCorrectionInput.model_fields},
    )
    assert same.status_code == 202 and same.json()["id"] == task["id"]


def test_resolved_source_quotes_use_codepoints_but_utf16_ranges():
    snapshot = mapping_snapshot("😀𠮷" + "原" * 798 + "。")
    value = preservation_output(
        {
            "原全部P提案": [{"item_id": "P01"}],
            "可引用原文": [{"source_id": "AUTHOR0001"}],
            "原固定条件": [{"condition_id": "FIX001"}],
        }
    )
    resolved = correction.resolve_preservation(snapshot, value)
    quote = resolved["item_checks"][0]["evidence"]
    assert len(quote) == 800 and resolved["item_checks"][0]["evidence_source"]["end"] == 802
    correction.verify_saved_preservation(snapshot, resolved)


@pytest.mark.parametrize(
    "required_stage,text",
    [
        ("completed", "林舟本章把测绘记录抄本寄给青石测量所，回执尚未到。"),
        ("planned", "林舟本章决定次日再把测绘记录抄本寄给青石测量所。"),
    ],
)
def test_ai_planning_identity_does_not_replace_required_story_action_stage(required_stage, text):
    snapshot = mapping_snapshot(text)
    snapshot["prepared_contract"]["items"][0]["transfer_scope"] = required_stage
    source = source_receipt(
        snapshot, mutate=lambda v: v["observations"][0].update(transfer_scope=required_stage)
    )
    proposal, core = correction.propose(snapshot, source)
    assert (
        proposal["items"][0]["status"] == "supported" and core["items"][0]["transfer_scope"] == required_stage
    )
    opposite = "planned" if required_stage == "completed" else "completed"
    source = source_receipt(snapshot, mutate=lambda v: v["observations"][0].update(transfer_scope=opposite))
    proposal, core = correction.propose(snapshot, source)
    assert proposal["items"][0]["status"] == "unknown" and core == snapshot["prepared_contract"]
