"""Explicit same-source /1 -> /2 with paid history and strict source-only gates."""

import asyncio
from copy import deepcopy

import pytest

from ainovel import authored_source_scope as authored
from ainovel import authored_source_classification as classification
from ainovel import author_source_recheck as bridge
from ainovel.db import DomainError, dumps
from ainovel.plot_audit import interpret_plot
from ainovel.worker import Worker
from helpers.authored_classification_fixture import SOURCE, native, source_unverified


def request(preview, nonce="same-source-policy-new-generation", **changes):
    return {
        "version": bridge.VERSION,
        "client_request_id": nonce,
        "preview_basis_sha256": preview["request_basis"]["preview_basis_sha256"],
        "reason": "用新分类核对同一原话，不补剧情。",
        **changes,
    }


def ready(client, parent):
    response = client.get(f"/api/tasks/{parent['id']}/author-source-recheck-preview")
    assert response.status_code == 200, response.text
    return response.json()


def child_worker(settings, domain, client, parent, **options):
    body = request(ready(client, parent))
    posted = client.post(f"/api/tasks/{parent['id']}/author-source-rechecks", json=body)
    assert posted.status_code == 202, posted.text
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == posted.json()["id"]
    return worker, native(worker, **options), body


def run_source(worker):
    return asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_plot", source_position=1))


def test_known_old_source_upgrade_preserves_original_nonce_manuscripts_steps_and_fees(
    settings, domain, story, client
):
    old, parent, sent = source_unverified(settings, domain, story, client=client)
    chapters = domain.db.all("SELECT * FROM chapters ORDER BY id")
    old_steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (parent["id"],))
    with old.budget.db() as db:
        calls = [dict(r) for r in db.execute("SELECT * FROM calls WHERE task_id=?", (parent["id"],))]
    preview = ready(client, parent)
    assert preview["version"] == bridge.VERSION
    assert preview["old_policy"] == authored.VERSION and preview["new_policy"] == classification.VERSION
    assert preview["source_unchanged"] and preview["starts_child"] and preview["eligible"]
    assert preview["current_source"]["text"] == SOURCE
    assert preview["budget"]["source_steps"]["owner_task_ids"] == [parent["id"]]
    worker, frames, body = child_worker(settings, domain, client, parent)
    child = worker.task
    assert (
        child["result"]["plan"] == parent["result"]["plan"]
        and child["payload"]["plots"] == parent["payload"]["plots"]
    )
    assert child["result"].get("repair_round", 0) == parent["result"].get("repair_round", 0)
    receipt = child["result"][bridge.RECEIPT]
    assert receipt["source_entry"] == receipt["previous_source_entry"] == preview["current_source"]
    assert receipt["previous_source_receipt"] == parent["result"][authored.RECEIPTS]["chapter:1"]
    assert receipt["policies"] == {**receipt["previous_policies"], authored.KEY: classification.VERSION}
    assert child["payload"]["budget_limit"] == preview["budget"]["remaining_run_limit"] < 2
    assert authored.validate_task(child, domain)
    assert bridge.ensure_runtime(child, domain, settings) is None
    contract = run_source(worker)
    record = worker.task["result"][authored.RECEIPTS]["chapter:1"]
    assert record["state"] == "ready" and len(contract["items"]) == 7
    assert set(record["phases"]) == {"observe", "interpret", "preserve"}
    assert authored.receipt_valid(record, SOURCE, {worker.task["id"]})
    assert [f["任务"]["动作"] for f in frames] == [
        authored.ACTION,
        "仅依据作者原话整理本场景情节",
        classification.ACTION,
    ]
    assert all("用新分类核对" not in dumps(f) for f in frames)
    assert "先行作者来源范围观察" not in dumps(frames[0]) and "完整P" not in dumps(frames[0])
    assert run_source(worker) == contract and len(frames) == 3
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters
    assert domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (parent["id"],)) == old_steps
    with old.budget.db() as db:
        assert [dict(r) for r in db.execute("SELECT * FROM calls WHERE task_id=?", (parent["id"],))] == calls
    repeated = client.post(f"/api/tasks/{parent['id']}/author-source-rechecks", json=body)
    assert repeated.status_code == 202 and repeated.json()["id"] == child["id"]
    looked = client.get(
        f"/api/tasks/{parent['id']}/author-source-rechecks",
        params={"client_request_id": body["client_request_id"]},
    )
    assert looked.status_code == 200 and looked.json()["task"]["id"] == child["id"]
    assert (
        client.post(
            f"/api/tasks/{parent['id']}/author-source-rechecks", json={**body, "reason": "改原因"}
        ).status_code
        == 409
    )
    assert domain.queue("workflow", parent["payload"], parent["branch_id"])["id"] == parent["id"]


@pytest.mark.parametrize(
    "options,expected_count",
    [
        ({"observe_unknown": True}, 1),
        ({"ambiguous": True}, 2),
        ({"preserve": "unknown"}, 3),
        ({"preserve": "fail"}, 3),
        ({"classification_result": "unknown"}, 3),
        ({"classification_result": "fail"}, 3),
    ],
)
def test_true_unknown_or_failed_classification_or_preservation_never_readies_or_retries(
    settings, domain, story, client, options, expected_count
):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, frames, _ = child_worker(settings, domain, client, parent, **options)
    with pytest.raises(DomainError, match="未核实"):
        run_source(worker)
    assert len(frames) == expected_count
    assert worker.task["result"][authored.RECEIPTS]["chapter:1"]["state"] == "unverified"
    assert not domain.db.all("SELECT * FROM artifacts")
    with pytest.raises(DomainError):
        run_source(worker)
    assert len(frames) == expected_count
    worker.checkpoint(worker.task["result"], "blocked_runtime", "保留已知待核停点。")
    assert client.get(f"/api/tasks/{worker.task['id']}/author-source-recheck-preview").status_code == 409
    # The original genuine-clarification entry remains available for /2 known stops.
    result = client.get(f"/api/tasks/{worker.task['id']}/author-source-clarification-preview")
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("action", [authored.ACTION, "仅依据作者原话整理本场景情节", classification.ACTION])
def test_unknown_paid_phase_retains_reserve_and_only_queries_existing_child(
    settings, domain, story, client, action
):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, frames, body = child_worker(settings, domain, client, parent, timeout_action=action)
    with pytest.raises(DomainError):
        run_source(worker)
    count = len(frames)
    before = worker.budget.report()
    assert next(r for r in before if r["provider"] == "deepseek")["reserved_unknown"] > 0
    with pytest.raises(DomainError):
        run_source(worker)
    assert len(frames) == count and worker.budget.report() == before
    assert (
        client.get(
            f"/api/tasks/{parent['id']}/author-source-rechecks",
            params={"client_request_id": body["client_request_id"]},
        ).json()["task"]["id"]
        == worker.task["id"]
    )
    assert (
        client.get(f"/api/tasks/{worker.task['id']}/author-source-clarification-preview").status_code == 409
    )


@pytest.mark.parametrize(
    "fault",
    [
        "body",
        "candidate",
        "later_dispatch",
        "missing_step",
        "unknown_fee",
        "source_hash",
        "other_active",
        "story_changed",
    ],
)
def test_preview_reuses_original_safety_gates_without_writes(settings, domain, story, client, fault):
    _, parent, _ = source_unverified(settings, domain, story)
    chapter = domain.db.all(
        "SELECT * FROM chapters WHERE branch_id=? AND position=1", (parent["branch_id"],)
    )[0]
    if fault == "body":
        domain.save(chapter["id"], 0, "已有正文")
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
                instruction="另一个任务",
                account="deepseek",
            ).model_dump(exclude_none=True),
            parent["branch_id"],
        )
    else:
        with domain.db.connect(True) as tx:
            if fault == "missing_step":
                tx.execute("DELETE FROM task_steps WHERE task_id=?", (parent["id"],))
            elif fault == "later_dispatch":
                domain.db.event(tx, parent["id"], "chapter_started", {"number": 1})
            else:
                state = deepcopy(parent["result"])
                if fault == "source_hash":
                    state[authored.RECEIPTS]["chapter:1"]["source_sha256"] = "0" * 64
                else:
                    state["expected_story_revision"] -= 1
                tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(state), parent["id"]))
    before = domain.db.all("SELECT * FROM tasks ORDER BY id")
    response = client.get(f"/api/tasks/{parent['id']}/author-source-recheck-preview")
    assert response.status_code == 409, response.text
    assert domain.db.all("SELECT * FROM tasks ORDER BY id") == before


@pytest.mark.parametrize("field", [bridge.KEY, bridge.RECEIPT, authored.KEY, authored.SNAPSHOT])
def test_deleted_marker_never_downgrades_or_dispatches(settings, domain, story, client, field):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, frames, _ = child_worker(settings, domain, client, parent)
    worker.task["result"].pop(field)
    with pytest.raises(DomainError):
        run_source(worker)
    assert frames == []


@pytest.mark.parametrize("nonce", ["opaque/slash/newline\nnonce", "literal-%2F-%0A-opaque"])
def test_same_nonce_query_and_extra_payload_rejections(settings, domain, story, client, nonce):
    _, parent, _ = source_unverified(settings, domain, story)
    body = request(ready(client, parent), nonce=nonce)
    path = f"/api/tasks/{parent['id']}/author-source-rechecks"
    for key, value in [
        ("clarification", "无补充"),
        ("source", "改原话"),
        ("account", "openai_1"),
        ("budget_limit", 80),
        ("chapters", 36),
    ]:
        assert client.post(path, json={**body, key: value}).status_code == 422
    response = client.post(path, json=body)
    assert response.status_code == 202, response.text
    assert client.get(path, params={"client_request_id": nonce}).json()["task"]["id"] == response.json()["id"]


def test_stale_budget_basis_and_new_origin_configuration_change_block(settings, domain, story, client):
    _, parent, _ = source_unverified(settings, domain, story)
    body = request(ready(client, parent))
    with Worker(settings).budget.db(True) as tx:
        tx.execute("UPDATE calls SET charged=charged+1 WHERE task_id=?", (parent["id"],))
    response = client.post(f"/api/tasks/{parent['id']}/author-source-rechecks", json=body)
    assert response.status_code == 409 and response.json()["code"] == "author_recheck_basis"
    worker, frames, _ = child_worker(settings, domain, client, parent)
    from dataclasses import replace

    settings.providers["deepseek"] = replace(settings.providers["deepseek"], model="changed-model")
    with pytest.raises(DomainError, match="账户"):
        run_source(worker)
    assert frames == []


def test_late_external_dispatch_after_creation_blocks_without_clearing_original(
    settings, domain, story, client
):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, frames, _ = child_worker(settings, domain, client, parent)
    with domain.db.connect(True) as tx:
        domain.db.event(tx, parent["id"], "chapter_started", {"number": 1})
    with pytest.raises(DomainError, match="派发"):
        run_source(worker)
    assert frames == []


def test_child_runs_source_then_stages_a_draft_without_overwriting_manuscript(
    settings, domain, story, client
):
    _, parent, _ = source_unverified(settings, domain, story)
    worker, frames, _ = child_worker(settings, domain, client, parent)
    asyncio.run(worker.workflow())
    actual = domain.task(worker.task["id"])
    assert actual["status"] == "needs_review", actual["error"]
    assert actual["result"][authored.RECEIPTS]["chapter:1"]["state"] == "ready"
    assert actual["result"]["waiting_artifact"]
    assert [f["任务"]["动作"] for f in frames[:4]] == [
        authored.ACTION,
        "仅依据作者原话整理本场景情节",
        classification.ACTION,
        "按章纲写完整正文",
    ]
    assert all(
        not c["content"]
        for c in domain.db.all("SELECT content FROM chapters WHERE branch_id=?", (parent["branch_id"],))
    )


def test_prior_genuine_clarification_is_preserved_verbatim_when_explicitly_upgraded(
    settings, domain, story, client
):
    from helpers.author_clarification_fixture import (
        source_unverified as ambiguous_parent,
        native as ambiguity_native,
        ADDITION,
    )
    from test_author_source_clarification import ready_request
    from ainovel import author_source_clarification as clarification

    _, parent, _ = ambiguous_parent(settings, domain, story)
    response = client.post(
        f"/api/tasks/{parent['id']}/author-source-clarifications", json=ready_request(client, parent)
    )
    assert response.status_code == 202, response.text
    writer = Worker(settings)
    writer.task = writer.claim()
    writer.attempt = writer.task["attempt"]
    ambiguity_native(writer, observe_unknown=True)
    with pytest.raises(DomainError):
        asyncio.run(writer.workflow())
    writer.checkpoint(writer.task["result"], "blocked_runtime", "完整返回的来源仍待核。")
    stopped = domain.task(writer.task["id"])
    preview = ready(client, stopped)
    assert (
        ADDITION in preview["current_source"]["text"] and ADDITION not in preview["original_source"]["text"]
    )
    response = client.post(f"/api/tasks/{stopped['id']}/author-source-rechecks", json=request(preview))
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["payload"]["plots"] == parent["payload"]["plots"]
    assert child["result"][clarification.RECEIPT] == stopped["result"][clarification.RECEIPT]
    assert child["result"][bridge.RECEIPT]["source_entry"] == preview["current_source"]
    assert authored.validate_task(child, domain)
    assert bridge.ensure_runtime(child, domain, settings) is None


def test_new_policy_known_preserve_stop_keeps_original_clarification_action_available(
    settings, domain, story, client
):
    from ainovel import author_source_clarification as clarification

    _, parent, _ = source_unverified(settings, domain, story)
    worker, _, _ = child_worker(settings, domain, client, parent, preserve="unknown")
    with pytest.raises(DomainError):
        run_source(worker)
    worker.checkpoint(worker.task["result"], "blocked_runtime", "完整保持待核。")
    stopped = domain.task(worker.task["id"])
    preview = client.get(f"/api/tasks/{stopped['id']}/author-source-clarification-preview")
    assert preview.status_code == 200, preview.text
    response = client.post(
        f"/api/tasks/{stopped['id']}/author-source-clarifications",
        json={
            "version": clarification.VERSION,
            "client_request_id": "genuine-clarification-after-upgrade",
            "preview_basis_sha256": preview.json()["request_basis"]["preview_basis_sha256"],
            "clarification": "本章请求只表示提出申请，仍未获准查阅。",
            "actor": "author",
            "reason": "明确作者补充。",
        },
    )
    assert response.status_code == 202, response.text
    child = response.json()
    assert child["result"][authored.KEY] == classification.VERSION
    assert authored.validate_task(child, domain)
    assert child["payload"]["plots"] == parent["payload"]["plots"]


@pytest.mark.parametrize(
    "source_account,target_account", [("deepseek", "openai_1"), ("openai_1", "openai_2")]
)
def test_formal_account_descendant_keeps_upgrade_origin_and_does_not_replay_sources(
    settings, domain, story, client, source_account, target_account
):
    from decimal import Decimal

    _, parent, _ = source_unverified(settings, domain, story, account=source_account)
    worker, frames, _ = child_worker(settings, domain, client, parent)
    kind = settings.providers[source_account].kind
    original_limit = settings.limits[kind]

    def stop_before_body(frame, value):
        if frame["任务"]["动作"] == classification.ACTION:
            settings.limits[kind] = Decimal("0")
        return value

    frames = native(worker, transform=stop_before_body)
    with pytest.raises(DomainError, match="预算"):
        asyncio.run(worker.workflow())
    worker.checkpoint(worker.task["result"], "blocked_runtime", "正文已知未发送，显式换账户。")
    settings.limits[kind] = original_limit
    stopped = domain.task(worker.task["id"])
    assert stopped["result"][authored.RECEIPTS]["chapter:1"]["state"] == "ready"
    assert not any(f["任务"]["动作"] == "按章纲写完整正文" for f in frames)
    response = client.post(
        f"/api/tasks/{stopped['id']}/continue",
        json={
            "account": target_account,
            "budget_limit": 2.0,
            "acknowledge_unknown": True,
            "reason": "同源保持已完成且正文已知未发送；显式换账户。",
            "client_request_id": "upgraded-explicit-account-continuation",
        },
    )
    assert response.status_code == 202, response.text
    checker = Worker(settings)
    checker.task = checker.claim()
    checker.attempt = checker.task["attempt"]
    new_frames = native(checker)
    assert bridge.ensure_runtime(checker.task, domain, settings) is None
    asyncio.run(checker.workflow())
    assert domain.task(checker.task["id"])["status"] == "needs_review"
    assert new_frames and new_frames[0]["任务"]["动作"] == "按章纲写完整正文"
    assert not any(
        f["任务"]["动作"] in {authored.ACTION, classification.ACTION, "仅依据作者原话整理本场景情节"}
        for f in new_frames
    )


def test_native_seed_can_use_unique_nonces_for_two_independent_novels_in_one_database(
    settings, domain, story, client
):
    _, first, _ = source_unverified(
        settings, domain, story, client=client, client_request_id="isolated-seed-first-novel"
    )
    novel = domain.create_novel("另一部匿名验证作品", "只用于同库隔离的工程验证。")
    chapter = domain.create_chapter(novel["branch_id"], "初章")
    other = {**novel, "chapter_id": chapter["id"]}
    _, second, _ = source_unverified(
        settings, domain, other, client=client, client_request_id="isolated-seed-second-novel"
    )
    assert first["id"] != second["id"] and first["novel_id"] != second["novel_id"]
    assert domain.task(first["id"])["result"] == first["result"]
