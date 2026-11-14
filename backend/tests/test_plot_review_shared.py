"""Deterministic routing/acceptance tests, not model detection-quality evidence."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest
from pydantic import ValidationError

from ainovel.db import DomainError, dumps
from ainovel.domain import same_task_payload
from ainovel.models import ModuleInput, WorkflowInput
from ainovel.plot_audit import VERSION
from ainovel.plot_review import module_plot_source
from ainovel.worker import Worker
from test_workflow_scope import SegmentModel, workflow_payload

AUTHOR = "沈砚留下复测标记，只向师傅提出怀疑，尚未确定造假者。"
LEAK = "沈砚向陆栖画出异常读数，说明自己已经留了复测绳。"
TEXT = "沈砚用旧尺测量潮水，留下可以回查的记录。" * 12 + LEAK


class PlotModel(SegmentModel):
    def __init__(self, verdict="fail", certainty="explicit"):
        super().__init__()
        self.verdict, self.certainty = verdict, certainty
        self.frames = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        instruction = value["任务"]
        self.frames.append(deepcopy(value))
        action = instruction.get("动作", instruction.get("action"))
        if action == "仅依据作者原话整理本场景情节":
            output = {
                "items": [
                    {
                        "author_evidence": "AUTHOR0001",
                        "actor": "沈砚",
                        "action": "提出怀疑",
                        "object": "复测异常",
                        "direction": "只向师傅",
                        "result": "尚未确定造假者",
                        "certainty": self.certainty,
                    }
                ],
                "note": "工程提案保留只与尚未，非新增作者条件。",
            }
            if instruction.get("情节核对契约版本") == VERSION:
                output["items"][0].update(recipient_scope="unspecified", scope_condition="")
        elif action == "逐项对照作者情节解释与本次正文":
            output = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": self.verdict,
                        "observed": "向陆栖展示异常并解释复测安排",
                        "evidence": "PLOT001P0001" if self.verdict != "unknown" else "",
                        "reason": "工程替身：预设不符用来验证阻断接线，不能代表真实模型识别能力。",
                        "repair": "核对作者限制的信息接收者，不改变其余情节。",
                    }
                ],
                "summary": "工程逐项核对",
            }
        elif action in {"writing.expand_plot", "按章纲写完整正文"}:
            output = TEXT
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": "plot-" + str(len(self.frames)),
            "model": "offline-plot-fixture",
        }


def module_request(story, action="writing.expand_plot", enabled=True):
    return ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action=action,
        instruction=AUTHOR,
        account="openai_1",
        plot_contract_check=enabled,
    ).model_dump()


@pytest.mark.parametrize("action", ["writing.expand_plot", "review.chapter"])
def test_author_only_source_comparison_is_saved_without_overwriting_original_review_or_manuscript(
    settings, domain, story, action
):
    request = module_request(story, action)
    if action == "review.chapter":
        domain.save(story["chapter_id"], 0, TEXT)
        request["base_revision"] = 1
    original = domain.chapter(story["chapter_id"])
    queued = domain.queue("module", request, story["branch_id"])
    model = PlotModel()
    assert asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    assert task["status"] == "succeeded", task["error"]
    saved = domain.artifact(task["result"]["artifact_id"])
    review = saved["payload"].get("review", saved["payload"])
    audit = review["plot_audit"]
    assert audit["status"] == "failed" and audit["version"] == VERSION
    assert audit["source_kind"] == "author_instruction"
    assert audit["source"]["text_sha256"] == hashlib.sha256(AUTHOR.encode()).hexdigest()
    assert audit["candidate_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert review["summary_before_plot_audit"] == "工程模拟审查"
    assert any(f["severity"] == "blocker" for f in review["findings"])
    if action == "writing.expand_plot":
        assert saved["payload"]["review_passed"] is False
    else:
        assert audit["target"] == {
            "kind": "saved_chapter",
            "chapter_id": original["id"],
            "revision": original["revision"],
            "content_sha256": audit["candidate_sha256"],
        }
    assert domain.chapter(story["chapter_id"]) == original
    source = next(v for v in model.frames if v["任务"].get("动作") == "仅依据作者原话整理本场景情节")
    assert source["任务"]["作者原话"] == AUTHOR
    assert LEAK not in dumps(source) and "genre_profile" not in source["上下文"]
    assert "author_contract" not in source["上下文"]
    assert audit["items"][0]["author_evidence"] == AUTHOR
    assert audit["items"][0]["direction"] == "只向师傅"
    assert audit["items"][0]["result"] == "尚未确定造假者"


@pytest.mark.parametrize("enabled", [False, True])
def test_false_path_has_no_additional_calls_and_unknown_never_becomes_pass(settings, domain, story, enabled):
    request = module_request(story, enabled=enabled)
    queued = domain.queue("module", request, story["branch_id"])
    model = PlotModel("unknown")
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    saved = domain.artifact(task["result"]["artifact_id"])
    assert saved["payload"]["review_passed"] is (not enabled)
    assert bool(saved["payload"].get("plot_audit")) is enabled
    assert model.calls == (4 if enabled else 2)


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
@pytest.mark.parametrize("verdict,certainty", [("unknown", "explicit"), ("pass", "ambiguous")])
def test_workflow_unverified_does_not_force_revision_and_plan_origin_remains_explicit(
    settings, domain, story, monkeypatch, mode, verdict, certainty
):
    request = workflow_payload(story, mode=mode)
    request["plot_contract_check"] = True
    if mode == "assisted":
        request["plots"][0]["plot"] = AUTHOR
    queued = domain.queue("workflow", request, story["branch_id"])
    if mode == "automatic":
        # Frozen pre-preparation tasks retain the exact post-draft audit path.
        legacy = {k: v for k, v in queued["result"].items() if k != "workflow_plan_preparation_version"}
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy), queued["id"]))
    model = PlotModel(verdict, certainty)

    async def unexpected(*args, **kwargs):
        pytest.fail("未知/歧义不能强行改判或自动改写正文")

    monkeypatch.setattr(Worker, "revise_chapter", unexpected)
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    assert task["status"] == "needs_review", task["error"]
    assert "未自动修订" in task["error"] and "有限修订" not in task["error"]
    saved = domain.artifact(task["result"]["waiting_artifact"])
    assert not saved["payload"]["review_passed"]
    assert saved["payload"]["plot_audit"]["status"] == "unverified"
    source = next(v["任务"] for v in model.frames if v["任务"].get("动作") == "仅依据作者原话整理本场景情节")
    source_label = "作者原话" if mode == "assisted" else "本章工作章纲（AI规划）"
    assert source[source_label] == (
        AUTHOR if mode == "assisted" else task["result"]["plan"]["chapters"][0]["plot"]
    )
    assert source["情节来源"] == ("author_plot" if mode == "assisted" else "workflow_plan")
    assert request["brief"] not in dumps(source)
    if mode == "automatic":
        assert "本章工作章纲（AI规划）" in saved["payload"]["plot_audit"]["policy"]


def test_explicit_plot_failures_use_existing_two_revisions_then_preserve_candidate(
    settings, domain, story, monkeypatch
):
    request = workflow_payload(story)
    request["plot_contract_check"] = True
    request["plots"][0]["plot"] = AUTHOR
    queued = domain.queue("workflow", request, story["branch_id"])
    model = PlotModel()
    revisions = []

    async def revise(self, text, instructions, review, context, minimum, maximum):
        revisions.append(review)
        return text + f"第{len(revisions)}次工程修订。"

    monkeypatch.setattr(Worker, "revise_chapter", revise)
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    assert task["status"] == "needs_review", task["error"]
    saved = domain.artifact(task["result"]["waiting_artifact"])
    assert len(revisions) == 2 and not saved["payload"]["review_passed"]
    assert all(any(f["severity"] == "blocker" for f in r["findings"]) for r in revisions)
    assert domain.chapter(saved["chapter_id"])["revision"] == 0
    # The same author-only interpretation is paid once; candidate-specific checks are distinct.
    assert sum(v["任务"].get("动作") == "仅依据作者原话整理本场景情节" for v in model.frames) == 1
    assert sum(v["任务"].get("动作") == "逐项对照作者情节解释与本次正文" for v in model.frames) == 3
    with pytest.raises(DomainError):
        domain.accept(saved["id"], "automatic-must-not-accept-plot-failure", True)
    assert domain.chapter(saved["chapter_id"])["revision"] == 0
    domain.accept(saved["id"], "author-explicitly-accepts-plot-disagreement")
    assert domain.chapter(saved["chapter_id"])["revision"] == 1


def test_direction_envelope_is_not_interpreted_as_plot_or_allowed_to_cross_target(story):
    request = module_request(story)
    basis = {
        "advice_id": "advice",
        "novel_id": story["id"],
        "branch_id": story["branch_id"],
        "chapter_id": story["chapter_id"],
        "candidate_revision": 0,
        "loaded_revision": 0,
        "source_revision": None,
    }
    request["instruction"] = (
        "作者本次确认并可编辑的情节方向：\n"
        + AUTHOR
        + "\n\n方向来源记录："
        + dumps(basis)
        + "\n以上来源只用于追踪作者选择。"
    )
    assert module_plot_source(request) == AUTHOR
    request["chapter_id"] = "different"
    with pytest.raises(DomainError, match="来源无法核对"):
        module_plot_source(request)


@pytest.mark.parametrize("value", ["true", 1, None, []])
def test_new_flags_are_strict_boolean_and_do_not_reinterpret_legacy_payloads(story, value):
    for model, payload in [(ModuleInput, module_request(story)), (WorkflowInput, workflow_payload(story))]:
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "plot_contract_check": value})
    assert same_task_payload({"account": "a"}, {"account": "a", "plot_contract_check": False})
    assert not same_task_payload({"account": "a"}, {"account": "a", "plot_contract_check": True})


def test_unsupported_writing_action_cannot_silently_ignore_enabled_plot_audit(story):
    with pytest.raises(ValidationError):
        ModuleInput.model_validate({**module_request(story), "action": "writing.rewrite_selection"})


@pytest.mark.parametrize(
    "action", ["writing.advise", "writing.rewrite_selection", "memory.extract", "intent.compile"]
)
def test_public_api_rejects_enabled_plot_check_on_unsupported_module(client, story, action):
    response = client.post("/api/modules", json={**module_request(story), "action": action})
    assert response.status_code == 422
    legacy = client.post(
        "/api/modules", json={**module_request(story, enabled=False), "action": "writing.advise"}
    )
    assert legacy.status_code == 202


def test_automatic_failure_report_names_workflow_plan_instead_of_author_lock(
    settings, domain, story, monkeypatch
):
    request = workflow_payload(story, mode="automatic")
    request["plot_contract_check"] = True
    queued = domain.queue("workflow", request, story["branch_id"])
    # This test is the old frozen workflow/report compatibility contract.
    legacy = {k: v for k, v in queued["result"].items() if k != "workflow_plan_preparation_version"}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(legacy), queued["id"]))
    model = PlotModel()

    async def unchanged(self, text, *args):
        return text

    monkeypatch.setattr(Worker, "revise_chapter", unchanged)
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(queued["id"])
    saved = domain.artifact(task["result"]["waiting_artifact"])
    audit = saved["payload"]["plot_audit"]
    assert audit["source_kind"] == "workflow_plan"
    assert audit["findings"][0]["reason"].startswith("本章工作章纲（AI规划）：")
    assert "解释来自本章工作章纲（AI规划）" in audit["policy"]


def test_original_findings_survive_plot_merge_and_metadata_tracks_source_version(
    settings, domain, story, monkeypatch
):
    worker = Worker(settings, PlotModel("pass"))
    queued = domain.queue("module", module_request(story), story["branch_id"])
    original = worker.review

    async def with_existing(*args, **kwargs):
        result = await original(*args, **kwargs)
        result["findings"].append(
            {
                "severity": "suggestion",
                "chapter": 1,
                "quote": TEXT[:10],
                "reason": "原审稿意见保留",
                "repair": "原建议",
            }
        )
        return result

    monkeypatch.setattr(worker, "review", with_existing)
    asyncio.run(worker.run_once())
    task = domain.task(queued["id"])
    artifact = domain.artifact(task["result"]["artifact_id"])
    assert any(f["reason"] == "原审稿意见保留" for f in artifact["payload"]["review"]["findings"])
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (queued["id"],)
    )
    audits = [
        json.loads(e["payload"])
        for e in events
        if "plot_contract_version" in json.loads(e["payload"])["request"]
    ]
    assert len(audits) == 2 and all(e["request"]["plot_contract_version"] == VERSION for e in audits)
