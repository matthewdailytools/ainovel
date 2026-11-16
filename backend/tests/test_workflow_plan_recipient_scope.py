"""Source-only scope inventory, evidence binding and historical execution compatibility."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel.db import DomainError, dumps
from ainovel.providers import ModelClient
from ainovel.prompt_encoding import expand_prompt
from ainovel.workflow_plan_preparation import prepare, frozen_contract
from ainovel.worker import Worker
from ainovel import workflow_plan_recipient_scope as scope
from helpers.plan_execution_fixture import plan_execution_output
from test_openai_schema import completed
from test_workflow_plan_preparation import claimed, CHECK_ACTION
from test_workflow_plan_source_roles import SOURCE, proposal


@pytest.fixture(autouse=True)
def legacy_second_policy_frames(monkeypatch):
    """This original file keeps exercising the frozen /2 source policy."""
    monkeypatch.setattr(scope, "NEW_TASK_VERSION", scope.CURRENT_VERSION)


def inventory(source, *, kind="named_destination", stage="completed", condition="", unknown=False):
    sources = scope.units(source)
    observations = [
        {
            "observation_id": "O01",
            "kind": kind,
            "supplier": "甲",
            "recipient": "校核站",
            "transfer_object": "记录抄本",
            "transfer_scope": stage,
            "evidence": sources[0]["source_id"],
            "restriction_evidence": sources[0]["source_id"] if kind != "named_destination" else "",
            "condition": condition,
            "reason": "按该原句识别邮寄范围，命名目的地不等于排他；不依靠解释卡。",
        }
    ]
    if len(sources) > 1:
        observations.append(
            {
                "observation_id": "O02",
                "kind": "action_permission",
                "supplier": "",
                "recipient": "",
                "transfer_object": "",
                "transfer_scope": "none",
                "evidence": sources[1]["source_id"],
                "restriction_evidence": sources[1]["source_id"],
                "condition": "",
                "reason": "仅许可另抄携带，不是许可人交付副本，也不排除邮寄的其他接收人。",
            }
        )
    checks = [
        {
            "source_id": u["source_id"],
            "status": "covered",
            "observation_ids": [observations[i]["observation_id"]],
            "reason": "定位本句动作或许可范围。",
        }
        for i, u in enumerate(sources)
    ]
    if unknown:
        observations = []
        checks = [
            {
                "source_id": u["source_id"],
                "status": "unknown",
                "observation_ids": [],
                "reason": "限制归属无法确认，不补造。",
            }
            for u in sources
        ]
    value = {
        "input_sha256": scope.sha({"version": scope.VERSION, "source": source}),
        "observations": observations,
        "unit_checks": checks,
        "note": "工程替身，非真实模型语义评价。",
    }
    return {
        "version": scope.VERSION,
        "input_sha256": value["input_sha256"],
        "source_sha256": scope.sha(source),
        "sources": sources,
        "inventory": value,
        "status": "supported" if scope.inventory_supported(value) else "needs_review",
    }


def output(source=SOURCE, *, only=False, stage="completed", condition=""):
    value = proposal(permission_scope="unspecified", stage=stage)
    value["items"][0].update(
        recipient_scope="only" if only else "unspecified",
        scope_condition=condition,
        scope_observation_ids=["O01"],
    )
    value["items"][1].update(recipient="", scope_observation_ids=["O02"])
    if len(scope.units(source)) == 1:
        value["items"] = value["items"][:1]
    return value


@pytest.mark.parametrize("kind", ["named_destination", "action_permission", "specific_recipient_exclusion"])
def test_one_destination_permission_or_excluding_someone_never_proves_only(kind):
    receipt = inventory(SOURCE)
    receipt["inventory"]["observations"][0]["kind"] = kind
    bad = output(only=True)
    assert scope.binding_issues(bad, receipt)
    assert bad["items"][0]["recipient_scope"] == "only"  # no silent normalization
    assert not scope.binding_issues(output(), receipt)


@pytest.mark.parametrize("stage", ["completed", "attempted", "planned"])
def test_genuine_only_same_tuple_and_stage_must_be_kept(stage):
    source = "甲只向校核站寄送记录抄本。"
    receipt = inventory(source, kind="recipient_exclusivity", stage=stage)
    value = output(source, only=True, stage=stage)
    assert not scope.binding_issues(value, receipt)
    for field, wrong in [
        ("supplier", "乙"),
        ("recipient", "陆某"),
        ("transfer_object", "原件"),
        ("transfer_scope", "none"),
        ("recipient_scope", "unspecified"),
    ]:
        broken = deepcopy(value)
        broken["items"][0][field] = wrong
        assert scope.binding_issues(broken, receipt)


def test_conditional_only_cannot_become_unconditional_or_borrow_another_transfer():
    source = "校核前甲只向校核站寄送记录抄本。"
    receipt = inventory(source, kind="recipient_exclusivity", condition="校核前")
    value = output(source, only=True, condition="校核前")
    assert not scope.binding_issues(value, receipt)
    value["items"][0]["scope_condition"] = ""
    assert scope.binding_issues(value, receipt)
    value = output(source, only=True, condition="校核前")
    value["items"][0]["scope_observation_ids"] = []
    assert scope.binding_issues(value, receipt)


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "unknown_id", "wrong_source", "wrong_token", "wrong_condition"]
)
def test_inventory_evidence_and_full_source_coverage_are_checked(fault):
    receipt = inventory(SOURCE)
    value = deepcopy(receipt["inventory"])
    if fault == "missing":
        value["unit_checks"].pop()
    elif fault == "duplicate":
        value["observations"][1]["observation_id"] = "O01"
    elif fault == "unknown_id":
        value["unit_checks"][0]["observation_ids"] = ["O20"]
    elif fault == "wrong_source":
        value["observations"][0]["evidence"] = "OLD0001"
    elif fault == "wrong_token":
        value["input_sha256"] = "a" * 64
    else:
        value["observations"][1]["condition"] = "原话没有这个条件"
    assert scope.inventory_issues(
        value, receipt["input_sha256"], {u["source_id"]: u for u in receipt["sources"]}
    )


def test_utf16_source_units_keep_emoji_extended_character_and_exact_quote():
    text = "甲😀𠀀寄往乙。仅乙可看。"
    for unit in scope.units(text):
        raw = text.encode("utf-16-le")[unit["start"] * 2 : unit["end"] * 2]
        assert raw.decode("utf-16-le") == unit["text"]
    assert scope.units(text)[0]["end"] > len(scope.units(text)[0]["text"])


class ScopeModel:
    def __init__(self, source=SOURCE, *, bad_first=False, unknown=False, only=False):
        self.source, self.bad_first, self.unknown, self.only = source, bad_first, unknown, only
        self.frames = []
        self.interpretations = 0

    def answer(self, frame):
        self.frames.append(deepcopy(frame))
        instruction = frame["任务"]
        action = instruction.get("动作")
        if action == scope.ACTION:
            assert set(frame["上下文"]) == {"scope"}
            assert "待识别事项" not in dumps(instruction)
            assert "recipient_scope" not in dumps(frame)
            assert "候选正文" not in dumps(frame) and "原始全批规划" not in dumps(frame)
            result = inventory(
                self.source,
                kind="recipient_exclusivity" if self.only else "named_destination",
                unknown=self.unknown,
            )["inventory"]
            result["input_sha256"] = instruction["input_sha256"]
            return result
        if action == "写前解释AI工作章纲":
            self.interpretations += 1
            result = output(self.source, only=self.only or (self.bad_first and self.interpretations == 1))
            if instruction.get("接收范围核对执行版本") == scope.CURRENT_VERSION:
                for item in result["items"]:
                    item.update(
                        scope_binding_status="supported", scope_binding_reason="独立工程样例同源绑定。"
                    )
            return result
        assert action == CHECK_ACTION
        return plan_execution_output(instruction)

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = expand_prompt(json.loads(prompt.split("\n只返回")[0]))
        value = self.answer(frame)
        return {
            "text": dumps(value),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-" + str(len(self.frames)),
        }


def worker_for(settings, domain, story, model, *, account="openai_1"):
    if account == "openai_1":
        worker, plot, chapter, _ = claimed(settings, domain, story, model=model)
    else:
        from test_workflow_scope import workflow_payload

        payload = workflow_payload(story, mode="automatic")
        payload.update(account=account, plot_contract_check=True, client_request_id="native-scope-account")
        domain.queue("workflow", payload, story["branch_id"])
        worker = Worker(settings, model)
        worker.task = worker.claim()
        worker.attempt = worker.task["attempt"]
        plot = {"number": 1, "title": "来源范围", "plot": model.source, "fixed": []}
        worker.task["result"].update(
            plan={"chapters": [plot], "ending": "后续核验"},
            next_chapter=1,
            expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
        )
        chapter = domain.chapter(story["chapter_id"])
    plot["plot"] = model.source
    worker.task["result"]["plan"]["chapters"] = [plot]
    worker.checkpoint(worker.task["result"])
    return worker, plot, chapter


def test_prior_source_inventory_rejects_false_only_then_repairs_without_rewriting_source(
    settings, domain, story
):
    model = ScopeModel(bad_first=True)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    result = asyncio.run(prepare(worker, plot, chapter))
    assert result and model.interpretations == 2
    row = worker.task["result"]["plot_preparations"]["1"]
    assert row[scope.KEY] == scope.CURRENT_VERSION and row["status"] == "ready"
    assert row["original_plot"] == SOURCE and row["choices"] == []
    assert row["prepared_contract"]["items"][0]["recipient_scope"] == "unspecified"
    assert row["prepared_contract"]["items"][0]["recipient"] == "校核站"
    assert row["prepared_contract"]["items"][0]["transfer_scope"] == "completed"
    assert [f["任务"]["动作"] for f in model.frames] == [
        scope.ACTION,
        "写前解释AI工作章纲",
        "写前解释AI工作章纲",
        CHECK_ACTION,
    ]
    assert "scope_observation_ids" not in dumps(row["prepared_contract"])
    assert frozen_contract(worker, SOURCE, 1, row["prepared_contract"]) == row["prepared_contract"]
    count = len(model.frames)
    assert asyncio.run(prepare(worker, plot, chapter)) == result
    assert len(model.frames) == count


def test_unknown_is_valid_observation_but_does_not_start_interpretation_or_prose(settings, domain, story):
    model = ScopeModel(unknown=True)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    assert len(model.frames) == 1 and model.interpretations == 0
    assert domain.chapter(chapter["id"])["content"] == ""
    assert worker.task["result"]["plot_preparations"]["1"]["status"] == "needs_review"


@pytest.mark.parametrize(
    "fault",
    [
        "delete",
        "unknown_version",
        "tampered_binding",
        "tampered_inventory",
    ],
)
def test_frozen_execution_and_proof_cannot_be_deleted_or_reassigned(settings, domain, story, fault):
    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    if fault == "delete":
        row.pop(scope.KEY)
    elif fault == "unknown_version":
        row[scope.KEY] = "future/9"
    elif fault == "tampered_binding":
        row["interpretations"][0]["recipient_scope_bindings"]["items"][0]["observation_ids"] = ["O02"]
    else:
        row["phases"]["recipient_scope:0"]["response"]["inventory"]["observations"][0]["kind"] = (
            "recipient_exclusivity"
        )
    count = len(model.frames)
    with pytest.raises(DomainError):
        frozen_contract(worker, SOURCE, 1, row["prepared_contract"])
    assert len(model.frames) == count


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
def test_native_wire_schema_source_only_and_three_phases_are_persistent(settings, domain, story, account):
    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model, account=account)
    sent = []

    def handler(request):
        wire = json.loads(request.content)
        sent.append(wire)
        prompt = wire.get("input", wire.get("messages"))[1]["content"]
        frame = expand_prompt(json.loads(prompt.split("\n只返回")[0]))
        value = model.answer(frame)
        if len(sent) == 1:
            schema = (
                wire["text"]["format"]["schema"]
                if "text" in wire
                else json.loads(prompt.split("\n只返回符合以下 JSON Schema 的对象：\n")[1])
            )
            assert schema["properties"]["unit_checks"]["minItems"] == len(scope.units(SOURCE))
            assert schema["$defs"]["ScopeObservation"]["properties"]["restriction_evidence"]["enum"] == [
                "",
                "SCOPE0001",
                "SCOPE0002",
            ]
        if "input" in wire:
            return completed(dumps(value))
        from test_workflow_plan_source_roles import deepseek_completed

        return deepseek_completed(value)

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    asyncio.run(prepare(worker, plot, chapter))
    assert len(sent) == 3
    assert len(domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (worker.task["id"],))) == 3
    events = [
        json.loads(r["payload"])
        for r in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (worker.task["id"],)
        )
    ]
    assert all(e["request"].get(scope.KEY) == scope.CURRENT_VERSION for e in events[:2])
    with worker.budget.db() as tx:
        calls = [dict(r) for r in tx.execute("SELECT * FROM calls")]
    assert len(calls) == 3 and all(c["status"] == "settled" for c in calls)


def test_unknown_transport_never_reissues_prior_inventory(settings, domain, story):
    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    sent = []

    def handler(request):
        sent.append(request)
        raise httpx.ReadTimeout("offline unknown", request=request)

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError):
        asyncio.run(prepare(worker, plot, chapter))
    assert len(sent) == 1
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    assert len(sent) == 1


def test_real_only_survives_new_inventory_and_ready_contract(settings, domain, story):
    source = "甲只向校核站寄送记录抄本。"
    model = ScopeModel(source=source, only=True)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    _, contract = asyncio.run(prepare(worker, plot, chapter))
    assert contract["items"][0]["recipient_scope"] == "only"
    assert contract["items"][0]["transfer_scope"] == "completed"
    assert len(model.frames) == 3


def test_historical_row_cannot_be_upgraded_by_adding_marker(settings, domain, story):
    from test_workflow_plan_source_roles import historical_role_row

    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    historical_role_row(worker, plot, chapter)
    row = worker.task["result"]["plot_preparations"]["1"]
    row[scope.KEY] = scope.VERSION
    with pytest.raises(DomainError, match="版本|见证"):
        asyncio.run(prepare(worker, plot, chapter))
    assert not model.frames


def test_deleted_new_row_cannot_be_recreated_as_fresh(settings, domain, story):
    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    asyncio.run(prepare(worker, plot, chapter))
    worker.task["result"]["plot_preparations"].pop("1")
    count = len(model.frames)
    with pytest.raises(DomainError, match="已有冻结准备历史"):
        asyncio.run(prepare(worker, plot, chapter))
    assert len(model.frames) == count


@pytest.mark.parametrize("fault", ["hash", "contract", "status", "owner"])
def test_ready_new_interpretation_requires_its_complete_exact_paid_step(settings, domain, story, fault):
    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    slot = row["phases"]["interpret:0"]
    if fault == "hash":
        slot["response_sha256"] = "0" * 64
    elif fault == "contract":
        slot["response"]["contract"]["note"] += "tampered"
    elif fault == "status":
        slot["status"] = "started"
    else:
        slot["owner_task_id"] = "foreign"
    with pytest.raises(DomainError):
        frozen_contract(worker, SOURCE, 1, row["prepared_contract"])


def test_genuine_unconditional_only_cannot_gain_extra_condition():
    source = "甲只向校核站寄送记录抄本。师傅只允许甲午后另抄副本随身。"
    receipt = inventory(source, kind="recipient_exclusivity")
    value = output(source, only=True, condition="午后")
    assert scope.binding_issues(value, receipt)


def test_native_format_repair_keeps_inventory_source_only(settings, domain, story):
    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    wires = []

    def handler(request):
        wire = json.loads(request.content)
        frame = expand_prompt(json.loads(wire["input"][1]["content"]))
        wires.append(frame)
        instruction = frame["任务"]
        if len(wires) == 1:
            assert instruction["动作"] == scope.ACTION
            return completed(dumps({"note": "模拟已付格式缺项"}))
        if instruction["动作"] == "仅修正结构化产物格式":
            assert instruction["原始任务"]["动作"] == scope.ACTION
            assert set(frame["上下文"]) == {"scope"}
            value = inventory(SOURCE)["inventory"]
            value["input_sha256"] = instruction["原始任务"]["input_sha256"]
        else:
            value = model.answer(frame)
        return completed(dumps(value))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    assert asyncio.run(prepare(worker, plot, chapter))
    assert len(wires) == 4
    assert domain.chapter(chapter["id"])["content"] == ""


def test_ready_check_accepts_existing_domain_only_adapter_without_worker_db(settings, domain, story):
    from types import SimpleNamespace

    model = ScopeModel()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    adapter = SimpleNamespace(task=worker.task, domain=domain)
    assert scope.enabled(adapter, row)
