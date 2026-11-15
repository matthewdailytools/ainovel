"""Open execution slots are not author facts or permission to change a plan."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel.db import DomainError, dumps
from ainovel.plot_audit import author_contract_issues, interpret_plot
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from ainovel.workflow_plan_open_details import KEY, VERSION
from ainovel.plot_audit import (
    OpenPlanInterpretation,
    open_plan_issues as issues,
    normalize_open_plan as normalize,
)
from ainovel.workflow_plan_preparation import CHECK_ACTION, CHOICE_ACTION, frozen_contract, prepare, sha
from ainovel.workflow_plan_restart import PlanRestartInput, restart
from helpers.plan_execution_fixture import plan_execution_output
from test_openai_schema import completed
from test_workflow_plan_preparation import PreparationModel, claimed

SOURCE = "门吏随口提到备用灯尚未试亮，学徒记下缺口。只解释流程，不肯说负责者。"


def item(*, recipient="", open_fields=None, evidence=SOURCE, scope_condition=""):
    return {
        "author_evidence": evidence,
        "actor": "门吏",
        "action": "随口提到",
        "object": "备用灯尚未试亮",
        "direction": "",
        "result": "学徒记下缺口；未试亮的准备缺口保留",
        "certainty": "explicit",
        "transfer_object": "备用灯尚未试亮的信息",
        "supplier": "门吏",
        "recipient": recipient,
        "transfer_scope": "attempted",
        "recipient_scope": "unspecified",
        "scope_condition": scope_condition,
        "open_fields": ["recipient"] if open_fields is None else open_fields,
        "open_reason": "原话明确提及及学徒记下，未指明此句直接说给谁听。",
    }


class OpenModel(PreparationModel):
    def __init__(self, *, unresolved_until=0, altered=False, bad_condition=False, same_side=False):
        super().__init__()
        self.unresolved_until = unresolved_until
        self.altered, self.bad_condition, self.same_side = altered, bad_condition, same_side

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("动作")
        if action == "写前解释AI工作章纲":
            round_number = instruction["写前准备契约"]["round"]
            open_slot = round_number <= self.unresolved_until
            row = item(recipient="" if open_slot else "学徒", open_fields=["recipient"] if open_slot else [])
            row["author_evidence"] = instruction["可引用原文"][0]["source_id"]
            if self.bad_condition:
                row["scope_condition"] = "原话未指定接收人"
            if self.same_side and not open_slot:
                row["recipient"] = "门吏"
            if "接收范围核对执行版本" in instruction:
                row["scope_observation_ids"] = []
                if instruction["接收范围核对执行版本"] == "workflow-plan-recipient-scope/2":
                    row.update(
                        scope_binding_status="supported",
                        scope_binding_reason="工程开放槽与原话范围关联；不作真实模型判定。",
                    )
            output = {"items": [row], "note": "只据AI粗章纲，开放槽不属于事实或作者锁定。"}
        elif action == CHOICE_ACTION:
            output = {
                "addendum": "门吏把备用灯尚未试亮的消息随口说给学徒，学徒记下缺口；未试亮、只讲流程、不肯说负责者均保留。",
                "rationale": "仅明确原粗纲未指定的直接接收人。",
            }
            if self.altered:
                output["addendum"] = "备用灯已经试亮，门吏公开说出负责者。"
        elif action == CHECK_ACTION:
            output = plan_execution_output(instruction)
            if self.altered and "已经试亮" in instruction["有效章纲"]:
                output["item_checks"][0].update(
                    result="fail", reason="原文尚未试亮且不肯谈负责者；附注倒置原结果和否定。"
                )
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        self.preparation_frames.append(deepcopy(frame))
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": f"open-{self.calls}"}


def open_worker(settings, domain, story, model=None, *, legacy=False):
    worker, plot, chapter, task = claimed(settings, domain, story, model=model or OpenModel(), legacy=legacy)
    plot["plot"] = SOURCE
    worker.task["result"]["plan"]["chapters"][0] = deepcopy(plot)
    worker.checkpoint(worker.task["result"])
    return worker, plot, chapter, task


def test_open_slot_does_not_fill_participant_and_final_strict_gate_remains():
    value = {"items": [item()], "note": "工程样例"}
    assert not issues(value, SOURCE)
    prepared = normalize(value)
    assert prepared["contract"]["items"][0]["certainty"] == "ambiguous"
    assert prepared["contract"]["items"][0]["recipient"] == ""
    assert prepared["open_details"][0]["source_certainty"] == "explicit"
    strict = deepcopy(prepared["contract"])
    strict["items"][0]["certainty"] = "explicit"
    assert author_contract_issues(strict, SOURCE, scoped=True)


@pytest.mark.parametrize("certainty", ["explicit", "ambiguous"])
def test_nontransfer_unknown_without_slots_retains_existing_readiness(certainty):
    source = "学徒发现木楔形状与旧图不合，原因仍未知；只记录，不下结论。"
    row = item(evidence=source, open_fields=[])
    row.update(
        actor="学徒",
        action="记录",
        object="木楔形状差异",
        result="原因仍未知，不下结论",
        certainty=certainty,
        transfer_object="",
        supplier="",
        recipient="",
        transfer_scope="none",
        open_reason="",
    )
    value = {"items": [row], "note": "保留剧情未知，不为补齐双方提前解谜。"}
    assert not issues(value, source)
    assert normalize(value)["contract"]["items"][0]["certainty"] == certainty
    assert normalize(value)["open_details"] == []


def test_nontransfer_cannot_create_a_transfer_slot_from_narrative_unknown():
    source = "学徒只记录未知原因，不下结论。"
    row = item(evidence=source)
    row.update(
        transfer_scope="none", transfer_object="", supplier="", recipient="", action="记录", object="未知原因"
    )
    assert issues({"items": [row], "note": "不能用接收人槽把谜底未知变成AI选择"}, source)


@pytest.mark.parametrize("stage", ["attempted", "planned"])
def test_transfer_slots_remain_available_without_changing_the_stated_stage(stage):
    source = SOURCE if stage == "attempted" else "门吏打算明日传出备用灯尚未试亮的消息，学徒将记下缺口。"
    row = {**item(evidence=source), "transfer_scope": stage}
    if stage == "planned":
        row.update(action="打算传出", result="学徒将记下缺口；未试亮状态仍保留")
    value = {"items": [row], "note": "仅验证开放槽和阶段保持"}
    assert not issues(value, source)
    normalized = normalize(value)
    assert normalized["contract"]["items"][0]["transfer_scope"] == stage
    assert normalized["contract"]["items"][0]["recipient"] == ""
    assert normalized["contract"]["items"][0]["certainty"] == "ambiguous"


@pytest.mark.parametrize(
    "change", ["invented_condition", "joined_quote", "occupied_slot", "missing_reason", "duplicate"]
)
def test_open_slot_cannot_hide_bad_source_or_overwrite_specified_party(change):
    row = item()
    if change == "invented_condition":
        row["scope_condition"] = "原话没有说明"
    elif change == "joined_quote":
        row["scope_condition"] = "备用灯；尚未试亮"
    elif change == "occupied_slot":
        row["recipient"] = "已指定的学徒"
    elif change == "missing_reason":
        row["open_reason"] = "  "
    else:
        row["open_fields"] *= 2
    assert issues({"items": [row], "note": ""}, SOURCE)


def test_executable_freeze_requires_choice_reinterpretation_and_independent_preservation(
    settings, domain, story
):
    worker, plot, chapter, task = open_worker(settings, domain, story)
    original = deepcopy(worker.task["result"]["plan"])
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    row = domain.task(task["id"])["result"]["plot_preparations"]["1"]
    assert row[KEY] == VERSION and row["round"] == 1 and row["status"] == "ready"
    assert row["interpretations"][0]["contract"]["items"][0]["recipient"] == ""
    assert row["interpretations"][0]["open_details"][0]["fields"] == ["recipient"]
    assert row["interpretations"][-1]["open_details"] == []
    assert contract["items"][0]["recipient"] == "学徒"
    assert contract["items"][0]["transfer_scope"] == "attempted"
    assert all(x["result"]["constraints_check"]["result"] == "pass" for x in row["preservation_checks"])
    assert frozen_contract(worker, effective["plot"], 1, contract) == contract
    assert worker.task["result"]["plan"] == original
    assert chapter["content"] == "" and not domain.db.all("SELECT id FROM facts")
    choice = next(f["任务"] for f in worker.client.preparation_frames if f["任务"]["动作"] == CHOICE_ACTION)
    assert choice["原话尚未确定的执行槽"][0]["author_evidence"] == SOURCE
    assert all("本次候选" not in dumps(f) for f in worker.client.preparation_frames)


@pytest.mark.parametrize("fault", ["bad_condition", "same_side"])
def test_source_and_different_sides_are_still_required(settings, domain, story, fault):
    worker, plot, chapter, _ = open_worker(settings, domain, story, OpenModel(**{fault: True}))
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(prepare(worker, plot, chapter))
    assert not domain.db.all("SELECT id FROM artifacts WHERE kind='chapter_draft'")
    assert domain.chapter(chapter["id"])["revision"] == 0


def test_choice_cannot_change_unfinished_result_or_forbidden_disclosure(settings, domain, story):
    worker, plot, chapter, task = open_worker(settings, domain, story, OpenModel(altered=True))
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    row = domain.task(task["id"])["result"]["plot_preparations"]["1"]
    assert row["status"] == "needs_review" and row["round"] == 1
    assert row["preservation_checks"][-1]["result"]["item_checks"][0]["result"] == "fail"
    assert worker.task["result"]["plan"]["chapters"][0] == plot
    assert domain.chapter(chapter["id"])["content"] == ""


def test_at_most_two_choices_and_no_automatic_new_generation(settings, domain, story):
    worker, plot, chapter, task = open_worker(settings, domain, story, OpenModel(unresolved_until=10))
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    row = domain.task(task["id"])["result"]["plot_preparations"]["1"]
    assert row["round"] == 2 and len(row["choices"]) == 2 and row["status"] == "needs_review"
    before = worker.client.calls
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    assert worker.client.calls == before


def test_half_auto_author_source_does_not_get_open_slot_policy(settings, domain, story):
    worker, _, _, _ = open_worker(settings, domain, story)
    worker.task["payload"]["mode"] = "assisted"
    with pytest.raises(DomainError, match="全自动写前"):
        asyncio.run(
            interpret_plot(
                worker,
                SOURCE,
                source_kind="author_plot",
                preparation={"version": "workflow-plan-preparation/1", KEY: VERSION},
            )
        )
    assert worker.client.calls == 0


@pytest.mark.parametrize("damage", ["missing_preparation", "legacy_plot", "half_auto", "unknown_open"])
def test_partial_or_unknown_policy_is_not_treated_as_legacy(settings, domain, story, damage):
    worker, plot, chapter, _ = open_worker(settings, domain, story)
    if damage == "missing_preparation":
        worker.task["result"].pop("workflow_plan_preparation_version")
    elif damage == "legacy_plot":
        worker.task["result"]["plot_contract_version"] = "author-plot-audit/1"
    elif damage == "half_auto":
        worker.task["payload"]["mode"] = "assisted"
    else:
        worker.task["result"][KEY] = "unrecognized-future-version"
    with pytest.raises(DomainError):
        asyncio.run(prepare(worker, plot, chapter))
    assert worker.client.calls == 0


def known_failed(settings, domain, story):
    worker, plot, chapter, task = open_worker(settings, domain, story, legacy=True)
    worker.task["result"].pop(KEY)
    worker.task["payload"]["budget_limit"] = 1
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(worker.task["payload"]), task["id"]))
    worker.checkpoint(worker.task["result"])
    dispatched = []

    def handler(request):
        dispatched.append(request.content)
        output = {
            "items": [item(evidence="AUTHOR0001", open_fields=[])],
            "note": f"已知格式完整但双方缺失的旧输出{len(dispatched)}",
        }
        output["items"][0].pop("open_fields")
        output["items"][0].pop("open_reason")
        return completed(dumps(output))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(prepare(worker, plot, chapter))
    assert len(dispatched) == 3
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (task["id"],))
    current = domain.task(task["id"])
    request = PlanRestartInput(
        client_request_id="explicit-plan-restart",
        reason="明确启用开放槽阶段新代际，保留旧失败和费用",
        chapter=1,
        base_story_revision=domain.branch(story["branch_id"])["story_revision"],
        base_progress_sha256=sha(current["result"]),
    ).model_dump()
    return worker, current, request, dispatched


def test_explicit_restart_forks_preserves_old_steps_plan_fees_and_nonce(settings, domain, story):
    worker, current, request, dispatched = known_failed(settings, domain, story)
    old_steps = domain.db.all("SELECT * FROM task_steps")
    with worker.budget.db() as db:
        old_usage = [dict(r) for r in db.execute("SELECT * FROM calls")]
    child = restart(domain, worker.budget, current, request)
    old = domain.task(current["id"])
    assert old["status"] == "continued" and old["result"] == {
        **current["result"],
        "continued_to": child["id"],
    }
    assert KEY not in old["result"] and child["result"][KEY] == VERSION
    assert child["result"]["plan"] == current["result"]["plan"]
    assert child["payload"]["account"] == current["payload"]["account"]
    assert child["payload"]["budget_limit"] == 1 - sum(c["charged"] for c in old_usage) / 1_000_000
    assert child["result"]["plot_preparations"] == {}
    assert (
        child["result"]["plan_preparation_restart"]["previous_preparation"]
        == current["result"]["plot_preparations"]["1"]
    )
    assert domain.db.all("SELECT * FROM task_steps") == old_steps
    with worker.budget.db() as db:
        assert [dict(r) for r in db.execute("SELECT * FROM calls")] == old_usage
    assert len(dispatched) == 3
    assert restart(domain, worker.budget, old, request)["id"] == child["id"]
    with pytest.raises(DomainError) as caught:
        restart(domain, worker.budget, old, {**request, "reason": "同标识不同决定"})
    assert caught.value.code == "idempotency"
    next_worker = Worker(settings, OpenModel())
    next_worker.task = next_worker.claim()
    next_worker.attempt = next_worker.task["attempt"]
    assert next_worker.task["id"] == child["id"]
    effective, contract = asyncio.run(
        prepare(next_worker, child["result"]["plan"]["chapters"][0], domain.chapter(story["chapter_id"]))
    )
    assert next_worker.client.calls == 5  # fresh interpretation/check/choice/interpretation/check
    assert frozen_contract(next_worker, effective["plot"], 1, contract) == contract


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "missing_step",
        "new_dispatch",
        "body",
        "candidate",
        "chapter_started",
        "half_auto",
        "source_changed",
        "valid_cache",
        "active",
        "selected_round",
    ],
)
def test_restart_rejects_unknown_late_changed_or_ineligible_without_side_effects(
    settings, domain, story, fault
):
    worker, current, request, _ = known_failed(settings, domain, story)
    if fault == "unknown":
        with worker.budget.db(True) as tx:
            tx.execute(
                "UPDATE calls SET status='unknown',charged=NULL WHERE id=(SELECT id FROM calls LIMIT 1)"
            )
    elif fault == "missing_step":
        with domain.db.connect(True) as tx:
            tx.execute(
                "DELETE FROM task_steps WHERE fingerprint=(SELECT fingerprint FROM task_steps LIMIT 1)"
            )
    elif fault == "new_dispatch":
        domain.db.emit(
            current["id"],
            "step_dispatched",
            {"fingerprint": "unresolved", "action": "写前解释AI工作章纲", "schema": "ScopedAuthorPlot"},
        )
    elif fault == "body":
        domain.save(story["chapter_id"], 0, "作者已经手写")
    elif fault == "candidate":
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO artifacts(id,task_id,novel_id,branch_id,chapter_id,kind,status,basis,payload,created) VALUES(?,?,?,?,?,'chapter_draft','candidate','{}','{}',0)",
                ("not-late", current["id"], current["novel_id"], current["branch_id"], story["chapter_id"]),
            )
    elif fault == "chapter_started":
        domain.db.emit(current["id"], "chapter_started", {"number": 1})
    elif fault == "half_auto":
        current["payload"]["mode"] = "assisted"
    elif fault == "source_changed":
        current["result"]["plan"]["chapters"][0]["plot"] += "已改来源"
        request["base_progress_sha256"] = sha(current["result"])
    elif fault == "valid_cache":
        with domain.db.connect(True) as tx:
            record = tx.execute("SELECT fingerprint,result FROM task_steps LIMIT 1").fetchone()
            response = json.loads(record["result"])
            body = json.loads(response["text"])
            body["items"][0]["recipient"] = "学徒"
            response["text"] = dumps(body)
            tx.execute(
                "UPDATE task_steps SET result=? WHERE fingerprint=?", (dumps(response), record["fingerprint"])
            )
    elif fault == "active":
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('other','module',?,?,'queued','{}','{}',0,0)",
                (current["novel_id"], current["branch_id"]),
            )
    else:
        current["result"]["plot_preparations"]["1"]["round"] = 1
        request["base_progress_sha256"] = sha(current["result"])
    before = domain.db.all("SELECT * FROM tasks")
    calls_before = worker.budget.report()
    with pytest.raises(DomainError):
        restart(domain, worker.budget, current, request)
    assert domain.db.all("SELECT * FROM tasks") == before
    assert worker.budget.report() == calls_before
    assert not domain.db.all("SELECT task_id FROM task_requests WHERE key=?", (request["client_request_id"],))


def test_strict_new_schema_and_no_author_model_open_fields():
    from ainovel.openai_schema import strict_schema
    from ainovel.plot_audit import ScopedAuthorPlot

    assert strict_schema(OpenPlanInterpretation.model_json_schema())["type"] == "object"
    with pytest.raises(ValueError):
        ScopedAuthorPlot.model_validate({"items": [item()], "note": "不能向作者情节传开放槽"})


def test_explicit_route_uses_own_account_remaining_budget_and_no_network(settings, domain, story, client):
    worker, current, request, dispatched = known_failed(settings, domain, story)
    before = domain.db.all("SELECT * FROM tasks")
    receipt_path = f"/api/tasks/{current['id']}/plan-restarts/{request['client_request_id']}"
    assert client.get(receipt_path).json()["task"] is None
    prepared = client.get(f"/api/tasks/{current['id']}/reprepare-plan-preview")
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["request_basis"] == {
        k: request[k] for k in ("chapter", "base_story_revision", "base_progress_sha256")
    }
    assert domain.db.all("SELECT * FROM tasks") == before
    response = client.post(f"/api/tasks/{current['id']}/reprepare-plan", json=request)
    assert response.status_code == 202, response.text
    child = response.json()
    assert client.get(receipt_path).json()["task"]["id"] == child["id"]
    assert client.get(f"/api/tasks/{current['id']}/plan-restarts/prep-original").status_code == 409
    assert child["payload"]["account"] == current["payload"]["account"]
    assert child["result"][KEY] == VERSION and child["status"] == "queued"
    assert client.post(f"/api/tasks/{current['id']}/reprepare-plan", json=request).json()["id"] == child["id"]
    assert len(dispatched) == 3


def test_known_failed_phase_survives_official_change_ack_and_pause_before_restart(
    settings, domain, story, client
):
    worker, current, request, dispatched = known_failed(settings, domain, story)
    old_row = deepcopy(current["result"]["plot_preparations"]["1"])
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (current["branch_id"],))
    response = client.post(
        f"/api/tasks/{current['id']}/resume",
        json={"acknowledge_changes": True, "acknowledge_unknown": True, "pause_after_maintenance": True},
    )
    assert response.status_code == 200, response.text
    model = OpenModel()
    asyncio.run(Worker(settings, model).run_once())
    paused = domain.task(current["id"])
    assert paused["status"] == "paused" and paused["error"] == ""
    assert paused["result"]["plot_preparations"]["1"] == old_row
    assert paused["result"]["next_chapter"] == 1 and model.calls == 0 and len(dispatched) == 3
    prepared = client.get(f"/api/tasks/{current['id']}/reprepare-plan-preview")
    assert prepared.status_code == 200, prepared.text
    request.update(prepared.json()["request_basis"])
    child = restart(domain, worker.budget, paused, request)
    assert child["result"][KEY] == VERSION and child["result"]["plan"] == current["result"]["plan"]
    assert model.calls == 0
