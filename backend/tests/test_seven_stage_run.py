import asyncio
from dataclasses import replace
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ainovel import seven_stage_run
from ainovel.api import create_app
from ainovel.backups import create_backup, unpack_verified
from ainovel.budget import Budget
from ainovel.db import DomainError, dumps, uid
from ainovel.domain import Domain
from ainovel.evidence_export import export_evidence
from ainovel.providers import RequestNotSent
from ainovel.reference_sync import reference_card_at
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF, IDEA
from test_reference_flow import SOURCE, reference as reference_fixture, update as update_reference
from test_reference_flow import run as run_reference
from test_work_snapshot import request_restore, route, save_project, verify
from test_workflow_inspection import author, item, read, report, source as chapter_source


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
    def __init__(
        self,
        during_write=None,
        settings=None,
        limit_inspection=False,
        setting_findings=False,
        new_promise=False,
    ):
        self.calls = []
        self.during_write = during_write
        self.settings = settings
        self.limit_inspection = limit_inspection
        self.setting_findings = setting_findings
        self.new_promise = new_promise

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
            if self.setting_findings and instruction["category_id"] == "world":
                value = {
                    "items": [
                        {
                            "title": "门铃规则",
                            "detail": "铜铃只在门内响。",
                            "links": ["N2"],
                            "setting_policy": {"strength": "hard", "scope": "门内"},
                        },
                        {
                            "title": "回声规则",
                            "detail": "回声只有在门外能听到。",
                            "links": [],
                            "setting_policy": {"strength": "hard", "scope": "门外"},
                        },
                    ]
                }
        elif action == "conception.review.bundle":
            value = {"issues": []}
        elif action == "生成四级大纲候选":
            chapters = instruction["章节目录"]
            value = plan(chapters, [item["id"] for item in instruction["已确认世界设定"]])
            if self.new_promise:
                assert not instruction["可用伏笔"]
                assert "new_promises" in instruction["要求"]
                value["new_promises"] = [
                    {
                        "title": "门环上的旧刻痕",
                        "notes": "确认刻痕能否指出师姐去向。",
                        "plant_chapter_id": chapters[0]["id"],
                        "opens_chapter_id": chapters[-1]["id"],
                        "closes_chapter_id": chapters[-1]["id"],
                    }
                ]
            if available := instruction.get("获准采用的手法卡"):
                assert SOURCE not in dumps(frame)
                card = available[0]
                assert card["conditions"] == "新动作须依靠已收到的部分线索"
                assert card["exceptions"] == "替换人物、物件与具体场景"
                value["volumes"][0]["units"][0]["chapters"][0]["reference_cards"] = [
                    {
                        key: card[key]
                        for key in ("session_id", "card_id", "revision", "version_id")
                        if key in card
                    }
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


@pytest.mark.parametrize("origin", ["manual", "idea", "settings", "outline"])
def test_same_two_chapter_fixture_reaches_the_author_delivery_gate(client, domain, settings, origin):
    work = domain.create_novel("遗铃 · 同一两章验收")
    branch = work["branch_id"]
    prefix = f"/api/branches/{branch}"
    outline_route = prefix + "/outline-workspace"
    model = Model(settings=settings)
    source = {"idea": IDEA}

    if origin != "idea":
        save_project(client, branch, 0, BRIEF["ending"])
        setting_id = setting_confirmation(client, branch)
        chapters = [domain.create_chapter(branch, title) for title in ("试铃", "回铃")]
        source = {"source_id": setting_id}
        if origin != "settings":
            outline = put_and_confirm(
                client,
                outline_route,
                {"base_revision": 0, "confirmation_id": setting_id, "plan": plan(chapters, ["bell"])},
            )
            source = {"source_id": outline["confirmed"]["id"]}

    if origin == "manual":
        for chapter in chapters:
            verify(
                client.put(
                    f"/api/chapters/{chapter['id']}",
                    json={"base_revision": 0, "content": BODY},
                )
            )
    else:
        started = client.post(
            prefix + "/seven-stage-runs",
            json={
                **source,
                "start_from": origin,
                "chapter_count": 2,
                "account": "deepseek",
                "budget_limit": 1,
                "client_request_id": uid(),
                "auto_confirm_planning": True,
                "auto_adopt_body": True,
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        drain(settings, model)
        run = domain.task(run_id)
        assert run["status"] == "needs_review", run["error"]
        assert run["result"]["seven_stage_run"]["stage"] == "inspection"

    assert model.calls.count("writing.expand_plot") == (0 if origin == "manual" else 2)
    assert ("conception.generate.project" in model.calls) is (origin == "idea")
    assert ("生成四级大纲候选" in model.calls) is (origin in {"idea", "settings"})
    assert verify(client.get(prefix + "/conception/project-brief"))["brief"]["ending"] == BRIEF["ending"]
    confirmed = verify(client.get(outline_route))["confirmed"]
    chapters = verify(client.get(prefix + "/chapters"))
    assert len(chapters) == 2
    assert [row["chapter_id"] for row in confirmed["plan"]["volumes"][0]["units"][0]["chapters"]] == [
        row["id"] for row in chapters
    ]
    chapter = domain.chapter(chapters[0]["id"])
    assert all(domain.chapter(row["id"])["content"] == BODY for row in chapters)

    completion = prefix + "/book-completion"
    decision = {
        "basis_sha256": verify(client.get(completion))["basis_sha256"],
        "read_note": "按同一两章工程样本逐项核对。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": BRIEF["ending"], "sources": [chapter_source(chapter)]},
        "characters": {"note": "学徒完成两轮核对。", "sources": [chapter_source(chapter)]},
        "promises": {"note": "核对两章样本记录。", "sources": [chapter_source(chapter)]},
        "intro": "学徒凭铜铃寻找失散的师姐。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    blocked = client.post(completion + "/confirm", json=decision)
    assert blocked.status_code == 409 and blocked.json()["code"] == "completion_blocked"
    inspection = prefix + "/workflow-inspections"
    preview = verify(client.get(inspection))["preview"]
    signed = client.post(
        inspection,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
            ],
            "length_targets": {"expected_chapters": 2},
        },
    )
    assert signed.status_code == 201, signed.text
    decision["basis_sha256"] = verify(client.get(completion))["basis_sha256"]
    delivered = client.post(completion + "/confirm", json=decision)
    assert delivered.status_code == 201, delivered.text
    manifest = delivered.json()["manifest"]
    assert manifest["inspection_id"] == signed.json()["id"]
    assert len(manifest["chapters"]) == 2
    assert set(manifest["files"]) == {"正文.md", "设定册.md", "简介.md", "试读.md"}


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


def test_idea_run_freezes_whole_book_chapter_count_separately_from_author_idea(client, domain, settings):
    idea = "学徒循遗铃查明师姐为何失踪。\n全书写一卷，约四千字，同一夜内收束。"
    branch, _, run_id = start(
        client, domain, None, "idea", idea=idea, chapter_count=2, auto_confirm_planning=False
    )
    instructions = []
    length = "全书一卷两章，约四千字，同一夜内收束。"

    class ProjectModel:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instructions.append(json.loads(prompt.split("\n只返回")[0])["任务"])
            return {"text": dumps({**BRIEF, "length": length}), "usage": {"input": 1, "output": 1}}

    drain(settings, ProjectModel())
    run = domain.task(run_id)
    assert run["status"] == "needs_review", run["error"]
    state = run["result"]["seven_stage_run"]
    assert state["stage"] == "project"
    child = domain.task(state["child_id"])
    frozen = child["result"]["conception_generation_snapshot"]
    scope = {"version": "project-planning-scope/1", "chapter_count": 2}
    assert child["payload"]["project_chapter_count"] == 2
    assert frozen["idea"] == idea and frozen["project_planning_scope"] == scope
    assert len(instructions) == 1
    instruction = instructions[0]
    assert instruction["作者创作思路"] == idea
    assert instruction["自动接力的整书范围"] == {"约束版本": scope["version"], "计划总章数": 2}
    assert "保留作者已明确且能与章数相容的篇幅" in instruction["要求"]
    assert "把建议字数写成作者原话" in instruction["要求"]
    candidate = domain.artifact(state["artifact_id"])
    assert candidate["basis"]["project_planning_scope"] == scope
    assert candidate["payload"] == {"idea": idea, "brief": {**BRIEF, "length": length}}
    project_route = f"/api/branches/{branch}/conception/project-brief"
    confirmed = verify(
        client.put(
            project_route,
            json={
                "base_revision": 0,
                "idempotency_key": uid(),
                "candidate_id": candidate["id"],
                **candidate["payload"],
            },
        )
    )
    assert confirmed["idea"] == idea and confirmed["brief"]["length"] == length


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
        assert len(updated["result"]["seven_stage_run"]["children"]) == child_count + 1
        child_count += 1
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
            "author_decisions": {
                "reading": "verified",
                "ending": "verified",
                "characters": "verified",
                "promises": "verified",
            },
            "ending": {"note": "主冲突以归还铜铃解决。", "sources": [chapter_source(chapter)]},
            "characters": {"note": "确认学徒找到线索。", "sources": [chapter_source(chapter)]},
            "promises": {"note": "铜铃线索完成本篇承诺。", "sources": [chapter_source(chapter)]},
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


def test_backup_recovers_automatic_milestones_and_their_chapter_and_outline_history(
    client, domain, settings, story, tmp_path
):
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "学徒在门外认出师姐的铃。")
    second = domain.create_chapter(branch, "第二卷·铃归原主")
    project = save_project(client, branch, 0, "铜铃归还师姐。")
    setting_id = setting_confirmation(client, branch)
    prefix = f"/api/branches/{branch}"
    outline_url = prefix + "/outline-workspace"
    original_plan = plan([first], ["bell"])
    next_volume = plan([second], ["bell"])["volumes"][0]
    next_volume.update(id="return", title="归还")
    next_volume["units"][0]["id"] = "return-bell"
    original_plan["volumes"].append(next_volume)
    authored = put_and_confirm(
        client,
        outline_url,
        {"base_revision": 0, "confirmation_id": setting_id, "plan": original_plan},
    )["confirmed"]

    runs = prefix + "/seven-stage-runs"
    request = {
        "start_from": "outline",
        "source_id": authored["id"],
        "account": "deepseek",
        "budget_limit": 1,
        "client_request_id": uid(),
        "auto_adopt_body": True,
    }
    started = verify(client.post(runs, json=request), 202)
    assert verify(client.post(runs, json=request), 202)["id"] == started["id"]
    model = Model(settings=settings)
    drain(settings, model)
    progress = domain.task(started["id"])["result"]["seven_stage_run"]
    assert progress["stage"] == "inspection"
    published_second = domain.chapter(second["id"])
    assert published_second["content"] == BODY

    inspection_url = prefix + "/workflow-inspections"
    preview = verify(client.get(inspection_url))["preview"]
    inspected = verify(
        client.post(
            inspection_url,
            json={
                "basis_sha256": preview["basis_sha256"],
                "author_reviews": [
                    author(row["check_id"], published_second)
                    for row in preview["items"]
                    if row["status"] == "pending"
                ],
            },
        ),
        201,
    )
    completion_url = prefix + "/book-completion"
    decision = {
        "basis_sha256": verify(client.get(completion_url))["basis_sha256"],
        "read_note": "作者通读两卷保存稿。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": "铜铃归还师姐。", "sources": [chapter_source(published_second)]},
        "characters": {"note": "确认二人重逢。", "sources": [chapter_source(published_second)]},
        "promises": {"note": "两卷均围绕还铃收束。", "sources": [chapter_source(published_second)]},
        "intro": "循着铜铃去找它的主人。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [first["id"]],
        "materials_reviewed": True,
    }
    completed = verify(client.post(completion_url + "/confirm", json=decision), 201)
    assert verify(client.post(completion_url + "/confirm", json=decision), 201)["id"] == completed["id"]
    verify(client.post(f"{runs}/{started['id']}/resume"))
    drain(settings, model)
    assert domain.task(started["id"])["status"] == "succeeded"

    saved = verify(client.get(route(branch)))
    labels = [row["label"] for row in saved if row["purpose"] == "milestone"]
    assert labels.count("启动七台接力") == labels.count("确认作品完结") == 1
    milestones = {row["label"]: row for row in saved if row["purpose"] == "milestone"}
    m1 = milestones["确认立项"]
    kickoff = milestones["启动七台接力"]
    first_end = milestones["接力到卷末 · 遗铃"]
    second_end = milestones["接力到卷末 · 归还"]
    m7 = milestones["确认作品完结"]
    assert m1["project_id"] == project["id"] and m1["outline_id"] is None
    assert kickoff["id"] == progress["start_snapshot_id"]
    assert kickoff["setting_id"] == setting_id and kickoff["outline_id"] == authored["id"]
    assert progress["volume_snapshot_ids"] == {"v": first_end["id"], "return": second_end["id"]}
    assert first_end["scoped_chapter_ids"] == [first["id"]]
    assert second_end["scoped_chapter_ids"] == [second["id"]]
    assert m7["outline_id"] == authored["id"]
    assert m7["reports"] == {"workflow_inspection": inspected["id"], "book_completion": completed["id"]}

    domain.save(second["id"], published_second["revision"], "后来改写的结尾。")
    later_plan = {**original_plan, "summary": "新的四级大纲"}
    updated = put_and_confirm(
        client,
        outline_url,
        {"base_revision": authored["revision"], "confirmation_id": setting_id, "plan": later_plan},
    )["confirmed"]
    backup = create_backup(settings, domain.db)
    isolated = tmp_path / "isolated-milestones"
    unpack_verified(Path(backup["path"]), isolated)
    assert (isolated / "usage-evidence.sqlite").is_file()

    with TestClient(create_app(replace(settings, data_dir=isolated))) as recovered:
        recovered.headers["X-Ainovel-CSRF"] = recovered.get("/api/session").json()["csrf"]
        points = {row["id"]: row for row in verify(recovered.get(route(branch)))}
        for mark in (m1, kickoff, first_end, second_end, m7):
            assert points[mark["id"]] == mark
        for mark, historical in (
            (m1, {**second, "content": ""}),
            (kickoff, {**second, "content": ""}),
            (first_end, first),
            (second_end, published_second),
            (m7, published_second),
        ):
            chapters = f"{route(branch)}/{mark['id']}/chapters"
            old = verify(recovered.get(f"{chapters}/{historical['id']}"))["snapshot"]
            assert old == {key: historical[key] for key in ("content", "revision")}
        assert verify(recovered.get(inspection_url + "/" + inspected["id"]))["current"] is False
        assert verify(recovered.get(completion_url + "/history/" + completed["id"]))["current"] is False
        fork = request_restore(recovered, branch, second_end["id"], "new_branch", branch_name="卷末恢复演练")
        restored = verify(recovered.get(f"/api/branches/{fork['branch_id']}/outline-workspace"))["confirmed"]
        assert restored["plan"]["summary"] == authored["plan"]["summary"]
        assert restored["setting_basis"]["restored_outline_id"] == authored["id"]
        forked_chapter = fork["chapters"][second["id"]]["id"]
        assert verify(recovered.get(f"/api/chapters/{forked_chapter}"))["content"] == BODY
        assert verify(recovered.get(outline_url))["confirmed"]["id"] == updated["id"]


def test_resume_from_adopted_project_keeps_author_ending_and_skips_ideation(client, domain, settings):
    branch = domain.create_novel("从立项接力的遗铃")["branch_id"]
    prefix = f"/api/branches/{branch}"
    endpoint = f"{prefix}/seven-stage-runs"
    project = f"{prefix}/conception/project-brief"
    body = {"idea": IDEA, "brief": {**BRIEF, "ending": "和师姐一起把铃交还铸铃师。"}}
    first = client.put(project, json={**body, "base_revision": 0, "idempotency_key": uid()}).json()
    body["brief"]["ending"] = "师姐亲手将铜铃挂回城门。"
    adopted = client.put(project, json={**body, "base_revision": 1, "idempotency_key": uid()}).json()
    request = {
        "start_from": "project",
        "source_id": first["id"],
        "chapter_count": 1,
        "account": "deepseek",
        "budget_limit": 1,
        "client_request_id": uid(),
        "auto_confirm_planning": True,
        "auto_adopt_body": True,
    }
    assert client.post(endpoint, json=request).json()["code"] == "project_brief_changed"
    started = client.post(endpoint, json={**request, "source_id": adopted["id"]})
    assert started.status_code == 202, started.text
    identifier = started.json()["id"]
    model = Model(settings=settings)
    drain(settings, model)
    task = domain.task(identifier)
    assert task["status"] == "needs_review", task["error"]
    state = task["result"]["seven_stage_run"]
    assert state["stage"] == "inspection" and state["project_id"] == adopted["id"]
    assert "conception.generate.project" not in model.calls
    assert "conception.generate.category" in model.calls
    confirmed = client.get(f"{prefix}/conception").json()
    assert confirmed["project"]["brief"]["ending"] == body["brief"]["ending"]
    assert confirmed["confirmation"]["current"]
    adopted_settings = domain.artifact(confirmed["confirmation"]["id"])
    assert adopted_settings["payload"]["project_confirmation_id"] == adopted["id"]


def test_resume_from_written_manuscript_or_passed_inspection_only_runs_remaining_stages(
    client, domain, settings, story
):
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    prefix = f"/api/branches/{branch}"
    endpoint = f"{prefix}/seven-stage-runs"
    report_url = f"{prefix}/workflow-inspections"
    setting_id = setting_confirmation(client, branch)
    put_and_confirm(
        client,
        f"{prefix}/outline-workspace",
        {"base_revision": 0, "confirmation_id": setting_id, "plan": plan([{"id": chapter_id}], ["bell"])},
    )
    assert client.get(endpoint + "/sources").json() == {"writing_id": None, "inspection_id": None}

    def launch(origin, source, status=202):
        response = client.post(
            endpoint,
            json={
                "start_from": origin,
                "source_id": source,
                "account": "deepseek",
                "budget_limit": 1,
                "client_request_id": uid(),
            },
        )
        assert response.status_code == status, response.text
        return response.json()

    first = domain.save(chapter_id, 0, BODY)
    original_source = client.get(endpoint + "/sources").json()
    assert original_source["writing_id"] and not original_source["inspection_id"]
    chapter = domain.save(chapter_id, first["revision"], BODY + "夜已深。")
    current_source = client.get(endpoint + "/sources").json()
    assert current_source["writing_id"] != original_source["writing_id"]
    assert launch("writing", original_source["writing_id"], 409)["code"] == "inspection_changed"
    writing = launch("writing", current_source["writing_id"])
    model = Model(settings=settings)
    drain(settings, model)
    run = domain.task(writing["id"])
    assert run["status"] == "needs_review", run["error"]
    state = run["result"]["seven_stage_run"]
    assert state["stage"] == "inspection"
    assert state["completed_chapters"] == [
        {"id": chapter_id, "revision": chapter["revision"], "existing": True}
    ]
    assert model.calls == ["对账立项章纲与节奏"]
    assert launch("inspection", state["inspection_id"], 409)["code"] == "seven_stage_active"
    assert client.post(f"{endpoint}/{run['id']}/cancel").status_code == 200
    assert launch("inspection", state["inspection_id"], 409)["code"] == "inspection_changed"

    def confirm_report():
        preview = client.get(report_url).json()["preview"]
        response = client.post(
            report_url,
            json={
                "basis_sha256": preview["basis_sha256"],
                "author_reviews": [
                    author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
                ],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    adopted = confirm_report()
    assert client.get(endpoint + "/sources").json()["inspection_id"] == adopted
    completion = launch("inspection", adopted)
    updated = confirm_report()
    assert updated != adopted
    drain(settings, model)
    run = domain.task(completion["id"])
    assert run["status"] == "needs_review" and "选定" in run["error"]
    assert model.calls == ["对账立项章纲与节奏"]
    assert client.post(f"{endpoint}/{run['id']}/resume").status_code == 200
    drain(settings, model)
    run = domain.task(completion["id"])
    assert run["status"] == "needs_review", run["error"]
    state = run["result"]["seven_stage_run"]
    assert state["stage"] == "completion" and state["inspection_id"] == updated
    assert model.calls == ["对账立项章纲与节奏", "准备完本收束核对建议", "起草完本交付简介"]
    assert domain.chapter(chapter_id)["revision"] == chapter["revision"]


@pytest.mark.parametrize("timing", ["before_completion_stage", "before_preparation_queue", "before_success"])
def test_red_inspection_stops_completion_and_same_run_resumes_after_review(
    client, settings, story, monkeypatch, timing
):
    prefix = f"/api/branches/{story['branch_id']}"
    runs = prefix + "/seven-stage-runs"
    inspection = prefix + "/workflow-inspections"
    completion = prefix + "/book-completion"
    chapter = verify(
        client.put(f"/api/chapters/{story['chapter_id']}", json={"base_revision": 0, "content": BODY})
    )
    put_and_confirm(
        client,
        prefix + "/outline-workspace",
        {"base_revision": 0, "plan": plan([chapter], [])},
    )
    model = Model(settings=settings)

    def sign_book():
        preview = read(client, inspection)["preview"]
        return report(
            client,
            inspection,
            preview,
            author_reviews=[
                author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
            ],
            length_targets={"expected_chapters": 1},
        )

    def sign_chapter(status):
        preview = read(client, inspection, scope="chapter", scope_id=chapter["id"])["preview"]
        return report(client, inspection, preview, author_reviews=[author("03", chapter, status)])

    def confirm():
        return client.post(
            completion + "/confirm",
            json={
                "basis_sha256": read(client, completion)["basis_sha256"],
                "read_note": "作者已通读保存稿。",
                "author_decisions": {
                    "reading": "verified",
                    "ending": "verified",
                    "characters": "verified",
                    "promises": "verified",
                },
                "ending": {"note": "主冲突已收束。", "sources": [chapter_source(chapter)]},
                "characters": {"note": "已核对学徒身份。", "sources": [chapter_source(chapter)]},
                "promises": {"note": "铜铃线索已处理。", "sources": [chapter_source(chapter)]},
                "intro": "学徒循着铃声寻找线索。",
                "spoiler_scope": "opening",
                "sample_chapter_ids": [chapter["id"]],
                "materials_reviewed": True,
            },
        )

    sign_book()
    origin = "inspection" if timing == "before_success" else "writing"
    sources = read(client, runs + "/sources")
    task = verify(
        client.post(
            runs,
            json={
                "start_from": origin,
                "source_id": sources[f"{origin}_id"],
                "account": "deepseek",
                "budget_limit": 1,
                "client_request_id": uid(),
            },
        ),
        202,
    )

    def status():
        current = read(client, f"/api/tasks/{task['id']}")
        return current["status"], current["result"]["seven_stage_run"]

    def resume():
        verify(client.post(f"{runs}/{task['id']}/resume"))

    original = None
    with monkeypatch.context() as race:
        if timing == "before_success":
            drain(settings, model)
            waiting, prepared = status()
            assert waiting == "needs_review" and prepared["completion_preparation_id"]
            original = verify(confirm(), 201)
            original_download = f"{completion}/{original['id']}/export"
            original_package = client.get(original_download)
            assert original_package.status_code == 200, original_package.text
            resume()
            checkpoint = Worker.checkpoint

            def checkpoint_with_red(worker, result, status="running", error="", **options):
                if worker.task["id"] == task["id"] and status == "succeeded":
                    sign_chapter("red")
                return checkpoint(worker, result, status, error, **options)

            race.setattr(Worker, "checkpoint", checkpoint_with_red)
        else:
            assert asyncio.run(Worker(settings, model).run_once())
            stage_status, stage = status()
            assert stage_status == "queued" and stage["stage"] == "completion"
            assert not model.calls
            if timing == "before_preparation_queue":
                drive_child = seven_stage_run._drive_child

                def queue_with_red(worker, state, step, kind, payload):
                    if worker.task["id"] == task["id"] and step.startswith("completion-"):
                        sign_chapter("red")
                    return drive_child(worker, state, step, kind, payload)

                race.setattr(seven_stage_run, "_drive_child", queue_with_red)
            else:
                sign_chapter("red")
        drain(settings, model)

    waiting, blocked = status()
    assert waiting == "needs_review"
    assert blocked["stage"] == "completion" and blocked["completion_basis_changed"]
    assert "completion_id" not in blocked
    prepared_count = model.calls.count("起草完本交付简介")
    if original:
        assert prepared_count == 1
        assert blocked["children"] == prepared["children"]
        assert not read(client, completion)["latest"]["current"]
        assert client.get(original_download).content == original_package.content
    else:
        assert not prepared_count and not blocked["children"]
    current = read(client, completion)
    assert next(check for check in current["checks"] if check["id"] == "inspection")["status"] == "red"
    assert verify(confirm(), 409)["code"] == "completion_blocked"

    resume()
    drain(settings, model)
    waiting, at_inspection = status()
    assert waiting == "needs_review" and at_inspection["stage"] == "inspection"
    assert item(read(client, inspection)["latest"], "03")["current_status"] == "red"
    assert model.calls.count("起草完本交付简介") == prepared_count
    sign_chapter("green")
    reviewed = sign_book()
    official = verify(confirm(), 201)
    resume()
    drain(settings, model)
    finished, receipt = status()
    assert finished == "succeeded" and receipt["completion_id"] == official["id"]
    assert receipt["inspection_id"] == official["manifest"]["inspection_id"] == reviewed["id"]
    assert all(child in receipt["children"] for child in blocked["children"])
    assert model.calls.count("起草完本交付简介") == prepared_count

    download = f"{completion}/{official['id']}/export"
    archive = client.get(download)
    assert archive.status_code == 200, archive.text
    sign_chapter("red")
    assert not read(client, completion)["latest"]["current"]
    assert client.get(download).content == archive.content
    assert status()[0] == "succeeded"


def test_known_same_time_state_conflict_stops_relay_until_story_is_corrected(client, settings, story):
    prefix = f"/api/branches/{story['branch_id']}"
    inspection = prefix + "/workflow-inspections"
    completion = prefix + "/book-completion"
    runs = prefix + "/seven-stage-runs"
    content = "学徒听见铃响。左手骨折。左手完好。"
    chapter = verify(
        client.put(f"/api/chapters/{story['chapter_id']}", json={"base_revision": 0, "content": content})
    )
    put_and_confirm(client, prefix + "/outline-workspace", {"base_revision": 0, "plan": plan([chapter], [])})

    def sign_book():
        preview = read(client, inspection)["preview"]
        return report(
            client,
            inspection,
            preview,
            author_reviews=[
                author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
            ],
        )

    def quote(text):
        start = len(content[: content.index(text)].encode("utf-16-le")) // 2
        return {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": start,
            "end": start + len(text.encode("utf-16-le")) // 2,
            "quote": text,
        }

    def save_record(record, archived=None):
        graph = read(client, prefix + "/story-graph")
        request = {"base_story_revision": graph["story_revision"], "record": record}
        if archived:
            result = verify(
                client.put(
                    f"{prefix}/story-records/{archived['id']}",
                    json={**request, "base_revision": archived["revision"], "archived": True},
                )
            )
        else:
            result = verify(client.post(prefix + "/story-records", json=request), 201)
        return next(row for row in result["records"] if row["payload"]["title"] == record["title"])

    actor = save_record({"kind": "character", "title": "无名学徒"})
    event = save_record(
        {
            "kind": "event",
            "title": "听铃",
            "status": "actual",
            "story_order": 1,
            "participants": [actor["id"]],
            "sources": [quote("学徒听见铃响。")],
        }
    )
    condition = {
        "kind": "state",
        "character_id": actor["id"],
        "event_id": event["id"],
        "category": "injury",
        "key": "左手伤势",
        "audience": "author",
        "truth": "actual",
    }
    save_record({**condition, "title": "左手骨折", "value": "骨折", "sources": [quote("左手骨折。")]})
    previous = sign_book()
    assert read(client, runs + "/sources")["inspection_id"] == previous["id"]
    conflicting = save_record(
        {**condition, "title": "左手完好", "value": "完好", "sources": [quote("左手完好。")]}
    )
    sources = read(client, runs + "/sources")
    assert sources["inspection_id"] is None
    task = verify(
        client.post(
            runs,
            json={
                "start_from": "writing",
                "source_id": sources["writing_id"],
                "account": "deepseek",
                "budget_limit": 1,
                "client_request_id": uid(),
            },
        ),
        202,
    )
    task_route = f"/api/tasks/{task['id']}"
    resume = f"{runs}/{task['id']}/resume"
    model = Model(settings=settings)
    drain(settings, model)

    waiting = read(client, task_route)
    state = waiting["result"]["seven_stage_run"]
    assert waiting["status"] == "needs_review" and state["stage"] == "inspection"
    assert "completion_id" not in state
    blocked = sign_book()
    assert item(blocked, "03")["current_status"] == "red"
    verify(client.post(resume))
    drain(settings, model)
    assert read(client, task_route)["result"]["seven_stage_run"]["stage"] == "inspection"
    assert read(client, runs + "/sources")["inspection_id"] is None
    assert "起草完本交付简介" not in model.calls

    save_record(conflicting["payload"], archived=conflicting)
    current = sign_book()
    assert read(client, runs + "/sources")["inspection_id"] == current["id"]
    verify(client.post(resume))
    drain(settings, model)
    next_step = read(client, task_route)
    review = next_step["result"]["seven_stage_run"]
    assert next_step["status"] == "needs_review" and review["stage"] == "completion"
    assert review["inspection_id"] == current["id"] and review["completion_preparation_id"]
    assert read(client, completion)["latest"] is None
    assert model.calls.count("起草完本交付简介") == 1


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
        assert state["children"][0] == child
        assert len(state["children"]) == 2
        assert state["stage"] == "inspection"
        assert model.calls == ["writing.expand_plot", "审查正文", "对账立项章纲与节奏"]
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


def formal_card(client, domain, reference, *, selected=True, complete=True):
    card = {
        "id": uid(),
        "observation": "交涉只给局部信息",
        "interpretation": "给主角一个具体的下一步",
        "technique": "由有限线索推动下一步",
        "conditions": "新动作须依靠已收到的部分线索",
        "exceptions": "替换人物、物件与具体场景" if complete else "",
        "evidence": SOURCE,
    }
    saved = update_reference(
        client, domain, reference, cards=[card], selected_ids=[card["id"]] if selected else []
    )
    with domain.db.connect() as tx:
        version = reference_card_at(
            tx, saved["payload"]["branch_id"], saved["id"], card["id"], saved["revision"]
        )
    return {
        "session_id": saved["id"],
        "card_id": card["id"],
        "revision": saved["revision"],
        "version_id": version.id,
    }, card


@pytest.mark.parametrize("choose_card", [True, False])
def test_confirmed_settings_relay_uses_only_explicitly_chosen_saved_cards(
    client, domain, settings, story, reference, choose_card
):
    chosen, _ = formal_card(client, domain, reference, selected=False)
    intended = [chosen] if choose_card else []
    branch, _, identifier = start(client, domain, story, "settings", reference_cards=intended)
    state = domain.task(identifier)["result"]["seven_stage_run"]
    assert state["stage"] == "outline"
    assert state.get("reference_references", []) == intended
    assert "reference_basis" not in state

    model = Model(settings=settings)
    drain(settings, model)
    finished = domain.task(identifier)
    state = finished["result"]["seven_stage_run"]
    assert state["stage"] == "inspection", finished["error"]
    assert model.calls == ["生成四级大纲候选", "writing.expand_plot", "审查正文", "对账立项章纲与节奏"]
    assert "reference_analysis_id" not in state
    children = [domain.task(task_id) for task_id in state["children"]]
    assert len(children) == 3
    assert children[0]["payload"]["outline_generation"].get("reference_cards", []) == intended
    assert all(child["payload"]["action"] != "reference.analyze" for child in children)
    with Budget(settings).db() as ledger:
        calls = ledger.execute("SELECT task_id FROM calls ORDER BY created").fetchall()
    assert [row["task_id"] for row in calls] == [
        children[0]["id"],
        children[1]["id"],
        children[1]["id"],
        children[2]["id"],
    ]
    saved = client.get(f"/api/branches/{branch}/outline-workspace").json()["confirmed"]
    assert saved["plan"]["volumes"][0]["units"][0]["chapters"][0].get("reference_cards", []) == intended
    assert client.get(f"/api/reference-sessions/{reference['id']}").json()["revision"] == chosen["revision"]


@pytest.mark.parametrize("mode", ["relay", "outline"])
def test_explicit_selection_rejects_old_incomplete_and_other_work_cards(
    client, domain, story, reference, mode
):
    branch = story["branch_id"]
    confirmed = setting_confirmation(client, branch)
    prefix = f"/api/branches/{branch}"
    outline = prefix + "/outline-workspace"
    endpoint, options = (
        (prefix + "/seven-stage-runs", {"start_from": "settings", "source_id": confirmed, "budget_limit": 1})
        if mode == "relay"
        else (outline + "/generation", {"base_revision": 0, "confirmation_id": confirmed})
    )
    active, card = formal_card(client, domain, reference)
    assert client.get(outline + "/reference-options").json()[0]["version_id"] == active["version_id"]

    def rejects(denied):
        response = client.post(
            endpoint,
            json={**options, "account": "deepseek", "client_request_id": uid(), "reference_cards": [denied]},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "reference_changed"

    update_reference(client, domain, reference, cards=[{**card, "technique": "仅保留与下一步有关的线索"}])
    assert client.get(outline + "/reference-options").json()[0]["version_id"] != active["version_id"]
    rejects(active)
    incomplete, _ = formal_card(client, domain, reference, selected=False, complete=False)
    rejects(incomplete)
    other = domain.create_novel("其他合成作品")
    update_reference(
        client,
        domain,
        reference,
        branch_id=other["branch_id"],
        chapter_id=None,
        cards=[card],
        selected_ids=[card["id"]],
    )
    other_option = client.get(
        f"/api/branches/{other['branch_id']}/outline-workspace/reference-options"
    ).json()[0]
    rejects({key: other_option[key] for key in active})
    invalid = {key: value for key, value in active.items() if key != "version_id"}
    response = client.post(
        endpoint,
        json={**options, "account": "deepseek", "client_request_id": uid(), "reference_cards": [invalid]},
    )
    assert response.status_code == 422, response.text


def test_author_pinned_card_stays_selectable_until_pin_is_removed(client, domain, settings, story, reference):
    pinned, card = formal_card(client, domain, reference)
    update_reference(
        client,
        domain,
        reference,
        cards=[{**card, "technique": "按新草稿安排查证"}],
        pinned_revisions={card["id"]: pinned["revision"]},
    )
    branch = story["branch_id"]
    options = client.get(f"/api/branches/{branch}/outline-workspace/reference-options").json()
    cards = {row["version_id"]: row for row in options}
    assert len(cards) == 2 and cards[pinned["version_id"]]["pinned"] is True
    other = next(row for row in options if row["version_id"] != pinned["version_id"])
    assert "pinned" not in other
    _, endpoint, identifier = start(client, domain, story, "settings", reference_cards=[pinned])
    body = domain.task(identifier)["payload"]
    update_reference(client, domain, reference, pinned_revisions={})
    model = Model(settings=settings)
    drain(settings, model)
    stopped = domain.task(identifier)
    assert stopped["status"] == "needs_review"
    assert stopped["result"]["seven_stage_run"]["interruption"]["code"] == "reference_changed"
    assert stopped["result"]["seven_stage_run"]["children"] == []
    assert model.calls == []
    assert client.post(f"{endpoint}/{identifier}/cancel").status_code == 200
    new = client.post(
        endpoint,
        json={
            **{key: value for key, value in body.items() if key != "branch_id"},
            "client_request_id": uid(),
            "reference_cards": [{key: other[key] for key in pinned}],
        },
    )
    assert new.status_code == 202, new.text


@pytest.mark.parametrize(("rounds", "pin_first"), [(1, False), (2, False), (2, True)])
def test_reference_rework_resumes_with_exact_author_selected_card(
    client, domain, settings, story, reference, rounds, pin_first
):
    branch, endpoint, identifier = start(
        client,
        domain,
        story,
        "settings",
        reference_session_id=reference["id"],
        reference_revision=reference["revision"],
    )
    model = Model(settings=settings)
    drain(settings, model)
    waiting = domain.task(identifier)
    initial = waiting["result"]["seven_stage_run"]
    assert waiting["status"] == "needs_review" and initial["stage"] == "reference"

    selected = None
    scope = {"start": 0, "end": 10}
    first_review = None
    for ordinal in range(rounds):
        if ordinal:
            scope = {"start": 11, "end": 20, "card_id": selected}
        review, _ = run_reference(client, settings, domain, reference["id"], "analyze", analysis_scope=scope)
        first_review = first_review or review
        card = review["payload"]["cards"][0]
        selected = selected or card["id"]
        saved = update_reference(
            client,
            domain,
            reference,
            cards=[
                dict(
                    card,
                    id=selected,
                    technique=f"作者定稿的第{ordinal + 1}轮拆解写法",
                    conditions="新动作须依靠已收到的部分线索",
                    exceptions="替换人物、物件与具体场景",
                )
            ],
            selected_ids=[selected],
        )
        assert saved["revision"] == reference["revision"] + ordinal + 1
        assert review["basis"]["reference_session"]["revision"] == saved["revision"] - 1

    approved_review = review
    approved_revision = saved["revision"]
    approved_technique = saved["payload"]["cards"][0]["technique"]
    if pin_first:
        approved_review = first_review
        approved_revision = reference["revision"] + 1
        approved_technique = "作者定稿的第1轮拆解写法"
        update_reference(client, domain, reference, pinned_revisions={selected: approved_revision})

    ledger = Budget(settings)
    manual_call = ledger.reserve(settings.providers["deepseek"], review["task_id"], 10, 5)
    ledger.settle(manual_call, {"input": 10, "output": 5})
    reply = client.post(f"{endpoint}/{identifier}/resume")
    assert reply.status_code == 200, reply.text
    drain(settings, model)
    resumed = domain.task(identifier)
    state = resumed["result"]["seven_stage_run"]
    assert state["stage"] == "inspection", resumed["error"]
    assert state["reference_analysis_id"] == initial["reference_analysis_id"]
    assert state["reference_review_analysis_ids"] == [approved_review["id"]]
    assert review["task_id"] not in state["children"]
    assert model.calls.count("拆解所选原文写法") == 1
    chosen = [{"session_id": reference["id"], "card_id": selected, "revision": approved_revision}]
    assert state["reference_references"] == chosen
    outline_task = next(
        child
        for task_id in state["children"]
        if (child := domain.task(task_id))["payload"].get("outline_generation")
    )
    assert outline_task["payload"]["outline_generation"]["reference_cards"] == chosen
    confirmed = client.get(f"/api/branches/{branch}/outline-workspace").json()["confirmed"]
    assert confirmed["plan"]["volumes"][0]["units"][0]["chapters"][0]["reference_cards"] == chosen
    assert confirmed["setting_basis"]["reference_cards"][0]["technique"] == approved_technique
    bundle = export_evidence(settings, [identifier])
    evidence = json.loads((Path(bundle["folder"]) / "evidence.json").read_text(encoding="utf-8"))["tasks"][0]
    assert evidence["billing_evidence"]["row_count"] > 0
    assert manual_call not in {row["id"] for row in evidence["calls"]}
    assert evidence["task"]["result"]["seven_stage_run"]["reference_review_analysis_ids"] == [
        approved_review["id"]
    ]


@pytest.mark.parametrize(
    "issue", ["unselected", "outside_selection", "changed_goal", "goal_changed_back", "foreign"]
)
def test_reference_rework_cannot_cross_frozen_author_scope(client, domain, settings, story, reference, issue):
    _, endpoint, identifier = start(
        client,
        domain,
        story,
        "settings",
        reference_session_id=reference["id"],
        reference_revision=reference["revision"],
    )
    model = Model(settings=settings)
    drain(settings, model)
    initial = domain.task(identifier)
    assert initial["status"] == "needs_review", initial["error"]

    selection = reference["id"]
    goal = reference["payload"]["goal"]
    if issue == "foreign":
        source = reference["payload"]["source"]
        created = client.post(
            "/api/reference-sessions",
            json={
                **{key: source[key] for key in ("unit_id", "start", "end", "quote")},
                "goal": goal,
                "branch_id": story["branch_id"],
                "chapter_id": story["chapter_id"],
            },
        )
        assert created.status_code == 201, created.text
        selection = created.json()["id"]
    if issue == "goal_changed_back":
        update_reference(client, domain, reference, goal="另一个未授权的学习目标")
        update_reference(client, domain, reference, goal=goal)
    review, _ = run_reference(
        client, settings, domain, selection, "analyze", analysis_scope={"start": 0, "end": 10}
    )
    card = review["payload"]["cards"][0]
    if issue == "outside_selection":
        card["evidence"] = "来客的目的地"
        assert (
            card["evidence"] in SOURCE
            and card["evidence"] not in review["payload"]["analysis_scope"]["quote"]
        )
    saved = update_reference(
        client,
        domain,
        reference,
        goal="另一个未授权的学习目标" if issue == "changed_goal" else goal,
        cards=[card],
        selected_ids=[] if issue == "unselected" else [card["id"]],
    )
    assert saved["payload"]["cards"] == [card]
    reply = client.post(f"{endpoint}/{identifier}/resume")
    assert reply.status_code == 200, reply.text
    drain(settings, model)
    waiting = domain.task(identifier)
    state = waiting["result"]["seven_stage_run"]
    assert waiting["status"] == "needs_review" and state["stage"] == "reference"
    assert state["reference_analysis_id"] == initial["result"]["seven_stage_run"]["reference_analysis_id"]
    assert not state.get("reference_review_analysis_ids") and not state.get("reference_references")
    assert model.calls == ["拆解所选原文写法"]


def test_auto_setting_conflicts_wait_for_author_confirmation(client, domain, settings, story):
    branch, endpoint, identifier = start(client, domain, story, "idea")
    model = Model(settings=settings, setting_findings=True)
    drain(settings, model)
    waiting = domain.task(identifier)
    assert waiting["status"] == "needs_review", waiting["error"]
    assert waiting["result"]["seven_stage_run"]["stage"] == "settings"
    assert "设定台" in waiting["error"]
    assert "生成四级大纲候选" not in model.calls
    config = f"/api/branches/{branch}/conception"
    current = client.get(config).json()
    assert current["confirmation"] is None
    assert any(row["kind"] == "policy_link" for row in current["setting_findings"])
    explicit = client.post(
        config + "/confirm",
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "idempotency_key": uid(),
            "note": "分别核对门内发声和门外听到回声的范围。",
            "reviewed_setting_findings": [row["id"] for row in current["setting_findings"]],
        },
    )
    assert explicit.status_code == 200, explicit.text
    assert client.post(f"{endpoint}/{identifier}/resume").status_code == 200
    drain(settings, model)
    advanced = domain.task(identifier)
    stage = advanced["result"]["seven_stage_run"]
    assert stage["stage"] == "inspection", advanced["error"]
    assert stage["setting_id"] == explicit.json()["confirmation"]["id"]
    assert model.calls.count("conception.generate.category") == 4


@pytest.mark.parametrize("mode", ["automatic", "manual", "change_before", "change_after"])
def test_outline_promise_receipt_allows_only_its_own_story_changes(client, domain, settings, story, mode):
    automatic = mode == "automatic"
    branch, run_endpoint, identifier = start(
        client, domain, story, "settings", auto_confirm_planning=automatic
    )
    outline = f"/api/branches/{branch}/outline-workspace"
    resume = f"{run_endpoint}/{identifier}/resume"
    graph_route = f"/api/branches/{branch}/story-graph"
    model = Model(settings=settings, new_promise=True)
    drain(settings, model)
    if not automatic:
        waiting = domain.task(identifier)
        state = waiting["result"]["seven_stage_run"]
        assert waiting["status"] == "needs_review" and state["stage"] == "outline", waiting["error"]
        candidate = domain.artifact(state["artifact_id"])
        assert candidate["payload"]["plan"]["new_promises"][0]["title"] == "门环上的旧刻痕"
        assert client.get(graph_route).json()["records"] == []
        draft_input = {
            "base_revision": state["outline_revision"],
            "source_generation_id": candidate["id"],
            "confirmation_id": state["setting_id"],
            "plan": candidate["payload"]["plan"],
        }
        saved = client.put(outline, json=draft_input)
        assert saved.status_code == 200, saved.text
        del draft_input["source_generation_id"]
        draft_input["base_revision"] = saved.json()["draft"]["revision"]
        draft_input["plan"]["new_promises"][0]["notes"] = "作者将寻找刻痕排在归铃之前。"
        saved = client.put(outline, json=draft_input)
        assert saved.status_code == 200, saved.text
        if mode == "change_before":
            domain.save(story["chapter_id"], 0, "作者在确认大纲之前另行修改正文。")
        confirmed = client.post(
            outline + "/confirm", json={"base_revision": saved.json()["draft"]["revision"]}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["confirmed"]["setting_basis"]["generation_id"] == candidate["id"]
        if mode == "change_after":
            domain.save(story["chapter_id"], 0, "作者在确认大纲之后另行修改正文。")
        resumed = client.post(resume)
        if mode.startswith("change"):
            assert resumed.status_code == 409 and resumed.json()["code"] == "seven_stage_changed"
            assert "writing.expand_plot" not in model.calls
            return
        assert resumed.status_code == 200, resumed.text
        drain(settings, model)

    run = domain.task(identifier)
    state = run["result"]["seven_stage_run"]
    assert state["stage"] == "inspection", run["error"]
    assert state["completed_chapters"][0]["id"] == story["chapter_id"]
    view = client.get(outline).json()
    assert view["confirmed"]["id"] == state["outline_id"]
    assert state["story_revision"] == view["story_revision"]
    assert [(row["title"], row["state"]) for row in view["promises"]] == [("门环上的旧刻痕", "planned")]
    assert len(client.get(graph_route).json()["records"]) == 1
    assert model.calls.count("生成四级大纲候选") == model.calls.count("writing.expand_plot") == 1
