"""Source-only numbered evidence is new policy; paid old policies stay frozen."""

import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from ainovel import source_interpretation_correction as correction
from ainovel.budget import Budget
from ainovel.db import DomainError, dumps
from ainovel.evidence import source_units
from ainovel.providers import ModelClient, strict_schema
from ainovel.worker import Worker
from test_budget_provider import stream
from test_source_interpretation_correction import (
    SourceModel,
    recheck_basis,
    stopped_source as stopped_source,
    submit,
)


def retag(domain, task, version):
    """Build an explicit historical fixture, never upgrade a production task."""
    payload, result = deepcopy(task["payload"]), deepcopy(task["result"])
    payload.pop(correction.SHAPE_KEY, None)
    result.pop(correction.SHAPE_KEY, None)
    if version is not None:
        payload[correction.SHAPE_KEY] = version
        result[correction.SHAPE_KEY] = version
    result["source_correction_snapshot"] = correction.freeze(domain, payload)
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?", (dumps(payload), dumps(result), task["id"])
        )
        tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (task["id"], correction.SHAPE_EVENT))
        if version is not None:
            domain.db.event(
                tx,
                task["id"],
                correction.SHAPE_EVENT,
                {
                    correction.SHAPE_KEY: version,
                    "payload_sha256": correction.sha(payload),
                    "snapshot_sha256": correction.sha(result["source_correction_snapshot"]),
                },
            )
    return domain.task(task["id"])


GOLDEN = {
    "legacy": [
        (
            "da2f87cb892ff444915fc596568de1835814baec321843522a7cfb2e3061e725",
            "fe471147b7ada8d9527290acc486ff7ba07b8a759cb535eafc9e185c809f1f89",
        ),
        (
            "5cab79b0085d6f9c6d9a143f6bddc5294c9cc8eb19a9f6e0d06244c634e42c4d",
            "bd118c0bfa68cfea1923d9e94bf47b63186aa05ddfac129a3bb0985aad1a330b",
        ),
    ],
    "shape1": [
        (
            "ba516da7ae5faf5d8d536368bcaefda57ec2ad74697f3759a4d830b269f29819",
            "b0e9d2d1762dab4284bf2a3fea4791da945d400102c1b2cd811cb0d58b3eea18",
        ),
        (
            "0e7138d2a1b617c1fb466c7b5ddfc1d98d4404a43bbc9a1509a5b73503f765c9",
            "8be946ad78d7a4cb3093ad19ebcd0675debcc7572c4d1b9af2c26019cf438f61",
        ),
    ],
}


@pytest.mark.parametrize("version", [None, correction.SHAPE_V1])
def test_both_old_frames_and_schemas_keep_exact_published_bytes(client, domain, stopped_source, version):
    _, candidate = stopped_source
    task = retag(domain, submit(client, candidate).json(), version)
    snapshot = task["result"]["source_correction_snapshot"]
    first = correction.source_frame(snapshot)
    raw = asyncio.run(
        SourceModel().generate(
            "openai_1", "toy", "", dumps({"任务": first, "上下文": {"scope": "source"}}), 8192
        )
    )
    proposal, _ = correction.resolve_proposal(json.loads(raw["text"]), snapshot)
    for index, value in enumerate((None, proposal)):
        frame = correction.source_frame(snapshot, proposal=value)
        schema = correction.output_contract(snapshot, preservation=value is not None)
        schema = (
            schema
            or (correction.Preservation if value is not None else correction.Proposal).model_json_schema()
        )
        actual = tuple(hashlib.sha256(dumps(item).encode()).hexdigest() for item in (frame, schema))
        assert actual == GOLDEN["legacy" if version is None else "shape1"][index]


def test_shape2_contracts_are_exact_citation_ids_and_source_only(client, domain, stopped_source):
    _, candidate = stopped_source
    task = submit(client, candidate).json()
    snapshot = task["result"]["source_correction_snapshot"]
    assert {row[correction.SHAPE_KEY] for row in (task["payload"], task["result"], snapshot)} == {
        correction.SHAPE_VERSION
    }
    authors = [unit["source_id"] for unit in source_units(snapshot["plot"]["plot"], "AUTHOR")]
    fixed = [f"FIX{i:03}" for i in range(1, len(snapshot["fixed"]) + 1)]
    for preservation in (False, True):
        schema = correction.output_contract(snapshot, preservation=preservation)
        properties = schema["$defs"]["ItemCheck" if preservation else "ProposedItem"]["properties"]
        assert properties["evidence"]["enum"] == authors
        assert "不是原句" in properties["evidence"]["description"]
        if preservation:
            assert schema["$defs"]["FixedCheck"]["properties"]["evidence"]["enum"] == fixed
        strict_schema(schema)
    frame = correction.source_frame(snapshot)
    assert frame["可为文本交代要求P"] == ["P05"]
    assert "分类不等于已发生" in frame["事项性质与主体要求"]
    assert "阶段、结果和顺序保持" in frame["事项性质与主体要求"]
    assert len(frame["未选择事项原样返回字段"]) == 6
    serialized = dumps(frame)
    for forbidden in (
        "CANDIDATE_SENTINEL",
        "OLD_REVIEW_SENTINEL",
        "REASON_SENTINEL",
        "FUTURE_PLAN_SENTINEL",
        "FUTURE_BRIEF_SENTINEL",
    ):
        assert forbidden not in serialized
    snapshot["fixed"] = []
    empty = correction.output_contract(snapshot, preservation=True)
    assert (
        empty["properties"]["fixed_checks"]["minItems"]
        == empty["properties"]["fixed_checks"]["maxItems"]
        == 0
    )
    assert "enum" not in empty["$defs"]["FixedCheck"]["properties"]["evidence"]
    strict_schema(empty)


@pytest.mark.parametrize("version", [correction.SHAPE_V1, correction.SHAPE_VERSION])
def test_another_valid_fixed_id_does_not_support_this_condition(client, domain, stopped_source, version):
    _, candidate = stopped_source
    task = retag(domain, submit(client, candidate).json(), version)
    snapshot = deepcopy(task["result"]["source_correction_snapshot"])
    snapshot["fixed"] = ["保留原定禁止项。", "保留未明原因。"]
    value = {
        "item_checks": [
            {
                "item_id": f"P{i:02}",
                "result": "unknown",
                "evidence": "AUTHOR0001",
                "reason": "本单测只核出处绑定。",
            }
            for i in range(1, 10)
        ],
        "fixed_checks": [
            {
                "condition_id": "FIX001",
                "result": "unknown",
                "evidence": "FIX002",
                "reason": "另一个合法编号不能支持本项。",
            },
            {"condition_id": "FIX002", "result": "unknown", "evidence": "FIX002", "reason": "保留原条件。"},
        ],
        "summary": "出处仍须逐项绑定，不按全局编号枚举制造通过。",
    }
    if version == correction.SHAPE_VERSION:
        schema = correction.output_contract(snapshot, preservation=True)
        assert schema["$defs"]["FixedCheck"]["properties"]["evidence"]["enum"] == ["FIX001", "FIX002"]
    with pytest.raises(DomainError, match="对应原编号"):
        correction.resolve_preservation(value, snapshot)


@pytest.mark.parametrize("version", [None, correction.SHAPE_V1, correction.SHAPE_VERSION])
def test_nonce_and_ready_receipt_keep_their_own_version(settings, domain, client, stopped_source, version):
    _, candidate = stopped_source
    original_basis = correction.preview(domain, candidate["id"])["basis_sha256"]
    task = retag(domain, submit(client, candidate).json(), version)
    assert submit(client, candidate).json() == task
    assert asyncio.run(Worker(settings, SourceModel()).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    receipt = domain.artifact(done["result"]["artifact_id"])
    assert receipt["payload"]["snapshot"].get(correction.SHAPE_KEY) == version
    assert receipt["payload"]["snapshot"]["basis_sha256"] == original_basis
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "receipt_id": receipt["id"],
            "preview_basis_sha256": recheck_basis(client, candidate, receipt["id"]),
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "body-uses-receipt-original-shape",
        },
    )
    assert response.status_code == 202, response.text
    assert domain.artifact(candidate["id"]) == candidate and domain.task(task["id"]) == done


@pytest.mark.parametrize(
    "place", ["payload", "result", "snapshot", "all_downgrade", "all_removed", "unknown"]
)
def test_shape2_cannot_mix_downgrade_or_remove_frozen_policy(settings, domain, client, stopped_source, place):
    _, candidate = stopped_source
    task = submit(client, candidate).json()
    payload, result = deepcopy(task["payload"]), deepcopy(task["result"])
    rows = {"payload": payload, "result": result, "snapshot": result["source_correction_snapshot"]}
    if place in {"all_downgrade", "all_removed", "unknown"}:
        for row in rows.values():
            if place == "all_removed":
                row.pop(correction.SHAPE_KEY)
            else:
                row[correction.SHAPE_KEY] = (
                    correction.SHAPE_V1 if place == "all_downgrade" else "source-correction-output-shape/999"
                )
    else:
        rows[place][correction.SHAPE_KEY] = correction.SHAPE_V1
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?", (dumps(payload), dumps(result), task["id"])
        )
    model = SourceModel()
    assert asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime" and model.frames == []
    assert domain.artifact(candidate["id"]) == candidate


@pytest.mark.parametrize(
    "bad",
    [
        "proposal_quote",
        "preservation_quote",
        "fixed_quote",
        "fixed_unknown_id",
        "narrative_actor",
        "event_to_narrative",
        "unselected",
    ],
)
def test_invalid_citations_and_meaning_still_stop_without_repair(
    settings, domain, client, stopped_source, bad
):
    _, candidate = stopped_source
    task = submit(client, candidate).json()

    def mutate(action, value):
        if action == correction.PROPOSE:
            if bad == "proposal_quote":
                value["items"][0]["evidence"] = "林舟用借来的短尺重复检查刻线"
            elif bad == "narrative_actor":
                value["items"][4]["actor"] = "未注明"
            elif bad == "event_to_narrative":
                value["items"][5].update(actor="", statement_kind="narrative_requirement")
            elif bad == "unselected":
                value["items"][0]["actor"] = "师傅"
        elif action == correction.VERIFY:
            if bad == "preservation_quote":
                value["item_checks"][0]["evidence"] = "林舟用借来的短尺重复检查刻线"
            elif bad == "fixed_quote":
                value["fixed_checks"][0]["evidence"] = "不发生战斗"
            elif bad == "fixed_unknown_id":
                value["fixed_checks"][0]["evidence"] = "FIX999"

    model = SourceModel(mutate=mutate)
    assert asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "blocked_runtime" and "artifact_id" not in done["result"]
    assert len(model.frames) == (2 if bad in {"preservation_quote", "fixed_quote", "fixed_unknown_id"} else 1)
    assert domain.artifact(candidate["id"]) == candidate


@pytest.mark.parametrize("version", [correction.SHAPE_V1, correction.SHAPE_VERSION])
def test_unknown_sent_call_keeps_reservation_and_never_replays_after_resume(
    settings, domain, client, stopped_source, version
):
    parent, candidate = stopped_source
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=? WHERE id=?",
            (dumps({**parent["payload"], "account": "deepseek"}), parent["id"]),
        )
    task = retag(domain, submit(client, candidate, account="deepseek").json(), version)
    sent = []

    def timeout(request):
        sent.append(str(request.url))
        raise httpx.ReadTimeout("isolated unknown result", request=request)

    worker = Worker(settings)
    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(timeout))
    assert asyncio.run(worker.run_once())
    assert len(sent) == 1 and domain.task(task["id"])["status"] == "blocked_runtime"
    calls = Budget(settings).report()[1]["calls"]
    assert len(calls) == 1 and calls[0]["status"] == "unknown" and calls[0]["charged"] is None
    assert (
        client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    assert asyncio.run(worker.run_once())
    assert len(sent) == 1 and Budget(settings).report()[1]["calls"] == calls
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert domain.artifact(candidate["id"]) == candidate


def test_true_ambiguity_is_readonly_unverified_not_forced_to_a_named_actor(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source
    task = submit(client, candidate).json()

    def mutate(action, value):
        if action == correction.PROPOSE:
            value["items"][5].update(actor="未注明", certainty="ambiguous", statement_kind="story_event")

    assert asyncio.run(Worker(settings, SourceModel(mutate=mutate)).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded" and done["result"]["source_correction_state"] == "unverified"
    receipt = domain.artifact(done["result"]["artifact_id"])
    response = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": receipt["id"]},
    )
    assert response.status_code == 409
    assert domain.artifact(candidate["id"]) == candidate


@pytest.mark.parametrize("version", [correction.SHAPE_V1, correction.SHAPE_VERSION])
def test_paid_complete_bad_quote_cache_never_resends_on_resume(
    settings, domain, client, stopped_source, version
):
    _, candidate = stopped_source
    task = retag(domain, submit(client, candidate).json(), version)
    model = SourceModel(
        mutate=lambda action, value: (
            value["items"][0].update(evidence="原句不自动转编号") if action == correction.PROPOSE else None
        )
    )
    assert asyncio.run(Worker(settings, model).run_once())
    steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (task["id"],))
    assert len(steps) == 1 and len(model.frames) == 1
    assert (
        client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    resumed = SourceModel()
    assert asyncio.run(Worker(settings, resumed).run_once())
    assert (
        resumed.frames == []
        and domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (task["id"],)) == steps
    )


@pytest.mark.parametrize("bad", [False, True])
def test_actual_deepseek_wire_uses_numbered_evidence_and_retains_invalid_fee(
    settings, domain, client, stopped_source, bad
):
    parent, candidate = stopped_source
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=? WHERE id=?",
            (dumps({**parent["payload"], "account": "deepseek"}), parent["id"]),
        )
    task = submit(client, candidate, account="deepseek").json()
    calls, model = (
        [],
        SourceModel(
            mutate=lambda action, value: (
                value["items"][0].update(evidence="原句") if bad and action == correction.PROPOSE else None
            )
        ),
    )

    async def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"]
        contract = json.loads(prompt.split("\n只返回符合以下 JSON Schema 的对象：\n")[1])
        calls.append(contract)
        output = await model.generate("deepseek", task["id"], "", prompt, 8192)
        return httpx.Response(
            200,
            content=stream(
                [
                    {"choices": [{"delta": {"content": output["text"]}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
                ],
                True,
            ),
        )

    worker = Worker(settings)
    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    assert asyncio.run(worker.run_once())
    done = domain.task(task["id"])
    assert calls[0]["$defs"]["ProposedItem"]["properties"]["evidence"]["enum"] == ["AUTHOR0001", "AUTHOR0002"]
    assert len(calls) == (1 if bad else 2)
    assert done["status"] == ("blocked_runtime" if bad else "succeeded"), done["error"]
    charged = Budget(settings).report()[1]["calls"]
    assert len(charged) == len(calls) and all(row["status"] == "settled" for row in charged)
    if bad:
        assert "artifact_id" not in done["result"]
    assert domain.artifact(candidate["id"]) == candidate
