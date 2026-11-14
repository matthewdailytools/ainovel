"""Engineering boundaries for before-prose AI work-plan preparation, not quality scores."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from ainovel.db import DomainError, dumps
from ainovel.plot_audit import audit_plot
from ainovel.worker import Worker
from ainovel.providers import ModelClient
from ainovel.workflow_plan_preparation import (
    CHECK_ACTION,
    CHOICE_ACTION,
    VERSION,
    frozen_contract,
    prepare,
    preservation_issues,
)
from helpers.plan_execution_fixture import plan_execution_output
from test_plot_review_shared import PlotModel
from test_workflow_scope import workflow_payload
from test_openai_schema import completed


class PreparationModel(PlotModel):
    def __init__(self, *, ambiguous_until=-1, item_count=1, preservation="pass", omission=False):
        super().__init__("pass")
        self.ambiguous_until = ambiguous_until
        self.item_count = item_count
        self.preservation = preservation
        self.omission = omission
        self.preparation_frames = []
        self.before_generation = None

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("动作")
        output = plan_execution_output(instruction)
        if output is None:
            if action == "按章纲写完整正文" and self.before_generation:
                self.before_generation(instruction)
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        self.preparation_frames.append(deepcopy(frame))
        if action == "写前解释AI工作章纲":
            round_number = instruction["写前准备契约"]["round"]
            if round_number == 0:
                output["items"] *= self.item_count
            for item in output["items"]:
                item["certainty"] = "ambiguous" if round_number <= self.ambiguous_until else "explicit"
        elif action == CHECK_ACTION:
            output["constraints_check"]["result"] = self.preservation
            if self.omission:
                output["item_checks"] = output["item_checks"][:-1]
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": f"prepare-{self.calls}"}


def queued(domain, story, *, mode="automatic", enabled=True):
    payload = workflow_payload(story, mode=mode)
    payload.update(plot_contract_check=enabled, client_request_id="prep-original")
    return domain.queue("workflow", payload, story["branch_id"])


def claimed(settings, domain, story, *, model=None):
    task = queued(domain, story)
    worker = Worker(settings, model or PreparationModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    plot = {
        "number": 1,
        "title": "测点",
        "plot": "沈砚测量并记录，原因继续未知，只向师傅说明怀疑，尚未认定造假者。",
        "fixed": ["母亲下落未知，不得改为已故"],
    }
    worker.task["result"].update(
        plan={"chapters": [plot], "ending": "后卷才核验原因"},
        next_chapter=1,
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
    )
    worker.checkpoint(worker.task["result"])
    return worker, plot, domain.chapter(story["chapter_id"]), task


@pytest.mark.parametrize(
    "mode,enabled,expected",
    [("automatic", True, True), ("assisted", True, False), ("automatic", False, False)],
)
def test_server_freezes_policy_only_for_new_eligible_tasks(domain, story, mode, enabled, expected):
    task = queued(domain, story, mode=mode, enabled=enabled)
    assert ("workflow_plan_preparation_version" in task["result"]) is expected
    if expected:
        assert task["result"]["workflow_plan_preparation_version"] == VERSION
    assert "workflow_plan_preparation_version" not in task["payload"]


@pytest.mark.parametrize("ambiguous_until,rounds", [(-1, 0), (0, 1), (1, 2)])
def test_new_automatic_prepares_before_prose_keeps_original_and_reuses_frozen_interpretation(
    settings, domain, story, ambiguous_until, rounds
):
    task = queued(domain, story)
    model = PreparationModel(ambiguous_until=ambiguous_until)
    before = []

    def assert_ready(instruction):
        saved = domain.task(task["id"])["result"]
        row = saved["plot_preparations"]["1"]
        assert row["status"] == "ready" and row["round"] == rounds
        assert saved["plan"]["chapters"][0]["plot"] == "测量并记录"
        assert instruction["本章"]["plot"] == row["effective_plot"]
        before.append(True)

    model.before_generation = assert_ready
    asyncio.run(Worker(settings, model).run_once())
    saved = domain.task(task["id"])
    assert saved["status"] == "segment_complete", saved["error"]
    assert before == [True]
    row = saved["result"]["plot_preparations"]["1"]
    assert len(row["choices"]) == rounds
    assert len(row["interpretations"]) == rounds + 1
    assert len(row["preservation_checks"]) == rounds + 1
    assert row["effective_plot"].startswith(row["original_plot"])
    artifact = domain.artifact(saved["result"]["latest_artifact"])
    assert artifact["payload"]["plot_audit"]["items"][0]["author_evidence"] == "测量并记录"
    assert artifact["payload"]["plot_audit"]["source"]["text_sha256"] == row["effective_plot_sha256"]
    assert not any(f["任务"].get("动作") == "仅依据作者原话整理本场景情节" for f in model.frames)
    for frame in model.preparation_frames:
        assert "本次候选" not in dumps(frame) and "沈砚向陆栖画出" not in dumps(frame)
        assert "recent" not in frame["上下文"] and "chapter" not in frame["上下文"]
        assert "genre_profile" not in frame["上下文"]


@pytest.mark.parametrize("verdict", ["fail", "unknown"])
def test_failed_or_unknown_preservation_stops_before_draft_and_never_claims_revisions(
    settings, domain, story, verdict
):
    task = queued(domain, story)
    model = PreparationModel(preservation=verdict)
    asyncio.run(Worker(settings, model).run_once())
    saved = domain.task(task["id"])
    assert saved["status"] == "needs_review", saved["error"]
    assert "未生成正文" in saved["error"] and "有限修订" not in saved["error"]
    assert "waiting_artifact" not in saved["result"]
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert not any(f["任务"].get("动作") == "按章纲写完整正文" for f in model.frames)


def test_at_most_two_choices_persist_rounds_and_resume_does_not_restart(settings, domain, story, client):
    task = queued(domain, story)
    model = PreparationModel(ambiguous_until=20)
    asyncio.run(Worker(settings, model).run_once())
    saved = domain.task(task["id"])
    row = saved["result"]["plot_preparations"]["1"]
    assert saved["status"] == "needs_review" and row["round"] == 2
    assert len(row["choices"]) == 2 and len(row["interpretations"]) == 3
    calls = model.calls
    assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 200
    asyncio.run(Worker(settings, model).run_once())
    again = domain.task(task["id"])
    assert again["status"] == "needs_review" and model.calls == calls
    assert again["result"]["plot_preparations"]["1"] == row


@pytest.mark.parametrize("marker", ["future/2", None, True, {}])
def test_unknown_marker_rejected_before_any_dispatch(settings, domain, story, marker):
    task = queued(domain, story)
    result = task["result"] | {"workflow_plan_preparation_version": marker}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    model = PreparationModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.calls == 0
    assert not domain.db.all(
        "SELECT id FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
    )


@pytest.mark.parametrize("version", ["author-plot-audit/1", None, "future/3"])
def test_new_preparation_marker_cannot_bypass_required_plot_contract_version(
    settings, domain, story, version
):
    task = queued(domain, story)
    result = {**task["result"], "plot_contract_version": version}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    model = PreparationModel()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "blocked_runtime"
    assert model.calls == 0


@pytest.mark.parametrize("bad", ["drop6", "drop8", "duplicate", "merge", "reorder"])
def test_cannot_drop_or_merge_original_p06_p08_to_remove_ambiguity(bad):
    contract = {"items": [{} for _ in range(9)]}
    rows = [
        {"item_id": f"P{i:02}", "prepared_item_id": f"P{i:02}", "result": "pass", "reason": "核对"}
        for i in range(1, 10)
    ]
    final = deepcopy(contract)
    if bad.startswith("drop"):
        rows.pop(int(bad[4:]) - 1)
        final["items"].pop()
    elif bad == "duplicate":
        rows[7] = rows[5]
    elif bad == "merge":
        rows[7]["prepared_item_id"] = "P06"
    else:
        rows[5], rows[7] = rows[7], rows[5]
    assert preservation_issues(
        {"item_checks": rows, "constraints_check": {"result": "pass", "reason": "核对"}}, contract, final
    )


def test_nine_original_items_including_p06_p08_remain_in_final_audit(settings, domain, story):
    worker, plot, chapter, _ = claimed(
        settings, domain, story, model=PreparationModel(item_count=9, ambiguous_until=0)
    )
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    assert len(contract["items"]) == 9
    assert [x["item_id"] for x in row["preservation_checks"][-1]["result"]["item_checks"]] == [
        f"P{i:02}" for i in range(1, 10)
    ]
    assert frozen_contract(worker, effective["plot"], 1, contract) == contract


@pytest.mark.parametrize("existing", ["saved", "candidate"])
def test_preparation_never_starts_after_seeing_existing_manuscript_or_candidate(
    settings, domain, story, existing
):
    worker, plot, chapter, _ = claimed(settings, domain, story)
    if existing == "saved":
        chapter = domain.save(chapter["id"], 0, "已经写出的正文不能反推计划。")
    else:
        domain.stage(
            {
                "novel_id": story["id"],
                "branch_id": story["branch_id"],
                "chapter_id": chapter["id"],
                "base_revision": 0,
            },
            "chapter_draft",
            {"text": "已有候选"},
            {},
        )
    with pytest.raises(DomainError, match="不能事后"):
        asyncio.run(prepare(worker, plot, chapter))
    assert worker.client.calls == 0


@pytest.mark.parametrize("change", ["original", "fixed", "plan", "effective", "contract", "scope", "target"])
def test_frozen_contract_is_bound_to_original_plan_conditions_and_target(settings, domain, story, change):
    worker, plot, chapter, _ = claimed(settings, domain, story)
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    if change == "original":
        worker.task["result"]["plan"]["chapters"][0]["plot"] += "改写"
    elif change == "fixed":
        worker.task["payload"]["fixed"].append("提前解谜")
    elif change == "plan":
        worker.task["result"]["plan"]["ending"] = "本章解谜"
    elif change == "effective":
        row["effective_plot"] += "事后附注"
    elif change == "contract":
        contract["items"][0]["result"] = "已认定造假者"
    elif change == "scope":
        worker.task["payload"]["completion_scope"] = "whole_book"
    else:
        row["chapter_id"] = domain.create_chapter(story["branch_id"], "另一章")["id"]
    with pytest.raises(DomainError):
        frozen_contract(worker, effective["plot"], 1, contract)


def test_author_plot_cannot_use_workflow_frozen_contract(settings, domain, story):
    worker, plot, chapter, _ = claimed(settings, domain, story)
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    with pytest.raises(DomainError, match="不能替代作者"):
        asyncio.run(
            audit_plot(
                worker, effective["plot"], "正文", 1, source_kind="author_plot", prepared_contract=contract
            )
        )


def test_initial_interpretation_distinguishes_narrative_unknown_without_solving_it(settings, domain, story):
    model = PreparationModel()
    worker, plot, chapter, _ = claimed(settings, domain, story, model=model)
    asyncio.run(prepare(worker, plot, chapter))
    instruction = model.preparation_frames[0]["任务"]
    assert "明确保留多种可能" in instruction["执行明确性"] and "绝不能" in instruction["执行明确性"]
    assert "原因继续未知" in instruction["本章工作章纲（AI规划）"]
    check = next(f["任务"] for f in model.preparation_frames if f["任务"].get("动作") == CHECK_ACTION)
    assert check["不可更改的来源"]["本章原固定条件"] == ["母亲下落未知，不得改为已故"]
    assert "不得提前解谜" in check["要求"]


def test_finished_step_crash_before_receipt_reuses_paid_cache_without_reissuing(
    settings, domain, story, monkeypatch
):
    model = PreparationModel()
    worker, plot, chapter, task = claimed(settings, domain, story, model=model)
    original = worker.checkpoint

    def crash(result, *args, **kwargs):
        row = result.get("plot_preparations", {}).get("1", {})
        if row.get("phases", {}).get("interpret:0", {}).get("status") == "completed":
            raise RuntimeError("crash-after-paid-step-before-receipt")
        return original(result, *args, **kwargs)

    monkeypatch.setattr(worker, "checkpoint", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(prepare(worker, plot, chapter))
    assert model.calls == 1
    restored = domain.task(task["id"])
    assert "response" not in restored["result"]["plot_preparations"]["1"]["phases"]["interpret:0"]
    worker.task = restored
    monkeypatch.setattr(worker, "checkpoint", original)
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    assert model.calls == 2  # one new independent check; interpretation comes from paid cache
    assert frozen_contract(worker, effective["plot"], 1, contract) == contract


def test_dispatch_checkpoint_without_complete_cache_stops_without_new_calls(
    settings, domain, story, monkeypatch
):
    worker, plot, chapter, task = claimed(settings, domain, story)

    async def interrupted(*args, **kwargs):
        raise DomainError("timeout", "未知结果，不重发")

    monkeypatch.setattr(worker, "grounded", interrupted)
    with pytest.raises(DomainError):
        asyncio.run(prepare(worker, plot, chapter))
    restored = domain.task(task["id"])
    next_worker = Worker(settings, PreparationModel())
    next_worker.task, next_worker.attempt = restored, worker.attempt
    assert asyncio.run(prepare(next_worker, plot, chapter)) is None
    row = domain.task(task["id"])["result"]["plot_preparations"]["1"]
    assert row["status"] == "needs_review" and "未自动重发" in row["reason"]
    assert next_worker.client.calls == 0 and row["round"] == 0


def test_legacy_nonce_replay_does_not_add_policy(domain, story):
    task = queued(domain, story)
    old = deepcopy(task["result"])
    old.pop("workflow_plan_preparation_version")
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(old), task["id"]))
    replayed = domain.queue("workflow", task["payload"], story["branch_id"])
    assert replayed["id"] == task["id"] and replayed["result"] == old


@pytest.mark.parametrize("legacy", [True, False])
def test_account_continuation_inherits_frozen_policy_including_absence(settings, domain, story, legacy):
    task = queued(domain, story)
    result = deepcopy(task["result"])
    if legacy:
        result.pop("workflow_plan_preparation_version")
    result["expected_story_revision"] = domain.branch(story["branch_id"])["story_revision"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused',result=? WHERE id=?", (dumps(result), task["id"]))
    current = domain.task(task["id"])
    payload = {**current["payload"], "account": "openai_2", "client_request_id": "new-account"}
    continued = domain.continue_workflow(
        current, payload, {"reason": "明确换账户", "acknowledge_unknown": True}
    )
    assert continued["result"].get("workflow_plan_preparation_version") == result.get(
        "workflow_plan_preparation_version"
    )
    assert ("workflow_plan_preparation_version" in continued["result"]) is (not legacy)


@pytest.mark.parametrize("kind", ["configuration", "budget", "unpriced", "context_limit"])
@pytest.mark.parametrize("switch_account", [False, True])
def test_real_adapter_proven_not_sent_failures_can_explicitly_resume_without_resetting_rounds(
    settings, domain, story, kind, switch_account
):
    worker, plot, chapter, task = claimed(settings, domain, story)
    provider = settings.providers["openai_1"]
    if kind == "configuration":
        settings.providers["openai_1"] = replace(provider, key="")
    elif kind == "budget":
        worker.task["payload"]["budget_limit"] = 0
    elif kind == "unpriced":
        settings.providers["openai_1"] = replace(provider, input_rate=None)
    else:
        settings.providers["openai_1"] = replace(provider, max_input_bytes=1)
    bodies = []

    def handler(request):
        bodies.append(request.content)
        instruction = json.loads(json.loads(request.content)["input"][1]["content"])["任务"]
        return completed(dumps(plan_execution_output(instruction)))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError) as caught:
        asyncio.run(prepare(worker, plot, chapter))
    assert caught.value.code == kind and not bodies
    result = domain.task(task["id"])["result"]
    assert result["plot_preparations"]["1"]["phases"]["interpret:0"]["status"] == "not_sent"
    assert worker.budget.report()[0]["settled"] == 0
    settings.providers["openai_1"] = provider
    # These are the existing explicit user recovery paths, not a worker retry.
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (task["id"],))
    if switch_account:
        current = domain.task(task["id"])
        payload = {**current["payload"], "account": "openai_2", "client_request_id": "resume-not-sent"}
        resumed = domain.continue_workflow(
            current, payload, {"reason": "已核对未派发并明确换账户", "acknowledge_unknown": True}
        )
    else:
        resumed = domain.task(task["id"])
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET status='queued' WHERE id=?", (resumed["id"],))
    next_worker = Worker(settings)
    next_worker.client = ModelClient(settings, next_worker.budget, httpx.MockTransport(handler))
    next_worker.task = next_worker.claim()
    next_worker.attempt = next_worker.task["attempt"]
    effective, contract = asyncio.run(prepare(next_worker, plot, chapter))
    row = next_worker.task["result"]["plot_preparations"]["1"]
    assert row["round"] == 0 and len(row["choices"]) == 0
    assert row["phases"]["interpret:0"]["not_sent_history"][0]["code"] == kind
    assert len(bodies) == 2 and frozen_contract(next_worker, effective["plot"], 1, contract) == contract


@pytest.mark.parametrize("boundary", ["pause", "cancel", "lease", "attempt", "source"])
def test_stop_and_source_guards_between_preparation_steps(settings, domain, story, boundary):
    model = PreparationModel()
    worker, plot, chapter, task = claimed(settings, domain, story, model=model)
    original = model.generate

    async def after_response(*args, **kwargs):
        response = await original(*args, **kwargs)
        with domain.db.connect(True) as tx:
            if boundary == "pause":
                tx.execute("UPDATE tasks SET pause=1 WHERE id=?", (task["id"],))
            elif boundary == "cancel":
                tx.execute("UPDATE tasks SET cancel=1,status='cancelled' WHERE id=?", (task["id"],))
            elif boundary == "lease":
                tx.execute("UPDATE tasks SET lease_until=0 WHERE id=?", (task["id"],))
            elif boundary == "attempt":
                tx.execute("UPDATE tasks SET attempt='new-attempt' WHERE id=?", (task["id"],))
            else:
                tx.execute(
                    "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (story["branch_id"],)
                )
        return response

    model.generate = after_response
    if boundary == "pause":
        assert asyncio.run(prepare(worker, plot, chapter)) is None
        assert domain.task(task["id"])["status"] == "paused"
    else:
        with pytest.raises(DomainError) as caught:
            asyncio.run(prepare(worker, plot, chapter))
        assert caught.value.code == ("story_changed" if boundary == "source" else "cancelled")
    assert model.calls == 1
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


def test_early_revelation_addendum_is_retained_but_rejected_by_independent_preservation(
    settings, domain, story
):
    model = PreparationModel(ambiguous_until=0)
    worker, plot, chapter, _ = claimed(settings, domain, story, model=model)
    original = model.generate

    async def reveal(*args, **kwargs):
        response = await original(*args, **kwargs)
        frame = json.loads(args[3].split("\n只返回")[0])["任务"]
        if frame.get("动作") == CHOICE_ACTION:
            response["text"] = dumps(
                {"addendum": "本章立即确认造假者并说明全部真相。", "rationale": "错误的提前解谜工程负例"}
            )
        elif frame.get("动作") == CHECK_ACTION and "立即确认造假者" in frame["有效章纲"]:
            output = json.loads(response["text"])
            output["item_checks"][0].update(result="fail", reason="原文明确尚未认定，附注却提前解谜。")
            response["text"] = dumps(output)
        return response

    model.generate = reveal
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    row = worker.task["result"]["plot_preparations"]["1"]
    assert row["round"] == 1 and row["status"] == "needs_review"
    assert "立即确认造假者" in row["choices"][0]["addendum"]
    assert row["preservation_checks"][-1]["result"]["item_checks"][0]["result"] == "fail"
    assert worker.task["result"]["plan"]["chapters"][0] == plot


def test_frozen_explanation_survives_prose_revision_and_account_continuation(
    settings, domain, story, monkeypatch
):
    model = PreparationModel(ambiguous_until=0)
    worker, plot, chapter, task = claimed(settings, domain, story, model=model)
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    receipt = deepcopy(worker.task["result"]["plot_preparations"]["1"])
    # An adopted chapter or a later full-book repair cannot cause new choices.
    domain.save(chapter["id"], 0, "已采用的正文待复检。")
    worker.task["result"]["expected_story_revision"] = domain.branch(story["branch_id"])["story_revision"]
    worker.checkpoint(worker.task["result"], "paused")
    current = domain.task(task["id"])
    continued = domain.continue_workflow(
        current,
        {**current["payload"], "account": "openai_2", "client_request_id": "frozen-account"},
        {"reason": "保留冻结解释续跑", "acknowledge_unknown": True},
    )
    next_worker = Worker(settings, model)
    next_worker.task = next_worker.claim()
    next_worker.attempt = next_worker.task["attempt"]
    calls_before = model.calls
    again, explanation = asyncio.run(prepare(next_worker, plot, domain.chapter(chapter["id"])))
    assert again == effective and explanation == contract and model.calls == calls_before
    assert next_worker.task["id"] == continued["id"]
    assert next_worker.task["result"]["plot_preparations"]["1"] == receipt


def test_new_schema_inventory_remains_supported_and_request_metadata_names_policy(settings, domain, story):
    from ainovel.openai_schema import strict_schema
    from ainovel.workflow_plan_preparation import PlanChoice, PlanPreservation

    for schema in (PlanChoice, PlanPreservation):
        assert strict_schema(schema.model_json_schema())["type"] == "object"
    worker, plot, chapter, task = claimed(settings, domain, story)
    asyncio.run(prepare(worker, plot, chapter))
    requests = [
        json.loads(r["payload"])["request"]
        for r in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
        )
    ]
    assert requests and all(r["workflow_plan_preparation_version"] == VERSION for r in requests)


def test_real_unknown_timeout_keeps_money_hold_and_cannot_be_relabelled_not_sent(settings, domain, story):
    worker, plot, chapter, task = claimed(settings, domain, story)
    captured = []

    def timeout(request):
        captured.append(request.content)
        raise httpx.ReadTimeout("unknown after send", request=request)

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(timeout))
    with pytest.raises(DomainError):
        asyncio.run(prepare(worker, plot, chapter))
    before = worker.budget.report()[0]
    assert len(captured) == 1 and before["reserved_unknown"] > 0
    restored = domain.task(task["id"])
    assert restored["result"]["plot_preparations"]["1"]["phases"]["interpret:0"]["status"] == "started"
    worker.task = restored
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    assert len(captured) == 1
    assert worker.budget.report()[0]["reserved_unknown"] == before["reserved_unknown"]


def test_known_local_error_after_a_reserved_call_does_not_claim_whole_phase_unsent(settings, domain, story):
    worker, plot, chapter, task = claimed(settings, domain, story)

    class ReservedFailure:
        async def generate(self, account, task_id, *args, **kwargs):
            worker.budget.reserve(settings.providers[account], task_id, 100, 4096)
            raise DomainError("budget", "工程故障：后续修复被预算阻止，之前调用结果未知")

    worker.client = ReservedFailure()
    with pytest.raises(DomainError):
        asyncio.run(prepare(worker, plot, chapter))
    slot = domain.task(task["id"])["result"]["plot_preparations"]["1"]["phases"]["interpret:0"]
    assert slot["status"] == "started" and "not_sent_code" not in slot


def test_two_prose_repairs_reuse_the_same_pre_prose_contract(settings, domain, story, monkeypatch):
    task = queued(domain, story)
    model = PreparationModel(ambiguous_until=0)
    model.verdict = "fail"
    repairs = []

    async def revise(self, text, instructions, review, *args):
        repairs.append(deepcopy(review["plot_audit"]["items"]))
        return text + f"第{len(repairs)}轮正文修订。"

    monkeypatch.setattr(Worker, "revise_chapter", revise)
    asyncio.run(Worker(settings, model).run_once())
    saved = domain.task(task["id"])
    assert saved["status"] == "needs_review", saved["error"]
    row = saved["result"]["plot_preparations"]["1"]
    assert len(repairs) == 2 and repairs[0] == repairs[1]
    assert len(row["interpretations"]) == 2 and len(row["choices"]) == 1
    assert sum(f["任务"].get("动作") == "逐项对照作者情节解释与本次正文" for f in model.frames) == 3
    assert not any(f["任务"].get("动作") == "仅依据作者原话整理本场景情节" for f in model.frames)


def test_pending_phase_account_switch_is_rejected_without_locking_original_and_cache_can_recover(
    settings, domain, story, monkeypatch
):
    worker, plot, chapter, task = claimed(settings, domain, story)
    original = worker.checkpoint

    def crash(result, *args, **kwargs):
        if (
            result.get("plot_preparations", {})
            .get("1", {})
            .get("phases", {})
            .get("interpret:0", {})
            .get("status")
            == "completed"
        ):
            raise RuntimeError("after-paid-completion-before-receipt")
        return original(result, *args, **kwargs)

    monkeypatch.setattr(worker, "checkpoint", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(prepare(worker, plot, chapter))
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (task["id"],))
    current = domain.task(task["id"])
    with pytest.raises(DomainError, match="先在原账户"):
        domain.continue_workflow(
            current,
            {**current["payload"], "account": "openai_2", "client_request_id": "must-not-create"},
            {"reason": "核对换账户", "acknowledge_unknown": True},
        )
    assert not domain.db.all("SELECT task_id FROM task_requests WHERE key='must-not-create'")
    unchanged = domain.task(task["id"])
    assert unchanged["status"] == "blocked_runtime" and "continued_to" not in unchanged["result"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='running' WHERE id=?", (task["id"],))
    worker.task = domain.task(task["id"])
    monkeypatch.setattr(worker, "checkpoint", original)
    effective, contract = asyncio.run(prepare(worker, plot, chapter))
    assert worker.client.calls == 2
    assert frozen_contract(worker, effective["plot"], 1, contract) == contract
