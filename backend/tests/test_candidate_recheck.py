"""Recheck an edited, stopped workflow candidate without writing or adopting prose."""

import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from test_openai_schema import completed
from test_plot_review_shared import PlotModel
from test_workflow_plan_preparation import PreparationModel
from test_workflow_scope import workflow_payload


REVIEW = "审查正文"
PLOT = "逐项对照作者情节解释与本次正文"
MEMORY = "从正文提取事实候选与章节摘要"


@pytest.fixture
def stopped_candidate(settings, domain, story, client):
    payload = workflow_payload(story, mode="automatic")
    payload.update(plot_contract_check=True, client_request_id="recheck-original-workflow")
    queued = domain.queue("workflow", payload, story["branch_id"])
    model = PreparationModel()
    model.verdict = "unknown"
    asyncio.run(Worker(settings, model).run_once())
    parent = domain.task(queued["id"])
    assert parent["status"] == "needs_review", parent["error"]
    candidate = domain.artifact(parent["result"]["waiting_artifact"])
    assert candidate["payload"]["plot_audit"]["status"] == "unverified"
    edited = client.patch(
        f"/api/artifacts/{candidate['id']}",
        json={"candidate_revision": 0, "text": "复测记录😀𠮷。\n" + candidate["payload"]["text"]},
    )
    assert edited.status_code == 200
    return domain.task(parent["id"]), edited.json()


def queue_recheck(client, candidate, **overrides):
    return client.post(
        f"/api/artifacts/{candidate['id']}/recheck",
        json={
            "candidate_revision": candidate["candidate_revision"],
            "account": "openai_1",
            "client_request_id": "recheck-request-nonce",
            **overrides,
        },
    )


def recheck_tasks(domain):
    return domain.db.all("SELECT * FROM tasks WHERE kind='candidate_recheck' ORDER BY created,id")


class RecheckModel(PlotModel):
    """Only current prose review, the frozen contract and memory may be requested."""

    def __init__(self, verdict="pass", *, review_result="pass", blocker=False, hook=None):
        super().__init__(verdict)
        self.actions = []
        self.hook = hook
        self.review_result = review_result
        self.blocker = blocker

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("动作")
        assert action in {REVIEW, PLOT, MEMORY}, "复检不能重解释、改章纲或重写正文"
        self.actions.append(action)
        if self.hook:
            self.hook(action, task_id)
        if action == REVIEW and (self.review_result != "pass" or self.blocker):
            self.calls += 1
            self.frames.append(deepcopy(frame))
            unit = instruction["可引用原文"][0]["source_id"]
            return {
                "text": dumps(
                    {
                        "checks": [
                            {"condition": c, "result": self.review_result, "evidence": unit}
                            for c in instruction["固定条件"]
                        ],
                        "findings": [
                            {
                                "severity": "blocker",
                                "chapter": instruction["章节号"],
                                "quote": unit,
                                "reason": "隔离替身的明确阻断",
                                "repair": "保留原稿供作者核对",
                            }
                        ]
                        if self.blocker
                        else [],
                        "summary": "隔离工程审查，不代表文学评价",
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": f"recheck-{self.calls}",
            }
        return await super().generate(account, task_id, system, prompt, max_output, **kwargs)


def assert_parent_not_resumed(domain, before):
    after = domain.task(before["id"])
    for key in ["status", "payload", "result", "attempt", "cancel", "pause"]:
        assert after[key] == before[key]


def test_endpoint_queues_one_stable_recheck_task(client, domain, stopped_candidate):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate, budget_limit=1)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["kind"] == "candidate_recheck" and queued["status"] == "queued"
    assert queued["branch_id"] == parent["branch_id"]
    assert queued["result"]["candidate_recheck_version"] == "candidate-recheck/1"
    assert queued["result"]["candidate_recheck_snapshot"]
    for key in ["genre_profile_snapshot", "author_contract_snapshot"]:
        assert queued["result"][key] == parent["result"][key]
    again = queue_recheck(client, candidate, budget_limit=1)
    assert again.status_code == 202 and again.json()["id"] == queued["id"]
    assert len(recheck_tasks(domain)) == 1
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_revision": True},
        {"candidate_revision": -1},
        {"candidate_revision": "1"},
        {"client_request_id": "short"},
        {"budget_limit": 0},
        {"budget_limit": -1},
        {"plan": {"plot": "客户端不能改冻结规划"}},
        {"prepared_contract": {}},
    ],
)
def test_recheck_request_is_strict_and_cannot_override_frozen_scope(
    client, domain, stopped_candidate, overrides
):
    _, candidate = stopped_candidate
    response = queue_recheck(client, candidate, **overrides)
    assert response.status_code == 422, response.text
    assert not recheck_tasks(domain)


def test_nonce_cannot_be_reused_for_a_different_account(client, domain, stopped_candidate):
    _, candidate = stopped_candidate
    first = queue_recheck(client, candidate)
    assert first.status_code == 202, first.text
    conflict = queue_recheck(client, candidate, account="openai_2")
    assert conflict.status_code == 409, conflict.text
    assert len(recheck_tasks(domain)) == 1


def test_recheck_requires_a_client_nonce(client, domain, stopped_candidate):
    _, candidate = stopped_candidate
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck",
        json={"candidate_revision": 1, "account": "openai_1"},
    )
    assert response.status_code == 422
    assert not recheck_tasks(domain)


def test_success_rechecks_frozen_plan_and_updates_only_candidate_review(
    settings, client, domain, stopped_candidate
):
    parent, candidate = stopped_candidate
    original = deepcopy(candidate)
    chapter = domain.chapter(candidate["chapter_id"])
    branch = domain.branch(candidate["branch_id"])
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    model = RecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    assert model.actions == [REVIEW, PLOT, MEMORY]
    saved = domain.artifact(candidate["id"])
    assert saved["payload"]["text"] == original["payload"]["text"]
    assert saved["candidate_revision"] == original["candidate_revision"] == 1
    assert saved["payload"]["review_passed"] is True
    assert saved["payload"]["memory"]["facts"]
    for key in ["previous_review", "previous_plot_audit"]:
        assert saved["payload"][key] == original["payload"][key]
    audit = saved["payload"]["plot_audit"]
    assert audit["version"] == "author-plot-audit/2" and audit["status"] == "model_pass"
    assert audit["candidate_revision"] == 1
    assert audit["candidate_sha256"] == hashlib.sha256(saved["payload"]["text"].encode()).hexdigest()
    frozen = parent["result"]["plot_preparations"]["1"]
    assert audit["source"]["text_sha256"] == frozen["effective_plot_sha256"]
    assert [item["author_evidence"] for item in audit["items"]] == [
        item["author_evidence"] for item in frozen["prepared_contract"]["items"]
    ]
    reports = domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert len(reports) == 1
    report = domain.artifact(reports[0]["id"])
    assert report["kind"] == "candidate_recheck"
    assert report["payload"]["version"] == "candidate-recheck/1"
    assert report["payload"]["source_workflow_id"] == parent["id"]
    assert report["payload"]["target"] == {
        "kind": "candidate",
        "artifact_id": candidate["id"],
        "revision": 1,
        "chapter_id": candidate["chapter_id"],
        "base_revision": chapter["revision"],
        "content_sha256": audit["candidate_sha256"],
    }
    assert report["payload"]["memory_generated"] is True
    denied = client.post(
        f"/api/artifacts/{report['id']}/accept", json={"idempotency_key": "read-only-report"}
    )
    assert denied.status_code == 409
    assert domain.chapter(candidate["chapter_id"]) == chapter
    assert domain.branch(candidate["branch_id"]) == branch
    assert not domain.db.all("SELECT * FROM facts")
    assert not domain.db.all("SELECT * FROM receipts")
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize("verdict", ["unknown", "fail"])
def test_nonpass_plot_never_extracts_memory_or_sets_review_passed(
    settings, client, domain, stopped_candidate, verdict
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    model = RecheckModel(verdict)
    asyncio.run(Worker(settings, model).run_once())
    saved = domain.artifact(candidate["id"])
    assert model.actions == [REVIEW, PLOT]
    assert saved["payload"]["review_passed"] is False
    assert saved["payload"]["memory"] == {}
    assert saved["payload"]["plot_audit"]["status"] == ("unverified" if verdict == "unknown" else "failed")
    assert saved["payload"]["text"] == candidate["payload"]["text"]
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
    assert_parent_not_resumed(domain, parent)


def test_general_review_blocker_prevents_memory_even_if_plot_passes(
    settings, client, domain, stopped_candidate
):
    _, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    model = RecheckModel(blocker=True)
    asyncio.run(Worker(settings, model).run_once())
    assert MEMORY not in model.actions
    saved = domain.artifact(candidate["id"])
    assert not saved["payload"]["review_passed"] and saved["payload"]["memory"] == {}


def contains_snapshot(value, expected):
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(contains_snapshot(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains_snapshot(item, expected) for item in value)
    return False


def test_repeated_recheck_and_edit_keep_every_previous_report_readable(
    settings, client, domain, stopped_candidate
):
    parent, candidate = stopped_candidate
    original_review = deepcopy(candidate["payload"]["previous_review"])
    original_audit = deepcopy(candidate["payload"]["previous_plot_audit"])
    first = queue_recheck(client, candidate)
    assert first.status_code == 202, first.text
    asyncio.run(Worker(settings, RecheckModel()).run_once())
    first_task = domain.task(first.json()["id"])
    assert first_task["status"] == "succeeded", first_task["error"]
    first_report = domain.artifact(first_task["result"]["artifact_id"])
    assert first_report["payload"]["previous_candidate"] == candidate["payload"]
    after_first = domain.artifact(candidate["id"])

    second = queue_recheck(client, candidate, client_request_id="second-recheck-nonce")
    assert second.status_code == 202, second.text
    asyncio.run(Worker(settings, RecheckModel("unknown")).run_once())
    second_task = domain.task(second.json()["id"])
    assert second_task["status"] == "succeeded", second_task["error"]
    second_report = domain.artifact(second_task["result"]["artifact_id"])
    assert second_report["payload"]["previous_candidate"] == after_first["payload"]
    after_second = domain.artifact(candidate["id"])
    assert not after_second["payload"]["review_passed"]
    edited = client.patch(
        f"/api/artifacts/{candidate['id']}",
        json={"candidate_revision": 1, "text": "再次明确化。\n" + candidate["payload"]["text"]},
    )
    assert edited.status_code == 200

    history = client.get(f"/api/artifacts/{candidate['id']}/history?offset=0")
    assert history.status_code == 200
    entries = history.json()
    assert entries["artifact_id"] == candidate["id"] and entries["next_offset"] is None
    assert len(entries["items"]) == 4  # Original edit, two rechecks, then another edit.
    assert {item["kind"] for item in entries["items"]} == {"candidate_edited", "candidate_rechecked"}
    latest = entries["items"][0]["payload"]
    assert latest["artifact_id"] == candidate["id"] and latest["previous_revision"] == 1
    assert (
        latest["previous_content_sha256"]
        == hashlib.sha256(after_second["payload"]["text"].encode()).hexdigest()
    )
    assert latest["previous_candidate"] == after_second["payload"]
    readable = [edited.json()["payload"], entries, first_report["payload"], second_report["payload"]]
    assert contains_snapshot(readable, original_review)
    assert contains_snapshot(readable, original_audit)
    assert contains_snapshot(readable, after_first["payload"]["review"])
    assert domain.artifact(first_report["id"]) == first_report
    assert domain.artifact(second_report["id"]) == second_report
    assert_parent_not_resumed(domain, parent)


def test_candidate_history_paginates_without_losing_revision_zero(client, domain, stopped_candidate):
    _, candidate = stopped_candidate
    for revision in range(1, 26):
        edited = client.patch(
            f"/api/artifacts/{candidate['id']}",
            json={"candidate_revision": revision, "text": f"历史第{revision + 1}版😀𠮷"},
        )
        assert edited.status_code == 200
    first = client.get(f"/api/artifacts/{candidate['id']}/history?offset=0").json()
    assert len(first["items"]) == 20 and first["next_offset"] == 20
    second = client.get(f"/api/artifacts/{candidate['id']}/history?offset=20").json()
    assert len(second["items"]) == 6 and second["next_offset"] is None
    entries = first["items"] + second["items"]
    assert len({item["id"] for item in entries}) == 26
    assert [item["payload"]["previous_revision"] for item in entries] == list(reversed(range(26)))
    assert all(item["payload"]["artifact_id"] == candidate["id"] for item in entries)
    assert "_candidate_revision" not in entries[-1]["payload"]["previous_candidate"]
    assert entries[-1]["payload"]["previous_revision"] == 0


def test_post_publish_exception_cannot_downgrade_committed_recheck_or_bill_again(
    settings, client, domain, stopped_candidate, monkeypatch
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    stage = type(domain).stage
    committed = []

    def fail_after_commit(self, request, kind, *args, **kwargs):
        result = stage(self, request, kind, *args, **kwargs)
        if kind == "candidate_recheck":
            committed.append(result["id"])
            raise RuntimeError("isolated failure after the publication transaction committed")
        return result

    monkeypatch.setattr(type(domain), "stage", fail_after_commit)
    model = RecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(response.json()["id"])
    assert committed and task["status"] == "succeeded", task["error"]
    assert task["result"]["artifact_id"] == committed[0]
    report = domain.artifact(committed[0])
    saved = domain.artifact(candidate["id"])
    assert report["payload"]["target"]["artifact_id"] == saved["id"]
    assert saved["payload"]["review_passed"] and saved["payload"]["text"] == candidate["payload"]["text"]
    assert model.actions == [REVIEW, PLOT, MEMORY]
    assert not domain.db.all(
        "SELECT * FROM task_events WHERE task_id=? AND kind IN ('stopped','internal_error')", (task["id"],)
    )
    replay = queue_recheck(client, candidate)
    assert replay.status_code == 202 and replay.json()["id"] == task["id"]
    assert not asyncio.run(Worker(settings, model).run_once())
    assert model.actions == [REVIEW, PLOT, MEMORY]
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize("interruption", ["cancel", "new_attempt"])
def test_late_recheck_result_cannot_publish_or_overwrite_new_attempt(
    settings, client, domain, stopped_candidate, interruption
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]

    def hook(action, current_id):
        if action != MEMORY:
            return
        assert current_id == task_id
        if interruption == "cancel":
            assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200
        else:
            with domain.db.connect(True) as tx:
                tx.execute("UPDATE tasks SET attempt='replacement-attempt' WHERE id=?", (task_id,))

    asyncio.run(Worker(settings, RecheckModel(hook=hook)).run_once())
    assert domain.artifact(candidate["id"]) == candidate
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (task_id,))
    task = domain.task(task_id)
    if interruption == "cancel":
        assert task["status"] == "cancelled"
    else:
        assert task["attempt"] == "replacement-attempt" and task["status"] == "running"
    assert domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (task_id,))
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize(
    "damage",
    ["legacy_plot", "missing_preparation", "not_ready", "wrong_waiting", "running", "assisted", "wrong_kind"],
)
def test_ineligible_or_legacy_sources_are_rejected_before_queue(client, domain, stopped_candidate, damage):
    parent, candidate = stopped_candidate
    result = deepcopy(parent["result"])
    payload = deepcopy(parent["payload"])
    state = parent["status"]
    if damage == "legacy_plot":
        result.pop("plot_contract_version")
    elif damage == "missing_preparation":
        result.pop("workflow_plan_preparation_version")
    elif damage == "not_ready":
        result["plot_preparations"]["1"]["status"] = "needs_review"
    elif damage == "wrong_waiting":
        result["waiting_artifact"] = "another-artifact"
    elif damage == "running":
        state = "running"
    elif damage == "assisted":
        payload["mode"] = "assisted"
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status=?,result=?,payload=? WHERE id=?",
            (state, dumps(result), dumps(payload), parent["id"]),
        )
        if damage == "wrong_kind":
            tx.execute("UPDATE artifacts SET kind='text_patch' WHERE id=?", (candidate["id"],))
    response = queue_recheck(client, candidate)
    assert response.status_code == 409, response.text
    assert not recheck_tasks(domain)


@pytest.mark.parametrize("change", ["candidate_revision", "saved_chapter", "story", "cross_work"])
def test_stale_or_cross_work_target_is_rejected_before_queue(client, domain, stopped_candidate, change):
    _, candidate = stopped_candidate
    overrides = {}
    if change == "candidate_revision":
        overrides["candidate_revision"] = 0
    elif change == "saved_chapter":
        domain.save(candidate["chapter_id"], 0, "手写新稿😀𠮷")
    elif change == "story":
        domain.create_chapter(candidate["branch_id"], "新章节改变故事基线")
    else:
        other = domain.create_novel("隔离作品")
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE artifacts SET branch_id=? WHERE id=?", (other["branch_id"], candidate["id"]))
    response = queue_recheck(client, candidate, **overrides)
    assert response.status_code in ({403, 409} if change == "cross_work" else {409}), response.text
    assert not recheck_tasks(domain)


def test_cross_work_parent_pointer_is_rejected_before_any_recheck_cost(
    settings, client, domain, stopped_candidate
):
    parent, candidate = stopped_candidate
    other = domain.create_novel("另一作品的合法全自动流程")
    other_chapter = domain.create_chapter(other["branch_id"], "另一作品首章")
    payload = workflow_payload({**other, "chapter_id": other_chapter["id"]}, mode="automatic")
    payload.update(plot_contract_check=True, client_request_id="other-stopped-workflow")
    other_task = domain.queue("workflow", payload, other["branch_id"])
    model = PreparationModel()
    model.verdict = "unknown"
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(other_task["id"])["status"] == "needs_review"

    # Even a ready receipt and matching waiting ID cannot authorize another work's parent.
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(parent["result"]), other_task["id"]))
        tx.execute("UPDATE artifacts SET task_id=? WHERE id=?", (other_task["id"], candidate["id"]))
    before = domain.artifact(candidate["id"])
    response = queue_recheck(client, before)
    assert response.status_code == 409 and response.json()["code"] == "candidate_recheck_source"
    assert not recheck_tasks(domain)
    assert domain.artifact(candidate["id"]) == before
    assert all(not row["calls"] for row in Budget(settings).report())


def test_prepared_chapter_identity_mismatch_is_rejected_before_any_recheck_cost(
    settings, client, domain, stopped_candidate
):
    parent, candidate = stopped_candidate
    other = domain.create_novel("不同章身份")
    other_chapter = domain.create_chapter(other["branch_id"], "另一章")
    result = deepcopy(parent["result"])
    result["plot_preparations"]["1"]["chapter_id"] = other_chapter["id"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), parent["id"]))
    response = queue_recheck(client, candidate)
    assert response.status_code == 409 and response.json()["code"] == "candidate_recheck_source"
    assert not recheck_tasks(domain)
    assert domain.artifact(candidate["id"]) == candidate
    assert all(not row["calls"] for row in Budget(settings).report())


@pytest.mark.parametrize("change", ["edit", "reject", "accept", "chapter", "story"])
@pytest.mark.parametrize("when", ["queued", "review", "memory"])
def test_source_changes_never_publish_a_report_over_newer_candidate(
    settings, client, domain, stopped_candidate, change, when
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate)
    assert response.status_code == 202, response.text
    after_change = []

    def mutate():
        if change == "edit":
            updated = client.patch(
                f"/api/artifacts/{candidate['id']}",
                json={"candidate_revision": 1, "text": "较新的候选😀𠮷，必须保留。"},
            )
            assert updated.status_code == 200
        elif change == "reject":
            assert (
                client.post(
                    f"/api/artifacts/{candidate['id']}/reject", json={"candidate_revision": 1}
                ).status_code
                == 200
            )
        elif change == "accept":
            domain.accept(candidate["id"], "explicit-author-choice", candidate_revision=1)
        elif change == "chapter":
            domain.save(candidate["chapter_id"], 0, "作者在生成中保存的新稿。")
        else:
            domain.create_chapter(candidate["branch_id"], "故事结构在生成中改变")
        after_change.append(deepcopy(domain.artifact(candidate["id"])))

    def hook(action, task_id):
        if not after_change and action == (REVIEW if when == "review" else MEMORY):
            mutate()

    if when == "queued":
        mutate()
    model = RecheckModel(hook=hook if when != "queued" else None)
    asyncio.run(Worker(settings, model).run_once())
    assert after_change
    saved = domain.artifact(candidate["id"])
    assert saved == after_change[0]
    task = domain.task(response.json()["id"])
    assert task["status"] == "blocked_runtime", task["error"]
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (task["id"],))
    if when == "queued":
        assert model.calls == 0
    else:
        assert domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (task["id"],))
    if change not in {"edit", "accept"}:
        assert_parent_not_resumed(domain, parent)


def test_unknown_timeout_is_a_single_real_dispatch_without_candidate_publication(
    settings, client, domain, stopped_candidate
):
    parent, candidate = stopped_candidate
    response = queue_recheck(client, candidate, budget_limit=1)
    assert response.status_code == 202, response.text
    sent = []

    def handler(request):
        sent.append(request.content)
        raise httpx.ReadTimeout("isolated transport: result unknown", request=request)

    ledger = Budget(settings)
    worker = Worker(settings, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    assert asyncio.run(worker.run_once())
    assert not asyncio.run(worker.run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "blocked_runtime" and len(sent) == 1
    assert domain.artifact(candidate["id"]) == candidate
    assert ledger.report()[0]["reserved_unknown"] > 0
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (task["id"],))
    assert_parent_not_resumed(domain, parent)


def test_budget_denial_is_before_http_and_never_clears_the_original_review(
    settings, client, domain, stopped_candidate
):
    _, candidate = stopped_candidate
    response = queue_recheck(client, candidate, budget_limit=0.000001)
    assert response.status_code == 202, response.text
    sent = []

    def handler(request):
        sent.append(request)
        pytest.fail("预算拒绝必须在真实发送前")

    ledger = Budget(settings)
    worker = Worker(settings, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    asyncio.run(worker.run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "blocked_runtime" and not sent
    assert domain.artifact(candidate["id"]) == candidate
    assert not ledger.report()[0]["calls"]


def test_real_provider_adapter_uses_three_calls_and_idempotent_replay_is_free(
    settings, client, domain, stopped_candidate
):
    parent, candidate = stopped_candidate
    queued = queue_recheck(client, candidate, budget_limit=1)
    assert queued.status_code == 202, queued.text
    requests = []

    def handler(request):
        body = json.loads(request.content)
        instruction = json.loads(body["input"][1]["content"])["任务"]
        action = instruction["动作"]
        requests.append(action)
        unit = instruction["可引用原文"][0]["source_id"]
        if action == REVIEW:
            output = {
                "checks": [
                    {"condition": c, "result": "pass", "evidence": unit} for c in instruction["固定条件"]
                ],
                "findings": [],
                "summary": "隔离审查，仅验证工程接线",
            }
        elif action == PLOT:
            output = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": "pass",
                        "observed": "保留测量记录",
                        "evidence": unit,
                        "reason": "隔离核对",
                        "repair": "",
                    }
                ],
                "summary": "同冻结契约核对",
            }
        else:
            assert action == MEMORY
            output = {"facts": [], "summary": "隔离正文已核对，暂不增加事实"}
        return completed(dumps(output))

    ledger = Budget(settings)
    model = ModelClient(settings, ledger, httpx.MockTransport(handler))
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    assert requests == [REVIEW, PLOT, MEMORY]
    assert len(ledger.report()[0]["calls"]) == 3
    replay = queue_recheck(client, candidate, budget_limit=1)
    assert replay.status_code == 202 and replay.json()["id"] == task["id"]
    assert not asyncio.run(Worker(settings, model).run_once())
    assert requests == [REVIEW, PLOT, MEMORY]
    assert domain.artifact(candidate["id"])["payload"]["text"] == candidate["payload"]["text"]
    assert_parent_not_resumed(domain, parent)
