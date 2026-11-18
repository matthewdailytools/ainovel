import asyncio
import json

import pytest

from ainovel.conception_generation import SETTING_FRAME_VERSION
from ainovel.db import uid
from ainovel.worker import Worker
from test_conception_workbench import basis, confirm, path, save
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_workflow_inspection import author


IDEA = "学徒靠刻痕找回失散的师姐。"
BRIEF = {
    "position": "以信物查案推动成长",
    "track": "短篇成长悬疑",
    "genre_ids": ["xianxia", "mystery"],
    "audience": "偏好克制成长和证据回收的读者",
    "distinction": "铜铃的每条限制都产生实质取舍",
    "length": "一卷两章",
    "cadence": "",
    "ending": "主角决定把铃留在门外。",
    "final_image": "门外的铜铃不再响。",
}


SETTING_SAMPLES = {
    "xianxia": {
        "core": ("修炼之道与终局", "主角发现破境代价后放弃升阶，只保住渡口。"),
        "characters": ("未入门的舟子", "舟子只能辨认残符的气息，不懂完整经诀。"),
        "relationships": ("渡口传承", "舟子知师父拒绝传诀，却不知道师父曾失去灵根。"),
        "world": ("残符的启用代价", "每启用一枚残符会损失一缕修为。"),
    },
    "mystery": {
        "core": ("遗铃的埋件", "绑绳换于退潮后，读者要到验第二份账簿才知道。"),
        "characters": ("只听见铃声的舟子", "舟子听到铃声但没看见绑绳被换，起初误判来人。"),
        "relationships": ("拒绝作证的师徒", "徒弟知道师父当晚在场，师父不知道徒弟听见铜铃。"),
        "world": ("账簿调阅", "没有掌柜许可，渡口的人不能阅览客船调度账簿。"),
    },
    "mixed": {
        "core": ("灵石账目中的真相", "先让读者看灵石亏空，验第二份账簿后才揭示谁调包。"),
        "characters": ("无资格破境的账房", "账房懂灵石品相却无突破资格，看到亏空也不知道调包者。"),
        "relationships": ("互瞒线索的师徒", "师父知宗门的灵石规则，弟子知账簿去向，双方分担核验代价。"),
        "world": ("宗门的低阶灵石", "渡口只准登记低阶灵石，高阶灵石须由宗门账房验签。"),
    },
}

SPECIAL_TOPICS = {
    "xianxia": {
        "core": "xianxia-24",
        "characters": "xianxia-4",
        "relationships": "xianxia-13",
        "world": "xianxia-12",
    },
    "mystery": {
        "core": "mystery-1",
        "characters": "mystery-4",
        "relationships": "mystery-5",
        "world": "mystery-3",
    },
}


class SettingFrameModel:
    def __init__(self, sample):
        self.sample = sample
        self.calls = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.calls.append(frame)
        task = frame["任务"]
        if task["action"] == "conception.review.bundle":
            output = {"issues": []}
        else:
            category = task["category_id"]
            title, detail = self.sample[category]
            item = {"title": title, "detail": detail, "links": []}
            if category == "world":
                item["setting_policy"] = {"strength": "hard", "scope": "旧渡口", "exceptions": "掌柜书面许可"}
            if category == "relationships":
                character = next(
                    entry
                    for entry in frame["上下文"]["可关联构思与来源"]
                    if entry["category_id"] == "characters"
                )
                item["links"] = [character["ref"]]
            output = {"items": [item]}
            if category == "core":
                output["items"].append({"title": "备用假说", "detail": "渡船从未离开旧码头。", "links": []})
        return {
            "text": json.dumps(output, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": uid(),
        }


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.calls.append(frame)
        if frame["任务"]["action"] == "conception.generate.project":
            assert frame["任务"]["作者创作思路"] == IDEA
            assert len(frame["任务"]["可选题材"]) == 36
            output = BRIEF
        else:
            output = {
                "items": [
                    {
                        "title": "门铃规则",
                        "detail": "铜铃只在山门内响。",
                        "links": [],
                        "setting_policy": {"strength": "hard", "scope": "山门", "exceptions": "维修时除外"},
                    },
                    {
                        "title": "查铃次序",
                        "detail": "通常先查账簿再去作坊。",
                        "links": [],
                        "setting_policy": {
                            "strength": "soft",
                            "scope": "初次访查",
                            "exceptions": "遇失火可先验物",
                        },
                    },
                ]
            }
        return {
            "text": json.dumps(output, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": uid(),
        }


def run(domain, settings, task, model):
    assert asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    return domain.artifact(done["result"]["artifact_id"])


@pytest.mark.parametrize(
    ("sample", "genres"),
    [("xianxia", ["xianxia"]), ("mystery", ["mystery"]), ("mixed", ["xianxia", "mystery"])],
)
def test_confirmed_project_setting_frames_flow_through_adoption_and_chapter_references(
    client, domain, settings, story, sample, genres
):
    branch, chapter = story["branch_id"], story["chapter_id"]
    endpoint = path(branch)
    exception = "无CP，证词不能替代物证。"
    hard_result = "结尾保住渡口"
    for definition in (
        {
            "kind": "reader",
            "text": "用证据推进悬念。",
            "audience": "女频",
            "genres": ["farm"],
            "mechanisms": ["无CP"],
            "author_exceptions": [exception],
        },
        {
            "kind": "intent",
            "text": f"{hard_result}。不写斗法。",
            "intent_contract": {"fixed_results": [hard_result], "prohibitions": ["不写斗法"]},
        },
    ):
        saved = client.post(
            f"/api/branches/{branch}/definitions",
            json={**definition, "base_story_revision": domain.branch(branch)["story_revision"]},
        )
        assert saved.status_code == 201, saved.text
    author_ending = "舟子归还遗铃，结尾保住渡口。"
    response = client.put(
        f"{endpoint}/project-brief",
        json={
            "base_revision": 0,
            "idempotency_key": uid(),
            "idea": IDEA,
            "brief": {**BRIEF, "genre_ids": genres, "ending": author_ending},
        },
    )
    assert response.status_code == 200, response.text
    project = response.json()

    current = client.get(endpoint).json()
    for category in current["categories"]:
        category["skipped"] = category["id"] not in SETTING_SAMPLES[sample]
        if category["id"] == "core":
            category["items"] = [
                {
                    "id": "draft-hypothesis",
                    "title": "未经核实的证词",
                    "detail": "旧铃已经毁掉。",
                    "adoption_state": "pending",
                }
            ]
    current = save(client, endpoint, current)
    response = client.post(
        f"{endpoint}/generate",
        json={
            **basis(current),
            "project_confirmation_id": project["id"],
            "account": "openai_1",
            "client_request_id": uid(),
        },
    )
    assert response.status_code == 202, response.text
    queued = response.json()
    frozen = queued["result"]["conception_generation_snapshot"]
    assert frozen["setting_frame_version"] == SETTING_FRAME_VERSION
    assert set(frozen["setting_frames"]) == SETTING_SAMPLES[sample].keys()
    model = SettingFrameModel(SETTING_SAMPLES[sample])
    bundle = run(domain, settings, queued, model)
    assert bundle["basis"]["setting_frame_version"] == SETTING_FRAME_VERSION
    assert bundle["basis"]["project_confirmation_id"] == project["id"]
    assert bundle["basis"]["author_contract_id"] and bundle["basis"]["genre_profile_id"]
    assert len(model.calls) == 5  # Four category requests and the existing consistency review.
    for call in model.calls[:-1]:
        task, context = call["任务"], call["上下文"]
        category_id = task["category_id"]
        framework = context["设定框架"]
        assert framework == frozen["setting_frames"][category_id]["context"]
        assert context["已确认立项"]["brief"]["ending"] == author_ending
        assert context["已确认立项"]["brief"]["audience"] == BRIEF["audience"]
        assert context["genre_profile"]["audience"] == "女频"
        assert context["author_contract"]["intent_contract"]["fixed_results"] == [hard_result]
        assert context["author_contract"]["intent_contract"]["prohibitions"] == ["不写斗法"]
        assert task["构思设定框架版本"] == SETTING_FRAME_VERSION
        guide = task["设定框架用法"]
        assert all(
            term in guide for term in ("作者底稿中的真相", "人物在相应时点可知或误判", "读者当时可见", "禁项")
        )
        assert "规划中的事件也不等同于正文已发生" in guide
        assert framework["category"] == category_id and exception in framework["author_exceptions"]
        assert [mechanism["id"] for mechanism in framework["mechanisms"]] == ["plugin-20"]
        groups = [framework["primary"], *framework["secondary"]]
        assert [group["id"] for group in groups] == genres
        for group in groups:
            assert SPECIAL_TOPICS[group["id"]][category_id] in {topic["id"] for topic in group["topics"]}
        references = context["可关联构思与来源"]
        assert (
            next(row for row in references if row["title"] == "未经核实的证词")["设定选用状态"] == "pending"
        )
        if category_id != "core":
            candidate = next(row for row in references if row["title"] == SETTING_SAMPLES[sample]["core"][0])
            assert candidate["origin"] == "本次先前分类的 AI 候选" and candidate["设定选用状态"] == "proposed"

    selected = [{**category, "items": category["items"][:1]} for category in bundle["payload"]["categories"]]
    items = {category["id"]: category["items"][0] for category in selected}
    assert items["relationships"]["links"] == [items["characters"]["id"]]
    items["characters"]["detail"] += " 作者补充：开篇不认识掌柜。"
    response = client.post(
        f"{endpoint}/adopt",
        json={
            **basis(current),
            "idempotency_key": uid(),
            "artifact_id": bundle["id"],
            "categories": selected,
        },
    )
    assert response.status_code == 200, response.text
    confirmed = confirm(client, endpoint, response.json())["confirmation"]
    outline_endpoint = f"/api/branches/{branch}/outline-workspace"
    options = client.get(outline_endpoint + "/setting-options").json()["items"]
    assert {row["id"] for row in options} == {item["id"] for item in items.values()}
    frozen_chapter = chapter_plan(chapter, "查清遗铃去向")
    frozen_chapter["setting_ids"] = [item["id"] for item in items.values()]
    outline_plan = {
        "summary": "追查渡口遗铃",
        "volumes": [
            {
                "id": "ferry",
                "title": "渡口",
                "summary": "找到遗铃线索",
                "units": [
                    {"id": "clue", "title": "查证", "summary": "先听铃再验账", "chapters": [frozen_chapter]}
                ],
            }
        ],
    }
    outline = put_and_confirm(
        client,
        outline_endpoint,
        {"base_revision": 0, "confirmation_id": confirmed["id"], "plan": outline_plan},
    )
    writing = client.post(
        "/api/modules",
        json={
            "action": "writing.advise",
            "novel_id": story["id"],
            "branch_id": branch,
            "chapter_id": chapter,
            "base_revision": domain.chapter(chapter, branch)["revision"],
            "instruction": "按当前作者设定继续。",
            "account": "openai_1",
            "conception_snapshot_id": confirmed["id"],
            "outline_revision_id": outline["confirmed"]["id"],
        },
    )
    assert writing.status_code == 202, writing.text
    bound = {row["id"]: row for row in writing.json()["result"]["outline"]["settings"]}
    assert {identifier: row["detail"] for identifier, row in bound.items()} == {
        item["id"]: item["detail"] for item in items.values()
    }
    assert {row["category_id"] for row in bound.values() if "category_id" in row} == {
        "core",
        "characters",
        "relationships",
    }
    assert bound[items["world"]["id"]]["setting_policy"]["exceptions"] == "掌柜书面许可"


def test_project_revision_preview_tracks_actual_downstream_records_and_rechecks_before_commit(
    client, domain, story
):
    def verify(response, code=200):
        assert response.status_code == code, response.text
        return response.json()

    branch = story["branch_id"]
    endpoint = f"/api/branches/{branch}"
    project_path = f"{path(branch)}/project-brief"
    body = {"base_revision": 0, "idempotency_key": uid(), "idea": IDEA, "brief": BRIEF}
    project = verify(client.put(project_path, json=body))
    impact_path = project_path + "/impact?base_revision=1"
    initial = verify(client.get(impact_path))
    assert initial["targets"] == []
    assert initial["project_id"] == project["id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒找到师姐，归还了铜铃。")
    setting = setting_confirmation(client, branch)
    outline_path = endpoint + "/outline-workspace"
    plan = {
        "summary": "学徒找到师姐。",
        "volumes": [
            {
                "id": "volume",
                "title": "归铃卷",
                "summary": "循着刻痕找到师姐。",
                "units": [
                    {
                        "id": "unit",
                        "title": "归铃单元",
                        "summary": "在门内归还。",
                        "chapters": [chapter_plan(chapter["id"], "归还铜铃")],
                    }
                ],
            }
        ],
    }
    outline = put_and_confirm(
        client, outline_path, {"base_revision": 0, "confirmation_id": setting, "plan": plan}
    )["confirmed"]
    draft = verify(
        client.put(
            outline_path,
            json={
                "base_revision": outline["revision"],
                "confirmation_id": setting,
                "plan": {**plan, "summary": "先修铃再归还。"},
            },
        )
    )["draft"]
    inspection_path = endpoint + "/workflow-inspections"
    inspections = {}
    for scope, scope_id in (("chapter", chapter["id"]), ("volume", "volume"), ("book", None)):
        params = {"scope": scope, **({"scope_id": scope_id} if scope_id else {})}
        preview = verify(client.get(inspection_path, params=params))["preview"]
        inspections[scope] = verify(
            client.post(
                inspection_path,
                json={
                    **params,
                    "basis_sha256": preview["basis_sha256"],
                    "length_targets": {"min_chars": 1, "expected_chapters": 1},
                    "author_reviews": [
                        author(row["check_id"], chapter)
                        for row in preview["items"]
                        if row["status"] == "pending"
                    ],
                },
            ),
            201,
        )["id"]
    completion_path = endpoint + "/book-completion"
    completion = verify(
        client.post(
            completion_path + "/confirm",
            json={
                "basis_sha256": verify(client.get(completion_path))["basis_sha256"],
                "read_note": "已通读当前稿。",
                "author_decisions": {
                    "reading": "verified",
                    "ending": "verified",
                    "characters": "verified",
                    "promises": "verified",
                },
                "ending": {"note": "归还铜铃。"},
                "characters": {"note": "学徒和师姐重逢。"},
                "promises": {"note": "信物有交代。"},
                "intro": "学徒寻找遗铃的失主。",
                "spoiler_scope": "opening",
                "sample_chapter_ids": [chapter["id"]],
                "materials_reviewed": True,
            },
        ),
        201,
    )
    export = f"{completion_path}/{completion['id']}/export"
    original_package = client.get(export).content
    run = verify(
        client.post(
            endpoint + "/seven-stage-runs",
            json={
                "start_from": "inspection",
                "source_id": inspections["book"],
                "client_request_id": uid(),
                "account": "openai_1",
                "budget_limit": 2,
            },
        ),
        202,
    )
    current = verify(client.get(impact_path))
    indexed = {row["id"]: row for row in current["targets"]}
    assert set(indexed) == {
        setting,
        outline["id"],
        draft["id"],
        completion["id"],
        run["id"],
        *inspections.values(),
    }
    assert indexed[setting]["kind"] == "settings"
    assert indexed[outline["id"]]["kind"] == "outline"
    assert indexed[draft["id"]]["kind"] == "outline_draft"
    assert indexed[inspections["volume"]]["title"] == "归铃卷"
    assert indexed[inspections["chapter"]]["title"] == chapter["title"]
    assert indexed[inspections["book"]]["scope"] == "book"
    assert indexed[completion["id"]]["kind"] == "completion"
    assert indexed[run["id"]]["status"] == "queued"
    assert chapter["content"] not in json.dumps(current, ensure_ascii=False)

    update = {
        **body,
        "base_revision": 1,
        "idempotency_key": uid(),
        "brief": {**BRIEF, "ending": "把铜铃交给师姐。"},
    }
    stale = client.put(project_path, json={**update, "impact_sha256": initial["basis_sha256"]})
    assert stale.status_code == 409 and stale.json()["code"] == "project_impact_changed"
    assert verify(client.get(project_path)) == project
    fresh = {**update, "impact_sha256": current["basis_sha256"]}
    second = verify(client.put(project_path, json=fresh))
    assert second["revision"] == 2
    assert verify(client.put(project_path, json=fresh)) == second
    assert client.get(impact_path).status_code == 409
    assert verify(client.get(project_path + "/impact?base_revision=2"))["targets"] == []
    assert domain.chapter(chapter["id"])["content"] == chapter["content"]
    continued = verify(client.get(completion_path))
    assert continued["latest"]["id"] == completion["id"] and not continued["latest"]["current"]
    assert client.get(export).content == original_package


def test_project_brief_edits_confirmation_and_world_handoff(client, domain, settings, story):
    endpoint = path(story["branch_id"])
    project_path = f"{endpoint}/project-brief"
    assert client.get(project_path).json() is None
    current = client.get(endpoint).json()
    queued = client.post(
        project_path + "/generate",
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "account": "openai_1",
            "client_request_id": uid(),
            "idea": IDEA,
        },
    )
    assert queued.status_code == 202, queued.text
    model = Model()
    draft = run(domain, settings, queued.json(), model)
    assert draft["kind"] == "project_brief_draft"
    assert client.get(project_path).json() is None

    authored_ending = "师姐带学徒入门，两人共同修复旧铜铃。"
    first_input = {
        "base_revision": 0,
        "idempotency_key": uid(),
        "candidate_id": draft["id"],
        "idea": IDEA,
        "brief": {**BRIEF, "ending": authored_ending},
    }
    saved = client.put(project_path, json=first_input)
    assert saved.status_code == 200, saved.text
    first = saved.json()
    assert first["revision"] == 1
    assert first["candidate_id"] == draft["id"]
    assert first["brief"]["ending"] == authored_ending
    assert client.put(project_path, json=first_input).json() == first
    assert client.put(project_path, json={**first_input, "idempotency_key": uid()}).status_code == 409

    current = confirm(client, endpoint, client.get(endpoint).json())
    body = {
        "base_revision": current["revision"],
        "base_story_revision": current["story_revision"],
        "account": "openai_1",
        "client_request_id": uid(),
        "category_id": "world",
        "project_confirmation_id": first["id"],
    }
    world = client.post(endpoint + "/generate", json=body)
    assert world.status_code == 202, world.text
    proposed = run(domain, settings, world.json(), model)
    assert proposed["basis"]["project_confirmation_id"] == first["id"]
    assert proposed["basis"]["project_revision"] == 1
    context = model.calls[-1]["上下文"]
    assert context["已确认立项"]["brief"]["ending"] == authored_ending
    assert context["设定框架"]["primary"]["id"] == "xianxia"
    assert context["设定框架"]["secondary"][0]["id"] == "mystery"
    adopted = client.post(
        endpoint + "/adopt",
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "idempotency_key": uid(),
            "artifact_id": proposed["id"],
            "categories": proposed["payload"]["categories"],
        },
    )
    assert adopted.status_code == 200, adopted.text
    hard, soft = proposed["payload"]["categories"][0]["items"]
    assert adopted.json()["project_impacts"] == []
    original = confirm(client, endpoint, adopted.json(), key=uid())

    updated = client.put(
        project_path,
        json={
            **first_input,
            "base_revision": 1,
            "idempotency_key": uid(),
            "candidate_id": None,
            "brief": {**BRIEF, "ending": "师姐继承铜铃，学徒回到旧铺。"},
        },
    )
    assert updated.status_code == 200, updated.text
    second = updated.json()
    assert second["revision"] == 2
    current = client.get(endpoint).json()
    assert current["confirmation"]["current"] is False
    assert current["project"] == second
    assert current["project_impacts"] == [
        {
            "item_id": item["id"],
            "category_id": "world",
            "title": item["title"],
            "source_project_id": first["id"],
            "source_project_revision": 1,
            "reconciled": False,
        }
        for item in (hard, soft)
    ]
    rewritten = {
        **hard,
        "detail": "铜铃只有挂回旧铺门内才会响。",
        "setting_policy": {"strength": "hard", "scope": "旧铺门内", "exceptions": "维修中须将铜铃挂到木架"},
    }
    world = next(category for category in current["categories"] if category["id"] == "world")
    world["items"] = [rewritten, soft]
    edited = client.put(
        endpoint,
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "categories": current["categories"],
        },
    )
    assert edited.status_code == 200, edited.text
    current = edited.json()
    decision = {
        "base_revision": current["revision"],
        "base_story_revision": current["story_revision"],
        "idempotency_key": uid(),
    }
    missing_review = client.post(endpoint + "/confirm", json=decision)
    assert missing_review.status_code == 409, missing_review.text
    assert missing_review.json()["code"] == "conception_project_review"
    partial = client.post(endpoint + "/confirm", json={**decision, "reconciled_project_items": [hard["id"]]})
    assert partial.status_code == 409 and partial.json()["code"] == "conception_project_review"
    author_note = "按新终局改写铜铃的硬限制和例外，查铃次序保留为可调整倾向。"
    retained = client.post(
        endpoint + "/confirm",
        json={**decision, "note": author_note, "reconciled_project_items": [hard["id"], soft["id"]]},
    )
    assert retained.status_code == 200, retained.text
    current = retained.json()
    assert current["confirmation"]["current"] is True
    assert all(item["reconciled"] for item in current["project_impacts"])
    frozen = domain.artifact(current["confirmation"]["id"])["payload"]
    assert frozen["project_confirmation_id"] == second["id"]
    assert frozen["note"] == author_note
    assert frozen["reconciled_project_items"] == [hard["id"], soft["id"]]
    assert frozen["origins"][hard["id"]]["edited"] and not frozen["origins"][soft["id"]]["edited"]
    assert all(
        frozen["origins"][item["id"]]["project_confirmation_id"] == first["id"] for item in (hard, soft)
    )
    assert next(category["items"] for category in frozen["categories"] if category["id"] == "world") == [
        rewritten,
        soft,
    ]
    historical = domain.artifact(original["confirmation"]["id"])["payload"]
    assert next(category["items"] for category in historical["categories"] if category["id"] == "world") == [
        hard,
        soft,
    ]
    assert domain.artifact(first["id"])["payload"]["brief"]["ending"] == authored_ending
    stale = client.post(
        endpoint + "/generate",
        json={
            **body,
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "client_request_id": uid(),
        },
    )
    assert stale.status_code == 409
    stale_adoption = client.post(
        endpoint + "/adopt",
        json={
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "idempotency_key": uid(),
            "artifact_id": proposed["id"],
            "categories": proposed["payload"]["categories"],
        },
    )
    assert stale_adoption.status_code == 409
    fresh = client.post(
        endpoint + "/generate",
        json={
            **body,
            "base_revision": current["revision"],
            "base_story_revision": current["story_revision"],
            "project_confirmation_id": second["id"],
            "client_request_id": uid(),
        },
    )
    assert fresh.status_code == 202, fresh.text
    assert run(domain, settings, fresh.json(), model)["basis"]["project_confirmation_id"] == second["id"]
    assert model.calls[-1]["上下文"]["已确认立项"] == second
