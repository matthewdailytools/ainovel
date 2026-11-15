"""New creations benefit by default; old snapshots and inherited policies stay frozen."""

import asyncio
from copy import deepcopy
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from ainovel.db import DomainError, dumps
from ainovel.flow_entailment import VERSION as FLOW_VERSION
from ainovel.models import WorkflowInput
from ainovel.worker import Worker
from ainovel.providers import ModelClient
from ainovel.workflow_plan_preparation import prepare
from ainovel.workflow_plan_recipient_scope import (
    ACTION as SCOPE_ACTION,
    KEY as SCOPE_KEY,
    CURRENT_VERSION as SCOPE_VERSION,
)
from ainovel.workflow_plan_restart import restart
from ainovel.workflow_plan_source_roles import (
    KEY,
    VERSION,
    CREATION_KEY,
    CREATION_VERSION,
    CREATION_EVENT,
    source_payload,
    validate_task,
    sha,
)
from helpers.historical_plan_task import queue_historical_plan
from helpers.plan_execution_fixture import plan_execution_output
from test_openai_schema import completed
from test_conditional_continuation import legacy_checkpoint as legacy_checkpoint
from test_flow_policy_continuation import parent as parent
from test_workflow_plan_source_roles import SOURCE, proposal, deepseek_completed, restart_request


def fresh_payload(story, **changes):
    return WorkflowInput(
        novel_id=story["id"],
        mode="automatic",
        brief="原创整书简报与粗纲，人物调查记录，保留未解原因。",
        account="openai_1",
        plot_contract_check=True,
        chapters=1,
        min_chars=200,
        max_chars=600,
        client_request_id="fresh-role-creation",
        budget_limit=1,
        **changes,
    ).model_dump(exclude_none=True)


def fresh_failed(settings, domain, story, *, client=None, chapters=1, number=1, account="openai_1"):
    body = fresh_payload(story)
    body.update(chapters=chapters, account=account)
    if client is None:
        queued = domain.queue("workflow", body, story["branch_id"])
    else:
        response = client.post("/api/workflows", json=body)
        assert response.status_code == 202, response.text
        queued = response.json()
    branch_id = queued["branch_id"]
    existing = domain.db.all("SELECT * FROM chapters WHERE branch_id=? AND archived=0", (branch_id,))
    for position in range(1, number + 1):
        if not any(c["position"] == position for c in existing):
            domain.create_chapter(branch_id, f"第{position}章", position)
    for prior in domain.db.all(
        "SELECT * FROM chapters WHERE branch_id=? AND position<?", (branch_id, number)
    ):
        domain.save(prior["id"], prior["revision"], "此前已采用的原创验证稿。")
    chapter = domain.db.all("SELECT * FROM chapters WHERE branch_id=? AND position=?", (branch_id, number))[0]
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == queued["id"]
    plot = {"number": number, "title": "本章原创验证", "plot": SOURCE, "fixed": []}
    worker.task["result"].update(
        plan={
            "chapters": [
                deepcopy(plot)
                if n == number
                else {"number": n, "title": f"第{n}章", "plot": "保留原规划安排", "fixed": []}
                for n in range(1, chapters + 1)
            ],
            "ending": "主线逐步闭合",
        },
        next_chapter=number,
        expected_story_revision=domain.branch(branch_id)["story_revision"],
    )
    worker.checkpoint(worker.task["result"])
    sent = []

    def handler(request):
        wire = json.loads(request.content)
        sent.append(wire)
        raw = wire["messages"][1]["content"] if account == "deepseek" else wire["input"][-1]["content"]
        frame = json.loads(raw.split("\n只返回")[0])
        instruction = frame["任务"]
        if instruction["动作"] == SCOPE_ACTION:
            output = plan_execution_output(instruction)
        else:
            assert instruction["动作"] == "写前解释AI工作章纲"
            output = proposal()
            output["note"] = f"完整无效的第{len(sent) - 1}次解释"
            if "接收范围核对执行版本" in instruction:
                for item in output["items"]:
                    item["scope_observation_ids"] = []
                    if instruction["接收范围核对执行版本"] == "workflow-plan-recipient-scope/2":
                        item.update(
                            scope_binding_status="supported",
                            scope_binding_reason="故意无效解释的工程路由，来源业务守卫仍须拒绝。",
                        )
        return deepseek_completed(output) if account == "deepseek" else completed(dumps(output))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(prepare(worker, plot, chapter))
    assert len(sent) == 4
    row = domain.task(queued["id"])["result"]["plot_preparations"][str(number)]
    assert row[SCOPE_KEY] == SCOPE_VERSION
    assert row["phases"]["recipient_scope:0"]["status"] == "completed"
    assert len(row["phases"]["interpret:0"]["before_calls"]) == 1
    assert len(domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (queued["id"],))) == 4
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (queued["id"],))
    return worker, domain.task(queued["id"]), sent


@pytest.mark.parametrize(
    "mode,check,expected", [("automatic", True, True), ("automatic", False, False), ("assisted", True, False)]
)
def test_fresh_creation_policy_scope_is_automatic_preparation_only(domain, story, mode, check, expected):
    payload = fresh_payload(story)
    payload.update(mode=mode, plot_contract_check=check)
    task = domain.queue("workflow", payload, story["branch_id"])
    assert (KEY in task["payload"]) is expected
    assert (KEY in task["result"]) is expected
    rows = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (task["id"], CREATION_EVENT)
    )
    assert len(rows) == int(expected)
    if expected:
        witness = task["payload"][CREATION_KEY]
        assert witness == task["result"][CREATION_KEY]
        assert witness == {
            "version": CREATION_VERSION,
            "task_id": task["id"],
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "guidance_version": VERSION,
            "source_payload_sha256": sha(payload),
        }
        assert json.loads(rows[0]["payload"]) == {
            KEY: VERSION,
            CREATION_KEY: witness,
            "payload_sha256": sha(task["payload"]),
        }
        assert "plan_preparation_restart" not in task["result"]
        assert validate_task(task, domain)


def test_fresh_default_http_nonce_returns_original_witness_without_upgrading_or_new_branch(
    client, domain, story
):
    payload = fresh_payload(story)
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 202, response.text
    first = response.json()
    tasks_before = domain.db.all("SELECT * FROM tasks")
    branches_before = domain.db.all("SELECT * FROM branches")
    events_before = domain.db.all("SELECT * FROM task_events")
    again = client.post("/api/workflows", json=payload)
    assert again.status_code == 202 and again.json() == first
    assert domain.db.all("SELECT * FROM tasks") == tasks_before
    assert domain.db.all("SELECT * FROM branches") == branches_before
    assert domain.db.all("SELECT * FROM task_events") == events_before
    changed = client.post("/api/workflows", json={**payload, "brief": "同nonce不同原话"})
    assert changed.status_code == 409


def test_racing_fresh_creations_return_exact_original_policy_witness(domain, story):
    payload = fresh_payload(story)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = list(
            pool.map(lambda _: domain.queue("workflow", deepcopy(payload), story["branch_id"]), range(2))
        )
    assert tasks[0]["id"] == tasks[1]["id"]
    assert tasks[0]["payload"][CREATION_KEY] == tasks[1]["payload"][CREATION_KEY]
    assert len(domain.db.all("SELECT id FROM tasks")) == 1
    assert len(domain.db.all("SELECT id FROM task_events WHERE kind=?", (CREATION_EVENT,))) == 1


def test_legacy_nonce_and_account_continuation_do_not_gain_current_default(domain, story):
    payload = fresh_payload(story)
    old = queue_historical_plan(domain, payload, story["branch_id"])
    before = domain.db.all("SELECT * FROM task_events")
    same = domain.queue("workflow", payload, story["branch_id"])
    assert same == old and KEY not in same["payload"] and KEY not in same["result"]
    assert domain.db.all("SELECT * FROM task_events") == before
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (old["id"],))
    old = domain.task(old["id"])
    child = domain.continue_workflow(
        old,
        {**old["payload"], "account": "openai_2", "client_request_id": "legacy-no-role-upgrade"},
        {"reason": "显式换账户", "acknowledge_unknown": False},
    )
    assert KEY not in child["payload"] and KEY not in child["result"] and CREATION_KEY not in child["result"]


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
def test_fresh_source_default_and_explicit_restart_keep_creation_distinct_and_prior_ledger(
    settings, domain, story, client, account
):
    worker, current, wires = fresh_failed(
        settings, domain, story, client=client, chapters=36, number=5, account=account
    )
    witness = current["payload"][CREATION_KEY]
    assert witness["task_id"] == current["id"] and "plan_preparation_restart" not in current["result"]
    old_usage = worker.budget.report()
    old_steps = domain.db.all("SELECT * FROM task_steps")
    response = client.get(f"/api/tasks/{current['id']}/reprepare-plan-preview")
    assert response.status_code == 200, response.text
    with worker.budget.db() as tx:
        original_calls = [
            dict(row) for row in tx.execute("SELECT * FROM calls WHERE task_id=?", (current["id"],))
        ]
    assert len(original_calls) == 4 and all(row["status"] == "settled" for row in original_calls)
    assert (
        response.json()["budget"]["source_task_settled"]
        == sum(row["charged"] for row in original_calls) / 1_000_000
    )
    child = restart(
        domain,
        worker.budget,
        current,
        {**restart_request(domain, current), **response.json()["request_basis"]},
    )
    assert child["payload"][CREATION_KEY] == child["result"][CREATION_KEY] == witness
    assert child["payload"]["budget_limit"] == pytest.approx(
        current["payload"]["budget_limit"] - response.json()["budget"]["source_task_settled"]
    )
    assert child["result"]["plan_preparation_restart"]["source_task_id"] == current["id"]
    assert validate_task(child, domain)
    assert worker.budget.report() == old_usage and domain.db.all("SELECT * FROM task_steps") == old_steps
    # A legal later continuation inherits the historical restart, not a fabricated new one.
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (child["id"],))
    child = domain.task(child["id"])
    grandchild = domain.continue_workflow(
        child,
        {
            **child["payload"],
            "account": "openai_2",
            "client_request_id": "creation-then-restart-then-account",
        },
        {"reason": "显式换账户保留全部历史政策", "acknowledge_unknown": False},
    )
    assert grandchild["result"]["plan_preparation_restart"] == child["result"]["plan_preparation_restart"]
    assert grandchild["payload"][CREATION_KEY] == witness and validate_task(grandchild, domain)
    assert worker.budget.report() == old_usage and len(wires) == 4


@pytest.mark.parametrize(
    "damage",
    [
        "payload_marker",
        "result_marker",
        "payload_witness",
        "result_witness",
        "event_missing",
        "event_changed",
        "origin_payload_changed",
        "both_markers_removed",
        "all_metadata_removed",
        "self_restart",
        "wrong_version",
        "wrong_branch",
    ],
)
def test_creation_witness_damage_stops_before_any_model_call(settings, domain, story, damage):
    task = domain.queue("workflow", fresh_payload(story), story["branch_id"])
    payload = deepcopy(task["payload"])
    result = deepcopy(task["result"])
    with domain.db.connect(True) as tx:
        if damage == "payload_marker":
            payload.pop(KEY)
        elif damage == "result_marker":
            result.pop(KEY)
        elif damage == "payload_witness":
            payload.pop(CREATION_KEY)
        elif damage == "result_witness":
            result.pop(CREATION_KEY)
        elif damage == "event_missing":
            tx.execute("DELETE FROM task_events WHERE task_id=? AND kind=?", (task["id"], CREATION_EVENT))
        elif damage == "event_changed":
            tx.execute(
                "UPDATE task_events SET payload='[]' WHERE task_id=? AND kind=?", (task["id"], CREATION_EVENT)
            )
        elif damage == "origin_payload_changed":
            payload["brief"] += "未冻结的改变"
        elif damage == "both_markers_removed":
            payload.pop(KEY)
            result.pop(KEY)
        elif damage == "all_metadata_removed":
            for value in (payload, result):
                value.pop(KEY)
                value.pop(CREATION_KEY)
        elif damage == "self_restart":
            result["plan_preparation_restart"] = {
                "version": "workflow-plan-restart/1",
                KEY: VERSION,
                "source_task_id": task["id"],
                "request": {KEY: VERSION},
            }
        elif damage == "wrong_version":
            payload[CREATION_KEY]["guidance_version"] = "unknown"
            result[CREATION_KEY]["guidance_version"] = "unknown"
        else:
            payload[CREATION_KEY]["branch_id"] = "foreign"
            result[CREATION_KEY]["branch_id"] = "foreign"
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?", (dumps(payload), dumps(result), task["id"])
        )
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    sent = []

    class Never:
        async def generate(self, *args, **kwargs):
            sent.append(True)
            raise AssertionError("must stop before transport")

    worker.client = Never()
    with pytest.raises(DomainError) as caught:
        asyncio.run(worker.call({"动作": "隔离版本检查"}, context={}))
    assert caught.value.code == "plan_source_roles_version"
    assert sent == [] and all(group["calls"] == [] for group in worker.budget.report())


@pytest.mark.parametrize("pretend_chain", [False, True])
def test_same_branch_creation_witness_cannot_be_swapped_between_fresh_sources(domain, story, pretend_chain):
    first = domain.queue("workflow", fresh_payload(story), story["branch_id"])
    payload = fresh_payload(story)
    payload["client_request_id"] = "other-fresh-creation"
    second = domain.queue("workflow", payload, story["branch_id"])
    new_payload = deepcopy(second["payload"])
    result = deepcopy(second["result"])
    new_payload[CREATION_KEY] = deepcopy(first["payload"][CREATION_KEY])
    result[CREATION_KEY] = deepcopy(first["result"][CREATION_KEY])
    if pretend_chain:
        result["continued_from"] = first["id"]
    second = {**second, "payload": new_payload, "result": result}
    with pytest.raises(DomainError, match="来源|创建"):
        validate_task(second, domain)


def test_conditional_legacy_continuation_never_invokes_fresh_default(client, legacy_checkpoint):
    response = client.post(
        f"/api/tasks/{legacy_checkpoint['id']}/continue-conditional-policy",
        json={"client_request_id": "legacy-cond-no-default-roles", "reason": "显式条件政策续跑"},
    )
    assert response.status_code == 202, response.text
    child = response.json()
    assert KEY not in child["payload"] and KEY not in child["result"] and CREATION_KEY not in child["result"]


def test_flow_legacy_continuation_never_invokes_fresh_default(client, parent):
    preview = client.get(f"/api/tasks/{parent['id']}/continue-flow-policy-preview")
    assert preview.status_code == 200, preview.text
    response = client.post(
        f"/api/tasks/{parent['id']}/continue-flow-policy",
        json={
            "client_request_id": "legacy-flow-no-default-roles",
            "reason": "显式流向政策续跑",
            "preview_basis_sha256": preview.json()["preview_basis_sha256"],
            "flow_source_entailment_version": FLOW_VERSION,
        },
    )
    assert response.status_code == 202, response.text
    child = response.json()
    assert KEY not in child["payload"] and KEY not in child["result"] and CREATION_KEY not in child["result"]


def test_internal_account_continuation_copies_frozen_server_fields_when_caller_omits_them(domain, story):
    task = domain.queue("workflow", fresh_payload(story), story["branch_id"])
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (task["id"],))
    current = domain.task(task["id"])
    child = domain.continue_workflow(
        current,
        {
            **source_payload(current["payload"]),
            "account": "openai_2",
            "client_request_id": "fresh-account-inherits-server-witness",
        },
        {"reason": "显式换账户，不修改内部政策", "acknowledge_unknown": False},
    )
    assert child["payload"][KEY] == VERSION
    assert child["payload"][CREATION_KEY] == current["payload"][CREATION_KEY]
    assert validate_task(child, domain)


@pytest.mark.parametrize("action,check", [("writing.advise", False), ("writing.expand_plot", True)])
def test_local_module_does_not_receive_open_plan_guidance(domain, story, action, check):
    from ainovel.models import ModuleInput

    payload = ModuleInput(
        action=action,
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        instruction="只给写作建议。",
        account="openai_1",
        plot_contract_check=check,
    ).model_dump()
    task = domain.queue("module", payload, story["branch_id"])
    assert KEY not in task["payload"] and KEY not in task["result"]
    assert not domain.db.all(
        "SELECT id FROM task_events WHERE task_id=? AND kind=?", (task["id"], CREATION_EVENT)
    )


@pytest.mark.parametrize("scope", ["run", "global"])
def test_real_fresh_run_limit_and_shared_cap_are_distinct_zero_dispatch_boundaries(
    settings, domain, story, scope
):
    from decimal import Decimal

    payload = fresh_payload(story)
    if scope == "run":
        payload["budget_limit"] = 0.000001
    else:
        settings.limits["openai"] = Decimal("0")
    task = domain.queue("workflow", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    plot = {"number": 1, "title": "预算边界", "plot": SOURCE, "fixed": []}
    worker.task["result"].update(plan={"chapters": [plot], "ending": "后续核验"}, next_chapter=1)
    worker.checkpoint(worker.task["result"])
    sent = []

    def unexpected(request):
        sent.append(True)
        raise AssertionError("cap must stop before HTTP")

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(unexpected))
    with pytest.raises(DomainError) as caught:
        asyncio.run(prepare(worker, plot, domain.chapter(story["chapter_id"])))
    assert caught.value.code == "budget" and sent == []
    report = worker.budget.report()[0]
    assert report["calls"] == [] and report["settled"] == 0
    assert report["remaining"] == (20 if scope == "run" else 0)
    assert domain.task(task["id"])["payload"] == task["payload"]
    row = domain.task(task["id"])["result"]["plot_preparations"]["1"]
    assert row[SCOPE_KEY] == SCOPE_VERSION
    assert "interpret:0" not in row["phases"]
    phase = row["phases"]["recipient_scope:0"]
    assert phase["status"] == "not_sent" and phase["not_sent_code"] == "budget"


@pytest.mark.parametrize("key", [KEY, CREATION_KEY])
def test_legacy_account_continuation_rejects_injected_server_policy(domain, story, key):
    old = queue_historical_plan(domain, fresh_payload(story), story["branch_id"])
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (old["id"],))
    old = domain.task(old["id"])
    forged = {
        **old["payload"],
        "account": "openai_2",
        "client_request_id": "legacy-injected-policy",
        key: VERSION if key == KEY else {"version": CREATION_VERSION},
    }
    before = domain.db.all("SELECT * FROM tasks")
    with pytest.raises(DomainError) as caught:
        domain.continue_workflow(old, forged, {"reason": "隔离注入反例", "acknowledge_unknown": False})
    assert caught.value.code == "plan_source_roles_version"
    assert domain.db.all("SELECT * FROM tasks") == before
