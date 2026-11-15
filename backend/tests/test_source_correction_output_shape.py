"""Output scope is all original P items, not only the selected edit scope."""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
import httpx

from ainovel import source_interpretation_correction as correction
from ainovel.db import dumps
from ainovel.worker import Worker
from ainovel.providers import ModelClient
from ainovel.budget import Budget
from test_budget_provider import stream
from test_openai_schema import completed
from test_source_interpretation_correction import (
    SourceModel,
    recheck_basis,
    stopped_source as stopped_source,
    submit,
)


class CaptureModel(SourceModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.contracts = []

    async def generate(self, *args, **kwargs):
        self.contracts.append(deepcopy(kwargs.get("schema")))
        return await super().generate(*args, **kwargs)


def legacy_task(domain, task):
    payload, result = deepcopy(task["payload"]), deepcopy(task["result"])
    for row in (payload, result, result["source_correction_snapshot"]):
        row.pop(correction.SHAPE_KEY, None)
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?", (dumps(payload), dumps(result), task["id"])
        )
        tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (task["id"], correction.SHAPE_EVENT))
    return domain.task(task["id"])


def test_new_source_freezes_three_markers_exact_two_contracts_and_complete_receipt(
    settings, domain, client, stopped_source
):
    parent, candidate = stopped_source
    preview = correction.preview(domain, candidate["id"])
    queued = submit(client, candidate)
    assert queued.status_code == 202, queued.text
    task = queued.json()
    snapshot = task["result"]["source_correction_snapshot"]
    for row in (task["payload"], task["result"], snapshot):
        assert row[correction.SHAPE_KEY] == correction.SHAPE_VERSION
    assert preview["basis_sha256"] == snapshot["basis_sha256"]
    model = CaptureModel()
    assert asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    ids = [f"P{i:02}" for i in range(1, 10)]
    first, second = model.contracts
    assert first["properties"]["items"]["minItems"] == first["properties"]["items"]["maxItems"] == 9
    assert first["$defs"]["ProposedItem"]["properties"]["item_id"]["enum"] == ids
    assert (
        second["properties"]["item_checks"]["minItems"]
        == second["properties"]["item_checks"]["maxItems"]
        == 9
    )
    assert second["$defs"]["ItemCheck"]["properties"]["item_id"]["enum"] == ids
    n = len(snapshot["fixed"])
    assert (
        second["properties"]["fixed_checks"]["minItems"]
        == second["properties"]["fixed_checks"]["maxItems"]
        == n
    )
    assert second["$defs"]["FixedCheck"]["properties"]["condition_id"]["enum"] == [
        f"FIX{i:03}" for i in range(1, n + 1)
    ]
    for frame in model.frames:
        assert frame["任务"]["全部P编号按原顺序"] == ids
        assert frame["任务"]["允许纠偏事项"] == ["P05", "P06", "P07"]
        assert len(frame["任务"]["未选择事项原样返回字段"]) == 6
        assert "不能只返回选中的3项" in frame["任务"]["完整输出要求"]
    receipt = domain.artifact(done["result"]["artifact_id"])
    assert len(receipt["payload"]["proposal"]["items"]) == 9
    assert len(receipt["payload"]["preservation"]["item_checks"]) == 9
    assert domain.artifact(candidate["id"]) == candidate and domain.task(parent["id"]) == parent
    evidence = os.environ.get("AINOVEL_SHAPE_EVIDENCE_DIR")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        (Path(evidence) / "new-task-frontend-bundle.json").write_text(
            dumps({"task": done, "preview": preview, "receipt": receipt, "candidate": candidate}),
            encoding="utf8",
        )


@pytest.mark.parametrize("stage", [correction.PROPOSE, correction.VERIFY])
def test_three_selected_items_cannot_be_mistaken_for_complete_output(
    settings, domain, client, stopped_source, stage
):
    _, candidate = stopped_source
    task = submit(client, candidate).json()

    def mutate(action, value):
        if action == stage:
            key = "items" if stage == correction.PROPOSE else "item_checks"
            value[key] = [row for row in value[key] if row["item_id"] in {"P05", "P06", "P07"}]

    model = CaptureModel(mutate=mutate)
    assert asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(task["id"])
    assert stopped["status"] == "blocked_runtime"
    assert len(model.frames) == (1 if stage == correction.PROPOSE else 2)
    assert "全部" in stopped["error"]
    assert "artifact_id" not in stopped["result"]
    assert domain.artifact(candidate["id"]) == candidate


@pytest.mark.parametrize("place", ["payload", "result", "snapshot", "all", "unknown"])
def test_shape_marker_deletion_or_mismatch_stops_before_any_model_call(
    settings, domain, client, stopped_source, place
):
    _, candidate = stopped_source
    task = submit(client, candidate).json()
    payload, result = deepcopy(task["payload"]), deepcopy(task["result"])
    rows = {"payload": payload, "result": result, "snapshot": result["source_correction_snapshot"]}
    if place == "all":
        for row in rows.values():
            row.pop(correction.SHAPE_KEY)
    elif place == "unknown":
        for row in rows.values():
            row[correction.SHAPE_KEY] = "future-shape/9"
    else:
        rows[place].pop(correction.SHAPE_KEY)
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?", (dumps(payload), dumps(result), task["id"])
        )
    model = CaptureModel()
    assert asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.frames == [] and domain.artifact(candidate["id"]) == candidate


def test_new_and_legacy_nonces_replay_original_policy_without_upgrade(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source
    first = submit(client, candidate).json()
    assert submit(client, candidate).json()["id"] == first["id"]
    old = legacy_task(domain, first)
    replay = submit(client, candidate)
    assert replay.status_code == 202 and replay.json() == old
    model = CaptureModel(
        mutate=lambda action, value: (
            value.update(items=value["items"][4:7]) if action == correction.PROPOSE else None
        )
    )
    assert asyncio.run(Worker(settings, model).run_once())
    stopped = domain.task(old["id"])
    assert stopped["status"] == "blocked_runtime" and len(model.frames) == 1
    assert model.contracts[0]["properties"]["items"]["minItems"] == 1
    assert model.contracts[0]["properties"]["items"]["maxItems"] == 12
    assert "来源纠偏输出契约版本" not in model.frames[0]["任务"]
    steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (old["id"],))
    assert (
        client.post(f"/api/tasks/{old['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    resumed = CaptureModel()
    assert asyncio.run(Worker(settings, resumed).run_once())
    assert resumed.frames == []
    assert domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (old["id"],)) == steps
    assert correction.SHAPE_KEY not in domain.task(old["id"])["payload"]
    assert "artifact_id" not in domain.task(old["id"])["result"]


def test_empty_fixed_contract_is_exact_empty_array_without_invalid_empty_enum():
    snapshot = {
        correction.SHAPE_KEY: correction.SHAPE_VERSION,
        "prepared_contract": {"items": [{}] * 9},
        "fixed": [],
        "plot": {"plot": "用于空固定条件契约检查的原始来源。"},
    }
    schema = correction.output_contract(snapshot, preservation=True)
    assert (
        schema["properties"]["fixed_checks"]["minItems"]
        == schema["properties"]["fixed_checks"]["maxItems"]
        == 0
    )
    assert "enum" not in schema["$defs"]["FixedCheck"]["properties"]["condition_id"]


@pytest.mark.parametrize("omit", [False, True])
def test_actual_openai_request_carries_both_exact_contracts_and_retains_paid_omission(
    settings, domain, client, stopped_source, omit
):
    _, candidate = stopped_source
    task = submit(client, candidate).json()
    bodies = []
    model = SourceModel(
        mutate=lambda action, value: (
            value.update(items=value["items"][4:7]) if omit and action == correction.PROPOSE else None
        )
    )

    async def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        result = await model.generate("openai_1", task["id"], "", body["input"][1]["content"], 8192)
        return completed(result["text"])

    worker = Worker(settings)
    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    assert asyncio.run(worker.run_once())
    first = bodies[0]["text"]["format"]["schema"]
    assert first["properties"]["items"]["minItems"] == first["properties"]["items"]["maxItems"] == 9
    assert first["$defs"]["ProposedItem"]["properties"]["item_id"]["enum"] == [
        f"P{i:02}" for i in range(1, 10)
    ]
    done = domain.task(task["id"])
    if omit:
        assert done["status"] == "blocked_runtime" and len(bodies) == 1
        assert "全部P" in done["error"] and "artifact_id" not in done["result"]
    else:
        assert done["status"] == "succeeded" and len(bodies) == 2
        second = bodies[1]["text"]["format"]["schema"]
        assert (
            second["properties"]["item_checks"]["minItems"]
            == second["properties"]["item_checks"]["maxItems"]
            == 9
        )
        assert second["$defs"]["FixedCheck"]["properties"]["condition_id"]["enum"] == [
            f"FIX{i:03}" for i in range(1, len(done["result"]["source_correction_snapshot"]["fixed"]) + 1)
        ]
    calls = Budget(settings).report()[0]["calls"]
    assert len(calls) == len(bodies) and all(row["status"] == "settled" for row in calls)
    assert domain.artifact(candidate["id"]) == candidate


def test_complete_legacy_ready_receipt_keeps_basis_and_is_usable_for_explicit_body_recheck(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source
    queued = submit(client, candidate).json()
    old = legacy_task(domain, queued)
    assert asyncio.run(Worker(settings, CaptureModel()).run_once())
    completed = domain.task(old["id"])
    assert completed["status"] == "succeeded"
    receipt = domain.artifact(completed["result"]["artifact_id"])
    assert correction.SHAPE_KEY not in receipt["payload"]["snapshot"]
    assert (
        correction.preview(domain, candidate["id"])["basis_sha256"]
        == receipt["payload"]["snapshot"]["basis_sha256"]
    )
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "receipt_id": receipt["id"],
            "preview_basis_sha256": recheck_basis(client, candidate, receipt["id"]),
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "legacy-ready-new-body-request",
        },
    )
    assert response.status_code == 202, response.text
    assert domain.artifact(candidate["id"]) == candidate
    assert domain.task(old["id"]) == completed


@pytest.mark.parametrize("omit", [False, True])
def test_deepseek_wire_contains_complete_source_contract_and_never_fills_missing_items(
    settings, domain, client, stopped_source, omit
):
    parent, candidate = stopped_source
    # The isolated parent and child use one provider and currency before any call.
    payload = {**parent["payload"], "account": "deepseek"}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(payload), parent["id"]))
    parent = domain.task(parent["id"])
    response = submit(client, candidate, account="deepseek")
    assert response.status_code == 202, response.text
    task = response.json()
    requests = []
    model = SourceModel(
        mutate=lambda action, value: (
            value.update(items=value["items"][4:7]) if omit and action == correction.PROPOSE else None
        )
    )

    async def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/chat/completions"
        assert body["response_format"] == {"type": "json_object"}
        prompt = body["messages"][1]["content"]
        frame, suffix = prompt.split("\n只返回符合以下 JSON Schema 的对象：\n", 1)
        schema = json.loads(suffix)
        action = json.loads(frame)["任务"]["动作"]
        key, definition = (
            ("items", "ProposedItem") if action == correction.PROPOSE else ("item_checks", "ItemCheck")
        )
        assert schema["properties"][key]["minItems"] == schema["properties"][key]["maxItems"] == 9
        assert schema["$defs"][definition]["properties"]["item_id"]["enum"] == [
            f"P{i:02}" for i in range(1, 10)
        ]
        if action == correction.VERIFY:
            count = len(task["result"]["source_correction_snapshot"]["fixed"])
            assert (
                schema["properties"]["fixed_checks"]["minItems"]
                == schema["properties"]["fixed_checks"]["maxItems"]
                == count
            )
        requests.append(body)
        result = await model.generate("deepseek", task["id"], "", prompt, 8192)
        return httpx.Response(
            200,
            content=stream(
                [
                    {
                        "model": "isolated-test-model",
                        "choices": [{"delta": {"content": result["text"]}, "finish_reason": "stop"}],
                    },
                    {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
                ],
                True,
            ),
        )

    worker = Worker(settings)
    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    assert asyncio.run(worker.run_once())
    done = domain.task(task["id"])
    assert done["status"] == ("blocked_runtime" if omit else "succeeded"), done["error"]
    assert len(requests) == (1 if omit else 2)
    if omit:
        assert "全部P" in done["error"] and "artifact_id" not in done["result"]
    else:
        receipt = domain.artifact(done["result"]["artifact_id"])
        assert receipt["payload"]["state"] == "ready"
        assert len(receipt["payload"]["proposal"]["items"]) == 9
    calls = Budget(settings).report()[1]["calls"]
    assert len(calls) == len(requests) and all(row["status"] == "settled" for row in calls)
    assert domain.artifact(candidate["id"]) == candidate and domain.task(parent["id"]) == parent
