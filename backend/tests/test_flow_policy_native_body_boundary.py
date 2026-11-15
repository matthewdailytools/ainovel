"""Real module dispatches share the clean next-chapter policy boundary."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from test_conditional_continuation import legacy_checkpoint as legacy_checkpoint
from test_flow_policy_continuation import (
    ledger_rows,
    parent as parent,
    preview,
    request,
    submit,
    update_task,
)
from test_flow_policy_continuation_boundaries import clean_next, other_workflow


def module_dispatch(client, domain, settings, current, target, action, *, selection=False):
    """Use the production HTTP entry point, Worker and provider timeout ledger."""
    sent = []

    def timeout(wire):
        sent.append(str(wire.url))
        assert wire.url.host == "api.deepseek.com"
        raise httpx.ReadTimeout("isolated sent request timeout", request=wire)

    body = {
        "action": action,
        "novel_id": domain.branch(target["branch_id"])["novel_id"],
        "branch_id": target["branch_id"],
        "chapter_id": target["id"],
        "base_revision": target["revision"],
        "instruction": "隔离原创测试：检查木箱，保持本章指定结果。",
        "account": "deepseek",
        "client_request_id": "formal-next-chapter-module-dispatch",
        "budget_limit": 1,
        "max_output_tokens": 256,
    }
    if selection:
        # The production API permits a zero-width selection on an empty chapter.
        # The boundary concerns its actual dispatched text_patch request.
        body.update(selection_start=0, selection_end=0, selection_text="")
    started = client.post("/api/modules", json=body)
    assert started.status_code == 202, started.text
    identifier = started.json()["id"]
    ledger = Budget(settings)
    adapter = ModelClient(settings, ledger, transport=httpx.MockTransport(timeout))
    worker = Worker(settings, adapter)
    asyncio.run(worker.run_once())
    task = domain.task(identifier)
    assert task["status"] == "blocked_runtime"
    assert task["kind"] == "module" and task["payload"]["action"] == action
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (identifier,)
    )
    assert len(events) == 1
    event = json.loads(events[0]["payload"])
    assert event["request"]["target_version"]["id"] == target["id"]
    assert domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (identifier,)) == []
    calls = ledger_rows(ledger)
    assert len(calls) == len(sent) == 1
    assert calls[0]["task_id"] == identifier and calls[0]["status"] == "unknown"
    assert calls[0]["charged"] is None and calls[0]["reserved"] > 0
    # A second worker poll cannot turn an uncertain request into an automatic resend.
    asyncio.run(worker.run_once())
    assert len(sent) == 1 and ledger_rows(ledger) == calls
    assert domain.task(current["id"]) == current
    return task, event, calls


@pytest.mark.parametrize(
    ("scope", "action", "blocked", "dispatch_action"),
    [
        ("next", "writing.expand_plot", True, "writing.expand_plot"),
        ("next", "writing.rewrite_selection", True, "writing.rewrite_selection"),
        ("other_branch", "writing.expand_plot", False, "writing.expand_plot"),
        ("prior", "writing.expand_plot", False, "writing.expand_plot"),
        ("next", "review.chapter", False, "审查正文"),
        ("next", "memory.extract", False, "从正文提取事实候选与章节摘要"),
        ("next", "writing.advise", False, "writing.advise"),
    ],
)
def test_native_unknown_module_body_only_blocks_its_same_branch_next_chapter(
    client, domain, settings, parent, scope, action, blocked, dispatch_action
):
    current, chapter = clean_next(domain, parent)
    frozen = request(client, current)
    target = chapter
    if scope == "other_branch":
        other = domain.create_novel("独立版本正文控制")
        target = domain.create_chapter(other["branch_id"], "其他作品第一章")
    elif scope == "prior":
        target = domain.chapter(domain.artifact(parent["result"]["latest_artifact"])["chapter_id"])
    chapter_before = deepcopy(domain.chapter(chapter["id"]))
    task, event, calls = module_dispatch(
        client, domain, settings, current, target, action, selection=action == "writing.rewrite_selection"
    )
    assert event["action"] == dispatch_action
    response = preview(client, current)
    started = submit(client, current, frozen)
    if blocked:
        assert response.status_code == started.status_code == 409
        assert response.json()["code"] == started.json()["code"] == "flow_checkpoint"
        assert domain.task(current["id"]) == current
        assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (frozen["client_request_id"],)) == []
    else:
        assert response.status_code == 200, response.text
        assert started.status_code == 202, started.text
    assert ledger_rows(Budget(settings)) == calls
    assert domain.task(task["id"]) == task
    assert domain.chapter(chapter["id"]) == chapter_before


@pytest.mark.parametrize("kind", ["workflow", "module"])
@pytest.mark.parametrize(
    "action",
    [
        "writing.expand_plot",
        "writing.rewrite_selection",
        "revision.targeted",
        "reference.draft",
        "根据情节补写正文",
        "按章纲写完整正文",
        "用局部替换修订正文",
        "按确认参考应用方案原创试写",
    ],
)
def test_every_registered_body_action_and_native_alias_is_checked_after_preview(
    client, domain, parent, kind, action
):
    current, chapter = clean_next(domain, parent)
    frozen = request(client, current)
    other = other_workflow(domain, current)
    update_task(domain, other["id"], kind=kind)
    with domain.db.connect(True) as tx:
        domain.db.event(
            tx,
            other["id"],
            "step_dispatched",
            {"action": action, "request": {"target_version": {"id": chapter["id"], "revision": 0}}},
        )
    response = submit(client, current, frozen)
    assert response.status_code == 409 and response.json()["code"] == "flow_checkpoint"
    assert domain.task(current["id"]) == current
    assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (frozen["client_request_id"],)) == []


@pytest.mark.parametrize(
    ("action", "target"),
    [
        ("reference.plan", "bound"),
        ("reference.check", "bound"),
        ("review.chapter", "bound"),
        ("逐项对照作者情节解释与本次正文", "bound"),
        ("仅修正结构化产物格式", "bound"),
        ("修正已知达到输出上限的结构化产物", "bound"),
        ("writing.expand_plot", "missing"),
        ("writing.expand_plot", "invalid"),
        ({"unexpected": "writing.expand_plot"}, "bound"),
    ],
)
def test_readonly_or_unbound_module_dispatch_never_guesses_a_next_body(
    client, domain, parent, action, target
):
    current, chapter = clean_next(domain, parent)
    other = other_workflow(domain, current)
    update_task(domain, other["id"], kind="module")
    target_version = (
        {"id": chapter["id"], "revision": 0}
        if target == "bound"
        else {"id": {"not": "a chapter id"}}
        if target == "invalid"
        else {}
    )
    with domain.db.connect(True) as tx:
        domain.db.event(
            tx,
            other["id"],
            "step_dispatched",
            {"action": action, "request": {"target_version": target_version}},
        )
    response = preview(client, current)
    assert response.status_code == 200, response.text
    assert submit(client, current, request(client, current)).status_code == 202


def test_wrapped_revision_keeps_its_original_body_evidence(client, domain, parent):
    current, chapter = clean_next(domain, parent)
    other = other_workflow(domain, current)
    update_task(domain, other["id"], kind="module")
    with domain.db.connect(True) as tx:
        for action in ("用局部替换修订正文", "仅修正结构化产物格式"):
            domain.db.event(
                tx,
                other["id"],
                "step_dispatched",
                {"action": action, "request": {"target_version": {"id": chapter["id"], "revision": 0}}},
            )
    response = preview(client, current)
    assert response.status_code == 409 and response.json()["code"] == "flow_checkpoint"


@pytest.mark.parametrize(
    "kind",
    ["source_interpretation_correction", "source_corrected_candidate_recheck", "candidate_recheck"],
)
def test_readonly_task_kind_does_not_become_body_producer_from_raw_label(client, domain, parent, kind):
    current, chapter = clean_next(domain, parent)
    other = other_workflow(domain, current)
    update_task(domain, other["id"], kind=kind)
    with domain.db.connect(True) as tx:
        domain.db.event(
            tx,
            other["id"],
            "step_dispatched",
            {
                "action": "writing.expand_plot",
                "request": {"target_version": {"id": chapter["id"], "revision": 0}},
            },
        )
    response = preview(client, current)
    assert response.status_code == 200, response.text
    assert submit(client, current, request(client, current)).status_code == 202
