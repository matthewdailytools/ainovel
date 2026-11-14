"""Real Worker/staging boundaries with the explicit offline browser adapter."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.models import ModuleInput
from ainovel.worker import Worker
from helpers.source_identity_fixture import SOURCE, BODY
from scripts.e2e_server import Model
from test_candidate_recheck import queue_recheck
from test_workflow_scope import workflow_payload


class Probe(Model):
    def __init__(self, hook=None):
        super().__init__()
        self.frames = []
        self.hook = hook

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.frames.append(frame)
        if self.hook:
            self.hook(frame["任务"], task_id)
        return await super().generate(account, task_id, system, prompt, max_output, **kwargs)


def assert_source_isolation(frames):
    labels = [
        f
        for f in frames
        if f["任务"].get("动作") in {"仅从情节原话补全角色称谓限定", "独立核对原话角色称谓限定"}
    ]
    assert len(labels) == 2
    for frame in labels:
        assert set(frame["上下文"]) == {"scope"}
        assert frame["任务"]["情节原话"] == SOURCE
        assert BODY not in json.dumps(frame, ensure_ascii=False)
        assert "预期流向" not in frame["任务"]
    flow = next(f for f in frames if f["任务"].get("动作") == "只从本次正文独立提取物品与信息流向")
    assert SOURCE not in json.dumps(flow, ensure_ascii=False)
    assert "限定提案" not in flow["任务"] and "独立整理的解释提案" not in flow["任务"]
    assert flow["任务"]["实体别名对照"]["E01"] == "渡口一位修船匠"


def test_module_real_worker_has_two_source_only_steps_and_never_writes_manuscript(settings, domain, story):
    payload = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.expand_plot",
        instruction=SOURCE,
        account="openai_1",
        plot_contract_check=True,
        source_identity_context=True,
    ).model_dump()
    task = domain.queue("module", payload)
    before = deepcopy(domain.chapter(story["chapter_id"]))
    model = Probe()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(task["id"])
    assert finished["status"] == "succeeded", finished["error"]
    artifact = domain.artifact(finished["result"]["artifact_id"])
    assert artifact["payload"]["plot_audit"]["status"] == "model_pass"
    assert artifact["payload"]["plot_audit"]["items"][0]["supplier"] == "修船匠"
    assert_source_isolation(model.frames)
    assert domain.chapter(story["chapter_id"]) == before
    assert not domain.db.all("SELECT * FROM facts")


@pytest.mark.parametrize("interruption", ["cancel", "edit"])
def test_label_check_late_return_cannot_publish_after_cancel_or_edit(
    settings, domain, story, client, interruption
):
    payload = workflow_payload(story)
    payload.update(plot_contract_check=True)
    payload["plots"][0].update(plot=SOURCE, fixed=[])
    original_task = domain.queue("workflow", payload, story["branch_id"])
    asyncio.run(Worker(settings, Probe()).run_once())
    parent = domain.task(original_task["id"])
    original = domain.artifact(parent["result"]["waiting_artifact"])
    queued = queue_recheck(client, original, source_identity_context=True)
    assert queued.status_code == 202
    child = queued.json()["id"]
    text = original["payload"]["text"] + "\n另一窗口保存的编辑。"

    def hook(instruction, tid):
        if instruction.get("动作") != "独立核对原话角色称谓限定":
            return
        assert tid == child
        if interruption == "cancel":
            assert client.post("/api/tasks/" + child + "/cancel").status_code == 200
        else:
            response = client.patch(
                "/api/artifacts/" + original["id"], json={"candidate_revision": 0, "text": text}
            )
            assert response.status_code == 200

    model = Probe(hook)
    asyncio.run(Worker(settings, model).run_once())
    after = domain.artifact(original["id"])
    if interruption == "cancel":
        assert domain.task(child)["status"] == "cancelled"
        assert after == original
    else:
        assert domain.task(child)["status"] == "blocked_runtime"
        assert after["payload"]["text"] == text and after["candidate_revision"] == 1
    assert not domain.db.all("SELECT * FROM artifacts WHERE task_id=?", (child,))
    assert not domain.db.all("SELECT * FROM facts")
    assert model.frames[-1]["任务"]["动作"] == "独立核对原话角色称谓限定"


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
def test_real_worker_recheck_isolated_source_labels_keep_parent_contract_and_formal_facts(
    settings, domain, story, client, mode
):
    payload = workflow_payload(story, mode=mode)
    payload.update(plot_contract_check=True, brief="source-identity-e2e：本批只借尺。")
    if mode == "assisted":
        payload["plots"][0]["plot"] = SOURCE
        payload["plots"][0]["fixed"] = ["归还仍是约定，未实际归还。"]
    queued = domain.queue("workflow", payload, story["branch_id"])
    asyncio.run(Worker(settings, Probe()).run_once())
    parent = domain.task(queued["id"])
    assert parent["status"] == "needs_review", parent["error"]
    original = domain.artifact(parent["result"]["waiting_artifact"])
    assert original["payload"]["plot_audit"]["status"] == "unverified"
    response = queue_recheck(client, original, source_identity_context=True)
    assert response.status_code == 202, response.text
    model = Probe()
    asyncio.run(Worker(settings, model).run_once())
    recheck = domain.task(response.json()["id"])
    assert recheck["status"] == "succeeded", recheck["error"]
    saved = domain.artifact(original["id"])
    assert saved["payload"]["review_passed"]
    assert saved["payload"]["plot_audit"]["items"] == original["payload"]["plot_audit"]["items"]
    assert saved["payload"]["memory"]["facts"]
    assert saved["payload"]["text"] == original["payload"]["text"]
    assert_source_isolation(model.frames)
    after = domain.task(parent["id"])
    for key in ("status", "payload", "result"):
        assert after[key] == parent[key]
    assert domain.chapter(original["chapter_id"])["content"] == ""
    assert not domain.db.all("SELECT * FROM facts")
