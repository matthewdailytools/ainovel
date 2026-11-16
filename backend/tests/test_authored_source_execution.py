"""Anonymous source-only execution epochs; no provider/network or daily data."""

import asyncio
from copy import deepcopy
import inspect
from unittest.mock import patch

import httpx
import pytest

from ainovel import authored_source_scope as authored
from ainovel import authored_source_classification as classification
from ainovel import authored_source_execution as execution
from ainovel.db import DomainError
from ainovel.models import ModuleInput
from ainovel.plot_audit import interpret_plot
from ainovel.worker import Worker
from helpers.authored_classification_fixture import SOURCE, native, observation
from test_authored_source_classification import module_worker, run


@pytest.fixture(autouse=True)
def deny_real_transport(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Real network/model requests are forbidden in execution tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)


def entry():
    return {
        "key": "module",
        "number": None,
        "kind": "author_instruction",
        "text": SOURCE,
        "sha256": authored.sha(SOURCE),
    }


def legacy_worker(settings, domain, story):
    with patch.object(execution, "NEW_TASK_VERSION", execution.LEGACY_VERSION):
        return module_worker(settings, domain, story)


class CaptureGrounded:
    def __init__(self):
        self.task = {"result": {}}
        self.frame = None

    async def grounded(self, *args, **kwargs):
        arguments = inspect.signature(Worker.grounded).bind(self, *args, **kwargs).arguments
        self.frame = dict(arguments)
        return observation(arguments["instruction"])


def test_old_unmarked_source2_request_schema_feedback_result_and_validation_are_byte_exact():
    # Digests captured from unchanged 96673 legacy code (source SHA 9dd6aea5...),
    # using only the anonymous fixture; no private source or baseline file is needed.
    expected = {
        "instruction": "9b35216f1f16ef8f941a583627aa207c2eaf55cd919fdcce04e4f9eb15eb7a04",
        "context": "bfd3511c528466da99ec6d4012c18dfdc8faf574db8c9fd95945c49e204eae27",
        "max_output": "864a936a35324151e1c79c44a2e903ff2497f52fa892282d340585f493c637f0",
        "contract": "ff51cb231479e38dfb2f4690a8bcf8f868ccc247941d2c57d135338927f32932",
        "diagnostic_label": "66da6c08adb0b558379f2bf52339e432f4245686357874cb2f0835af61d9acc4",
    }
    new = CaptureGrounded()
    actual = asyncio.run(classification.observe(new, entry()))
    assert authored.sha(SOURCE) == "02dca536460b074fdead9c1a6165f7eeb2526e7d5c17e32669ce027d2d407bcc"
    assert authored.sha(actual) == "0ff96be541d7d328ced85bc753bbded06eab49fbc33cc87ba27d0621e86e06df"
    for key, checksum in expected.items():
        assert authored.sha(new.frame[key]) == checksum, key
    assert (
        authored.sha(new.frame["schema"].model_json_schema())
        == "3384a6cc18e6176c408c4d45b5b0b9d73d2818ce2b82e8f1dd80d9637a10829d"
    )
    assert (
        authored.sha(new.frame["repair_feedback"]())
        == "62984439a99b64b53703edd3cfa1da03cf9c40e353cffd74df5106709d18784b"
    )
    bad = deepcopy(actual["inventory"])
    bad["unit_checks"][2].update(status="covered", observation_ids=["O02"])
    assert new.frame["validator"](bad) == ["单元覆盖必须对应所关联观察的动作或限制原句，不能移用别句。"]
    assert execution.KEY not in new.frame["instruction"] and execution.KEY not in actual


def test_exec2_source_witness_event_record_and_three_independent_phases(settings, domain, story):
    worker, payload = module_worker(settings, domain, story)
    result = worker.task["result"]
    assert result[authored.KEY] == classification.VERSION
    assert result[execution.KEY] == execution.VERSION
    assert result[authored.SNAPSHOT][execution.KEY] == execution.VERSION
    assert execution.KEY not in payload and execution.SNAPSHOT not in payload
    assert execution.validate_execution(worker.task, domain) == execution.VERSION
    frames = native(worker)
    run(worker)
    record = worker.task["result"][authored.RECEIPTS]["module"]
    assert set(record["phases"]) == {"observe", "interpret", "preserve"}
    assert record[execution.KEY] == execution.VERSION
    assert record["phases"]["observe"]["response"][execution.SNAPSHOT] == record[execution.SNAPSHOT]
    assert authored.receipt_valid(record, SOURCE, {worker.task["id"]})
    assert frames[0]["任务"][execution.KEY] == execution.VERSION
    assert "直接引用覆盖" in frames[0]["任务"]["直接引用覆盖与请求边界"]
    assert "没有写未获准" in frames[0]["任务"]["直接引用覆盖与请求边界"]
    assert len(frames) == 3
    assert len(frames[-1]["任务"]["全部原话连续单元"]) == 7
    assert not domain.db.all("SELECT * FROM artifacts")
    assert domain.chapter(story["chapter_id"])["content"] == ""


def bad_coverage(frame, value):
    if frame["任务"]["动作"] == authored.ACTION:
        value["unit_checks"][2].update(status="covered", observation_ids=["O02"])
        # Vary only the engineering note, forcing three distinct returned paid cache entries.
        old = frame["任务"].get("前次产物", {}).get("note", "")
        value["note"] = old + "独立工程响应。"
    return value


def test_anonymous_known_three_returned_legacy_frames_replay_exact_cache_without_provider(
    settings, domain, story
):
    worker, _ = legacy_worker(settings, domain, story)
    frames = native(worker, transform=bad_coverage)
    with pytest.raises(DomainError, match="单元覆盖"):
        run(worker)
    assert len(frames) == 3 and execution.KEY not in frames[0]["任务"]
    calls = domain.db.all(
        "SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (worker.task["id"],)
    )
    with worker.budget.db() as db:
        paid = [dict(r) for r in db.execute("SELECT * FROM calls WHERE task_id=?", (worker.task["id"],))]
    assert len(paid) == 3 and all(c["status"] == "settled" for c in paid)
    with pytest.raises(DomainError, match="单元覆盖"):
        run(worker)
    assert len(frames) == 3
    assert (
        domain.db.all("SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (worker.task["id"],))
        == calls
    )
    with worker.budget.db() as db:
        assert [
            dict(r) for r in db.execute("SELECT * FROM calls WHERE task_id=?", (worker.task["id"],))
        ] == paid
    assert worker.task["result"][authored.RECEIPTS]["module"]["phases"]["observe"]["status"] == "started"
    assert not domain.db.all("SELECT * FROM artifacts")


def test_exec2_same_bad_anchor_remains_rejected_with_exact_unit_o_and_anchors(settings, domain, story):
    worker, _ = module_worker(settings, domain, story)
    frames = native(worker, transform=bad_coverage)
    with pytest.raises(DomainError, match="SCOPE0003.*O02|O02.*SCOPE0002"):
        run(worker)
    assert len(frames) == 3
    assert "unit=SCOPE0003→O=O02" in frames[1]["任务"]["定位错误"][0]
    assert "evidence=SCOPE0002" in frames[1]["任务"]["定位错误"][0]
    assert "restriction_evidence=空" in frames[1]["任务"]["定位错误"][0]
    assert "清空或改动" in frames[1]["任务"]["引用修正要求"]
    assert "本阶段没有正文" in frames[1]["任务"]["引用修正要求"]
    assert not domain.db.all("SELECT * FROM artifacts")


def test_detailed_validator_never_changes_valid_invalid_or_unknown_decisions():
    request = classification.observation_request(entry(), execution.VERSION)
    valid = observation(request["instruction"])
    cases = [valid]
    wrong = deepcopy(valid)
    wrong["unit_checks"][2].update(status="covered", observation_ids=["O02"])
    cases.append(wrong)
    unknown = deepcopy(valid)
    unknown["observations"][1]["kind"] = "unknown"
    unknown["unit_checks"][1].update(status="unknown", observation_ids=[])
    cases.append(unknown)
    named = deepcopy(valid)
    named["observations"][1].update(restriction_evidence="SCOPE0003", condition="铜印和货款都未交付")
    cases.append(named)
    choices = {u["source_id"]: u for u in authored.core.units(SOURCE)}
    for value in cases:
        old = authored.core.inventory_issues(value, request["instruction"]["input_sha256"], choices)
        new = execution.inventory_issues_detailed(value, request["instruction"]["input_sha256"], choices)
        assert bool(old) == bool(new) and len(old) == len(new)
    assert not authored.core.inventory_supported(unknown)
    assert (
        request["contract"]
        == classification.observation_request(entry(), execution.LEGACY_VERSION)["contract"]
    )


@pytest.mark.parametrize("verdict", ["fail", "unknown"])
def test_knowledge_exclusion_or_permission_drift_still_requires_independent_preserve(
    settings, domain, story, verdict
):
    worker, _ = module_worker(settings, domain, story)
    frames = native(worker, classification_result=verdict)
    with pytest.raises(DomainError, match="尚未核实"):
        run(worker)
    record = worker.task["result"][authored.RECEIPTS]["module"]
    assert len(frames) == 3 and record["state"] == "unverified"
    assert record["phases"]["preserve"]["response"]["source_checks"][0]["classification_result"] == verdict
    assert not authored.receipt_valid(record, SOURCE, {worker.task["id"]})
    assert not domain.db.all("SELECT * FROM artifacts")


@pytest.mark.parametrize(
    "fault",
    [
        "outer_version",
        "outer_snapshot",
        "inner_marker",
        "event_missing",
        "event_mutated",
        "old_task_upgrade",
        "record_hash",
        "record_version",
    ],
)
def test_removed_forged_or_mismatched_execution_witness_stops_before_dispatch(settings, domain, story, fault):
    if fault == "old_task_upgrade":
        worker, _ = legacy_worker(settings, domain, story)
        worker.task["result"][execution.KEY] = execution.VERSION
    else:
        worker, _ = module_worker(settings, domain, story)
        if fault in {"record_hash", "record_version"}:
            native(worker)
            run(worker)
            record = worker.task["result"][authored.RECEIPTS]["module"]
            record[execution.RECORD_HASH if fault == "record_hash" else execution.KEY] = "0" * 64
        elif fault == "outer_version":
            worker.task["result"].pop(execution.KEY)
        elif fault == "outer_snapshot":
            worker.task["result"].pop(execution.SNAPSHOT)
        elif fault == "inner_marker":
            worker.task["result"][authored.SNAPSHOT].pop(execution.KEY)
        else:
            with domain.db.connect(True) as tx:
                if fault == "event_missing":
                    tx.execute(
                        "DELETE FROM task_events WHERE task_id=? AND kind=?",
                        (worker.task["id"], execution.EVENT),
                    )
                else:
                    tx.execute(
                        "UPDATE task_events SET payload='{}' WHERE task_id=? AND kind=?",
                        (worker.task["id"], execution.EVENT),
                    )
    with pytest.raises(DomainError):
        authored.validate_task(worker.task, domain)


@pytest.mark.parametrize("field", [execution.KEY, execution.SNAPSHOT, execution.RECORD_HASH])
def test_client_template_injection_is_not_a_freeze_api(settings, domain, story, field):
    worker, payload = legacy_worker(settings, domain, story)
    payload.update(client_request_id="different-injected-nonce", **{field: execution.VERSION})
    with pytest.raises(DomainError, match="服务器冻结"):
        domain.queue("module", payload, story["branch_id"])
    assert len(domain.db.all("SELECT * FROM tasks")) == 1


def test_existing_task_cannot_receive_a_server_template_stamp(settings, domain, story):
    worker, _ = legacy_worker(settings, domain, story)
    with pytest.raises(DomainError, match="已存在"):
        execution.freeze_execution(
            domain, worker.task["id"], worker.task["kind"], worker.task["payload"], worker.task["result"]
        )


def test_source1_new_creation_keeps_legacy_two_phase_shape(settings, domain, story):
    with patch.object(authored, "NEW_TASK_VERSION", authored.VERSION):
        worker, _ = module_worker(settings, domain, story)
    assert execution.KEY not in worker.task["result"]
    assert execution.KEY not in worker.task["result"][authored.SNAPSHOT]
    frames = native(worker)
    run(worker)
    assert len(frames) == 2
    assert set(worker.task["result"][authored.RECEIPTS]["module"]["phases"]) == {"observe", "interpret"}
    assert all(execution.KEY not in frame["任务"] for frame in frames)


@pytest.mark.parametrize("fault", ["knowledge_exclusion", "permission_not_granted"])
def test_source_local_semantic_drift_can_be_rejected_only_by_full_independent_preserve(
    settings, domain, story, fault
):
    source = SOURCE.replace(
        "林舟向管理员请求查看公开目录，尚未获准。", "林舟向管理员提出查看公开目录的请求。"
    )
    payload = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.expand_plot",
        instruction=source,
        account="openai_1",
        fixed=["不动手"],
        plot_contract_check=True,
        client_request_id="semantic-drift-execution-test",
    ).model_dump(exclude_none=True)
    domain.queue("module", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    target = 5 if fault == "knowledge_exclusion" else 3

    def drift(frame, value):
        action = frame["任务"]["动作"]
        if action == authored.ACTION:
            if fault == "knowledge_exclusion":
                value["observations"].append(
                    {
                        "observation_id": "O05",
                        "kind": "specific_recipient_exclusion",
                        "supplier": "林舟",
                        "recipient": "管理员",
                        "transfer_object": "全部调查",
                        "transfer_scope": "none",
                        "evidence": "SCOPE0006",
                        "restriction_evidence": "SCOPE0006",
                        "condition": "",
                        "reason": "工程错误提案：扩大成禁止管理员收到全部调查。",
                    }
                )
                value["unit_checks"][5].update(status="covered", observation_ids=["O05"])
            else:
                value["observations"][2].update(
                    transfer_scope="attempted", reason="工程错误提案：请求未获准。"
                )
        elif action == "仅依据作者原话整理本场景情节":
            item = value["items"][target]
            if fault == "knowledge_exclusion":
                item.update(
                    supplier="林舟",
                    recipient="管理员",
                    transfer_object="全部调查",
                    transfer_scope="none",
                    scope_observation_ids=["O05"],
                    result="不得向管理员传递全部调查。",
                )
            else:
                item.update(transfer_scope="attempted", result="提出的请求尚未获准。")
        elif action == classification.ACTION:
            value["source_checks"][target].update(
                classification_result="fail",
                result="fail",
                reason="原句仅给知识边界或请求已提出，不能增加传递禁止或未获准结论。",
            )
            value["item_checks"][target].update(result="fail", reason="错误提案未保留完整原义。")
        return value

    frames = native(worker, transform=drift)
    with pytest.raises(DomainError, match="尚未核实"):
        asyncio.run(interpret_plot(worker, source, source_kind="author_instruction"))
    record = worker.task["result"][authored.RECEIPTS]["module"]
    assert record["state"] == "unverified" and len(frames) == 3
    assert record["phases"]["preserve"]["response"]["item_checks"][target]["result"] == "fail"
    assert (
        frames[-1]["任务"]["全部原话连续单元"][target]["text"] == authored.core.units(source)[target]["text"]
    )
    assert "AI提案不是真值" in next(key for key in frames[-1]["任务"] if key.startswith("先行分类"))
    assert not domain.db.all("SELECT * FROM artifacts")


def test_source_witness_clone_changes_only_new_owner_metadata_and_never_source(settings, domain, story):
    worker, _ = legacy_worker(settings, domain, story)
    original = deepcopy(worker.task)
    payload = {
        **deepcopy(original["payload"]),
        "client_request_id": "new-clone-only-nonce",
        "budget_limit": 0.75,
    }
    progress = deepcopy(original["result"])
    witness = execution.clone_source_witness(original, "a" * 32, payload, progress)
    assert witness["sources"] == original["result"][authored.SNAPSHOT]["sources"]
    assert witness["inherited"] == original["result"][authored.SNAPSHOT]["inherited"]
    assert witness["task_id"] == "a" * 32 and witness["payload_sha256"] == authored.sha(payload)
    assert worker.task == original
    with pytest.raises(DomainError, match="不能升级"):
        execution.clone_source_witness(original, original["id"], payload, {})
    with pytest.raises(DomainError):
        execution.clone_source_witness(original, "b" * 32, {**payload, "instruction": "不能改掉原话"}, {})
    # Workflow payloads can omit branch_id: authoritative identity lives in the task/source witness.
    without_branch = deepcopy(original)
    without_branch["payload"].pop("branch_id")
    request = {**deepcopy(without_branch["payload"]), "client_request_id": "workflow-missing-branch"}
    cloned = execution.clone_source_witness(without_branch, "c" * 32, request, {})
    assert cloned["branch_id"] == original["branch_id"] and "branch_id" not in request


def test_record_and_observation_cannot_disagree_about_the_execution_epoch(settings, domain, story):
    worker, _ = module_worker(settings, domain, story)
    native(worker)
    run(worker)
    record = worker.task["result"][authored.RECEIPTS]["module"]
    observed = record["phases"]["observe"]["response"]
    observed.pop(execution.KEY)
    record["phases"]["observe"]["response_sha256"] = authored.sha(observed)
    assert not authored.receipt_valid(record, SOURCE, {worker.task["id"]})
    with pytest.raises(DomainError, match="已返回来源观察"):
        authored.validate_task(worker.task, domain)
