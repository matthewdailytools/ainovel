"""Recipient-boundary contract tests; engineering observations are not model quality."""

import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from ainovel.db import DomainError, dumps
from ainovel.models import ModuleInput, WorkflowInput
from ainovel.plot_audit import (
    AuthorPlot,
    ScopedAuthorPlot,
    LEGACY_VERSION,
    VERSION,
    execution_version,
    independent_flows,
    interpret_plot,
)
from ainovel.plot_review import attach_plot_review, bind_plot_audit, review_passed, unresolved_without_blocker
from ainovel.worker import Worker
from test_plot_audit import Integrated, AUTHOR

TEXT = "甲把同一条线索告诉乙，也向丙展示这条线索。"


def item(scope="only", condition="", stage="completed"):
    return {
        "id": "P01",
        "author_evidence": "只向乙",
        "actor": "甲",
        "action": "告知",
        "object": "线索",
        "direction": "甲向乙",
        "result": "告知",
        "certainty": "explicit",
        "transfer_object": "线索",
        "supplier": "甲",
        "recipient": "乙",
        "transfer_scope": stage,
        "recipient_scope": scope,
        "scope_condition": condition,
    }


def check():
    return {
        "item_id": "P01",
        "result": "pass",
        "observed": "甲告诉乙",
        "evidence": "先前模型依据",
        "reason": "原模型认为相符",
        "repair": "",
    }


class Flows:
    def __init__(
        self,
        extra="unknown",
        *,
        stage="completed",
        certainty="explicit",
        different_object=False,
        different_supplier=False,
    ):
        self.extra, self.stage, self.certainty = extra, stage, certainty
        self.different_object, self.different_supplier = different_object, different_supplier
        self.inputs = []

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        self.inputs.append(deepcopy(instruction))
        names = {name: key for key, name in instruction["实体别名对照"].items()}
        obj = next(k for k, v in instruction["待识别物品名称"].items() if v == "线索")
        rows = [
            {
                "object_id": obj,
                "supplier_id": names["甲"],
                "recipient_id": names["乙"],
                "stage": "completed",
                "certainty": "explicit",
                "evidence": "FLOW0001",
                "note": "正向观察",
            }
        ]
        if self.extra is not None:
            rows.append(
                {
                    "object_id": "other" if self.different_object else obj,
                    "supplier_id": names["乙"] if self.different_supplier else names["甲"],
                    "recipient_id": names[self.extra] if self.extra != "unknown" else "unknown",
                    "stage": self.stage,
                    "certainty": self.certainty,
                    "evidence": "FLOW0001",
                    "note": "额外方向，不推断未知身份",
                }
            )
        result = schema.model_validate({"flows": rows, "note": "工程观察，非真实模型识别"}).model_dump()
        assert not validate(result)
        return result


class ScopedAudit(Flows):
    task = {"result": {"plot_contract_version": VERSION}}

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        action = instruction["动作"]
        if action == "只从本次正文独立提取物品与信息流向":
            return await super().grounded(instruction, schema, context, validate, **kwargs)
        if action == "仅依据作者原话整理本场景情节":
            value = {"items": [{k: v for k, v in item().items() if k != "id"}], "note": "工程原话解释"}
            value["items"][0]["author_evidence"] = "AUTHOR0001"
        else:
            value = {"checks": [{**check(), "evidence": "PLOT001P0001"}], "summary": "原模型对照判pass"}
        result = schema.model_validate(value).model_dump()
        assert not validate(result)
        return result


def test_additional_recipient_guard_survives_merge_as_unknown_with_original_pass_preserved():
    original = {"checks": [], "findings": [], "summary": "原普通审稿"}
    result = asyncio.run(
        attach_plot_review(ScopedAudit(), original, "甲只向乙说明线索。", TEXT, 1, "author_instruction")
    )
    audited = result["plot_audit"]
    assert audited["version"] == VERSION and audited["status"] == "unverified"
    assert audited["checks"][0]["result"] == "unknown"
    assert audited["checks"][0]["original_model_check"]["result"] == "pass"
    assert audited["original_model_summary"] == "原模型对照判pass"
    assert not audited["findings"] and not result["findings"]
    assert not review_passed(result) and unresolved_without_blocker(result)
    assert result["summary_before_plot_audit"] == "原普通审稿" and original["summary"] == "原普通审稿"


@pytest.mark.parametrize("condition", ["", "核验前"])
def test_only_with_forward_and_unknown_recipient_holds_without_guessing_or_blocker(condition):
    source = item(condition=condition)
    rows = [check()]
    original = deepcopy(rows[0])
    result = asyncio.run(independent_flows(Flows(), [source], TEXT, rows, version=VERSION))
    assert rows[0]["result"] == "unknown" and rows[0]["original_model_check"] == original
    override = rows[0]["guard_override"]
    assert override["code"] == "restricted_recipient_unverified" and override["scope_condition"] == condition
    assert override["observations"][0]["recipient_id"] == "unknown"
    assert rows[0]["evidence"] == TEXT and len(result["flows"]) == 2
    assert not rows[0]["result"] == "fail" and "unknown是谁" in rows[0]["reason"]


def test_known_other_recipient_is_kept_for_scope_and_time_review_not_forced_failure():
    second = {**item(), "id": "P02", "supplier": "丙", "recipient": "乙", "transfer_object": "别的内容"}
    rows = [check(), {**check(), "item_id": "P02"}]
    observed = asyncio.run(independent_flows(Flows("丙"), [item(), second], TEXT, rows, version=VERSION))
    assert rows[0]["result"] == "unknown"
    extra = rows[0]["guard_override"]["observations"][0]
    assert observed["entities"][extra["recipient_id"]] == "丙"


@pytest.mark.parametrize(
    "scope,version", [("unspecified", VERSION), ("unrestricted", VERSION), ("only", LEGACY_VERSION)]
)
def test_no_explicit_v2_scope_never_invents_a_recipient_restriction(scope, version):
    rows = [check()]
    worker = Flows()
    asyncio.run(independent_flows(worker, [item(scope)], TEXT, rows, version=version))
    assert rows[0]["result"] == "pass" and "guard_override" not in rows[0]
    assert ("情节核对契约版本" in worker.inputs[0]) is (version == VERSION)


@pytest.mark.parametrize(
    "options",
    [
        {"stage": "planned"},
        {"stage": "refused"},
        {"stage": "unknown"},
        {"certainty": "ambiguous"},
        {"different_object": True},
        {"different_supplier": True},
        {"extra": None},
    ],
)
def test_nonactual_or_unrelated_observation_does_not_create_a_new_current_violation(options):
    rows = [check()]
    asyncio.run(independent_flows(Flows(**options), [item()], TEXT, rows, version=VERSION))
    assert rows[0]["result"] == "pass"


def test_future_plan_is_not_required_to_have_already_happened():
    rows = [check()]
    worker = Flows()
    result = asyncio.run(independent_flows(worker, [item(stage="planned")], TEXT, rows, version=VERSION))
    assert result["status"] == "not_applicable" and not worker.inputs and rows[0]["result"] == "pass"


def test_binder_never_stamps_legacy_items_as_scoped_v2():
    legacy = {"items": [{"id": "P01"}], "checks": []}
    assert bind_plot_audit(legacy, "原话", TEXT, "author_plot")["version"] == LEGACY_VERSION
    assert legacy == {"items": [{"id": "P01"}], "checks": []}
    with pytest.raises(DomainError, match="未执行对应"):
        bind_plot_audit({**legacy, "version": VERSION}, "原话", TEXT, "author_plot")


def test_new_queue_marks_v2_but_old_nonce_replay_does_not_upgrade_existing_task(domain, story):
    payload = ModuleInput(
        action="review.chapter",
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        instruction="只向乙",
        account="openai_1",
        plot_contract_check=True,
        client_request_id="legacy-nonce-replay",
    ).model_dump()
    queued = domain.queue("module", payload, story["branch_id"])
    assert queued["result"]["plot_contract_version"] == VERSION
    legacy = deepcopy(queued["result"])
    legacy.pop("plot_contract_version")
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy), queued["id"]))
    replay = domain.queue("module", payload, story["branch_id"])
    assert replay["id"] == queued["id"] and replay["result"] == legacy
    assert execution_version(type("W", (), {"task": replay})()) == LEGACY_VERSION
    payload.update(client_request_id="false-no-new-marker", plot_contract_check=False)
    assert "plot_contract_version" not in domain.queue("module", payload, story["branch_id"])["result"]


@pytest.mark.parametrize("marker", [None, LEGACY_VERSION, VERSION])
def test_account_continuation_preserves_frozen_contract_without_upgrading(marker, domain, story):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="automatic",
        brief="原创简报",
        chapters=1,
        account="openai_1",
        plot_contract_check=True,
    ).model_dump()
    current = domain.queue("workflow", payload, story["branch_id"])
    progress = deepcopy(current["result"])
    if marker is None:
        progress.pop("plot_contract_version")
    else:
        progress["plot_contract_version"] = marker
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused',result=? WHERE id=?", (dumps(progress), current["id"]))
    current = domain.task(current["id"])
    next_payload = {**payload, "account": "openai_2", "client_request_id": "continuation-keeps-marker"}
    continued = domain.continue_workflow(
        current, next_payload, {"reason": "工程换账户", "acknowledge_unknown": False}
    )
    assert continued["result"].get("plot_contract_version") == marker


def test_v1_reference_schema_and_paid_parse_step_recover_without_new_scope_calls(settings, domain, story):
    payload = ModuleInput(
        action="review.chapter",
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        instruction=AUTHOR,
        account="openai_1",
        plot_contract_check=True,
    ).model_dump()
    queued = domain.queue("module", payload, story["branch_id"])
    legacy = deepcopy(queued["result"])
    legacy.pop("plot_contract_version")
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy), queued["id"]))
    model = Integrated()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    first = asyncio.run(interpret_plot(worker, AUTHOR))
    assert len(model.audits) == 1 and "recipient_scope" not in first["items"][0]
    assert "情节核对契约版本" not in model.audits[0]["任务"]
    assert set(AuthorPlot.model_json_schema()["$defs"]["PlotItem"]["properties"]).isdisjoint(
        {"recipient_scope", "scope_condition"}
    )
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='queued',attempt=NULL,lease_until=0 WHERE id=?", (queued["id"],))
    restored = Worker(settings, model)
    restored.task = restored.claim()
    restored.attempt = restored.task["attempt"]
    assert asyncio.run(interpret_plot(restored, AUTHOR)) == first and len(model.audits) == 1
    with pytest.raises(ValidationError):
        ScopedAuthorPlot.model_validate(first)
