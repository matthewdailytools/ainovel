"""New /3 source routing, complete preservation and frozen /2 compatibility."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel import workflow_plan_recipient_scope as scope
from ainovel import workflow_plan_scope_classification as classification
from ainovel.db import DomainError
from ainovel.workflow_plan_preparation import prepare, frozen_contract
from helpers.auto_scope_classification_fixture import AutoScopeClassificationModel
from test_workflow_plan_recipient_scope import ScopeModel, worker_for, inventory
from test_workflow_plan_preparation import CHECK_ACTION


class Scope3Model(ScopeModel):
    def __init__(self, *args, final_verdict="pass", **kwargs):
        super().__init__(*args, **kwargs)
        self.final_verdict = final_verdict

    def answer(self, frame):
        instruction = frame["任务"]
        if instruction.get("动作") == classification.ACTION:
            from helpers.auto_scope_classification_fixture import preservation_output

            self.frames.append(deepcopy(frame))
            return preservation_output(instruction, verdict=self.final_verdict)
        value = super().answer(frame)
        if (
            instruction.get("动作") == "写前解释AI工作章纲"
            and instruction.get("接收范围核对执行版本") == scope.NEW_VERSION
        ):
            for item in value["items"]:
                item.update(scope_binding_status="supported", scope_binding_reason="匿名同一来源工程绑定。")
        return value


def test_new_row_three_requires_complete_final_preservation_and_reuses_it(settings, domain, story):
    model = Scope3Model()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    assert row[scope.KEY] == scope.NEW_VERSION and row["status"] == "ready"
    assert row[classification.RECORDS][-1]["result"]["original_checks"]
    assert [f["任务"]["动作"] for f in model.frames] == [
        scope.ACTION,
        "写前解释AI工作章纲",
        CHECK_ACTION,
        classification.ACTION,
    ]
    assert row["prepared_contract"]["items"][0]["recipient_scope"] == "unspecified"
    count = len(model.frames)
    assert (
        frozen_contract(worker, row["effective_plot"], 1, row["prepared_contract"])
        == row["prepared_contract"]
    )
    assert asyncio.run(prepare(worker, plot, chapter)) and len(model.frames) == count
    assert not domain.chapter(chapter["id"])["content"]


def test_unknown_final_preservation_is_never_dispatched_again(settings, domain, story):
    from ainovel.providers import ModelClient
    from ainovel.prompt_encoding import expand_prompt
    from ainovel.db import dumps
    from test_openai_schema import completed

    model = Scope3Model()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    sent = []

    def handler(request):
        wire = json.loads(request.content)
        sent.append(wire)
        prompt = wire["input"][1]["content"]
        frame = expand_prompt(json.loads(prompt.split("\n只返回")[0]))
        if frame["任务"]["动作"] == classification.ACTION:
            raise httpx.ReadTimeout("anonymous result unknown", request=request)
        return completed(dumps(model.answer(frame)))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError):
        asyncio.run(prepare(worker, plot, chapter))
    before = worker.budget.report()
    assert len(sent) == 4 and not asyncio.run(prepare(worker, plot, chapter))
    assert len(sent) == 4 and worker.budget.report() == before
    assert not domain.chapter(chapter["id"])["content"] and not domain.db.all("SELECT * FROM artifacts")


def test_existing_authorized_one_round_open_choice_reaches_new_full_preservation(settings, domain, story):
    from helpers.auto_scope_classification_fixture import preservation_output
    from helpers.plan_execution_fixture import plan_execution_output
    from test_workflow_plan_open_details import OpenModel, open_worker
    from ainovel.db import dumps
    from ainovel.prompt_encoding import expand_prompt

    class Open3(OpenModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = expand_prompt(json.loads(prompt.split("\n只返回")[0]))
            instruction = frame["任务"]
            action = instruction.get("动作")
            if action in {scope.ACTION, classification.ACTION}:
                self.calls += 1
                self.preparation_frames.append(deepcopy(frame))
                value = (
                    preservation_output(instruction)
                    if action == classification.ACTION
                    else plan_execution_output(instruction)
                )
                return {
                    "text": dumps(value),
                    "usage": {"input": 1, "output": 1},
                    "call_id": f"open3-{self.calls}",
                }
            value = await super().generate(account, task_id, system, prompt, max_output, **kwargs)
            if action == "写前解释AI工作章纲":
                output = json.loads(value["text"])
                for item in output["items"]:
                    item.update(
                        scope_binding_status="supported",
                        scope_binding_reason="原AI开放槽不是范围歧义，按既有授权选择。",
                    )
                value["text"] = dumps(output)
            return value

    model = Open3()
    worker, plot, chapter, task = open_worker(settings, domain, story, model)
    original = deepcopy(worker.task["result"]["plan"])
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    assert row[scope.KEY] == scope.NEW_VERSION and row["round"] == 1 and len(row["choices"]) == 1
    assert row["status"] == "ready" and contract["items"][0]["recipient"] == "学徒"
    assert row[classification.RECORDS][-1]["round"] == 1
    assert model.preparation_frames[-1]["任务"]["动作"] == classification.ACTION
    assert worker.task["result"]["plan"] == original
    assert frozen_contract(worker, effective["plot"], 1, contract) == contract
    assert chapter["content"] == "" and not domain.db.all("SELECT id FROM facts")


@pytest.mark.parametrize("verdict", ["unknown", "fail"])
def test_final_independent_unknown_or_failure_stops_without_body(settings, domain, story, verdict):
    model = Scope3Model(final_verdict=verdict)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert not asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    assert (
        row["status"] == "needs_review"
        and row[classification.RECORDS][-1]["result"]["constraints_check"]["result"] == verdict
    )
    count = len(model.frames)
    assert not asyncio.run(prepare(worker, plot, chapter)) and len(model.frames) == count
    assert not domain.chapter(chapter["id"])["content"] and not domain.db.all("SELECT * FROM artifacts")


def test_genuine_scope_unknown_never_enters_interpretation_or_final_check(settings, domain, story):
    model = Scope3Model(unknown=True)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert not asyncio.run(prepare(worker, plot, chapter))
    assert len(model.frames) == 1 and not domain.chapter(chapter["id"])["content"]


def test_genuine_only_is_not_erased_by_non_scope_guidance(settings, domain, story):
    model = Scope3Model(source="甲只向校核站寄送记录抄本。", only=True)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    assert (
        worker.task["result"]["plot_preparations"]["1"]["prepared_contract"]["items"][0]["recipient_scope"]
        == "only"
    )


@pytest.mark.parametrize(
    "change", ["missing_source", "missing_item", "foreign_evidence", "unknown_classification"]
)
def test_full_meaning_coverage_cannot_be_shortened_or_marked_unknown_ready(settings, domain, story, change):
    model = Scope3Model()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    current = classification.basis(worker, row, plot, row["prepared_contract"])
    value = deepcopy(row[classification.RECORDS][-1]["result"])
    if change == "missing_source":
        value["effective_checks"].pop()
    elif change == "missing_item":
        value["item_checks"].pop()
    elif change == "foreign_evidence":
        value["item_checks"][0]["evidence_ids"] = ["FIX001"]
    else:
        value["effective_checks"][0]["classification_result"] = "unknown"
        assert not classification.passed(value)
        return
    assert classification.issues(value, current)


def test_ready_row_without_its_final_receipt_is_not_reusable(settings, domain, story):
    model = Scope3Model()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    row[classification.RECORDS].clear()
    with pytest.raises(DomainError, match="完整原意保持"):
        frozen_contract(worker, row["effective_plot"], 1, row["prepared_contract"])


def test_whitespace_reason_cannot_pass_any_preservation_group(settings, domain, story):
    model = Scope3Model()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    current = classification.basis(worker, row, plot, row["prepared_contract"])
    for group in ("original_checks", "effective_checks", "item_checks", "fixed_checks"):
        value = deepcopy(row[classification.RECORDS][-1]["result"])
        value[group][0]["reason"] = " \n\t"
        assert classification.issues(value, current)


def test_complete_api_worker_model_helper_routes_new_phase_before_a_candidate(settings, domain, story):
    from ainovel.worker import Worker
    from test_workflow_plan_preparation import queued

    queued_task = queued(domain, story)
    model = AutoScopeClassificationModel()
    assert asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued_task["id"])
    assert task["status"] in {"needs_review", "segment_complete"}, task["error"]
    assert task["result"]["plot_preparations"]["1"][scope.KEY] == scope.NEW_VERSION
    assert (
        task["result"]["plot_preparations"]["1"][classification.RECORDS][-1]["result"]["constraints_check"][
            "result"
        ]
        == "pass"
    )


@pytest.mark.parametrize("version", [scope.VERSION, scope.CURRENT_VERSION])
def test_old_observer_frames_are_identical_to_frozen_baseline(version):
    source = "甲把记录抄一份寄往校核站。"

    class Capture:
        def __init__(self):
            self.frames = []

        async def grounded(self, instruction, schema, context, validator, **kwargs):
            self.frames.append(
                (
                    deepcopy(instruction),
                    schema.model_json_schema(),
                    deepcopy(context),
                    deepcopy(kwargs["contract"]),
                )
            )
            result = inventory(source)["inventory"]
            result["input_sha256"] = instruction["input_sha256"]
            assert not validator(result)
            return result

    # Frozen from ce19fd4c (Source2 archive 7ce6ba04), using this anonymous source.
    # Includes full instruction/context/schema and returned receipt, without
    # needing any private archive or external file when the test runs in CI.
    expected = {
        scope.VERSION: "1e47b03b0f17ae249a8dfb09b3fe281b26c799a902ef1eddd21c4d30f780d3bf",
        scope.CURRENT_VERSION: "3092084b779fda11057787790a61063da229470af85b08a6c92d05d3655ab592",
    }
    captured = Capture()
    result = asyncio.run(scope.observe(captured, source, version=version))
    assert scope.sha({"frames": captured.frames, "result": result}) == expected[version]


def test_failed_trade_story_unknown_and_request_remain_complete_without_fake_scope_unknown(
    settings, domain, story
):
    source = "甲向货商购买铜环，抬价后交易失败，铜环与价款均未交付。乙承认缺行且不知道原因。甲向乙申请查阅公开簿册，尚未获准。"

    class Anonymous(Scope3Model):
        def answer(self, frame):
            instruction = frame["任务"]
            action = instruction.get("动作")
            if action == classification.ACTION:
                return super().answer(frame)
            self.frames.append(deepcopy(frame))
            if action == scope.ACTION:
                units = scope.units(source)
                return {
                    "input_sha256": instruction["input_sha256"],
                    "observations": [
                        {
                            "observation_id": "O01",
                            "kind": "named_destination",
                            "supplier": "货商",
                            "recipient": "甲",
                            "transfer_object": "铜环",
                            "transfer_scope": "attempted",
                            "evidence": units[0]["source_id"],
                            "restriction_evidence": "",
                            "condition": "",
                            "reason": "购买失败但尝试双方及对象明确，无排他原话。",
                        }
                    ],
                    "unit_checks": [
                        {
                            "source_id": unit["source_id"],
                            "status": "covered" if index == 0 else "not_scope_related",
                            "observation_ids": ["O01"] if index == 0 else [],
                            "reason": "否定事件、认知未知与请求保留在完整情节，不虚造接收范围。",
                        }
                        for index, unit in enumerate(units)
                    ],
                    "note": "匿名确定性预设分类，不是真实模型分类质量证明。",
                }
            if action == "写前解释AI工作章纲":
                common = {
                    "certainty": "explicit",
                    "recipient_scope": "unspecified",
                    "scope_condition": "",
                    "open_fields": [],
                    "open_reason": "",
                    "scope_binding_status": "supported",
                    "scope_binding_reason": "工程替身逐项绑定，原无范围的事件仍保留。",
                }
                return {
                    "items": [
                        {
                            **common,
                            "author_evidence": "AUTHOR0001",
                            "actor": "甲",
                            "action": "购买失败",
                            "object": "铜环",
                            "direction": "货商向甲交付的尝试未完成",
                            "result": "交易失败，铜环与价款均未交付",
                            "transfer_object": "铜环",
                            "supplier": "货商",
                            "recipient": "甲",
                            "transfer_scope": "attempted",
                            "scope_observation_ids": ["O01"],
                        },
                        {
                            **common,
                            "author_evidence": "AUTHOR0001",
                            "actor": "乙",
                            "action": "承认缺行",
                            "object": "缺行原因",
                            "direction": "",
                            "result": "不知道原因",
                            "transfer_object": "",
                            "supplier": "",
                            "recipient": "",
                            "transfer_scope": "none",
                            "scope_observation_ids": [],
                        },
                        {
                            **common,
                            "author_evidence": "AUTHOR0001",
                            "actor": "甲",
                            "action": "申请查阅",
                            "object": "公开簿册",
                            "direction": "",
                            "result": "向乙提出请求，尚未获准",
                            "transfer_object": "",
                            "supplier": "",
                            "recipient": "",
                            "transfer_scope": "none",
                            "scope_observation_ids": [],
                        },
                    ],
                    "note": "保留原失败/未知/申请未获准，不补成交、全知或许可。",
                }
            assert action == CHECK_ACTION
            from helpers.plan_execution_fixture import plan_execution_output

            return plan_execution_output(instruction)

    model = Anonymous(source=source)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    assert row["status"] == "ready" and len(row["prepared_contract"]["items"]) == 3
    assert len(row[classification.RECORDS][-1]["result"]["effective_checks"]) == 3
    assert row["prepared_contract"]["items"][0]["transfer_scope"] == "attempted"
    assert row["prepared_contract"]["items"][1]["result"] == "不知道原因"
    assert row["prepared_contract"]["items"][2]["result"] == "向乙提出请求，尚未获准"
    assert not domain.chapter(chapter["id"])["content"]
