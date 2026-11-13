"""A completed writing segment is never a completed book or a settled promise."""

import asyncio
import json
import re

import pytest
from pydantic import ValidationError

from ainovel.db import DomainError, dumps
from ainovel.domain import same_task_payload
from ainovel.genre_catalog import assemble_profile, profile_for_action
from ainovel.models import WorkflowInput
from ainovel.story_graph import SaveRecord, graph, save_record
from ainovel.worker import Worker

from test_api_worker import FakeModel
from test_author_contract import FIELDS, adopt_intent


class SegmentModel(FakeModel):
    """Deterministic engineering adapter, also usable by the isolated browser server."""

    def __init__(self):
        super().__init__()
        self.segment_requests = []
        self.on_report = None
        self.on_generation = None
        self.blocker = False
        self.omit_checks = False

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        data = json.loads(prompt.split("\n只返回")[0])
        instruction = data["任务"]
        action = instruction.get("动作")
        if action == "按章纲写完整正文" and self.on_generation:
            self.on_generation(task_id)
        if action not in {"阶段批次规划", "批次范围检查"}:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        self.segment_requests.append(instruction)
        if action == "阶段批次规划":
            self.plan_context = data["上下文"]
            output = {
                "chapters": [
                    {"number": n, "title": f"第{n}章", "plot": "测量并记录", "fixed": []}
                    for n in range(1, instruction["章数"] + 1)
                ],
                "ending": "未来公开核验，当前阶段仍在调查",
            }
        else:
            unit = instruction["可引用原文"][0]

            def evidence_for(condition):
                chapter = re.match(r"第(\d+)章", condition)
                return next(
                    item["source_id"]
                    for item in instruction["可引用原文"]
                    if not chapter or item["chapter"] == int(chapter[1])
                )

            output = {
                "complete": not self.blocker,
                "promises": [
                    {"condition": item, "result": "pass", "evidence": evidence_for(item)}
                    for item in instruction["必须逐项核对的本批约定"]
                ]
                if not self.omit_checks
                else [],
                "findings": [
                    {
                        "severity": "blocker",
                        "chapter": unit["chapter"],
                        "quote": unit["source_id"],
                        "reason": "当前阶段的认知冲突尚未修复",
                        "repair": "核对当前原文，不能用未来计划掩盖",
                    }
                ]
                if self.blocker
                else [],
                "summary": "仅完成本批工程样本，后续暗号与终局仍待续。",
            }
            if self.on_report:
                self.on_report(task_id)
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": "offline-scope"}


def workflow_payload(story, *, scope="segment", mode="assisted", count=1, adoption="automatic"):
    return WorkflowInput(
        novel_id=story["id"],
        mode=mode,
        completion_scope=scope,
        brief="这只是长篇开篇的一批章节；后续才解释暗号，不在本批结束全书。",
        chapters=count,
        plots=[
            {"number": n, "title": f"章{n}", "plot": "测量并记录", "fixed": ["记录读数"]}
            for n in range(1, count + 1)
        ]
        if mode == "assisted"
        else [],
        min_chars=200,
        max_chars=600,
        adoption=adoption,
        account="openai_1",
    ).model_dump()


def future_promise(domain, story):
    saved = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={"kind": "promise", "title": "后卷才解释的暗号", "stages": [{"status": "planned"}]},
        ),
    )
    return next(row for row in saved["records"] if row["payload"]["title"] == "后卷才解释的暗号")


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
@pytest.mark.parametrize("scope", ["segment", "whole_book"])
@pytest.mark.parametrize("adoption", ["manual", "automatic"])
def test_segment_can_finish_with_unresolved_future_promise_while_book_cannot(
    settings, domain, story, client, mode, scope, adoption
):
    promise = future_promise(domain, story)
    task = domain.queue(
        "workflow", workflow_payload(story, scope=scope, mode=mode, adoption=adoption), story["branch_id"]
    )
    model = SegmentModel()
    assert asyncio.run(Worker(settings, model).run_once())
    if adoption == "manual":
        waiting = domain.task(task["id"])
        assert waiting["status"] == "needs_review"
        candidate = waiting["result"]["waiting_artifact"]
        domain.accept(candidate, "manual-scope-" + candidate)
        assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 200
        asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    completion = domain.completion(story["branch_id"])
    assert stored["status"] == ("segment_complete" if scope == "segment" else "blocked_creative"), stored[
        "error"
    ]
    assert completion["segment_complete"] is (scope == "segment")
    assert completion["production_complete"] is False
    assert completion["accepted"] is False
    records = graph(domain, story["branch_id"])["records"]
    maintained = next(row for row in records if row["id"] == promise["id"])
    assert maintained["revision"] == promise["revision"]
    assert maintained["payload"]["stages"] == promise["payload"]["stages"]
    assert stored["result"]["maintained_promises"]["closed"] is False
    if scope == "segment":
        assert stored["result"]["completion"]["closed"] is False
        assert stored["result"]["completion"]["scope"] == "segment"
        assert stored["result"]["completion"]["complete"] is True


def test_scope_defaults_and_legacy_idempotency_keep_whole_book(client, story):
    request = workflow_payload(story, scope="whole_book")
    request.pop("completion_scope")
    request["client_request_id"] = "workflow-default-scope"
    response = client.post("/api/workflows", json=request)
    assert response.status_code == 202
    assert response.json()["payload"]["completion_scope"] == "whole_book"
    assert (
        client.post("/api/workflows", json={**request, "completion_scope": "whole_book"}).json()["id"]
        == response.json()["id"]
    )
    assert client.post("/api/workflows", json={**request, "completion_scope": "segment"}).status_code == 409
    assert same_task_payload({}, {"completion_scope": "whole_book"})
    assert not same_task_payload({}, {"completion_scope": "segment"})
    with pytest.raises(ValidationError):
        WorkflowInput.model_validate({**request, "completion_scope": "anything"})


def test_segment_preserves_future_author_ending_and_reads_each_batch_in_full(settings, domain, story):
    adopt_intent(domain, story)
    task = domain.queue("workflow", workflow_payload(story, count=7), story["branch_id"])
    model = SegmentModel()
    asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    assert stored["status"] == "segment_complete", stored["error"]
    reports = [item for item in model.segment_requests if item["动作"] == "批次范围检查"]
    assert len(reports) == 3
    for report in reports:
        checks = report["必须逐项核对的本批约定"]
        assert FIELDS["ending"] not in checks
        assert FIELDS["fixed_results"][0] not in checks
        assert FIELDS["prohibitions"][0] in checks
        assert "不要求未来全书终局" in report["作者契约核对"]
    assert len(stored["result"]["volume_reports"]) == 2
    assert len(stored["result"]["completion"]["source_versions"]) == 7
    assert all(item["closed"] is False for item in stored["result"]["volume_reports"])


def test_segment_cannot_omit_batch_fixed_plot_checks(settings, domain, story):
    task = domain.queue("workflow", workflow_payload(story), story["branch_id"])
    model = SegmentModel()
    model.omit_checks = True
    asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    assert stored["status"] == "blocked_runtime"
    assert domain.completion(story["branch_id"])["segment_complete"] is False
    assert 1 <= len(model.segment_requests) <= 3


def test_segment_current_blocker_still_stops_at_finite_revision_boundary(settings, domain, story):
    payload = workflow_payload(story)
    task = domain.queue("workflow", payload, story["branch_id"])
    # Start at the last permitted revision round to isolate the final blocker gate.
    progress = {
        **task["result"],
        "plan": {"chapters": payload["plots"], "ending": "后续收束"},
        "next_chapter": 1,
        "repair_round": 2,
    }
    with domain.db.connect(True) as connection:
        connection.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(progress), task["id"]))
    model = SegmentModel()
    model.blocker = True
    asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    assert stored["status"] == "blocked_creative", stored["error"]
    assert stored["result"]["completion"]["findings"][0]["severity"] == "blocker"
    assert domain.completion(story["branch_id"])["segment_complete"] is False


def test_segment_pause_during_range_check_resumes_without_repeating_completed_calls(
    settings, domain, story, client
):
    task = domain.queue("workflow", workflow_payload(story), story["branch_id"])
    model = SegmentModel()
    model.on_report = lambda task_id: client.post(f"/api/tasks/{task_id}/pause")
    asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    assert stored["status"] == "paused"
    assert stored["result"]["completion"]["scope"] == "segment"
    calls = model.calls
    assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 200
    model.on_report = None
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "segment_complete"
    assert model.calls == calls


def test_segment_cancel_queued_pause_and_explicit_resume_keep_scope(settings, domain, story, client):
    task = domain.queue("workflow", workflow_payload(story, count=2), story["branch_id"])
    model = SegmentModel()
    assert client.post(f"/api/tasks/{task['id']}/pause").json()["status"] == "paused"
    assert not asyncio.run(Worker(settings, model).run_once()) and model.calls == 0
    assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 200
    model.on_generation = lambda task_id: client.post(f"/api/tasks/{task_id}/cancel")
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(task["id"])["status"] == "cancelled"
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 409
    assert (
        client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True}).status_code == 200
    )
    model.on_generation = None
    asyncio.run(Worker(settings, model).run_once())
    stored = domain.task(task["id"])
    assert stored["status"] == "segment_complete", stored["error"]
    assert stored["payload"]["completion_scope"] == "segment"


@pytest.mark.parametrize("scope", ["segment", "whole_book"])
def test_checkpoint_cannot_promote_wrong_scope_and_pending_maintenance_cannot_be_skipped(
    settings, domain, story, scope
):
    task = domain.queue("workflow", workflow_payload(story, scope=scope), story["branch_id"])
    worker = Worker(settings, SegmentModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = {
        **task["result"],
        "completion": {"scope": "segment", "closed": False, "complete": True, "promises": [], "findings": []},
    }
    with pytest.raises(DomainError, match="运行范围不一致"):
        worker.checkpoint(result, "production_complete" if scope == "segment" else "segment_complete")
    if scope == "segment":
        result["pending_structure"] = {"chapter_id": story["chapter_id"], "revision": 0}
        with pytest.raises(DomainError, match="结构维护尚未完成"):
            worker.checkpoint(result, "segment_complete")
    assert domain.task(task["id"])["status"] == "running"


def test_segment_still_blocks_invalid_story_dependencies(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "旧尺读数。" * 50)
    saved = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "character",
                "title": "测量者",
                "sources": [
                    {
                        "chapter_id": chapter["id"],
                        "revision": chapter["revision"],
                        "start": 0,
                        "end": 2,
                        "quote": "旧尺",
                    }
                ],
            },
        ),
    )
    invalid_id = saved["records"][0]["id"]
    domain.save(chapter["id"], chapter["revision"], "新尺读数。" * 50)
    task = domain.queue("workflow", workflow_payload(story), story["branch_id"])
    worker = Worker(settings, SegmentModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    result = {
        **task["result"],
        "completion": {"scope": "segment", "closed": False, "complete": True, "promises": [], "findings": []},
    }
    worker.checkpoint(result, "segment_complete")
    stored = domain.task(task["id"])
    assert stored["status"] == "blocked_creative"
    assert stored["result"]["invalid_story_records"] == [invalid_id]
    assert domain.completion(story["branch_id"])["segment_complete"] is False
    assert domain.chapter(chapter["id"])["content"] == "新尺读数。" * 50


@pytest.mark.parametrize(
    ("action", "existing"), [("阶段批次规划", "全书滚动规划"), ("批次范围检查", "整书完结检查")]
)
def test_segment_actions_keep_selected_channel_and_genre_topics(action, existing):
    profile = assemble_profile(
        {"audience": "女频", "genres": ["xianxia", "mystery"], "author_exceptions": ["无CP"]}
    )
    profile.update(source_artifact_id="scope-reader", source_story_revision=12)
    current = profile_for_action(profile, action)
    baseline = profile_for_action(profile, existing)
    assert current["required_topics"] == baseline["required_topics"]
    assert any(topic["owner"].startswith("T") for topic in current["required_topics"])
    assert current["genre_ids"] == ["xianxia", "mystery"]
    assert current["author_exceptions"] == ["无CP"]
    assert current["source_artifact_id"] == "scope-reader"
