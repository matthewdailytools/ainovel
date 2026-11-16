"""The first AI inventory is evidence to inspect, never author truth."""

import asyncio
from copy import deepcopy
import pytest

from ainovel.workflow_plan_preparation import prepare
from ainovel import workflow_plan_recipient_scope as scope
from test_workflow_plan_recipient_scope import ScopeModel, worker_for


@pytest.fixture(autouse=True)
def historical_second_policy(monkeypatch):
    """This file retains the original /2 disagreement and no-forcing contract."""
    monkeypatch.setattr(scope, "NEW_TASK_VERSION", scope.CURRENT_VERSION)


class Disagrees(ScopeModel):
    def answer(self, frame):
        value = super().answer(frame)
        if frame["任务"].get("动作") == scope.ACTION:
            value["observations"][0].update(kind="recipient_exclusivity", restriction_evidence="SCOPE0001")
        elif frame["任务"].get("动作") == "写前解释AI工作章纲":
            value["items"][0].update(
                scope_binding_status="unknown",
                scope_binding_reason="普通寄往原话不支持先行观察的only；不改变原收件人或事件阶段。",
            )
        return value


def test_valid_scope_disagreement_stops_without_forcing_first_model_or_open_choices(settings, domain, story):
    model = Disagrees()
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert not asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    assert row[scope.KEY] == scope.CURRENT_VERSION
    assert row["status"] == "needs_review" and row["round"] == 0 and row["choices"] == []
    assert row["preservation_checks"] == []
    assert row["interpretations"][0]["contract"]["items"][0]["recipient_scope"] == "unspecified"
    assert row["interpretations"][0]["recipient_scope_bindings"]["items"][0]["status"] == "unknown"
    assert len(model.frames) == 2 and not domain.db.all("SELECT * FROM artifacts")
    saved = deepcopy(row)
    assert not asyncio.run(prepare(worker, plot, chapter))
    assert len(model.frames) == 2 and row["interpretations"] == saved["interpretations"]


def test_supported_disagreement_is_still_invalid_and_true_only_is_not_erased(settings, domain, story):
    model = ScopeModel(source="甲只向校核站寄送记录抄本。", only=True)
    worker, plot, chapter = worker_for(settings, domain, story, model)
    assert asyncio.run(prepare(worker, plot, chapter))
    row = worker.task["result"]["plot_preparations"]["1"]
    binding = row["interpretations"][0]["recipient_scope_bindings"]
    assert scope.bindings_supported(binding)
    assert row["prepared_contract"]["items"][0]["recipient_scope"] == "only"
    bad = deepcopy(row["interpretations"][0]["contract"])
    bad["items"][0]["recipient_scope"] = "unspecified"
    assert not scope.bindings_valid(bad, binding, row["phases"]["recipient_scope:0"]["response"])


def test_named_positive_transfer_cannot_claim_supported_after_stage_or_object_drift():
    from test_workflow_plan_recipient_scope import inventory, output, SOURCE

    receipt = inventory(SOURCE)
    receipt["version"] = scope.CURRENT_VERSION
    for field, wrong in [
        ("transfer_scope", "attempted"),
        ("transfer_object", "原件"),
        ("recipient", "另一收件人"),
    ]:
        value = output()
        for item in value["items"]:
            item.update(scope_binding_status="supported", scope_binding_reason="待核对原话。")
        value["items"][0][field] = wrong
        assert scope.binding_issues(value, receipt)
        value["items"][0].update(
            scope_binding_status="unknown",
            scope_binding_reason="保留原提案与先行观察分歧，回到原话核对，不硬跟随。",
        )
        assert not scope.binding_issues(value, receipt)
