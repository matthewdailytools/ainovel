import asyncio
import json

import pytest

from ainovel.budget import Budget
from ainovel.db import dumps, uid
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF, IDEA
from test_workflow_inspection import author


BODY = "学徒进门听见铃响，获得一条新线索。"


def plan(chapters, setting_ids):
    return {
        "summary": "凭铃声查找线索",
        "volumes": [
            {
                "id": "v",
                "title": "遗铃",
                "summary": "先试探铜铃，再找到师姐。",
                "units": [
                    {
                        "id": "u",
                        "title": "铃响何处",
                        "summary": "学徒核对铃声规则。",
                        "chapters": [
                            {**chapter_plan(chapter["id"], "确认铜铃响声"), "setting_ids": setting_ids}
                            for chapter in chapters
                        ],
                    }
                ],
            }
        ],
    }


class Model:
    def __init__(self, during_write=None, settings=None):
        self.calls = []
        self.during_write = during_write
        self.settings = settings

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        if self.settings:
            budget = Budget(self.settings)
            reservation = budget.reserve(
                self.settings.providers[account], task_id, 1000, max_output, kwargs["run_limit"]
            )
            budget.settle(reservation, {"input": 1, "output": 1})
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("action") or instruction["动作"]
        self.calls.append(action)
        if action == "conception.generate.project":
            value = BRIEF
        elif action == "conception.generate.category":
            assert frame["上下文"]["已确认立项"]["idea"] == IDEA
            value = {"items": [{"title": "门铃规则", "detail": "铜铃只在门内响。", "links": []}]}
        elif action == "生成四级大纲候选":
            value = plan(instruction["章节目录"], [item["id"] for item in instruction["已确认世界设定"]])
        elif action == "writing.expand_plot":
            assert frame["上下文"]["采用章纲"]["chapter"]["result"] == "获得一条新线索"
            if self.during_write:
                self.during_write()
            value = BODY
        else:
            assert action == "审查正文", frame
            value = {
                "checks": [
                    {
                        "condition": key,
                        "result": "pass",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                    }
                    for key in instruction["固定条件"]
                ],
                "findings": [],
                "summary": "当前正文覆盖了章纲结果。",
            }
        return {
            "text": value if isinstance(value, str) else dumps(value),
            "usage": {"input": 1, "output": 1},
            "call_id": uid(),
        }


def drain(settings, model):
    async def all_steps():
        worker = Worker(settings, model)
        for _ in range(40):
            if not await worker.run_once():
                return
        pytest.fail("七台接力未到达可见检查点")

    asyncio.run(all_steps())


def start(client, domain, story, origin, **extra):
    if origin == "idea":
        branch = domain.create_novel("遗铃自动试跑")["branch_id"]
        source = {"idea": IDEA, "chapter_count": 1}
    else:
        branch = story["branch_id"]
        setting_id = setting_confirmation(client, branch)
        if origin == "outline":
            confirmed = put_and_confirm(
                client,
                f"/api/branches/{branch}/outline-workspace",
                {
                    "base_revision": 0,
                    "confirmation_id": setting_id,
                    "plan": plan([{"id": story["chapter_id"]}], ["bell"]),
                },
            )
            source = {"source_id": confirmed["confirmed"]["id"]}
        else:
            source = {"source_id": setting_id}
    body = {
        "start_from": origin,
        "account": "deepseek",
        "budget_limit": 1.0,
        "client_request_id": uid(),
        "auto_confirm_planning": True,
        "auto_adopt_body": True,
        **source,
        **extra,
    }
    endpoint = f"/api/branches/{branch}/seven-stage-runs"
    response = client.post(endpoint, json=body)
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]
    assert client.post(endpoint, json=body).json()["id"] == task_id
    return branch, endpoint, task_id


@pytest.mark.parametrize("origin", ["idea", "settings", "outline"])
def test_seven_stage_run_reaches_verified_checkpoints(client, domain, settings, story, origin):
    branch, endpoint, run_id = start(client, domain, story, origin)
    model = Model(settings=settings)
    paused = client.post(f"{endpoint}/{run_id}/pause")
    assert paused.status_code == 200, paused.text
    drain(settings, model)
    assert not model.calls
    assert client.post(f"{endpoint}/{run_id}/resume").status_code == 200
    drain(settings, model)
    run = domain.task(run_id)
    assert run["status"] == "needs_review", run["error"]
    state = run["result"]["seven_stage_run"]
    assert state["stage"] == "inspection"
    assert state["inspection_id"]
    chapter = domain.chapter(state["completed_chapters"][0]["id"])
    assert chapter["content"] == BODY and chapter["revision"] == 1
    assert model.calls.count("writing.expand_plot") == 1
    assert ("生成四级大纲候选" in model.calls) is (origin != "outline")
    assert ("conception.generate.project" in model.calls) is (origin == "idea")
    child_count = len(state["children"])

    inspection = f"/api/branches/{branch}/workflow-inspections"
    if origin == "outline":
        original_report = state["inspection_id"]
        chapter = domain.save(chapter["id"], chapter["revision"], BODY + "窗外风停了。")
        assert client.post(f"{endpoint}/{run_id}/resume").status_code == 200
        drain(settings, model)
        updated = domain.task(run_id)
        assert updated["result"]["seven_stage_run"]["inspection_id"] != original_report
        assert updated["result"]["seven_stage_run"]["completed_chapters"] == state["completed_chapters"]
        assert model.calls.count("writing.expand_plot") == 1
    preview = client.get(inspection).json()["preview"]
    approved = client.post(
        inspection,
        json={
            "basis_sha256": preview["basis_sha256"],
            "length_targets": {"min_chars": 1, "expected_chapters": 1},
            "author_reviews": [
                author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
            ],
        },
    )
    assert approved.status_code == 201, approved.text
    assert client.post(f"{endpoint}/{run_id}/resume").status_code == 200
    drain(settings, model)
    run = domain.task(run_id)
    assert run["status"] == "needs_review", run["error"]
    assert run["result"]["seven_stage_run"]["stage"] == "completion"

    completion = f"/api/branches/{branch}/book-completion"
    confirmed = client.post(
        completion + "/confirm",
        json={
            "basis_sha256": client.get(completion).json()["basis_sha256"],
            "read_note": "作者已通读保存稿。",
            "ending": {"note": "主冲突以归还铜铃解决。"},
            "characters": {"note": "确认学徒找到线索。"},
            "promises": {"note": "铜铃线索完成本篇承诺。"},
            "intro": "学徒循着一枚旧铃寻找师姐。",
            "spoiler_scope": "opening",
            "sample_chapter_ids": [chapter["id"]],
            "materials_reviewed": True,
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    assert client.post(f"{endpoint}/{run_id}/resume").status_code == 200
    drain(settings, model)
    done = domain.task(run_id)
    assert done["status"] == "succeeded", done["error"]
    final = done["result"]["seven_stage_run"]
    assert final["completion_id"] == confirmed.json()["id"]
    assert len(final["children"]) == child_count


def test_budget_and_concurrent_edit_leave_original_intact(client, domain, settings, story):
    _, endpoint, run_id = start(client, domain, story, "outline", budget_limit=0.000001)
    model = Model(settings=settings)
    drain(settings, model)
    run = domain.task(run_id)
    assert run["status"] == "needs_review"
    assert not model.calls
    assert "预算" in run["error"]
    before = run["result"]["seven_stage_run"]["children"]
    assert client.post(f"{endpoint}/{run_id}/resume").status_code == 200
    drain(settings, model)
    assert domain.task(run_id)["result"]["seven_stage_run"]["children"] == before
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert client.post(f"{endpoint}/{run_id}/cancel").status_code == 200

    branch = story["branch_id"]
    outline = client.get(f"/api/branches/{branch}/outline-workspace").json()["confirmed"]
    queued = client.post(
        endpoint,
        json={
            "start_from": "outline",
            "source_id": outline["id"],
            "account": "deepseek",
            "budget_limit": 1.0,
            "client_request_id": uid(),
            "auto_adopt_body": True,
        },
    )
    assert queued.status_code == 202, queued.text

    def edit():
        domain.save(story["chapter_id"], 0, "作者亲笔修改。")

    drain(settings, Model(during_write=edit))
    assert domain.chapter(story["chapter_id"])["content"] == "作者亲笔修改。"
    assert domain.task(queued.json()["id"])["status"] == "needs_review"
