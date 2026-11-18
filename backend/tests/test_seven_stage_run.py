import asyncio
import json

import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError, dumps, uid
from ainovel.domain import Domain
from ainovel.providers import RequestNotSent
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF, IDEA
from test_reference_flow import SOURCE, reference as reference_fixture, update as update_reference
from test_work_snapshot import request_restore, route
from test_workflow_inspection import author


BODY = "学徒进门听见铃响，获得一条新线索。"
reference = reference_fixture


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
    def __init__(self, during_write=None, settings=None, limit_inspection=False):
        self.calls = []
        self.during_write = during_write
        self.settings = settings
        self.limit_inspection = limit_inspection

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction.get("action") or instruction["动作"]
        if action == "对账立项章纲与节奏" and self.limit_inspection:
            self.calls.append(action)
            error = DomainError("context_limit", "模拟全书体检超出语义窗口。", 409)
            raise RequestNotSent(error, task_id, account, "context_limit") from error
        if self.settings:
            budget = Budget(self.settings)
            try:
                reservation = budget.reserve(
                    self.settings.providers[account], task_id, 1000, max_output, kwargs["run_limit"]
                )
            except DomainError as error:
                raise RequestNotSent(error, task_id, account, "budget_reserve") from error
            budget.settle(reservation, {"input": 1, "output": 1})
        self.calls.append(action)
        if action == "conception.generate.project":
            value = BRIEF
        elif action == "conception.generate.category":
            assert frame["上下文"]["已确认立项"]["idea"] == IDEA
            value = {"items": [{"title": "门铃规则", "detail": "铜铃只在门内响。", "links": []}]}
        elif action == "conception.review.bundle":
            value = {"issues": []}
        elif action == "生成四级大纲候选":
            value = plan(instruction["章节目录"], [item["id"] for item in instruction["已确认世界设定"]])
            if available := instruction.get("获准采用的手法卡"):
                assert SOURCE not in dumps(frame)
                card = available[0]
                assert card["conditions"] == "新动作须依靠已收到的部分线索"
                assert card["exceptions"] == "替换人物、物件与具体场景"
                value["volumes"][0]["units"][0]["chapters"][0]["reference_cards"] = [
                    {key: card[key] for key in ("session_id", "card_id", "revision")}
                ]
        elif action == "拆解所选原文写法":
            assert instruction["选段"] == SOURCE
            assert instruction["学习目标"] == "有限信息下取得局部进展"
            assert "recent" not in frame["上下文"]
            value = {
                "cards": [
                    {
                        "observation": "先给一部分信息",
                        "interpretation": "形成再次查证动机",
                        "technique": "用局部收获触发下一步行动",
                        "conditions": "新动作须依靠已收到的部分线索",
                        "exceptions": "替换人物、物件与具体场景",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                    }
                ],
                "coverage_note": "只核对本次已保存选段。",
            }
        elif action == "writing.expand_plot":
            assert frame["上下文"]["采用章纲"]["chapter"]["result"] == "获得一条新线索"
            if self.during_write:
                self.during_write()
            value = BODY
        elif action == "准备完本收束核对建议":
            source_id = instruction["正文依据"][0]["source_id"]
            value = {
                key: {"note": "学徒凭门铃找到线索，实际收束待作者核对。", "source_ids": [source_id]}
                for key in ("ending", "characters", "promises")
            }
        elif action == "起草完本交付简介":
            assert instruction["剧透范围"] == "仅开篇试读范围"
            value = {"intro": "学徒依迹寻找门铃的主人。"}
        elif action == "对账立项章纲与节奏":
            value = {
                "items": [
                    {
                        "check_id": key,
                        "result": "unknown",
                        "reason": "这项结论仍需作者核对。",
                        "paragraph_refs": [],
                        "basis_refs": [],
                    }
                    for key in ("01", "02", "10")
                ]
            }
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
    count = 2 if origin == "idea" else 1
    branch, endpoint, run_id = start(
        client, domain, story, origin, **({"chapter_count": count} if origin == "idea" else {})
    )
    model = Model(settings=settings, limit_inspection=origin == "settings")
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
    if model.limit_inspection:
        assert "输入上限" in run["error"]
        assert state["child_recovery"]["state"] == "retryable"
        assert not state.get("inspection_candidate_id")
    else:
        semantic = domain.artifact(state["inspection_candidate_id"])
        assert semantic["kind"] == "inspection_semantic_candidate"
        assert semantic["payload"]["scope"] == "book"
    milestones = client.get(route(branch)).json()
    kickoff = next(row for row in milestones if row["id"] == state["start_snapshot_id"])
    assert kickoff["label"] == "启动七台接力"
    end_volume = next(row for row in milestones if row["id"] in state["volume_snapshot_ids"].values())
    assert end_volume["scope"] == "volume"
    chapter = domain.chapter(state["completed_chapters"][0]["id"])
    assert chapter["content"] == BODY and chapter["revision"] == 1
    assert len(state["completed_chapters"]) == count
    assert model.calls.count("writing.expand_plot") == count
    assert ("生成四级大纲候选" in model.calls) is (origin != "outline")
    assert ("conception.generate.project" in model.calls) is (origin == "idea")
    if origin == "idea":
        context = client.get(f"/api/branches/{branch}/conception").json()
        assert [category["id"] for category in context["categories"] if category["items"]] == [
            "core",
            "characters",
            "world",
            "relationships",
        ]
        assert context["confirmation"]["current"]
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
            "length_targets": {"min_chars": 1, "expected_chapters": count},
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
    completion_state = run["result"]["seven_stage_run"]
    assert completion_state["stage"] == "completion"
    prepared = domain.artifact(completion_state["completion_preparation_id"])
    assert prepared["kind"] == "completion_preparation_candidate"
    assert prepared["payload"]["intro"] == "学徒依迹寻找门铃的主人。"
    assert prepared["payload"]["sample_chapter_ids"] == [chapter["id"]]
    assert model.calls.count("起草完本交付简介") == 1
    if model.limit_inspection:
        assert model.calls.count("对账立项章纲与节奏") == 1
    assert client.post(f"{endpoint}/{run_id}/resume").status_code == 200
    drain(settings, model)
    assert model.calls.count("起草完本交付简介") == 1

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
    assert len(final["children"]) == child_count + 1


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


def test_rollback_during_model_call_keeps_recovered_work_and_stops_followups(client, domain, settings, story):
    branch, endpoint, run_id = start(client, domain, story, "outline")
    snapshots = client.get(route(branch)).json()
    checkpoint = next(item for item in snapshots if item["purpose"] == "milestone")
    restored = []

    def rollback():
        restored.append(request_restore(client, branch, checkpoint["id"]))

    model = Model(during_write=rollback)
    drain(settings, model)
    assert restored[0]["recovery_snapshot_id"]
    assert domain.chapter(story["chapter_id"])["content"] == ""
    parent = domain.task(run_id)
    assert parent["status"] == "needs_review"
    state = parent["result"]["seven_stage_run"]
    assert state["completed_chapters"] == []
    assert state["stage"] == "writing"
    assert state["interruption"]["code"] == "outline_changed"
    assert client.post(f"{endpoint}/{run_id}/resume").json()["code"] == "seven_stage_changed"
    assert model.calls == ["writing.expand_plot"]


@pytest.mark.parametrize("known_no_send", [True, False])
def test_resume_distinguishes_pre_send_failure_from_missing_receipt(
    client, domain, settings, story, known_no_send
):
    _, endpoint, run_id = start(client, domain, story, "outline")

    class InterruptedReview(Model):
        failures = 0

        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            if instruction.get("动作") == "审查正文" and not self.failures:
                self.failures += 1
                error = DomainError("connection", "临时连接失败。", 409)
                if known_no_send:
                    raise RequestNotSent(error, task_id, account, "before_send")
                raise error
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    model = InterruptedReview(settings=settings)
    drain(settings, model)
    state = domain.task(run_id)["result"]["seven_stage_run"]
    child = state["child_id"]
    assert state["child_recovery"]["state"] == ("retryable" if known_no_send else "unknown")
    assert model.calls == ["writing.expand_plot"]
    assert domain.chapter(story["chapter_id"])["content"] == ""
    resumed = client.post(f"{endpoint}/{run_id}/resume")
    if known_no_send:
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "waiting_child"
        drain(settings, model)
        state = domain.task(run_id)["result"]["seven_stage_run"]
        assert state["children"] == [child]
        assert state["stage"] == "inspection"
        assert model.calls == ["writing.expand_plot", "审查正文"]
        assert domain.chapter(story["chapter_id"])["content"] == BODY
    else:
        assert resumed.status_code == 409, resumed.text
        assert resumed.json()["code"] == "unknown_result"
        assert state["child_recovery"]["dispatch_event_ids"]
        drain(settings, model)
        assert model.calls == ["writing.expand_plot"]
        assert domain.task(child)["status"] == "blocked_runtime"


def test_parent_cancelled_between_child_success_and_adoption(client, domain, settings, story, monkeypatch):
    _, endpoint, run_id = start(client, domain, story, "outline")
    accept = Domain.accept

    def cancelled_accept(self, artifact_id, *args, **kwargs):
        stopped = client.post(f"{endpoint}/{run_id}/cancel")
        assert stopped.status_code == 200, stopped.text
        return accept(self, artifact_id, *args, **kwargs)

    monkeypatch.setattr(Domain, "accept", cancelled_accept)
    model = Model()
    drain(settings, model)
    parent = domain.task(run_id)
    assert parent["status"] == "cancelled"
    child = domain.task(parent["result"]["seven_stage_run"]["child_id"])
    artifact = domain.artifact(child["result"]["artifact_id"])
    assert artifact["status"] == "candidate"
    assert domain.acceptance_receipt(artifact["id"])["receipt"] is None
    assert domain.chapter(story["chapter_id"])["content"] == ""


def test_old_failed_parent_cannot_resume_alongside_new_active_parent(client, domain, story):
    branch, endpoint, run_id = start(client, domain, story, "outline")
    old = domain.task(run_id)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='blocked_runtime' WHERE id=?", (run_id,))
    replacement = {
        "client_request_id": uid(),
        **{k: v for k, v in old["payload"].items() if k not in {"branch_id", "client_request_id"}},
    }
    newer = client.post(endpoint, json=replacement)
    assert newer.status_code == 202, newer.text
    denied = client.post(f"{endpoint}/{run_id}/resume")
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "seven_stage_active"
    assert domain.task(run_id)["status"] == "blocked_runtime"
    assert domain.task(newer.json()["id"])["branch_id"] == branch


@pytest.mark.parametrize("auto_adopt", [False, True])
def test_explicit_reference_scope_analysis_and_card_handoff(
    client, domain, settings, story, reference, auto_adopt
):
    branch, endpoint, identifier = start(
        client,
        domain,
        story,
        "settings",
        reference_session_id=reference["id"],
        reference_revision=reference["revision"],
        auto_adopt_reference=auto_adopt,
    )
    model = Model(settings=settings)
    drain(settings, model)
    if not auto_adopt:
        waiting = domain.task(identifier)
        assert waiting["status"] == "needs_review", waiting["error"]
        stage = waiting["result"]["seven_stage_run"]
        assert stage["stage"] == "reference" and stage["reference_analysis_id"]
        assert model.calls == ["拆解所选原文写法"]
        draft = domain.artifact(stage["reference_analysis_id"])
        card = draft["payload"]["cards"][0]
        update_reference(client, domain, reference, cards=[card], selected_ids=[card["id"]])
        assert client.post(f"{endpoint}/{identifier}/resume").status_code == 200
        drain(settings, model)
    waiting = domain.task(identifier)
    state = waiting["result"]["seven_stage_run"]
    assert state["stage"] == "inspection", waiting["error"]
    assert model.calls.count("拆解所选原文写法") == 1
    chosen = state["reference_references"]
    assert len(chosen) == 1
    assert chosen[0]["session_id"] == reference["id"]
    assert chosen[0]["revision"] == reference["revision"] + 1
    outline_task = next(
        child
        for task_id in state["children"]
        if (child := domain.task(task_id))["payload"].get("outline_generation")
    )
    assert outline_task["payload"]["outline_generation"]["reference_cards"] == chosen
    adopted = client.get(f"/api/branches/{branch}/outline-workspace").json()["confirmed"]
    assert adopted["plan"]["volumes"][0]["units"][0]["chapters"][0]["reference_cards"] == chosen
    assert state["reference_basis"]["source"]["quote_sha256"]
    assert state["reference_basis"]["revision"] == reference["revision"]
    assert domain.branch(branch)["story_revision"] > 0
