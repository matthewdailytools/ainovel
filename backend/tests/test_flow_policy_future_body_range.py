"""A policy child owns the full remaining plan, including later body dispatches."""

from copy import deepcopy

import pytest

from ainovel.budget import Budget
from ainovel.db import dumps
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
from test_flow_policy_native_body_boundary import module_dispatch


@pytest.fixture
def full_plan(domain, parent):
    plots = [
        {"number": number, "title": f"第{number}章", "plot": "保留原有调查安排。", "fixed": []}
        for number in range(1, 37)
    ]
    return update_task(
        domain,
        parent["id"],
        payload=dumps({**parent["payload"], "chapters": 36, "plots": plots}),
        result={**parent["result"], "plan": {**parent["result"]["plan"], "chapters": deepcopy(plots)}},
    )


@pytest.mark.parametrize(
    ("position", "scope", "action", "blocked"),
    [
        (2, "same", "writing.expand_plot", True),
        (3, "same", "writing.expand_plot", True),
        (5, "same", "writing.expand_plot", True),
        (36, "same", "writing.expand_plot", True),
        (3, "same", "review.chapter", False),
        (36, "same", "review.chapter", False),
        (3, "other", "writing.expand_plot", False),
        (1, "same", "writing.expand_plot", False),
    ],
)
def test_native_future_body_unknown_blocks_full_remaining_plan_only(
    client, domain, settings, full_plan, position, scope, action, blocked
):
    current, next_chapter = clean_next(domain, full_plan)
    if position == 2:
        target = next_chapter
    elif position == 1:
        target = domain.chapter(domain.artifact(current["result"]["latest_artifact"])["chapter_id"])
    else:
        target = domain.create_chapter(current["branch_id"], f"未写第{position}章", position)
    if scope == "other":
        other = domain.create_novel("不同工作版本对照")
        target = domain.create_chapter(other["branch_id"], "不同工作版本第三章", position)
    current = update_task(
        domain,
        current["id"],
        result={
            **current["result"],
            "expected_story_revision": domain.branch(current["branch_id"])["story_revision"],
        },
    )
    frozen = request(client, current)
    chapters_before = domain.db.all("SELECT * FROM chapters ORDER BY id")
    task, event, calls = module_dispatch(client, domain, settings, current, target, action)
    assert event["request"]["target_version"]["id"] == target["id"]
    assert task["status"] == "blocked_runtime"
    response, started = preview(client, current), submit(client, current, frozen)
    if blocked:
        assert response.status_code == started.status_code == 409
        assert response.json()["code"] == started.json()["code"] == "flow_checkpoint"
        assert domain.task(current["id"]) == current
        assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (frozen["client_request_id"],)) == []
    else:
        assert response.status_code == 200, response.text
        assert started.status_code == 202, started.text
        assert started.json()["result"]["next_chapter"] == 2
        assert started.json()["result"]["plan"] == current["result"]["plan"]
    assert ledger_rows(Budget(settings)) == calls
    assert domain.task(task["id"]) == task
    assert domain.db.all("SELECT * FROM chapters ORDER BY id") == chapters_before
    assert domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],)) == []


@pytest.mark.parametrize("number", [2, 3, 5, 36])
def test_later_real_chapter_started_number_is_checked_again_on_submit(client, domain, full_plan, number):
    current, _ = clean_next(domain, full_plan)
    frozen = request(client, current)
    other = other_workflow(domain, current, status="cancelled")
    with domain.db.connect(True) as tx:
        domain.db.event(tx, other["id"], "chapter_started", {"number": number})
    response = submit(client, current, frozen)
    assert response.status_code == 409 and response.json()["code"] == "flow_checkpoint"
    assert domain.task(current["id"]) == current
    assert domain.db.all("SELECT * FROM task_requests WHERE key=?", (frozen["client_request_id"],)) == []


@pytest.mark.parametrize("number", [1, 0, True, "3", 3.0])
def test_prior_or_unreliable_started_number_does_not_gain_future_scope(client, domain, full_plan, number):
    current, _ = clean_next(domain, full_plan)
    other = other_workflow(domain, current, status="cancelled")
    with domain.db.connect(True) as tx:
        domain.db.event(tx, other["id"], "chapter_started", {"number": number})
    response = preview(client, current)
    assert response.status_code == 200, response.text
    assert submit(client, current, request(client, current)).status_code == 202
