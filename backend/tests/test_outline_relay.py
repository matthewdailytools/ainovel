"""Frozen author outlines can hand off to writing without replanning or overwriting."""

import asyncio
import json
from uuid import uuid4

from ainovel.budget import Budget
from ainovel.db import dumps
from ainovel.worker import Worker
from test_workflow_scope import SegmentModel, workflow_payload

OUTLINE_ONE = "沈砚用旧尺测量潮水，留下可以回查的记录。"
OUTLINE_TWO = "沈砚复查读数，作者尚需明确是否公开。"
CLARIFICATION = "本章只完成复查，不对外公开。"


class RelayModel(SegmentModel):
    def __init__(self, settings, *, ambiguity=False, budget_stop=False):
        super().__init__()
        self.settings = settings
        self.budget = Budget(settings)
        self.ambiguity = ambiguity
        self.budget_stop = budget_stop
        self.steps = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        action = instruction.get("动作")
        prohibit = (
            self.budget_stop
            and account == "openai_1"
            and action == "按章纲写完整正文"
            and instruction["本章"]["number"] == 2
        )
        call_id = self.budget.reserve(
            self.settings.providers[account], task_id, 1000 if prohibit else 10, 1, kwargs.get("run_limit")
        )
        self.steps.append((task_id, action, instruction))
        if action == "仅依据作者原话整理本场景情节":
            text = instruction["作者原话"]
            unresolved = self.ambiguity and OUTLINE_TWO in text and CLARIFICATION not in text
            result = {
                "items": [
                    {
                        "author_evidence": instruction["可引用原文"][0]["source_id"],
                        "actor": "沈砚",
                        "action": "读旧尺",
                        "object": "潮水读数",
                        "direction": "",
                        "result": "完成复查",
                        "certainty": "ambiguous" if unresolved else "explicit",
                        "recipient_scope": "unspecified",
                        "scope_condition": "",
                    }
                ],
                "note": "明确下一步是否公开。" if unresolved else "按本次作者安排核对。",
            }
        elif action == "逐项对照作者情节解释与本次正文":
            result = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": "pass",
                        "observed": "沈砚完成复查。",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                        "reason": "工程替身使用本章原文。",
                        "repair": "",
                    }
                ],
                "summary": "复查已完成。",
            }
        else:
            generated = await super().generate(account, task_id, system, prompt, max_output, **kwargs)
            self.budget.settle(call_id, generated["usage"])
            return {**generated, "call_id": call_id}
        usage = {"input": 1, "output": 1}
        self.budget.settle(call_id, usage)
        return {"text": dumps(result), "usage": usage, "call_id": call_id}


def setup_relay(client, domain, story, *, budget=None):
    branch_id = story["branch_id"]
    chapters = [
        domain.chapter(story["chapter_id"]),
        domain.create_chapter(branch_id, "第二章"),
    ]
    chapters = [domain.save(c["id"], c["revision"], "作者已写的原稿，保留。") for c in chapters]
    source = f"/api/branches/{branch_id}"
    for chapter, text in zip(chapters, (OUTLINE_ONE, OUTLINE_TWO)):
        response = client.put(
            f"{source}/chapters/{chapter['id']}/writing-context/outline",
            json={"base_version": None, "text": text},
        )
        assert response.status_code == 200, response.text
    outlines = client.get(f"{source}/outline-relay").json()
    request = workflow_payload(story, scope="whole_book", count=2)
    request.update(
        client_request_id=uuid4().hex,
        source_branch_id=branch_id,
        plot_contract_check=True,
        budget_limit=budget,
        outline_sources=[{"chapter_id": c["id"], "version": c["outline"]["version"]} for c in outlines],
        plots=[
            {"number": i, "title": c["title"], "plot": c["outline"]["text"], "fixed": []}
            for i, c in enumerate(outlines, 1)
        ],
    )
    return chapters, request


def start(client, request):
    response = client.post("/api/workflows", json=request)
    assert response.status_code == 202, response.text
    return response.json()


def run_next(settings, domain, task_id, model):
    assert asyncio.run(Worker(settings, model).run_once())
    return domain.task(task_id)


def test_author_decision_resumes_same_relay_after_one_written_chapter(client, domain, story, settings):
    originals, request = setup_relay(client, domain, story)
    stale = {
        **request,
        "outline_sources": [
            {**request["outline_sources"][0], "version": "stale"},
            request["outline_sources"][1],
        ],
    }
    rejected = client.post("/api/workflows", json=stale)
    assert rejected.status_code == 409 and rejected.json()["code"] == "outline_changed"
    assert len(domain.db.all("SELECT id FROM branches WHERE novel_id=?", (story["id"],))) == 1
    task = start(client, request)
    assert start(client, request)["id"] == task["id"]
    model = RelayModel(settings, ambiguity=True)
    waiting = run_next(settings, domain, task["id"], model)
    assert waiting["status"] == "needs_review", waiting["error"]
    checkpoint = waiting["result"]["outline_relay_waiting"]
    assert waiting["result"]["next_chapter"] == checkpoint["number"] == 2
    assert waiting["branch_id"] != story["branch_id"]
    assert not domain.chapter(checkpoint["chapter_id"])["content"]
    assert [step[2]["本章"]["number"] for step in model.steps if step[1] == "按章纲写完整正文"] == [1]
    assert client.post(f"/api/tasks/{task['id']}/resume", json={}).status_code == 409
    decision = {
        "checkpoint_id": checkpoint["id"],
        "clarification": CLARIFICATION,
        "client_request_id": uuid4().hex,
    }
    endpoint = f"/api/tasks/{task['id']}/outline-relay/clarify"
    assert client.post(endpoint, json={**decision, "checkpoint_id": "wrong"}).status_code == 409
    resumed = client.post(endpoint, json=decision)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["id"] == task["id"]
    assert client.post(endpoint, json=decision).json()["id"] == task["id"]
    assert client.post(endpoint, json={**decision, "clarification": "新决定"}).status_code == 409
    done = run_next(settings, domain, task["id"], model)
    assert done["status"] == "production_complete", done["error"]
    assert done["payload"]["plots"] == request["plots"]
    assert done["result"]["outline_relay_clarifications"][0]["previous_preparation"] == checkpoint
    assert [step[2]["本章"]["number"] for step in model.steps if step[1] == "按章纲写完整正文"] == [1, 2]
    assert all(step[1] != "全书滚动规划" for step in model.steps)
    assert [domain.chapter(chapter["id"]) for chapter in originals] == originals
    chapter = domain.chapter(checkpoint["chapter_id"])
    result = client.get(f"/api/branches/{done['branch_id']}/chapters/{chapter['id']}/writing-context").json()
    assert result["outline"]["text"] == OUTLINE_TWO + "\n\n作者明确补充：" + CLARIFICATION


def test_budget_reaccount_reuses_ready_outline_and_completed_chapter(client, domain, story, settings):
    originals, request = setup_relay(client, domain, story, budget=0.001)
    task = start(client, request)
    model = RelayModel(settings, budget_stop=True)
    blocked = run_next(settings, domain, task["id"], model)
    assert blocked["status"] == "blocked_runtime" and "预算不足" in blocked["error"]
    assert blocked["result"]["next_chapter"] == 2
    receipt = blocked["result"]["outline_relay_preparations"]["2"]
    first = domain.db.one("SELECT * FROM chapters WHERE branch_id=? AND position=1", (task["branch_id"],))
    continuation = {
        "client_request_id": uuid4().hex,
        "account": "openai_2",
        "budget_limit": 0.01,
        "reason": "从现有写前检查点继续",
        "acknowledge_unknown": True,
    }
    continued = client.post(f"/api/tasks/{task['id']}/continue", json=continuation)
    assert continued.status_code == 202, continued.text
    successor = continued.json()
    assert successor["result"]["continued_from"] == task["id"] and successor["id"] != task["id"]
    assert successor["result"]["outline_relay_preparations"]["2"] == receipt
    done = run_next(settings, domain, successor["id"], model)
    assert done["status"] == "production_complete", done["error"]
    resumed_steps = [step for step in model.steps if step[0] == successor["id"]]
    assert resumed_steps[0][1] == "按章纲写完整正文" and resumed_steps[0][2]["本章"]["number"] == 2
    assert not any(step[1] == "仅依据作者原话整理本场景情节" for step in resumed_steps)
    assert domain.chapter(first["id"]) == first
    assert [domain.chapter(chapter["id"]) for chapter in originals] == originals
    with model.budget.db() as usage:
        rows = usage.execute("SELECT task_id,charged,status FROM calls").fetchall()
    assert {r["task_id"] for r in rows} == {task["id"], successor["id"]}
    assert all(row["charged"] > 0 and row["status"] == "settled" for row in rows)


def test_edited_candidate_recheck_keeps_the_author_amended_plot(client, domain, story, settings):
    _, request = setup_relay(client, domain, story)
    request.update(
        chapters=1,
        plots=[{**request["plots"][1], "number": 1}],
        outline_sources=request["outline_sources"][1:],
        adoption="manual",
    )
    task = start(client, request)
    model = RelayModel(settings, ambiguity=True)
    waiting = run_next(settings, domain, task["id"], model)
    checkpoint = waiting["result"]["outline_relay_waiting"]
    decision = {
        "checkpoint_id": checkpoint["id"],
        "clarification": CLARIFICATION,
        "client_request_id": uuid4().hex,
    }
    assert client.post(f"/api/tasks/{task['id']}/outline-relay/clarify", json=decision).status_code == 202
    drafted = run_next(settings, domain, task["id"], model)
    assert drafted["status"] == "needs_review", drafted["error"]
    candidate_id = drafted["result"]["waiting_artifact"]
    candidate = domain.artifact(candidate_id)
    content = candidate["payload"]["text"] + "\n他等潮声落下。"
    edited = client.patch(f"/api/artifacts/{candidate_id}", json={"candidate_revision": 0, "text": content})
    assert edited.status_code == 200, edited.text
    recheck = client.post(
        f"/api/artifacts/{candidate_id}/recheck",
        json={"candidate_revision": 1, "account": "openai_1", "client_request_id": uuid4().hex},
    )
    assert recheck.status_code == 202, recheck.text
    rechecked = run_next(settings, domain, recheck.json()["id"], model)
    assert rechecked["status"] == "succeeded", rechecked["error"]
    reread = domain.artifact(candidate_id)["payload"]
    assert reread["text"] == content
    assert reread["plot_audit"]["source"] == candidate["payload"]["plot_audit"]["source"]
    assert not domain.chapter(checkpoint["chapter_id"])["content"]


def test_legacy_assisted_outline_requires_reconfirmation_with_fixed_conditions(
    client, domain, story, settings
):
    branch_id, chapter_id = story["branch_id"], story["chapter_id"]
    legacy = workflow_payload(story, count=1, adoption="manual")
    legacy.update(plot_contract_check=True)
    legacy["plots"][0].update(plot=OUTLINE_ONE, fixed=["记录读数"])
    task = domain.queue("workflow", legacy, branch_id)
    result = run_next(settings, domain, task["id"], RelayModel(settings))
    assert result["status"] == "needs_review", result["error"]
    endpoint = f"/api/branches/{branch_id}"
    [chapter] = client.get(f"{endpoint}/outline-relay").json()
    assert chapter["outline"]["source"] == "author" and not chapter["confirmed"]
    request = {
        **legacy,
        "source_branch_id": branch_id,
        "client_request_id": uuid4().hex,
        "outline_sources": [{"chapter_id": chapter_id, "version": chapter["outline"]["version"]}],
        "plots": [{"number": 1, "title": chapter["title"], "plot": OUTLINE_ONE, "fixed": []}],
    }
    rejected = client.post("/api/workflows", json=request)
    assert rejected.status_code == 409 and rejected.json()["code"] == "outline_reconfirm"
    assert len(domain.db.all("SELECT id FROM branches WHERE novel_id=?", (story["id"],))) == 1
    text = OUTLINE_ONE + "\n仍须遵守的固定条件：记录读数。"
    confirmed = client.put(
        f"{endpoint}/chapters/{chapter_id}/writing-context/outline",
        json={"base_version": chapter["outline"]["version"], "text": text},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert client.get(f"{endpoint}/outline-relay").json()[0]["confirmed"]
    request["outline_sources"][0]["version"] = confirmed.json()["version"]
    request["plots"][0]["plot"] = text
    queued = start(client, request)
    assert queued["payload"]["plots"][0]["plot"] == text
