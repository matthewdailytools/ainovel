"""Original source remains immutable; only compatible explicit supplements proceed."""

import asyncio
from copy import deepcopy
import json
import pytest
from ainovel import author_source_clarification as bridge
from ainovel import authored_source_scope as authored
from ainovel.worker import Worker
from ainovel.db import DomainError
from helpers.author_clarification_fixture import source_unverified, native, SOURCE, ADDITION


def request(preview, nonce="clarify-author-new-generation", **changes):
    return {
        "version": bridge.VERSION,
        "client_request_id": nonce,
        "preview_basis_sha256": preview["request_basis"]["preview_basis_sha256"],
        "clarification": ADDITION,
        "actor": "authorized_operator",
        "reason": "",
        **changes,
    }


def test_native_known_source_stop_preview_child_and_nonce_keep_original_plan_and_ledger(
    settings, domain, story, client
):
    worker, parent, sent = source_unverified(settings, domain, story, client=client)
    chapters = domain.db.all("SELECT * FROM chapters ORDER BY id")
    steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (parent["id"],))
    result = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview")
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview["original_source"]["text"] == SOURCE and preview["source_review"]["state"] == "unverified"
    assert preview["repair_round"] == 1 and preview["adoption"] == "manual"
    posted = client.post(f"/api/tasks/{parent['id']}/author-source-clarifications", json=request(preview))
    assert posted.status_code == 202, posted.text
    child = posted.json()
    assert child["branch_id"] == parent["branch_id"] and child["status"] == "queued"
    assert child["payload"]["plots"] == parent["payload"]["plots"]
    assert child["result"]["plan"] == parent["result"]["plan"] and child["result"]["repair_round"] == 1
    assert child["payload"]["account"] == parent["payload"]["account"]
    assert child["payload"]["budget_limit"] == preview["budget"]["remaining_run_limit"] < 2
    assert child["result"][bridge.RECEIPT]["request"] == request(preview)
    assert child["result"][bridge.RECEIPT]["author_literary_approval"] == "unverified"
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters
    assert (
        domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (parent["id"],))
        == steps
    )
    actual = domain.task(parent["id"])
    assert (
        actual["status"] == "continued"
        and {k: v for k, v in actual["result"].items() if k != "continued_to"} == parent["result"]
    )
    assert (
        client.post(f"/api/tasks/{parent['id']}/author-source-clarifications", json=request(preview)).json()[
            "id"
        ]
        == child["id"]
    )
    lookup = client.get(
        f"/api/tasks/{parent['id']}/author-source-clarifications/{request(preview)['client_request_id']}"
    )
    assert lookup.status_code == 200 and lookup.json()["task"]["id"] == child["id"]
    changed = client.post(
        f"/api/tasks/{parent['id']}/author-source-clarifications",
        json=request(preview, clarification="不同补充"),
    )
    assert changed.status_code == 409
    fresh = Worker(settings)
    fresh.task = fresh.claim()
    fresh.attempt = fresh.task["attempt"]
    assert authored.validate_task(fresh.task, domain)
    sent = native(fresh)
    asyncio.run(fresh.workflow())
    state = domain.task(child["id"])
    assert state["status"] == "needs_review", state["error"]
    assert state["result"][bridge.CHECKS]["status"] == "ready"
    assert state["result"]["waiting_artifact"]
    assert [f["任务"]["动作"] for f in sent[:4]] == [
        authored.ACTION,
        "仅依据作者原话整理本场景情节",
        bridge.ACTION,
        "按章纲写完整正文",
    ]
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters


def ready_request(client, parent, **kwargs):
    path = f"/api/tasks/{parent['id']}/author-source-clarification-preview"
    result = client.get(path)
    assert result.status_code == 200, result.text
    return request(result.json(), **kwargs)


def child_worker(settings, domain, client, parent, *, preserve="pass", timeout_action=None):
    body = ready_request(client, parent)
    response = client.post(f"/api/tasks/{parent['id']}/author-source-clarifications", json=body)
    assert response.status_code == 202, response.text
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == response.json()["id"]
    sent = native(worker, preserve=preserve, timeout_action=timeout_action)
    return worker, sent, body


@pytest.mark.parametrize("preserve", ["fail", "unknown"])
def test_incompatible_or_uncertain_addition_keeps_original_and_stops_before_prose(
    settings, domain, story, client, preserve
):
    _, parent, _ = source_unverified(settings, domain, story)
    chapters = domain.db.all("SELECT * FROM chapters ORDER BY id")
    worker, sent, body = child_worker(settings, domain, client, parent, preserve=preserve)
    with pytest.raises(DomainError, match="补充"):
        asyncio.run(worker.workflow())
    assert [f["任务"]["动作"] for f in sent] == [
        authored.ACTION,
        "仅依据作者原话整理本场景情节",
        bridge.ACTION,
    ]
    assert worker.task["result"][bridge.CHECKS]["status"] == "unverified"
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (worker.task["id"],))
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters
    assert worker.task["payload"]["plots"] == parent["payload"]["plots"]


def test_unknown_preservation_dispatch_reserves_and_never_reissues(settings, domain, story, client):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, body = child_worker(settings, domain, client, parent, timeout_action=bridge.ACTION)
    with pytest.raises(DomainError):
        asyncio.run(worker.workflow())
    assert len(sent) == 3
    before = deepcopy(worker.budget.report())
    assert next(r for r in before if r["provider"] == "deepseek")["reserved_unknown"] > 0
    with pytest.raises(DomainError):
        asyncio.run(worker.workflow())
    assert len(sent) == 3 and worker.budget.report() == before
    path = f"/api/tasks/{parent['id']}/author-source-clarifications/{body['client_request_id']}"
    assert client.get(path).json()["task"]["id"] == worker.task["id"]
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (worker.task["id"],))


@pytest.mark.parametrize(
    "fault",
    [
        "body",
        "candidate",
        "later_body",
        "later_revision",
        "source_started",
        "missing_step",
        "unknown_fee",
        "story_changed",
        "source_hash",
        "other_active",
    ],
)
def test_preview_rejects_unsafe_or_unproven_checkpoint_without_changes(
    settings, domain, story, client, fault
):
    _, parent, _ = source_unverified(settings, domain, story)
    chapter = domain.db.all(
        "SELECT * FROM chapters WHERE branch_id=? AND position=2", (parent["branch_id"],)
    )[0]
    later = domain.db.all("SELECT * FROM chapters WHERE branch_id=? AND position=3", (parent["branch_id"],))[
        0
    ]
    if fault == "body":
        domain.save(chapter["id"], 0, "已存在正文")
    elif fault == "candidate":
        domain.stage(
            {
                "novel_id": parent["novel_id"],
                "branch_id": parent["branch_id"],
                "chapter_id": chapter["id"],
                "base_revision": 0,
            },
            "chapter_draft",
            {"text": "已有候选"},
            {"story_revision": parent["result"]["expected_story_revision"]},
        )
    elif fault == "later_body":
        with domain.db.connect(True) as tx:
            domain.db.event(tx, parent["id"], "chapter_started", {"number": 3})
    elif fault == "later_revision":
        domain.save(later["id"], 0, "后章已写")
    elif fault == "missing_step":
        with domain.db.connect(True) as tx:
            tx.execute("DELETE FROM task_steps WHERE task_id=?", (parent["id"],))
    elif fault == "unknown_fee":
        with Worker(settings).budget.db(True) as tx:
            tx.execute("UPDATE calls SET status='unknown',charged=NULL WHERE task_id=?", (parent["id"],))
    elif fault == "other_active":
        from ainovel.models import ModuleInput

        domain.queue(
            "module",
            ModuleInput(
                novel_id=parent["novel_id"],
                branch_id=parent["branch_id"],
                chapter_id=chapter["id"],
                base_revision=0,
                action="writing.expand_plot",
                instruction="另一次生成",
                account="deepseek",
            ).model_dump(exclude_none=True),
            parent["branch_id"],
        )
    else:
        state = deepcopy(parent["result"])
        if fault == "source_started":
            state[authored.RECEIPTS]["chapter:2"]["phases"]["interpret"]["status"] = "started"
        elif fault == "source_hash":
            state[authored.RECEIPTS]["chapter:2"]["source_sha256"] = "0" * 64
        else:
            state["expected_story_revision"] -= 1
        with domain.db.connect(True) as tx:
            tx.execute(
                "UPDATE tasks SET result=? WHERE id=?", (json.dumps(state, ensure_ascii=False), parent["id"])
            )
    before = domain.db.all("SELECT * FROM tasks ORDER BY id")
    response = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview")
    assert response.status_code == 409, response.text
    assert domain.db.all("SELECT * FROM tasks ORDER BY id") == before


def test_stale_preview_and_extra_budget_or_account_are_rejected_without_overriding_source(
    settings, domain, story, client
):
    _, parent, _ = source_unverified(settings, domain, story)
    body = ready_request(client, parent)
    path = f"/api/tasks/{parent['id']}/author-source-clarifications"
    for key, value in [("account", "openai_1"), ("budget_limit", 80), ("chapters", 36), ("source_entry", {})]:
        assert client.post(path, json={**body, key: value}).status_code == 422
    with Worker(settings).budget.db(True) as tx:
        tx.execute("UPDATE calls SET charged=charged+1 WHERE task_id=?", (parent["id"],))
    response = client.post(path, json=body)
    assert response.status_code == 409 and response.json()["code"] == "author_clarification_basis"
    assert domain.task(parent["id"])["status"] == "blocked_runtime"


@pytest.mark.parametrize("field", [bridge.KEY, bridge.RECEIPT])
def test_deleted_new_child_marker_cannot_downgrade_or_dispatch(settings, domain, story, client, field):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, _ = child_worker(settings, domain, client, parent)
    worker.task["result"].pop(field)
    with pytest.raises(DomainError, match="澄清"):
        asyncio.run(worker.workflow())
    assert sent == []


def test_concurrent_distinct_nonce_creates_only_one_child_and_old_nonce_still_reads(
    settings, domain, story, client
):
    from concurrent.futures import ThreadPoolExecutor

    _, parent, _ = source_unverified(settings, domain, story)
    body = ready_request(client, parent)
    path = f"/api/tasks/{parent['id']}/author-source-clarifications"

    def post(index):
        return client.post(path, json={**body, "client_request_id": f"concurrent-clarification-{index}"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(post, [1, 2]))
    assert sorted(r.status_code for r in result) == [202, 409]
    assert (
        len(
            domain.db.all(
                "SELECT id FROM tasks WHERE json_extract(result,'$.author_source_clarification_version')=?",
                (bridge.VERSION,),
            )
        )
        == 1
    )
    assert (
        client.get(f"/api/tasks/{parent['id']}/author-source-clarifications/not-yet-submitted").json()["task"]
        is None
    )


@pytest.mark.parametrize(
    "target_position,action,other_branch,blocked",
    [
        (2, "writing.expand_plot", False, True),
        (3, "writing.expand_plot", False, True),
        (1, "writing.expand_plot", False, False),
        (3, "review.chapter", False, False),
        (3, "writing.expand_plot", True, False),
    ],
)
def test_registered_body_dispatch_scope_preserves_readonly_prior_and_other_branch_controls(
    settings, domain, story, client, target_position, action, other_branch, blocked
):
    from ainovel.models import ModuleInput
    from ainovel.providers import ModelClient
    import httpx

    _, parent, _ = source_unverified(settings, domain, story)
    if other_branch:
        other = domain.create_novel("另一作品")
        chapter = domain.create_chapter(other["branch_id"], "另章", target_position)
        branch = other["branch_id"]
        novel = other["id"]
    else:
        branch = parent["branch_id"]
        novel = parent["novel_id"]
        chapter = domain.db.all(
            "SELECT * FROM chapters WHERE branch_id=? AND position=?", (branch, target_position)
        )[0]
    module = domain.queue(
        "module",
        ModuleInput(
            novel_id=novel,
            branch_id=branch,
            chapter_id=chapter["id"],
            base_revision=chapter["revision"],
            action=action,
            instruction="独立工作",
            account="deepseek",
        ).model_dump(exclude_none=True),
        branch,
    )
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("isolated body unknown", request=request)

    worker = Worker(settings)
    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(timeout))
    assert asyncio.run(worker.run_once()) and domain.task(module["id"])["status"] == "blocked_runtime"
    before = deepcopy(worker.budget.report())
    response = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview")
    assert response.status_code == (409 if blocked else 200), response.text
    assert worker.budget.report() == before
    if action == "writing.expand_plot":
        assert (
            len(calls) == 1 and next(r for r in before if r["provider"] == "deepseek")["reserved_unknown"] > 0
        )
        assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (module["id"],))


def test_late_external_body_dispatch_stops_queued_child_but_nonce_still_reveals_history(
    settings, domain, story, client
):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, body = child_worker(settings, domain, client, parent)
    # A later real producer event, after atomic creation; the model boundary
    # rechecks this while immutable receipt lookup remains available.
    with domain.db.connect(True) as tx:
        domain.db.event(tx, parent["id"], "chapter_started", {"number": 3})
    with pytest.raises(DomainError, match="其他正文派发"):
        asyncio.run(worker.workflow())
    assert sent == []
    lookup = client.get(f"/api/tasks/{parent['id']}/author-source-clarifications/{body['client_request_id']}")
    assert lookup.status_code == 200 and lookup.json()["task"]["id"] == worker.task["id"]


@pytest.mark.parametrize("external_body", [False, True])
def test_retry_parent_clarification_allows_own_body_but_still_rejects_other_producers(
    settings, domain, story, client, external_body
):
    from ainovel import author_source_observation_retry as retry
    from ainovel import authored_source_classification as classification
    from ainovel.models import ModuleInput
    from helpers.authored_classification_fixture import native as classified_native
    from helpers.author_observation_retry_fixture import known_returned_failure, wrong_unit

    old_worker, failed, _, _ = known_returned_failure(settings, domain, story, client=client)
    preview = client.get(f"/api/tasks/{failed['id']}/author-source-observation-retry-preview")
    assert preview.status_code == 200, preview.text
    retried = client.post(
        f"/api/tasks/{failed['id']}/author-source-observation-retries",
        json={
            "version": retry.VERSION,
            "client_request_id": "retry-parent-before-explicit-clarification",
            "preview_basis_sha256": preview.json()["request_basis"]["preview_basis_sha256"],
            "reason": "保留旧返回与费用，显式核对原来源。",
        },
    )
    assert retried.status_code == 202, retried.text
    retry_worker = Worker(settings)
    retry_frames = classified_native(retry_worker, transform=wrong_unit)
    assert asyncio.run(retry_worker.run_once()) and len(retry_frames) == 3
    parent = domain.task(retried.json()["id"])
    assert parent["status"] == "blocked_runtime"
    assert parent["result"][retry.RECEIPT]["task_id"] == parent["id"]
    body = ready_request(
        client,
        parent,
        nonce="clarification-after-retry-parent-format-failure",
        clarification="交易失败与双方未交付仍须保持，借尺仍须按约归还；没有另改结果。",
        reason="只补充原意，保留三份观察格式失败及费用。",
    )
    posted = client.post(f"/api/tasks/{parent['id']}/author-source-clarifications", json=body)
    assert posted.status_code == 202, posted.text
    child = posted.json()
    assert child["result"][retry.RECEIPT]["task_id"] == parent["id"]
    assert child["result"][bridge.RECEIPT]["observation_failure"]
    old_steps = domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint")
    old_chapters = domain.db.all("SELECT * FROM chapters ORDER BY id")
    with old_worker.budget.db() as usage:
        old_calls = [dict(row) for row in usage.execute("SELECT * FROM calls ORDER BY id")]
    if external_body:
        chapter = domain.db.all(
            "SELECT * FROM chapters WHERE branch_id=? AND position=1", (parent["branch_id"],)
        )[0]
        other = domain.queue(
            "module",
            ModuleInput(
                novel_id=parent["novel_id"],
                branch_id=parent["branch_id"],
                chapter_id=chapter["id"],
                base_revision=0,
                action="writing.expand_plot",
                instruction="另一次正文生产者，禁止与澄清子任务重复派发。",
                account="deepseek",
            ).model_dump(exclude_none=True),
            parent["branch_id"],
        )
        with domain.db.connect(True) as tx:
            domain.db.event(tx, other["id"], "chapter_started", {"number": 1})
        cancelled = client.post(f"/api/tasks/{other['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    worker = Worker(settings)
    frames = classified_native(worker)
    assert asyncio.run(worker.run_once())
    current = domain.task(child["id"])
    assert worker.task["id"] == child["id"]
    events = domain.db.all("SELECT kind,payload FROM task_events WHERE task_id=? ORDER BY id", (child["id"],))
    if external_body:
        assert current["status"] == "blocked_runtime" and frames == []
        assert [json.loads(event["payload"])["code"] for event in events if event["kind"] == "stopped"] == [
            "author_observation_retry_late"
        ]
        assert not any(event["kind"] == "step_dispatched" for event in events)
        assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (child["id"],))
    else:
        assert current["status"] == "needs_review", current["error"]
        assert [frame["任务"]["动作"] for frame in frames[:5]] == [
            authored.ACTION,
            "仅依据作者原话整理本场景情节",
            classification.ACTION,
            bridge.ACTION,
            "按章纲写完整正文",
        ]
        assert current["result"][bridge.CHECKS]["status"] == "ready"
        assert any(event["kind"] == "chapter_started" for event in events)
        assert any(
            event["kind"] == "step_dispatched"
            and json.loads(event["payload"])["action"] == "按章纲写完整正文"
            for event in events
        )
        assert current["result"]["waiting_artifact"]
    assert current["payload"]["plots"] == parent["payload"]["plots"]
    assert current["result"]["plan"] == parent["result"]["plan"]
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == old_chapters
    old_ids = {row["task_id"] for row in old_steps}
    assert [
        row
        for row in domain.db.all("SELECT * FROM task_steps ORDER BY task_id,fingerprint")
        if row["task_id"] in old_ids
    ] == old_steps
    with old_worker.budget.db() as usage:
        assert [
            dict(row) for row in usage.execute("SELECT * FROM calls ORDER BY id") if row["task_id"] in old_ids
        ] == old_calls


@pytest.mark.parametrize("limit_kind", ["run", "global"])
def test_budget_boundaries_do_not_create_or_reset_task_or_calls(settings, domain, story, client, limit_kind):
    from decimal import Decimal

    _, parent, _ = source_unverified(settings, domain, story)
    if limit_kind == "run":
        worker = Worker(settings)
        with worker.budget.db(True) as tx:
            row = tx.execute("SELECT id FROM calls WHERE task_id=? LIMIT 1", (parent["id"],)).fetchone()
            tx.execute("UPDATE calls SET charged=2000000 WHERE id=?", (row["id"],))
    else:
        settings.limits["deepseek"] = Decimal("0")
    before = domain.db.all("SELECT * FROM tasks ORDER BY id")
    response = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview")
    if limit_kind == "run":
        assert response.status_code == 409 and response.json()["code"] == "budget"
    else:
        assert (
            response.status_code == 200
            and next(r for r in response.json()["shared_budget"] if r["provider"] == "deepseek")["remaining"]
            == 0
        )
        # Creation retains the cap; first call must stop, never reset CNY80.
        posted = client.post(
            f"/api/tasks/{parent['id']}/author-source-clarifications", json=request(response.json())
        )
        assert posted.status_code == 202
        worker = Worker(settings)
        worker.task = worker.claim()
        worker.attempt = worker.task["attempt"]
        sent = native(worker)
        with pytest.raises(DomainError, match="预算"):
            asyncio.run(worker.workflow())
        assert sent == [] and worker.budget.report()[1]["limit"] == "0"
    if limit_kind == "run":
        assert domain.db.all("SELECT * FROM tasks ORDER BY id") == before


def test_new_candidate_explicit_recheck_keeps_combined_source_without_reinterpretation(
    settings, domain, story, client
):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, _ = child_worker(settings, domain, client, parent)
    asyncio.run(worker.workflow())
    state = domain.task(worker.task["id"])
    artifact = domain.artifact(state["result"]["waiting_artifact"])
    response = client.post(
        f"/api/artifacts/{artifact['id']}/recheck",
        json={"candidate_revision": 0, "account": "deepseek", "client_request_id": "explicit-body-recheck"},
    )
    assert response.status_code == 202, response.text
    frozen = response.json()["result"]["candidate_recheck_snapshot"]
    assert frozen["plot"]["plot"] == state["result"][bridge.RECEIPT]["source_entry"]["text"]
    assert SOURCE in frozen["plot"]["plot"] and ADDITION in frozen["plot"]["plot"]
    assert frozen["prepared_contract"]["items"][0]["actor"] == "伙计"
    assert state["payload"]["plots"][1]["plot"] == SOURCE


def test_unambiguous_role_without_personal_name_is_ready_without_author_clarification(
    settings, domain, story, client
):
    from ainovel.models import WorkflowInput
    from ainovel.plot_audit import interpret_plot
    import httpx
    from ainovel.providers import ModelClient
    from helpers.author_clarification_fixture import answer
    from test_openai_schema import completed

    clear = SOURCE.replace("他把账页", "伙计把账页")
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        brief="原明确角色安排，无需姓名。",
        chapters=1,
        plots=[{"number": 1, "title": "未命名角色", "plot": clear, "fixed": []}],
        account="openai_1",
        min_chars=200,
        max_chars=600,
        plot_contract_check=True,
    ).model_dump(exclude_none=True)
    domain.queue("workflow", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    calls = []

    def handler(request):
        frame = json.loads(json.loads(request.content)["input"][1]["content"])
        calls.append(frame)
        value = answer(frame)
        if frame["任务"]["动作"] == authored.ACTION:
            value["observations"][0]["supplier"] = "伙计"
        elif frame["任务"]["动作"] == "仅依据作者原话整理本场景情节":
            value["items"][0].update(actor="伙计", supplier="伙计", certainty="explicit")
        return completed(json.dumps(value, ensure_ascii=False))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    result = asyncio.run(interpret_plot(worker, clear, source_kind="author_plot", source_position=1))
    assert result["items"][0]["actor"] == "伙计" and result["items"][0]["certainty"] == "explicit"
    assert worker.task["result"][authored.RECEIPTS]["chapter:1"]["state"] == "ready" and len(calls) == 3


def test_complete_semantic_unknown_inventory_can_be_clarified_without_a_candidate(
    settings, domain, story, client
):
    _, parent, sent = source_unverified(settings, domain, story, observe_unknown=True)
    assert len(sent) == 1
    preview = client.get(f"/api/tasks/{parent['id']}/author-source-clarification-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_review"]["bindings"] is None
    assert preview.json()["source_review"]["contract"]["items"] == []
    response = client.post(
        f"/api/tasks/{parent['id']}/author-source-clarifications", json=request(preview.json())
    )
    assert response.status_code == 202
    assert response.json()["payload"]["plots"] == parent["payload"]["plots"]


def test_preservation_wire_enum_and_repair_are_source_only_without_text_invention(
    settings, domain, story, client
):
    import httpx
    from ainovel.providers import ModelClient
    from helpers.author_clarification_fixture import answer
    from test_workflow_plan_source_roles import deepseek_completed

    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, _ = child_worker(settings, domain, client, parent)
    frames = []
    checks = 0

    def handler(request):
        nonlocal checks
        wire = json.loads(request.content)
        raw = wire["messages"][1]["content"]
        frame = json.loads(raw.split("\n只返回")[0])
        frames.append(frame)
        value = answer(frame)
        if frame["任务"]["动作"] == bridge.ACTION:
            checks += 1
            instruction = frame["任务"]
            schema = json.loads(raw.split("\n只返回符合以下 JSON Schema 的对象：\n")[1])
            allowed = [
                u["source_id"]
                for u in [
                    *instruction["原话连续单元"],
                    *instruction["本次补充单元"],
                    *instruction["本章及全书固定条件"],
                ]
            ]
            for field in ("source_checks", "addition_checks", "fixed_checks"):
                assert (
                    schema["properties"][field]["items"]["properties"]["evidence_ids"]["items"]["enum"]
                    == allowed
                )
            assert set(frame["上下文"]) == {"scope"}
            if checks == 1:
                value["source_checks"][0]["evidence_ids"] = ["NOT_CURRENT_SOURCE"]
            else:
                assert "本步骤没有正文或候选" in instruction["引用修正要求"]
                assert "回到本次正文" not in instruction["引用修正要求"]
                assert instruction["原话与既有补充"] == SOURCE
        return deepseek_completed(value)

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    asyncio.run(worker.workflow())
    assert checks == 2 and domain.task(worker.task["id"])["status"] == "needs_review"


@pytest.mark.parametrize("fault", ["initial_config", "forged_account_change", "policy_deleted"])
def test_account_or_policy_cannot_be_changed_without_its_explicit_frozen_source(
    settings, domain, story, client, fault
):
    from dataclasses import replace

    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, _ = child_worker(settings, domain, client, parent)
    if fault == "initial_config":
        settings.providers["deepseek"] = replace(settings.providers["deepseek"], model="changed-after-post")
    elif fault == "forged_account_change":
        worker.task["payload"]["account"] = "openai_1"
    else:
        worker.task["result"].pop("plot_contract_version")
    with pytest.raises(DomainError):
        asyncio.run(worker.workflow())
    assert sent == []


def test_unknown_preservation_cannot_be_cleared_by_new_account_continuation(settings, domain, story, client):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, sent, _ = child_worker(settings, domain, client, parent, timeout_action=bridge.ACTION)
    with pytest.raises(DomainError):
        asyncio.run(worker.workflow())
    worker.checkpoint(worker.task["result"], "blocked_runtime", "保持结果未知")
    before = deepcopy(worker.budget.report())
    result = client.post(
        f"/api/tasks/{worker.task['id']}/continue",
        json={
            "account": "openai_1",
            "budget_limit": 2,
            "acknowledge_unknown": True,
            "reason": "不能借新账户重做未知保持。",
            "client_request_id": "unknown-preservation-account-rejected",
        },
    )
    assert result.status_code == 409 and result.json()["code"] == "unknown_result"
    assert worker.budget.report() == before
