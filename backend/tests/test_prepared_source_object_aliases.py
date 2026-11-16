"""Opt-in object naming links preserve the blind source and every archived field."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel import prepared_source_object_aliases as aliases
from ainovel import prepared_source_scope_correction as old
from ainovel.budget import Budget
from ainovel.db import DomainError, dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.prepared_scope_fixture import stopped, inventory_output, preservation_output, BODY
from test_candidate_recheck import assert_parent_not_resumed
from test_openai_schema import completed
from test_prepared_scope_boundaries import mapping_snapshot, source_receipt
from test_prepared_source_scope_correction import SourceModel, BodyModel, execute, submit as old_submit

ALIAS = "这次所抄测绘记录的副本"


@pytest.fixture
def old_auto(settings, domain, story):
    return stopped(settings, domain, story)


def submit(client, candidate, **changes):
    preview = client.get(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-preview",
        params={"verify_object_aliases": True},
    )
    assert preview.status_code == 200, preview.text
    return client.post(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-corrections",
        json={
            "candidate_revision": candidate["candidate_revision"],
            "preview_basis_sha256": preview.json()["basis_sha256"],
            "account": "openai_1",
            "client_request_id": "explicit-object-alias-request-123",
            "item_ids": ["P01"],
            "reason": "AUDIT_FREE_REASON_SENTINEL",
            "budget_limit": 1,
            "verify_object_aliases": True,
            **changes,
        },
    )


def association_output(i, *, relation="same_transfer_same_instance"):
    inventory = i["独立冻结原文观察（AI，非真值）"]
    by_id = {r["observation_id"]: r for r in inventory["inventory"]["observations"]}
    return {
        "input_sha256": i["input_sha256"],
        "items": [
            {
                "item_id": item["item_id"],
                "checks": [
                    {
                        "observation_id": identifier,
                        "relation": relation,
                        "evidence": by_id[identifier]["evidence"] if relation != "unknown" else "",
                        "reason": "原创工程对照：逐项核同次实例，非真实模型语义保证。",
                    }
                    for identifier in item["须逐一核对的原观察"]
                ],
                "reason": "全部备选均已逐项核，唯一性另由服务端判断。",
            }
            for item in i["所选旧P身份提案"]
        ],
        "note": "离线关联替身，不成为事实或作者认可。",
    }


class AliasModel(SourceModel):
    def __init__(
        self,
        *,
        relation="same_transfer_same_instance",
        final_alias="pass",
        exact=False,
        verdict="pass",
        sufficient="yes",
    ):
        super().__init__()
        self.relation, self.final_alias, self.exact = relation, final_alias, exact
        self.verdict, self.sufficient = verdict, sufficient

    def output(self, i):
        if i["动作"] == old.OBSERVE:
            value = inventory_output(i)
            if not self.exact:
                value["observations"][0]["transfer_object"] = ALIAS
            return value
        if i["动作"] == aliases.ASSOCIATE:
            return association_output(i, relation=self.relation)
        assert i["动作"] == aliases.VERIFY
        value = preservation_output(i, verdict=self.verdict, sufficient=self.sufficient)
        required = i["物品称谓关联（AI，不是真值）"]["required_item_ids"]
        for row in value["item_checks"]:
            row["object_alias_check"] = self.final_alias if row["item_id"] in required else "not_needed"
        return value

    async def generate(self, account, task_id, system, prompt, max_output, **kw):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.frames.append(frame)
        return {
            "text": dumps(self.output(frame["任务"])),
            "usage": {"input": 1, "output": 1},
            "call_id": f"object-alias-{len(self.frames)}",
        }


def mapping(*, mutate=None):
    snapshot = mapping_snapshot()
    snapshot.update(
        version=aliases.VERSION,
        verify_object_aliases=True,
        source_text_sha256=old.sha(snapshot["plot"]["plot"]),
    )
    inventory = source_receipt(snapshot, mutate=lambda v: v["observations"][0].update(transfer_object=ALIAS))
    if mutate:
        mutate(inventory["inventory"])
    return snapshot, inventory


def output_for(snapshot, inventory, relations=None):
    selected = []
    for n, item in enumerate(snapshot["prepared_contract"]["items"], 1):
        identifier = f"P{n:02}"
        if identifier in aliases.requirements(snapshot, inventory):
            selected.append(
                {
                    "item_id": identifier,
                    "须逐一核对的原观察": [r["observation_id"] for r in aliases.candidates(item, inventory)],
                }
            )
    value = association_output(
        {
            "input_sha256": aliases.association_token(snapshot, inventory),
            "独立冻结原文观察（AI，非真值）": inventory,
            "所选旧P身份提案": selected,
        }
    )
    if relations:
        for row in value["items"]:
            for check, relation in zip(row["checks"], relations, strict=True):
                check["relation"] = relation
                if relation == "unknown":
                    check["evidence"] = ""
    return value


def test_opt_in_three_source_steps_keep_raw_inventory_original_objects_and_history(
    client, domain, settings, old_auto
):
    parent, candidate = old_auto
    task_response = submit(client, candidate)
    assert task_response.status_code == 202, task_response.text
    task = task_response.json()
    assert task["payload"]["verify_object_aliases"] is True
    assert task["result"][old.KEY] == aliases.VERSION
    receipt, model = execute(settings, domain, task, AliasModel())
    assert [f["任务"]["动作"] for f in model.frames] == [old.OBSERVE, aliases.ASSOCIATE, aliases.VERIFY]
    value = receipt["payload"]
    assert value["version"] == aliases.VERSION and value["state"] == "ready"
    assert value["inventory"]["version"] == old.VERSION
    assert value["inventory"]["inventory"]["observations"][0]["transfer_object"] == ALIAS
    original = parent["result"]["plot_preparations"]["1"]["prepared_contract"]
    assert (
        value["corrected_contract"]["items"][0]["transfer_object"] == original["items"][0]["transfer_object"]
    )
    assert value["corrected_contract"]["items"][0]["recipient_scope"] == "unspecified"
    assert value["corrected_contract"]["note"] == original["note"]
    link = value["object_alias_resolution"]
    assert link["state"] == "supported" and link["items"][0]["observation_id"] == "O01"
    assert link["items"][0]["checks"][0]["evidence_source"]["source_id"] == "SCOPE0001"
    assert value["preservation"]["item_checks"][0]["object_alias_check"] == "pass"
    assert domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)
    raw, associate = (dumps(f) for f in model.frames[:2])
    for forbidden in (
        BODY,
        "OLD_BODY_CHECK_SENTINEL",
        "OLD_REVIEW_SENTINEL",
        "AUDIT_FREE_REASON_SENTINEL",
        "FUTURE_PLAN_SENTINEL",
    ):
        assert forbidden not in raw and forbidden not in associate
    assert "所选旧P身份提案" not in raw and "原全部P提案" not in raw
    assert "recipient_scope" not in associate and "scope_condition" not in associate


def test_true_preview_basis_and_body_version_are_explicit(client, domain, settings, old_auto):
    _, candidate = old_auto
    url = f"/api/artifacts/{candidate['id']}/prepared-source-scope-preview"
    legacy = client.get(url).json()
    disabled = client.get(url, params={"verify_object_aliases": False}).json()
    new = client.get(url, params={"verify_object_aliases": True}).json()
    assert legacy == disabled and "verify_object_aliases" not in legacy
    assert new["version"] == aliases.VERSION and new["basis_sha256"] != legacy["basis_sha256"]
    wrong = submit(client, candidate, preview_basis_sha256=legacy["basis_sha256"])
    assert wrong.status_code == 409
    receipt, _ = execute(settings, domain, submit(client, candidate).json(), AliasModel())
    body = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": receipt["id"]},
    )
    assert body.status_code == 200, body.text
    assert body.json()["version"] == aliases.RECHECK_VERSION and body.json()["verify_object_aliases"] is True


def test_old_nonce_and_false_payload_and_paid_observation_frame_stay_v1(client, domain, settings, old_auto):
    _, candidate = old_auto
    legacy = old_submit(client, candidate).json()
    assert "verify_object_aliases" not in legacy["payload"]
    replay = old_submit(client, candidate, verify_object_aliases=False)
    assert replay.status_code == 202 and replay.json()["id"] == legacy["id"]
    first_receipt, first = execute(settings, domain, legacy, AliasModel())
    assert (
        first_receipt["payload"]["version"] == old.VERSION
        and first_receipt["payload"]["state"] == "unverified"
    )
    changed = submit(client, candidate, client_request_id=legacy["payload"]["client_request_id"])
    assert changed.status_code == 409
    second_receipt, second = execute(settings, domain, submit(client, candidate).json(), AliasModel())
    assert second_receipt["payload"]["state"] == "ready"
    assert first.frames[0] == second.frames[0]
    queried = client.get(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-corrections",
        params={"client_request_id": legacy["payload"]["client_request_id"]},
    )
    assert queried.status_code == 200 and queried.json()["task"]["id"] == legacy["id"]


def test_exact_unambiguous_object_needs_no_extra_link_call(client, domain, settings, old_auto):
    _, candidate = old_auto
    receipt, model = execute(settings, domain, submit(client, candidate).json(), AliasModel(exact=True))
    assert len(model.frames) == 2 and receipt["payload"]["state"] == "ready"
    assert receipt["payload"]["object_alias_resolution"]["state"] == "not_needed"
    assert receipt["payload"]["object_alias_resolution"]["items"] == []


@pytest.mark.parametrize("relation", ["unknown", "different_transfer_or_instance"])
def test_alias_unknown_or_different_instance_stays_unverified_without_body(
    client, domain, settings, old_auto, relation
):
    _, candidate = old_auto
    receipt, model = execute(
        settings, domain, submit(client, candidate).json(), AliasModel(relation=relation)
    )
    assert len(model.frames) == 2 and receipt["payload"]["state"] == "unverified"
    assert receipt["payload"]["preservation"] is None
    assert domain.artifact(candidate["id"]) == candidate
    assert (
        client.get(
            f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
            params={"receipt_id": receipt["id"]},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("flag", ["fail", "unknown", "not_needed"])
def test_independent_preservation_must_confirm_required_alias_again(client, domain, settings, old_auto, flag):
    _, candidate = old_auto
    receipt, model = execute(settings, domain, submit(client, candidate).json(), AliasModel(final_alias=flag))
    assert len(model.frames) == 3 and receipt["payload"]["state"] == "unverified"
    assert domain.artifact(candidate["id"]) == candidate


@pytest.mark.parametrize(
    "field,value", [("supplier", "师傅"), ("recipient", "顾平"), ("transfer_scope", "planned")]
)
def test_alias_never_repairs_other_roles_or_transfer_stage(field, value):
    snapshot, inventory = mapping(mutate=lambda v: v["observations"][0].update({field: value}))
    assert aliases.requirements(snapshot, inventory) == []
    proposal, core = aliases.propose(snapshot, inventory, aliases.not_needed(snapshot, inventory))
    assert proposal["items"][0]["status"] == "unknown" and core == snapshot["prepared_contract"]


def two_observations():
    snapshot, inventory = mapping()
    other = deepcopy(inventory["inventory"]["observations"][0])
    other.update(observation_id="O02", transfer_object="另一次交接的第二份抄本")
    inventory["inventory"]["observations"].append(other)
    inventory["inventory"]["unit_checks"][0]["observation_ids"].append("O02")
    return snapshot, inventory


@pytest.mark.parametrize(
    "relations",
    [
        ["same_transfer_same_instance", "same_transfer_same_instance"],
        ["same_transfer_same_instance", "unknown"],
    ],
)
def test_two_copies_or_unexcluded_second_transfer_cannot_be_merged(relations):
    snapshot, inventory = two_observations()
    resolution = aliases.resolve_association(snapshot, inventory, output_for(snapshot, inventory, relations))
    assert resolution["state"] == "unverified"
    proposal, core = aliases.propose(snapshot, inventory, resolution)
    assert proposal["items"][0]["status"] == "unknown" and core == snapshot["prepared_contract"]


def test_missing_alternative_or_duplicate_coverage_is_rejected():
    snapshot, inventory = two_observations()
    value = output_for(snapshot, inventory)
    value["items"][0]["checks"].pop()
    assert aliases.association_issues(value, snapshot, inventory)
    with pytest.raises(DomainError):
        aliases.resolve_association(snapshot, inventory, value)
    value = output_for(snapshot, inventory)
    value["items"][0]["checks"][1]["observation_id"] = "O01"
    assert aliases.association_issues(value, snapshot, inventory)


def test_unique_same_instance_requires_evidence_for_excluding_other_copy():
    snapshot, inventory = two_observations()
    value = output_for(snapshot, inventory, ["same_transfer_same_instance", "different_transfer_or_instance"])
    resolution = aliases.resolve_association(snapshot, inventory, value)
    assert resolution["state"] == "supported"
    value["items"][0]["checks"][1]["evidence"] = ""
    assert aliases.association_issues(value, snapshot, inventory)


@pytest.mark.parametrize("kind", ["action_permission", "specific_recipient_exclusion", "unknown"])
def test_permission_or_single_person_exclusion_is_never_a_same_delivery_scope(kind):
    snapshot, inventory = mapping(
        mutate=lambda v: v["observations"][0].update(kind=kind, restriction_evidence="SCOPE0001")
    )
    inventory["status"] = "needs_review" if kind == "unknown" else "supported"
    resolution = aliases.not_needed(snapshot, inventory)
    proposal, core = aliases.propose(snapshot, inventory, resolution)
    assert proposal["items"][0]["status"] == "unknown" and core == snapshot["prepared_contract"]


def test_real_only_and_condition_are_not_cleared_by_object_naming_link():
    snapshot, inventory = mapping(
        mutate=lambda v: v["observations"][0].update(
            kind="recipient_exclusivity", restriction_evidence="SCOPE0001"
        )
    )
    resolution = aliases.resolve_association(snapshot, inventory, output_for(snapshot, inventory))
    proposal, core = aliases.propose(snapshot, inventory, resolution)
    assert proposal["items"][0]["status"] == "supported" and core["items"][0]["recipient_scope"] == "only"
    assert core["items"][0]["transfer_object"] == snapshot["prepared_contract"]["items"][0]["transfer_object"]


def test_saved_source_evidence_and_selected_observation_cannot_be_tampered():
    snapshot, inventory = mapping()
    saved = aliases.resolve_association(snapshot, inventory, output_for(snapshot, inventory))
    for mutate in [
        lambda r: r["items"][0]["checks"][0]["evidence_source"].update(start=1),
        lambda r: r["items"][0].update(observation_id="O99"),
        lambda r: r.update(input_sha256="0" * 64),
    ]:
        changed = deepcopy(saved)
        mutate(changed)
        with pytest.raises((DomainError, ValueError, KeyError)):
            aliases.verify_saved_association(snapshot, inventory, changed)


def test_explicit_v2_body_keeps_flow2_unknown_and_never_adopts(client, domain, settings, old_auto):
    parent, candidate = old_auto
    receipt, _ = execute(settings, domain, submit(client, candidate).json(), AliasModel())
    preview = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": receipt["id"]},
    ).json()
    task = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "receipt_id": receipt["id"],
            "preview_basis_sha256": preview["basis_sha256"],
            "account": "openai_1",
            "client_request_id": "body-after-object-alias-123",
            "budget_limit": 1,
        },
    ).json()
    assert task["result"]["source_corrected_recheck_version"] == aliases.RECHECK_VERSION
    report, model = execute(settings, domain, task, BodyModel())
    assert len(model.frames) == 4 and report["payload"]["version"] == aliases.RECHECK_VERSION
    assert report["payload"]["review_passed"] is False
    assert (
        report["payload"]["plot_audit"]["source_scope_correction"]["object_alias_resolution"]["state"]
        == "supported"
    )
    assert domain.artifact(candidate["id"])["payload"]["text"] == candidate["payload"]["text"]
    assert domain.chapter(candidate["chapter_id"])["content"] == ""
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize("stage", [2, 3])
def test_alias_or_final_preservation_unknown_fee_is_not_resent_after_resume(
    client, domain, settings, old_auto, stage
):
    _, candidate = old_auto
    task = submit(client, candidate).json()
    model = AliasModel()
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        i = json.loads(calls[-1]["input"][-1]["content"].split("\n只返回")[0])["任务"]
        if len(calls) == stage:
            raise httpx.ReadTimeout("remote result unknown")
        return completed(dumps(model.output(i)))

    budget = Budget(settings)
    wire = ModelClient(settings, budget, httpx.MockTransport(handler))
    asyncio.run(Worker(settings, wire).run_once())
    assert len(calls) == stage and domain.task(task["id"])["status"] == "blocked_runtime"
    before = budget.report()
    assert (
        client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    asyncio.run(Worker(settings, wire).run_once())
    assert len(calls) == stage and budget.report() == before
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


@pytest.mark.parametrize("where", ["payload", "result", "snapshot", "event"])
def test_v2_marker_or_explicit_flag_cannot_be_downgraded_before_dispatch(
    client, settings, domain, old_auto, where
):
    _, candidate = old_auto
    task = submit(client, candidate).json()
    with domain.db.connect(True) as tx:
        if where == "event":
            tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (task["id"], old.EVENT))
        else:
            value = task[where] if where != "snapshot" else task["result"][old.SNAPSHOT]
            value.pop(old.KEY if where != "snapshot" else "verify_object_aliases")
            field = "payload" if where == "payload" else "result"
            tx.execute(f"UPDATE tasks SET {field}=? WHERE id=?", (dumps(task[field]), task["id"]))
    model = AliasModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and model.frames == []


@pytest.mark.parametrize("operation", ["edit", "accept", "reject"])
def test_v2_receipts_remain_readonly(client, settings, domain, old_auto, operation):
    _, candidate = old_auto
    receipt, _ = execute(settings, domain, submit(client, candidate).json(), AliasModel())
    if operation == "edit":
        response = client.patch(
            f"/api/artifacts/{receipt['id']}", json={"candidate_revision": 0, "text": "强造就绪"}
        )
    else:
        response = client.post(
            f"/api/artifacts/{receipt['id']}/{operation}",
            json={
                "candidate_revision": 0,
                **({"idempotency_key": "v2-receipt-readonly-123"} if operation == "accept" else {}),
            },
        )
    assert response.status_code == 409, response.text
    assert domain.artifact(receipt["id"]) == receipt


@pytest.mark.parametrize("sufficient", ["no", "unknown"])
def test_object_link_does_not_clear_insufficient_action_or_historical_note(
    client, settings, domain, old_auto, sufficient
):
    parent, candidate = old_auto
    receipt, model = execute(
        settings, domain, submit(client, candidate).json(), AliasModel(sufficient=sufficient)
    )
    assert len(model.frames) == 3 and receipt["payload"]["state"] == "unverified"
    assert (
        receipt["payload"]["corrected_contract"]["note"]
        == parent["result"]["plot_preparations"]["1"]["prepared_contract"]["note"]
    )
    assert domain.artifact(candidate["id"]) == candidate


def test_alias_evidence_keeps_utf16_positions_for_supplementary_characters():
    from helpers.prepared_scope_fixture import SOURCE
    from ainovel import workflow_plan_recipient_scope as scope

    text = "😀𠮷" + SOURCE
    snapshot = mapping_snapshot(text)
    snapshot.update(version=aliases.VERSION, verify_object_aliases=True, source_text_sha256=old.sha(text))
    inventory = source_receipt(snapshot, mutate=lambda v: v["observations"][0].update(transfer_object=ALIAS))
    resolved = aliases.resolve_association(snapshot, inventory, output_for(snapshot, inventory))
    evidence = resolved["items"][0]["checks"][0]["evidence_source"]
    assert evidence == scope.units(text)[0]
    assert evidence["end"] == len(evidence["text"].encode("utf-16-le")) // 2
    assert evidence["end"] == len(evidence["text"]) + 2


def test_editing_candidate_during_alias_step_prevents_publication(client, settings, domain, old_auto):
    _, candidate = old_auto
    task = submit(client, candidate).json()

    class ConcurrentEdit(AliasModel):
        def output(self, i):
            if i["动作"] == aliases.ASSOCIATE:
                changed = deepcopy(candidate["payload"])
                changed.update(text="作者本窗口的新稿", _candidate_revision=1)
                with domain.db.connect(True) as tx:
                    tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(changed), candidate["id"]))
            return super().output(i)

    model = ConcurrentEdit()
    asyncio.run(Worker(settings, model).run_once())
    assert len(model.frames) == 2 and domain.task(task["id"])["status"] == "blocked_runtime"
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert domain.artifact(candidate["id"])["payload"]["text"] == "作者本窗口的新稿"


def test_invalid_frozen_blind_inventory_is_refused_before_an_alias_call():
    snapshot, inventory = mapping()
    inventory["input_sha256"] = "0" * 64

    class NoDispatch:
        async def grounded(self, *args, **kwargs):
            raise AssertionError("Invalid frozen source must never incur another call")

    with pytest.raises(DomainError, match="未派发关联"):
        asyncio.run(aliases.associate(NoDispatch(), snapshot, inventory))


def test_alias_citation_repair_stays_source_only_and_preserves_all_candidates(
    client, settings, domain, old_auto
):
    _, candidate = old_auto
    task = submit(client, candidate).json()

    class CitationRepair(AliasModel):
        alias_calls = 0

        def output(self, i):
            value = super().output(i)
            if i["动作"] == aliases.ASSOCIATE:
                self.alias_calls += 1
                if self.alias_calls == 1:
                    value["items"][0]["checks"][0]["observation_id"] = "O99"
                else:
                    assert "冻结AI章纲" in i["引用修正要求"]
                    assert "回到本次正文" not in i["引用修正要求"]
                    assert BODY not in dumps(i)
                    assert i["所选旧P身份提案"][0]["须逐一核对的原观察"] == ["O01"]
            return value

    receipt, model = execute(settings, domain, task, CitationRepair())
    assert model.alias_calls == 2 and len(model.frames) == 4
    assert receipt["payload"]["state"] == "ready"
    assert domain.artifact(candidate["id"]) == candidate
