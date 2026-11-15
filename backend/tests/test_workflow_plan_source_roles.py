"""Permission is not a document transfer; explicit known-invalid recovery stays bounded."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel.db import DomainError, dumps
from ainovel.plot_audit import open_plan_issues
from ainovel.worker import Worker
from ainovel.providers import ModelClient
from ainovel.workflow_plan_open_details import KEY as OPEN_KEY
from ainovel.workflow_plan_preparation import prepare, sha, frozen_contract
from ainovel.workflow_plan_restart import PlanRestartInput, restart
from ainovel.workflow_plan_source_roles import KEY, VERSION, RepairState
from helpers.plan_execution_fixture import plan_execution_output
from test_openai_schema import completed
from test_workflow_plan_open_details import open_worker

SOURCE = "甲把记录抄一份寄往校核站，回执迟迟未到。师傅只允许甲另抄副本带在身上，原件留在铺内。"


def proposal(*, permission_scope="only", stage="completed", evidence="AUTHOR0001"):
    common = dict(
        author_evidence=evidence,
        certainty="explicit",
        recipient_scope="unspecified",
        scope_condition="",
        open_fields=[],
        open_reason="",
    )
    return {
        "items": [
            {
                **common,
                "actor": "甲",
                "action": "寄往",
                "object": "记录抄本",
                "direction": "甲→校核站",
                "result": "记录已寄出，回执未到",
                "transfer_object": "记录抄本",
                "supplier": "甲",
                "recipient": "校核站",
                "transfer_scope": stage,
            },
            {
                **common,
                "actor": "师傅",
                "action": "只允许",
                "object": "甲另抄副本带在身上",
                "direction": "",
                "result": "仅可另抄副本带在身上；原件留铺",
                "transfer_object": "",
                "supplier": "",
                "recipient": "甲",
                "transfer_scope": "none",
                "recipient_scope": permission_scope,
            },
        ],
        "note": "只解析原工作章纲；不引入正文。",
    }


def resolved(value):
    value = deepcopy(value)
    for row in value["items"]:
        row["author_evidence"] = SOURCE
    return value


def seed_failed(
    settings, domain, story, *, guidance=False, chapters=1, number=1, future_positions=(), account=None
):
    if guidance:
        parent_worker, current, _ = seed_failed(
            settings,
            domain,
            story,
            chapters=chapters,
            number=number,
            future_positions=future_positions,
            account=account,
        )
        restart(domain, parent_worker.budget, current, restart_request(domain, current))
        worker = Worker(settings)
        worker.task = worker.claim()
        worker.attempt = worker.task["attempt"]
        plot = worker.task["result"]["plan"]["chapters"][number - 1]
        chapter = domain.chapter(current["result"]["plot_preparations"][str(number)]["chapter_id"])
    else:
        worker, plot, chapter, _ = open_worker(settings, domain, story, legacy=True)
        plot["plot"] = SOURCE
        if number > 1:
            for position in range(2, number + 1):
                chapter = domain.create_chapter(story["branch_id"], f"第{position}章")
            for prior in domain.db.all("SELECT * FROM chapters WHERE branch_id=?", (story["branch_id"],)):
                if prior["position"] < number:
                    domain.save(prior["id"], 0, "此前已采用的原创验证稿。")
        for position in future_positions:
            domain.create_chapter(story["branch_id"], f"后续第{position}章", position)
        plot["number"] = number
        worker.task["payload"].update(chapters=chapters, budget_limit=1)
        if account is not None:
            worker.task["payload"]["account"] = account
        worker.task["result"]["plan"]["chapters"] = [
            deepcopy(plot)
            if n == number
            else {"number": n, "title": f"第{n}章", "plot": "保留原规划安排", "fixed": []}
            for n in range(1, chapters + 1)
        ]
        worker.task["result"].update(
            next_chapter=number,
            expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
            flow_source_entailment_version="source-flow-entailment/1",
            conditional_book_policy_version="conditional-book-check/1",
            book_applicability_version="book-applicability/1",
        )
        with domain.db.connect(True) as tx:
            tx.execute(
                "UPDATE tasks SET payload=? WHERE id=?", (dumps(worker.task["payload"]), worker.task["id"])
            )
        worker.checkpoint(worker.task["result"])
    wires = []

    def handler(request):
        wires.append(json.loads(request.content))
        output = proposal()
        output["note"] = f"完整但无效的第{len(wires)}次解释"
        if account == "deepseek":
            return deepseek_completed(output)
        return completed(dumps(output))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(prepare(worker, plot, chapter))
    assert len(wires) == 3
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (worker.task["id"],))
    return worker, domain.task(worker.task["id"]), wires


def restart_request(domain, current):
    return PlanRestartInput(
        client_request_id="explicit-role-reprepare",
        reason="显式重做许可与传递字段解释；保留旧失败和费用",
        chapter=current["result"]["next_chapter"],
        base_story_revision=domain.branch(current["branch_id"])["story_revision"],
        base_progress_sha256=sha(current["result"]),
        workflow_plan_source_roles_version=VERSION,
    ).model_dump(exclude_none=True)


def test_permission_only_stays_in_source_action_without_fabricating_transfer():
    invalid = resolved(proposal())
    assert open_plan_issues(invalid, SOURCE)
    valid = resolved(proposal(permission_scope="unspecified"))
    valid["items"][1]["recipient"] = ""
    assert not open_plan_issues(valid, SOURCE)
    assert valid["items"][1]["action"] == "只允许"
    assert "另抄副本" in valid["items"][1]["object"]
    assert valid["items"][1]["supplier"] == valid["items"][1]["transfer_object"] == ""
    assert valid["items"][0]["transfer_scope"] == "completed"


@pytest.mark.parametrize("stage", ["completed", "attempted", "planned"])
def test_true_only_transfer_still_requires_real_different_parties_object_and_stage(stage):
    value = resolved(proposal(permission_scope="unspecified"))
    row = value["items"][0]
    row["recipient_scope"] = "only"
    row["transfer_scope"] = stage
    assert not open_plan_issues(value, SOURCE)
    for field in ("supplier", "recipient", "transfer_object"):
        damaged = deepcopy(value)
        damaged["items"][0][field] = ""
        assert open_plan_issues(damaged, SOURCE)
    row["supplier"] = row["recipient"]
    assert open_plan_issues(value, SOURCE)


def test_repair_feedback_targets_permission_p02_and_records_stage_diff_without_hard_lock():
    state = RepairState(SOURCE, open_plan_issues)
    errors = state.validate(resolved(proposal()))
    assert len(errors) == 1 and errors[0].startswith("P02")
    assert "transfer_scope=none" in errors[0] and "recipient_scope=only" in errors[0]
    assert state.feedback()["先前字段核对未报错的AI提案（不是原话真值）"][0]["item_id"] == "P01"
    corrected = resolved(proposal(permission_scope="unspecified"))
    corrected["items"][1]["recipient"] = ""
    assert not state.validate(corrected)
    corrected["items"][0]["transfer_scope"] = "attempted"
    assert not state.validate(corrected)
    assert state.receipt()["changes"][-1] == {
        "proposal": 3,
        "item_id": "P01",
        "previous": {"transfer_scope": "completed"},
        "current": {"transfer_scope": "attempted"},
    }
    # A semantic stage change is not accepted as true here; full source review decides.
    earlier_wrong = RepairState(SOURCE, open_plan_issues)
    assert earlier_wrong.validate(resolved(proposal(stage="attempted")))
    assert not earlier_wrong.validate(resolved(proposal(stage="completed", permission_scope="unspecified")))
    assert any(
        change["item_id"] == "P01"
        and change["previous"].get("transfer_scope") == "attempted"
        and change["current"].get("transfer_scope") == "completed"
        for change in earlier_wrong.receipt()["changes"]
    )


def test_invalid_source_is_never_anchored_or_silently_normalized():
    state = RepairState(SOURCE, open_plan_issues)
    bad = resolved(proposal())
    bad["items"][0]["author_evidence"] = "不存在的来源"
    assert state.validate(bad)
    assert state.feedback()["先前字段核对未报错的AI提案（不是原话真值）"] == []
    assert bad["items"][1]["recipient_scope"] == "only"


def test_native_preview_post_receipt_preserve_plan_policies_budget_old_steps_and_nonce(
    settings, domain, story, client
):
    worker, current, wires = seed_failed(settings, domain, story)
    old_steps = domain.db.all("SELECT * FROM task_steps")
    with worker.budget.db() as tx:
        old_usage = [dict(r) for r in tx.execute("SELECT * FROM calls")]
    url = f"/api/tasks/{current['id']}"
    preview = client.get(url + "/reprepare-plan-preview")
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data[KEY] == data["request_basis"][KEY] == VERSION
    assert domain.task(current["id"])["result"] == current["result"]
    request = {**restart_request(domain, current), **data["request_basis"]}
    response = client.post(url + "/reprepare-plan", json=request)
    assert response.status_code == 202, response.text
    child = response.json()
    receipt = child["result"]["plan_preparation_restart"]
    assert child["payload"][KEY] == child["result"][KEY] == receipt[KEY] == VERSION
    assert receipt["request"] == request
    assert child["result"]["plan"] == current["result"]["plan"]
    for key in (
        "flow_source_entailment_version",
        "conditional_book_policy_version",
        "book_applicability_version",
        OPEN_KEY,
    ):
        assert child["result"][key] == current["result"][key]
    assert child["payload"]["account"] == current["payload"]["account"]
    assert child["payload"]["budget_limit"] == 1 - sum(c["charged"] for c in old_usage) / 1_000_000
    assert receipt["previous_preparation"] == current["result"]["plot_preparations"]["1"]
    assert child["result"]["plot_preparations"] == {}
    assert domain.db.all("SELECT * FROM task_steps") == old_steps
    with worker.budget.db() as tx:
        assert [dict(r) for r in tx.execute("SELECT * FROM calls")] == old_usage
    assert client.post(url + "/reprepare-plan", json=request).json()["id"] == child["id"]
    assert client.get(url + "/plan-restarts/explicit-role-reprepare").json()["task"]["id"] == child["id"]
    assert (
        client.post(url + "/reprepare-plan", json={**request, "reason": "同nonce不同决定"}).status_code == 409
    )
    legacy = {k: v for k, v in request.items() if k != KEY}
    assert client.post(url + "/reprepare-plan", json=legacy).status_code == 409
    assert len(wires) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "missing_step",
        "missing_dispatch",
        "duplicate_dispatch",
        "wrong_schema",
        "wrong_marker",
        "valid_reply",
        "choice",
        "preservation",
        "round",
        "body",
        "candidate",
        "body_dispatch",
        "other_body_dispatch",
        "active",
    ],
)
def test_open_restart_never_erases_unknown_choices_preservation_or_body(
    settings, domain, story, client, fault
):
    worker, current, _ = seed_failed(settings, domain, story)
    task_id = current["id"]
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
    elif fault in ("missing_dispatch", "duplicate_dispatch", "wrong_schema", "wrong_marker"):
        event = domain.db.all("SELECT * FROM task_events WHERE kind='step_dispatched' LIMIT 1")[0]
        data = json.loads(event["payload"])
        with domain.db.connect(True) as tx:
            if fault == "missing_dispatch":
                tx.execute("DELETE FROM task_events WHERE id=?", (event["id"],))
            elif fault == "duplicate_dispatch":
                domain.db.event(tx, task_id, "step_dispatched", data)
            else:
                if fault == "wrong_schema":
                    data["schema"] = "ScopedAuthorPlot"
                else:
                    data["request"][KEY] = VERSION
                tx.execute("UPDATE task_events SET payload=? WHERE id=?", (dumps(data), event["id"]))
    elif fault == "valid_reply":
        with domain.db.connect(True) as tx:
            step = tx.execute("SELECT * FROM task_steps LIMIT 1").fetchone()
            response = json.loads(step["result"])
            response["text"] = dumps(proposal(permission_scope="unspecified"))
            tx.execute(
                "UPDATE task_steps SET result=? WHERE fingerprint=?", (dumps(response), step["fingerprint"])
            )
    elif fault in ("choice", "preservation", "round"):
        row = current["result"]["plot_preparations"]["1"]
        if fault == "choice":
            row["choices"] = [{"round": 1}]
        elif fault == "preservation":
            row["preservation_checks"] = [{"round": 0}]
        else:
            row["round"] = 1
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(current["result"]), task_id))
    elif fault == "body":
        domain.save(story["chapter_id"], 0, "作者已写")
    elif fault == "candidate":
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO artifacts(id,task_id,novel_id,branch_id,chapter_id,kind,status,basis,payload,created) VALUES('body-candidate',?,?,?,?,'chapter_draft','candidate','{}','{}',0)",
                (task_id, current["novel_id"], current["branch_id"], story["chapter_id"]),
            )
    elif fault in ("body_dispatch", "other_body_dispatch"):
        owner = task_id
        if fault == "other_body_dispatch":
            owner = "other-body"
            with domain.db.connect(True) as tx:
                tx.execute(
                    "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,'module',?,?,'cancelled','{}','{}',0,0)",
                    (owner, current["novel_id"], current["branch_id"]),
                )
        domain.db.emit(
            owner,
            "step_dispatched",
            {"action": "writing.expand_plot", "request": {"target_version": {"id": story["chapter_id"]}}},
        )
    else:
        with domain.db.connect(True) as tx:
            tx.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES('active','module',?,?,'queued','{}','{}',0,0)",
                (current["novel_id"], current["branch_id"]),
            )
    current = domain.task(task_id)
    request = restart_request(domain, current)
    before = domain.db.all("SELECT * FROM tasks")
    usage = worker.budget.report()
    response = client.post(f"/api/tasks/{task_id}/reprepare-plan", json=request)
    assert response.status_code == 409, response.text
    assert domain.db.all("SELECT * FROM tasks") == before
    assert worker.budget.report() == usage
    assert not domain.db.all("SELECT * FROM task_requests WHERE key=?", (request["client_request_id"],))


@pytest.mark.parametrize(
    "damage",
    ["missing_payload", "missing_result", "unknown", "missing_row", "both_missing", "receipt_missing"],
)
def test_new_marker_cannot_be_deleted_or_downgraded(settings, domain, story, damage):
    worker, current, _ = seed_failed(settings, domain, story, guidance=True)
    if damage == "missing_payload":
        worker.task["payload"].pop(KEY)
    elif damage == "missing_result":
        worker.task["result"].pop(KEY)
    elif damage == "unknown":
        worker.task["result"][KEY] = "future"
    elif damage == "both_missing":
        worker.task["payload"].pop(KEY)
        worker.task["result"].pop(KEY)
    elif damage == "receipt_missing":
        worker.task["result"]["plan_preparation_restart"]["request"].pop(KEY)
    else:
        worker.task["result"]["plot_preparations"]["1"].pop(KEY)
    before = worker.client
    with pytest.raises(DomainError, match="版本|政策"):
        asyncio.run(
            prepare(worker, current["result"]["plan"]["chapters"][0], domain.chapter(story["chapter_id"]))
        )
    assert worker.client is before


def test_new_wire_guidance_repair_is_source_only_and_does_not_rewrite_old_cache(
    settings, domain, story, client
):
    worker, current, wires = seed_failed(settings, domain, story, guidance=True)
    events = [
        json.loads(e["payload"])
        for e in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched' ORDER BY id",
            (current["id"],),
        )
    ]
    assert len(events) == 3 and all(e["request"][KEY] == VERSION for e in events)
    assert all(e["schema"] == "OpenPlanInterpretation" for e in events)
    # OpenAI puts the source frame in input; schema descriptions are also real wire.
    prompts = [wire["input"] for wire in wires]
    assert all(VERSION in dumps(prompt) for prompt in prompts)
    assert "许可" in dumps(wires[0]["text"]["format"]["schema"])
    assert "P02" in dumps(prompts[1]) and "completed" in dumps(prompts[1])
    frames = [json.loads(prompt[-1]["content"]) for prompt in prompts]
    assert all(set(frame) == {"任务", "上下文"} for frame in frames)
    assert all(set(frame["上下文"]) == {"scope"} for frame in frames)
    assert all("reason" not in frame["任务"] for frame in frames)
    assert all("本次候选" not in frame["任务"] for frame in frames)
    # An old phase is replayed only from its exact three paid cache entries.
    steps = domain.db.all("SELECT * FROM task_steps")
    used = worker.budget.report()
    resumed = client.post(f"/api/tasks/{current['id']}/resume", json={"acknowledge_unknown": True})
    assert resumed.status_code == 200, resumed.text
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(
            prepare(worker, current["result"]["plan"]["chapters"][0], domain.chapter(story["chapter_id"]))
        )
    assert (
        len(wires) == 3
        and domain.db.all("SELECT * FROM task_steps") == steps
        and worker.budget.report() == used
    )


def test_explicit_child_executes_new_source_interpretation_and_independent_full_preservation(
    settings, domain, story
):
    old_worker, current, _ = seed_failed(settings, domain, story)
    child = restart(domain, old_worker.budget, current, restart_request(domain, current))
    frames = []

    def handler(request):
        wire = json.loads(request.content)
        frame = json.loads(wire["input"][-1]["content"])
        frames.append(frame)
        if frame["任务"]["动作"] == "写前解释AI工作章纲":
            output = proposal(permission_scope="unspecified")
            output["items"][1]["recipient"] = ""
        else:
            assert frame["任务"]["动作"] == "独立核对AI工作章纲保持"
            output = plan_execution_output(frame["任务"])
        return completed(dumps(output))

    worker = Worker(settings, ModelClient(settings, old_worker.budget, httpx.MockTransport(handler)))
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == child["id"]
    plot = child["result"]["plan"]["chapters"][0]
    effective, contract = asyncio.run(prepare(worker, plot, domain.chapter(story["chapter_id"])))
    assert len(frames) == 2
    assert frames[0]["任务"][KEY] == VERSION
    row = domain.task(child["id"])["result"]["plot_preparations"]["1"]
    assert row[KEY] == VERSION and row["status"] == "ready" and row["round"] == 0
    assert row["choices"] == [] and len(row["preservation_checks"]) == 1
    assert len(row["preservation_checks"][0]["result"]["item_checks"]) == 2
    assert effective == plot and contract["items"][0]["transfer_scope"] == "completed"
    assert contract["items"][1]["action"] == "只允许"
    assert frozen_contract(worker, effective["plot"], 1, contract) == contract
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert not domain.db.all("SELECT id FROM artifacts")


def test_transaction_rechecks_body_dispatch_before_new_child(settings, domain, story, monkeypatch):
    from contextlib import contextmanager

    worker, current, _ = seed_failed(settings, domain, story)
    original = domain.db.connect
    injected = False

    @contextmanager
    def racing_connect(write=False):
        nonlocal injected
        if write and not injected:
            injected = True
            domain.db.emit(
                current["id"],
                "step_dispatched",
                {"action": "writing.expand_plot", "request": {"target_version": {"id": story["chapter_id"]}}},
            )
        with original(write) as tx:
            yield tx

    monkeypatch.setattr(domain.db, "connect", racing_connect)
    before = domain.db.all("SELECT * FROM tasks")
    with pytest.raises(DomainError, match="正文"):
        restart(domain, worker.budget, current, restart_request(domain, current))
    assert domain.db.all("SELECT * FROM tasks") == before


def test_full_36_plan_and_preceding_chapters_remain_after_open_chapter5_restart(
    settings, domain, story, client
):
    worker, current, _ = seed_failed(settings, domain, story, chapters=36, number=5)
    chapters_before = domain.db.all("SELECT * FROM chapters")
    preview = client.get(f"/api/tasks/{current['id']}/reprepare-plan-preview")
    assert preview.status_code == 200, preview.text
    request = {**restart_request(domain, current), **preview.json()["request_basis"]}
    child = restart(domain, worker.budget, current, request)
    assert child["result"]["next_chapter"] == 5
    assert child["result"]["plan"] == current["result"]["plan"]
    assert len(child["result"]["plan"]["chapters"]) == 36
    assert domain.db.all("SELECT * FROM chapters") == chapters_before


@pytest.mark.parametrize(
    "first_stage,final_stage,verdict",
    [
        ("attempted", "completed", "pass"),
        ("completed", "attempted", "fail"),
        ("completed", "attempted", "unknown"),
    ],
)
def test_repaired_stage_is_decided_by_independent_all_p_source_review_not_first_model(
    settings, domain, story, first_stage, final_stage, verdict
):
    old_worker, current, _ = seed_failed(settings, domain, story)
    child = restart(domain, old_worker.budget, current, restart_request(domain, current))
    frames = []
    interpretations = 0

    def handler(request):
        nonlocal interpretations
        wire = json.loads(request.content)
        frame = json.loads(wire["input"][-1]["content"])
        instruction = frame["任务"]
        frames.append(frame)
        if instruction["动作"] == "写前解释AI工作章纲":
            interpretations += 1
            output = proposal(
                stage=first_stage if interpretations == 1 else final_stage,
                permission_scope="only" if interpretations == 1 else "unspecified",
            )
        else:
            assert instruction["动作"] == "独立核对AI工作章纲保持"
            assert instruction["原始本章"]["plot"] == SOURCE
            audit = instruction["来源解释修正差异（不是原话真值）"]
            assert audit["version"] == VERSION and audit["proposal_count"] == 2
            assert any(
                change["item_id"] == "P01"
                and change["previous"].get("transfer_scope") == first_stage
                and change["current"].get("transfer_scope") == final_stage
                for change in audit["changes"]
            )
            assert len(instruction["本轮全部事项"]) == 2
            output = plan_execution_output(instruction)
            output["item_checks"][0].update(
                result=verdict, reason="仅据原文寄往且回执未到，寄出与回执是两事件；不把第一次AI字段当真值。"
            )
        return completed(dumps(output))

    worker = Worker(settings, ModelClient(settings, old_worker.budget, httpx.MockTransport(handler)))
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = asyncio.run(
        prepare(worker, child["result"]["plan"]["chapters"][0], domain.chapter(story["chapter_id"]))
    )
    saved = domain.task(child["id"])["result"]["plot_preparations"]["1"]
    assert len(frames) == 3 and interpretations == 2
    assert saved["preservation_checks"][0]["result"]["item_checks"][0]["result"] == verdict
    assert saved["status"] == ("ready" if verdict == "pass" else "needs_review")
    assert (result is not None) == (verdict == "pass")
    assert domain.chapter(story["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT id FROM artifacts")


def deepseek_completed(value):
    from test_budget_provider import stream

    return httpx.Response(
        200,
        content=stream(
            [
                {
                    "model": "isolated-deepseek-flash",
                    "choices": [{"delta": {"content": dumps(value)}, "finish_reason": "stop"}],
                },
                {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
            ],
            True,
        ),
    )


@pytest.mark.parametrize("timeout", [False, True])
def test_deepseek_native_wire_freezes_roles_schema_repairs_and_unknown_no_resend(
    settings, domain, story, client, timeout
):
    from dataclasses import replace

    settings.providers["deepseek"] = replace(
        settings.providers["deepseek"], reasoning="high", structured_output_min_tokens=32768
    )
    old_worker, current, _ = seed_failed(settings, domain, story, account="deepseek")
    child = restart(domain, old_worker.budget, current, restart_request(domain, current))
    sent = []
    interpretations = 0

    def handler(request):
        nonlocal interpretations
        assert request.url.path == "/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 32768
        raw, suffix = body["messages"][1]["content"].split("\n只返回符合以下 JSON Schema 的对象：\n", 1)
        frame = json.loads(raw)
        schema = json.loads(suffix)
        sent.append(frame)
        assert set(frame["上下文"]) == {"scope"} or frame["任务"]["动作"] == "独立核对AI工作章纲保持"
        assert frame["任务"][KEY] == VERSION
        if timeout:
            raise httpx.ReadTimeout("isolated unknown call", request=request)
        if frame["任务"]["动作"] == "写前解释AI工作章纲":
            interpretations += 1
            fields = schema["$defs"]["OpenPlanItem"]["properties"]
            assert fields["author_evidence"]["enum"] == ["AUTHOR0001"]
            assert "许可" in fields["recipient_scope"]["description"]
            output = proposal(
                stage="attempted" if interpretations == 1 else "completed",
                permission_scope="only" if interpretations == 1 else "unspecified",
            )
        else:
            assert frame["任务"]["动作"] == "独立核对AI工作章纲保持"
            assert (
                schema["properties"]["item_checks"]["minItems"]
                == schema["properties"]["item_checks"]["maxItems"]
                == 2
            )
            assert frame["任务"]["来源解释修正差异（不是原话真值）"]["proposal_count"] == 2
            output = plan_execution_output(frame["任务"])
        return deepseek_completed(output)

    worker = Worker(settings, ModelClient(settings, old_worker.budget, httpx.MockTransport(handler)))
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    if timeout:
        with pytest.raises(DomainError):
            asyncio.run(
                prepare(worker, child["result"]["plan"]["chapters"][0], domain.chapter(story["chapter_id"]))
            )
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (child["id"],))
        response = client.get(f"/api/tasks/{child['id']}/reprepare-plan-preview")
        assert response.status_code == 409 and response.json()["code"] == "unknown_result"
        assert len(sent) == 1
        assert (
            restart(domain, worker.budget, domain.task(current["id"]), restart_request(domain, current))["id"]
            == child["id"]
        )
    else:
        result = asyncio.run(
            prepare(worker, child["result"]["plan"]["chapters"][0], domain.chapter(story["chapter_id"]))
        )
        assert result[1]["items"][0]["transfer_scope"] == "completed" and len(sent) == 3
    assert not domain.db.all("SELECT id FROM artifacts")
    assert domain.chapter(story["chapter_id"])["revision"] == 0
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (child["id"],)
    )
    assert len(events) == len(sent) and all(
        json.loads(e["payload"])["request"][KEY] == VERSION for e in events
    )
