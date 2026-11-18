import asyncio
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from sqlite3 import connect
from zipfile import ZipFile

from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.backups import unpack_verified
from ainovel.budget import Budget
from ainovel.db import uid
from ainovel.worker import Worker
from test_conception_workbench import (
    confirm as confirm_settings,
    path as settings_path,
)
from test_outline_workspace import chapter_plan, put_and_confirm
from test_project_brief import IDEA, Model as ProjectModel, run as run_project
from test_reference_flow import SOURCE, run as run_reference, update as update_reference
from test_seven_stage_run import Model as RelayModel, drain
from test_workflow_inspection import item, read, report, source


CONTENT = [
    "😀学徒阿芹在门内摇响铜铃，看到两道刻痕，并记下师姐留下的旧师门暗号。",
    "阿芹只认出第一道刻痕，在门内听见同样的铃声。",
    "阿芹回到门内摇铃，凭第二道刻痕找到师姐。",
    "师姐在门内接过铃。两道刻痕指向同一人，阿芹选择把铃归还师门。",
]
RULE = {
    "id": "bell",
    "title": "铜铃的响声边界",
    "detail": "铜铃只能在门内被摇响，门外无法听见铃声。",
    "links": [],
    "setting_policy": {"strength": "hard", "scope": "两卷中实际出现的这枚铜铃", "exceptions": "无"},
}
CONFIRMATIONS = {
    "01": ("对照立项中寻人与归铃的承诺", "逐一核对查刻痕、找到师姐和归铃的进展。"),
    "02": ("对照章纲中的目标、阻碍、转折、结果", "分别核对刻痕的出现、辨认、追查与交付。"),
    "03": ("核对阿芹与师姐的身份和关系", "保存稿的阿芹身份保持，师姐在后卷出现。"),
    "04": ("核对门内外转换与证据的前后顺序", "先发现刻痕，后辨认归属，再归还信物。"),
    "05": ("对照门内外的硬规定", "逐处核对实际出现的铃声。"),
    "06": ("对照阿芹在各场景中能够知道的内容", "后卷的师姐身份由刻痕推动披露。"),
    "07": ("核对刻痕的已确认计划和实际回收", "核对本范围对应的埋设、入窗或收束节点。"),
    "08": ("检查保存稿的采用依据与待复检项", "核对当前章稿冻结修订及设定引用。"),
    "09": ("按采用的有效字符与章数口径核对", "对照本范围保存章稿。"),
    "10": ("比较本范围内转折和读者获得的回报", "用刻痕线索和阿芹的选择检查推进。"),
}


def accepted(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def sign_reviews(preview, chapters, *, force_red=None):
    available = {chapter["id"]: chapter for chapter in chapters}
    scope = [available[entry["id"]] for entry in preview["chapters"]]
    reviews = []
    for row in preview["items"]:
        key = row["check_id"]
        if row["status"] != "pending" and key != force_red:
            continue
        basis_note, reason = CONFIRMATIONS[key]
        if row["can_mark_not_applicable"]:
            reviews.append(
                {
                    "check_id": key,
                    "status": "not_applicable",
                    "basis_note": basis_note,
                    "reason": "已核对，这章未包含该伏笔的原文或计划窗口节点。",
                }
            )
        else:
            reviews.append(
                {
                    "check_id": key,
                    "status": "red" if key == force_red else "green",
                    "basis_note": basis_note,
                    "reason": "仍需重新核对两卷转折和回报的衔接。" if key == force_red else reason,
                    "evidence": [source(chapter) for chapter in scope],
                }
            )
    return reviews


def reviewed_scope(client, route, scope, chapters, *, force_red=None):
    preview = read(client, route, **scope)["preview"]
    return report(
        client,
        route,
        preview,
        author_reviews=sign_reviews(preview, chapters, force_red=force_red),
        **(
            {"length_targets": {"expected_chapters": len(preview["chapters"]), "min_chars": 1}}
            if not preview["length_target"]
            else {}
        ),
    )


def statuses(value):
    return {row["check_id"]: row["current_status"] for row in value["items"]}


def assert_blocked(client, endpoint, data, gate):
    preview = accepted(client.post(endpoint + "/preview", json=data))
    assert [row["id"] for row in preview["checks"] if row["status"] != "green"] == [gate]
    rejected = accepted(client.post(endpoint + "/confirm", json=data), 409)
    assert rejected["code"] == "completion_blocked"
    assert client.get(endpoint).json()["latest"] is None


def test_two_volume_inspection_and_completion_share_one_versioned_manuscript(
    client,
    domain,
    settings,
    tmp_path,
):
    story = accepted(client.post("/api/novels", json={"title": "合成验收 · 门内铜铃"}), 201)
    branch = story["branch_id"]
    route = f"/api/branches/{branch}"
    inspection = route + "/workflow-inspections"
    completion = route + "/book-completion"
    outlines = route + "/outline-workspace"
    records = route + "/story-records"
    chapters = [
        accepted(client.post(route + "/chapters", json={"title": f"第{number}章"}), 201)
        for number in range(1, 5)
    ]

    def read_chapter(id):
        return accepted(client.get(f"/api/chapters/{id}"))

    def save_chapter(chapter, text):
        return accepted(
            client.put(
                f"/api/chapters/{chapter['id']}",
                json={"base_revision": chapter["revision"], "content": text},
            )
        )

    for position, expected in enumerate(CONTENT[:3]):
        chapters[position] = save_chapter(chapters[position], expected)
    first, second, third, fourth = chapters
    setting_route = settings_path(branch)
    state = accepted(client.get(setting_route))
    proposal_task = accepted(
        client.post(
            setting_route + "/project-brief/generate",
            json={
                "base_revision": state["revision"],
                "base_story_revision": state["story_revision"],
                "account": "deepseek",
                "client_request_id": uid(),
                "idea": IDEA,
            },
        ),
        202,
    )
    project_model = ProjectModel()
    proposal = run_project(domain, settings, proposal_task, project_model)
    assert accepted(client.get(setting_route + "/project-brief")) is None
    project = accepted(
        client.put(
            setting_route + "/project-brief",
            json={
                "base_revision": 0,
                "idempotency_key": "stage-three-project",
                "candidate_id": proposal["id"],
                "idea": IDEA,
                "brief": {
                    **proposal["payload"]["brief"],
                    "length": "两卷四章",
                    "ending": "学徒找到师姐，将铜铃归还师门。",
                },
            },
        )
    )
    assert project["candidate_id"] == proposal["id"]
    assert project["brief"]["ending"] != proposal["payload"]["brief"]["ending"]

    book = accepted(
        client.post("/api/library/import", files={"file": ("合成拆解.txt", SOURCE.encode())}), 201
    )
    unit = accepted(client.get(f"/api/library/books/{book['id']}/units"))[0]
    template = accepted(
        client.post(
            "/api/reference-sessions",
            json={
                "unit_id": unit["id"],
                "start": 0,
                "end": len(SOURCE.encode("utf-16-le")) // 2,
                "quote": SOURCE,
                "goal": "有限信息下取得局部进展",
                "branch_id": branch,
                "chapter_id": fourth["id"],
            },
        ),
        201,
    )
    analysis, _ = run_reference(client, settings, domain, template["id"], "analyze")
    selected = analysis["payload"]["cards"][0]
    assert selected["evidence"] == SOURCE
    update_reference(client, domain, template, cards=[selected], selected_ids=[selected["id"]])
    pinned = accepted(client.get(outlines + "/reference-options"))[0]
    address = {key: pinned[key] for key in ("session_id", "card_id", "revision", "version_id")}

    state = accepted(client.get(setting_route))
    setting_basis = {"base_revision": state["revision"], "base_story_revision": state["story_revision"]}
    suggested = run_project(
        domain,
        settings,
        accepted(
            client.post(
                setting_route + "/generate",
                json={
                    **setting_basis,
                    "account": "deepseek",
                    "client_request_id": uid(),
                    "category_id": "world",
                    "project_confirmation_id": project["id"],
                },
            ),
            202,
        ),
        project_model,
    )
    assert suggested["basis"]["project_confirmation_id"] == project["id"]
    assert project_model.calls[-1]["上下文"]["已确认立项"]["brief"]["ending"] == project["brief"]["ending"]
    rule = {**RULE, "id": suggested["payload"]["categories"][0]["items"][0]["id"]}
    edited_setting = accepted(
        client.post(
            setting_route + "/adopt",
            json={
                **setting_basis,
                "idempotency_key": uid(),
                "artifact_id": suggested["id"],
                "categories": [{"id": "world", "items": [rule]}],
            },
        )
    )
    confirmed_setting = confirm_settings(client, setting_route, edited_setting)
    setting_id = confirmed_setting["confirmation"]["id"]

    planted = {
        "kind": "promise",
        "title": "铜铃的两道刻痕指向何人",
        "notes": "首卷埋设，末卷辨认铜铃主人。",
        "stages": [{"status": "planned"}, {"status": "planted", "sources": [source(first)]}],
    }
    promise = accepted(
        client.post(
            records,
            json={"base_story_revision": domain.branch(branch)["story_revision"], "record": planted},
        ),
        201,
    )["records"][0]

    def add_record(record):
        graph = accepted(
            client.post(
                records,
                json={"base_story_revision": domain.branch(branch)["story_revision"], "record": record},
            ),
            201,
        )
        return next(row["id"] for row in graph["records"] if row["payload"]["title"] == record["title"])

    apprentice = add_record({"kind": "character", "title": "阿芹", "sources": [source(first)]})
    sister = add_record({"kind": "character", "title": "师姐", "sources": [source(first)]})
    discovery = add_record(
        {
            "kind": "event",
            "title": "门内响铃并初见刻痕",
            "status": "actual",
            "story_order": 1,
            "participants": [apprentice],
            "sources": [source(first)],
        }
    )
    relation = add_record(
        {
            "kind": "relationship",
            "title": "同门师姐妹",
            "from_id": apprentice,
            "to_id": sister,
            "event_id": discovery,
            "truth": "actual",
            "relation": "同出师门。",
            "sources": [source(first)],
        }
    )
    knowledge = add_record(
        {
            "kind": "state",
            "title": "阿芹记下旧师门暗号",
            "category": "knowledge",
            "character_id": apprentice,
            "event_id": discovery,
            "key": "旧师门的暗号",
            "value": "阿芹记下了师姐留下的暗号。",
            "audience": "character",
            "sources": [source(first)],
        }
    )
    plan = {
        "summary": "阿芹沿铜铃刻痕查证师门暗号，找到师姐后将铜铃归还。",
        "volumes": [
            {
                "id": f"volume-{number}",
                "title": title,
                "summary": summary,
                "units": [
                    {
                        "id": f"unit-{number}",
                        "title": title,
                        "summary": summary,
                        "chapters": [
                            {
                                **chapter_plan(chapter["id"], CONTENT[chapter["position"] - 1]),
                                "setting_ids": [rule["id"]],
                            }
                            for chapter in scoped
                        ],
                    }
                ],
            }
            for number, title, summary, scoped in (
                (1, "旧铃", "取得并初认刻痕。", [first, second]),
                (2, "还铃", "凭第二道刻痕寻人并归还。", [third, fourth]),
            )
        ],
        "promises": [
            {
                "promise_id": promise["id"],
                "plant_chapter_id": first["id"],
                "opens_chapter_id": third["id"],
                "closes_chapter_id": fourth["id"],
            }
        ],
    }
    plan["volumes"][1]["units"][0]["chapters"][1]["reference_cards"] = [address]
    prior_outline = put_and_confirm(
        client, outlines, {"base_revision": 0, "confirmation_id": setting_id, "plan": plan}
    )["confirmed"]
    assert prior_outline["setting_basis"]["reference_cards"] == [pinned]

    class WorkshopModel(RelayModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            request = frame["任务"]
            action = request.get("动作") or request["action"]
            if action == "writing.expand_plot":
                basis = frame["上下文"]["采用章纲"]
                assert basis["chapter"]["chapter_id"] == fourth["id"]
                assert basis["reference_cards"][0]["version_id"] == address["version_id"]
                assert SOURCE not in prompt
                result = await super().generate(account, task_id, system, prompt, max_output, **kwargs)
                return {**result, "text": CONTENT[3]}
            if action == "审查正文" and not request["固定条件"]:
                assert request["正文"] == fourth["content"]
                return {
                    "text": json.dumps(
                        {
                            "checks": [],
                            "findings": [],
                            "summary": "比照修订后第三章，末章回收仍对应两道刻痕。",
                        },
                        ensure_ascii=False,
                    ),
                    "usage": {"input": 1, "output": 1},
                    "call_id": uid(),
                }
            if action != "对照单条已确认设定与保存稿":
                return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
            assert request["适用规则"] == RULE["setting_policy"]
            selected = request["可引用原文"][0]
            conflict = "在门外摇响铜铃" in selected["text"]
            return {
                "text": json.dumps(
                    {
                        "result": "fail" if conflict else "pass",
                        "evidence": selected["source_id"],
                        "reason": "门外铃响，违反适用范围。" if conflict else "保存稿将响铃放在门内。",
                    },
                    ensure_ascii=False,
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": uid(),
            }

    model = WorkshopModel(settings=settings)
    candidate_task = accepted(
        client.post(
            "/api/modules",
            json={
                "action": "writing.expand_plot",
                "novel_id": story["id"],
                "branch_id": branch,
                "chapter_id": fourth["id"],
                "base_revision": fourth["revision"],
                "outline_revision_id": prior_outline["id"],
                "instruction": "沿锁定章纲写归铃，正文待作者核对。",
                "fixed": ["获得一条新线索"],
                "account": "deepseek",
                "client_request_id": uid(),
            },
        ),
        202,
    )
    assert asyncio.run(Worker(settings, model).run_once())
    candidate_run = accepted(client.get(f"/api/tasks/{candidate_task['id']}"))
    assert candidate_run["status"] == "succeeded", candidate_run["error"]
    candidate_id = candidate_run["result"]["artifact_id"]
    candidate_url = f"/api/artifacts/{candidate_id}"
    old_candidate = accepted(client.get(candidate_url))
    assert old_candidate["basis"]["outline"]["id"] == prior_outline["id"]
    assert read_chapter(fourth["id"])["revision"] == 0

    plan["volumes"][1]["units"][0]["chapters"][1]["turn"] = "确认两道刻痕都指向师姐后归铃。"
    outline_id = put_and_confirm(
        client,
        outlines,
        {
            "base_revision": prior_outline["revision"],
            "confirmation_id": setting_id,
            "plan": plan,
        },
    )["confirmed"]["id"]
    rejected = accepted(
        client.post(
            candidate_url + "/accept",
            json={
                "idempotency_key": uid(),
                "candidate_revision": old_candidate["candidate_revision"],
            },
        ),
        409,
    )
    assert rejected["code"] == "outline_changed"
    assert accepted(client.get(candidate_url))["basis"] == old_candidate["basis"]
    assert read(client, inspection)["latest"] is None

    runs = route + "/seven-stage-runs"
    request = {
        "start_from": "outline",
        "source_id": outline_id,
        "account": "deepseek",
        "budget_limit": 1,
        "auto_adopt_body": True,
        "client_request_id": uid(),
    }
    relay = accepted(client.post(runs, json=request), 202)
    assert accepted(client.post(runs, json=request), 202)["id"] == relay["id"]
    run_url = f"{runs}/{relay['id']}"
    drain(settings, model)

    def relay_state():
        task = accepted(client.get(f"/api/tasks/{relay['id']}"))
        return task["status"], task["result"]["seven_stage_run"]

    status, progressed = relay_state()
    assert status == "needs_review" and progressed["stage"] == "inspection"
    assert progressed["project_id"] == project["id"] and progressed["setting_id"] == setting_id
    assert progressed["outline_id"] == outline_id and progressed["inspection_candidate_id"]
    assert [row["id"] for row in progressed["completed_chapters"]] == [chapter["id"] for chapter in chapters]
    assert all(row["existing"] for row in progressed["completed_chapters"][:3])
    assert progressed["completed_chapters"][3]["artifact_id"] != candidate_id
    assert set(progressed["volume_snapshot_ids"]) == {"volume-1", "volume-2"}
    fourth = chapters[3] = read_chapter(fourth["id"])
    assert fourth["revision"] == 1 and fourth["content"] == CONTENT[3]

    def chapter_review(chapter, *, hard_rule=True):
        task = accepted(
            client.post(
                "/api/modules",
                json={
                    "action": "review.chapter",
                    "novel_id": story["id"],
                    "branch_id": branch,
                    "chapter_id": chapter["id"],
                    "base_revision": chapter["revision"],
                    "instruction": (
                        "核对铜铃响声的硬规则。" if hard_rule else "复查改动后第三章如何承接末章伏笔回收。"
                    ),
                    **(
                        {"conception_snapshot_id": setting_id, "setting_item_id": rule["id"]}
                        if hard_rule
                        else {}
                    ),
                    "account": "deepseek",
                    "client_request_id": uid(),
                },
            ),
            202,
        )
        assert asyncio.run(Worker(settings, model).run_once())
        done = domain.task(task["id"])
        assert done["status"] == "succeeded", done["error"]
        return done["result"]["artifact_id"]

    for chapter in chapters:
        chapter_review(chapter)
    missing = read(client, inspection)
    assert missing["latest"]["id"] == progressed["inspection_id"]
    assert item(missing["preview"], "01")["status"] == "pending"
    overdue = report(client, inspection, missing["preview"])
    assert item(overdue, "07")["current_status"] == "yellow"
    assert item(overdue, "07")["basis"]["windows"][0]["due"] == "due"
    assert item(overdue, "07")["basis"]["records"][0]["stages"][1]["sources"] == [source(first)]

    resolved_graph = accepted(
        client.put(
            f"{records}/{promise['id']}",
            json={
                "base_story_revision": domain.branch(branch)["story_revision"],
                "base_revision": promise["revision"],
                "record": {
                    **planted,
                    "stages": [*planted["stages"], {"status": "resolved", "sources": [source(fourth)]}],
                },
            },
        )
    )
    resolved = next(row for row in resolved_graph["records"] if row["id"] == promise["id"])
    scopes = {
        "book": {},
        "first_volume": {"scope": "volume", "scope_id": "volume-1"},
        "second_volume": {"scope": "volume", "scope_id": "volume-2"},
        "first": {"scope": "chapter", "scope_id": first["id"]},
        "second": {"scope": "chapter", "scope_id": second["id"]},
        "third": {"scope": "chapter", "scope_id": third["id"]},
    }
    baseline = {key: reviewed_scope(client, inspection, scope, chapters) for key, scope in scopes.items()}
    for label, document in baseline.items():
        assert document["current"], document
        expected = {"07": "not_applicable"} if label == "second" else {}
        assert statuses(document) == {key: expected.get(key, "green") for key in CONFIRMATIONS}
    book = baseline["book"]
    assert [entry["id"] for entry in book["chapters"]] == [chapter["id"] for chapter in chapters]
    assert [row["id"] for row in baseline["first_volume"]["chapters"]] == [first["id"], second["id"]]
    assert item(book, "01")["basis"]["project"]["id"] == project["id"]
    assert item(book, "02")["basis"]["outline_id"] == outline_id
    assert relation in {row["id"] for row in item(book, "03")["basis"]["records"]}
    assert [row["id"] for row in item(book, "04")["basis"]["events"]] == [discovery]
    assert item(book, "05")["basis"]["rules"][0]["setting_policy"]["strength"] == "hard"
    assert [row["id"] for row in item(book, "06")["basis"]["knowledge"]] == [knowledge]
    assert item(book, "07")["basis"]["records"][0]["stages"][2]["sources"] == [source(fourth)]
    assert item(book, "10")["author_review"]["evidence"] == [source(chapter) for chapter in chapters]
    historical_overdue = read(client, f"{inspection}/{overdue['id']}")
    assert item(historical_overdue, "07")["status"] == "yellow"
    assert item(historical_overdue, "07")["current_status"] == "outdated"

    data = {
        "basis_sha256": client.get(completion).json()["basis_sha256"],
        "read_note": "作者按目录通读两卷四章。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": "铜铃归还兑现终局。", "sources": [source(fourth)]},
        "characters": {"note": "阿芹找到师姐并作出归还选择。", "sources": [source(third), source(fourth)]},
        "promises": {"note": "两道刻痕的信物来历已回收。", "sources": [source(first), source(fourth)]},
        "intro": "阿芹从一枚旧铃的刻痕追查师门信物。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [first["id"], second["id"]],
        "materials_reviewed": True,
    }
    for field, replacement, gate in (
        ("read_note", "", "manuscript"),
        ("ending", {"note": ""}, "ending"),
        ("characters", {"note": ""}, "characters"),
        ("promises", {"note": ""}, "promises"),
        ("sample_chapter_ids", [second["id"]], "delivery"),
    ):
        assert_blocked(client, completion, {**data, field: replacement}, gate)
    red_review = reviewed_scope(client, inspection, {}, chapters, force_red="10")
    assert statuses(red_review)["10"] == "red"
    assert_blocked(
        client,
        completion,
        {**data, "basis_sha256": client.get(completion).json()["basis_sha256"]},
        "inspection",
    )

    conflict = save_chapter(third, "阿芹在门外摇响铜铃，凭第二道刻痕找到师姐。")
    chapters[2] = conflict
    fail_id = chapter_review(conflict)
    unrelated = read(client, f"{inspection}/{baseline['second']['id']}")
    assert unrelated["current"] and statuses(unrelated) == statuses(baseline["second"])
    assert domain.chapter(fourth["id"])["stale"]
    for label in ("first", "first_volume"):
        previous = read(client, f"{inspection}/{baseline[label]['id']}")
        assert not previous["current"]
        assert {key for key, status in statuses(previous).items() if status == "outdated"} == {"07", "08"}
        assert item(previous, "07")["basis"]["records"][0]["stages"][2]["sources"] == [source(fourth)]
    for label in ("third", "second_volume", "book"):
        previous = read(client, f"{inspection}/{baseline[label]['id']}")
        assert not previous["current"]
        assert item(previous, "02")["status"] == "green"
        assert item(previous, "02")["current_status"] == "outdated"
        assert source(third) in item(previous, "02")["author_review"]["evidence"]
        next_report = reviewed_scope(client, inspection, scopes[label], chapters)
        conflict_item = item(next_report, "05")
        assert conflict_item["current_status"] == "red"
        assert conflict_item["basis"]["rules"][0]["detail"] == RULE["detail"]
        evidence = next(row for row in conflict_item["basis"]["reports"] if row["report_id"] == fail_id)
        assert evidence["rule"]["confirmation_id"] == setting_id
        assert evidence["revision"] == conflict["revision"]
        assert evidence["evidence"]["text"] == conflict["content"]
    preview = read(client, inspection)["preview"]
    repair_attempt = report(
        client,
        inspection,
        preview,
        author_reviews=[
            {
                "check_id": "05",
                "status": "green",
                "basis_note": "作者复查铜铃规则。",
                "reason": "尝试直接覆盖已确定的硬冲突。",
                "evidence": [source(conflict)],
            }
        ],
    )
    assert item(repair_attempt, "05")["current_status"] == "red"
    assert accepted(client.post(completion + "/confirm", json=data), 409)["code"] == "completion_changed"

    repaired = save_chapter(conflict, "阿芹在门外听不见铃声，回到门内摇铃才凭第二道刻痕找到师姐。")
    chapters[2] = repaired
    final_rule = chapter_review(repaired)
    accepted(
        client.post(
            f"/api/artifacts/{chapter_review(fourth, hard_rule=False)}/accept",
            json={"idempotency_key": "stage-three-fourth-recheck"},
        )
    )
    assert not domain.chapter(fourth["id"])["stale"]
    for label in ("first", "second", "first_volume"):
        assert read(client, f"{inspection}/{baseline[label]['id']}")["current"]
    final = {
        key: reviewed_scope(client, inspection, scopes[key], chapters)
        for key in ("third", "second_volume", "book")
    }
    for label, document in final.items():
        assert document["current"]
        assert set(statuses(document).values()) == {"green"}
        assert item(document, "05")["basis"]["reports"]
        assert final_rule in {row["report_id"] for row in item(document, "05")["basis"]["reports"]}
        assert source(repaired) in item(document, "02")["author_review"]["evidence"]
        assert final[label]["id"] != baseline[label]["id"]
    accepted(client.post(run_url + "/resume"))
    drain(settings, model)
    status, pending = relay_state()
    assert status == "needs_review" and pending["stage"] == "completion"
    assert pending["inspection_id"] == final["book"]["id"] and not pending.get("completion_id")
    preparation = accepted(client.get(f"/api/artifacts/{pending['completion_preparation_id']}"))
    assert preparation["payload"]["sample_chapter_ids"] == [first["id"]]
    assert preparation["kind"] == "completion_preparation_candidate"
    data["basis_sha256"] = client.get(completion).json()["basis_sha256"]
    data["characters"]["sources"] = [source(repaired), source(fourth)]
    preview = accepted(client.post(completion + "/preview", json=data))
    assert all(row["status"] == "green" for row in preview["checks"])
    frozen = accepted(client.post(completion + "/confirm", json=data), 201)
    assert frozen["current"] and frozen["manifest"] == preview["manifest"]
    accepted(client.post(run_url + "/resume"))
    drain(settings, model)
    status, settled = relay_state()
    assert status == "succeeded" and settled["completion_id"] == frozen["id"]
    assert settled["children"] == pending["children"]
    assert model.calls.count("writing.expand_plot") == 2
    assert model.calls.count("准备完本收束核对建议") == 1
    detail = accepted(client.get(f"{completion}/history/{frozen['id']}"))
    assert detail["current"] and detail["review"]["characters"]["sources"] == [
        source(repaired),
        source(fourth),
    ]
    export = client.get(f"{completion}/{frozen['id']}/export")
    assert export.status_code == 200, export.text
    with ZipFile(BytesIO(export.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest == frozen["manifest"]
        assert manifest["project_id"] == project["id"]
        assert manifest["setting_id"] == setting_id
        assert manifest["outline_id"] == outline_id
        assert manifest["inspection_id"] == final["book"]["id"]
        assert manifest["length_target"] == final["book"]["length_target"]
        assert manifest["promises"] == [{"id": promise["id"], "revision": resolved["revision"]}]
        assert manifest["sample_chapter_ids"] == [first["id"], second["id"]]
        assert manifest["chapters"] == [
            {
                **{key: chapter[key] for key in ("id", "title", "position", "revision")},
                "sha256": sha256(chapter["content"].encode()).hexdigest(),
            }
            for chapter in chapters
        ]
        assert set(manifest["files"]) == {"正文.md", "设定册.md", "简介.md", "试读.md"}
        assert all(
            sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest["files"].items()
        )
        body = archive.read("正文.md").decode()
        assert [body.index(chapter["content"]) for chapter in chapters] == sorted(
            body.index(chapter["content"]) for chapter in chapters
        )
        assert conflict["content"] not in body and third["content"] not in body
        assert RULE["detail"] in archive.read("设定册.md").decode()
        assert data["intro"] in archive.read("简介.md").decode()
        sample = archive.read("试读.md").decode()
        assert first["content"] in sample and second["content"] in sample
        assert not any(chapter["content"] in sample for chapter in [repaired, fourth])
        delivery_payload = {name: archive.read(name) for name in archive.namelist()}

    card_reference = item(final["book"], "08")["basis"]["card_references"][0]
    assert (
        card_reference["chapter_id"] == fourth["id"] and card_reference["version_id"] == address["version_id"]
    )
    assert card_reference["source"]["title"] == pinned["source_title"]
    assert SOURCE not in json.dumps(card_reference, ensure_ascii=False)
    query = "SELECT id,task_id,account,charged,status FROM calls ORDER BY id"
    with Budget(settings).db() as ledger:
        costs = [tuple(row) for row in ledger.execute(query)]
    assert costs and all(row[2] == "deepseek" and row[3] > 0 for row in costs)

    snapshots = route + "/work-snapshots"
    point = accepted(
        client.post(
            snapshots,
            json={"scope": "volume", "scope_id": "volume-2", "label": "还铃卷 · 已核对后"},
        ),
        201,
    )
    assert point["scoped_chapter_ids"] == [third["id"], fourth["id"]]
    assert point["reports"] == {"workflow_inspection": final["book"]["id"], "book_completion": frozen["id"]}
    changed = save_chapter(repaired, "阿芹把铜铃暂留在门边，先去找师姐。")
    accepted(
        client.put(
            f"/api/chapters/{third['id']}",
            json={"base_revision": repaired["revision"], "content": "另一个窗口误用旧修订提交的稿件。"},
        ),
        409,
    )
    assert read_chapter(changed["id"])["content"] == changed["content"]
    preview = accepted(client.get(f"{snapshots}/{point['id']}/preview"))
    refusal = accepted(
        client.post(
            f"{snapshots}/{point['id']}/restore",
            json={
                "mode": "in_place",
                "basis_sha256": preview["basis_sha256"],
                "idempotency_key": uid(),
            },
        ),
        409,
    )
    assert refusal["code"] == "snapshot_restore_scope" and "伏笔窗口跨越本卷边界" in refusal["message"]
    assert read_chapter(changed["id"])["revision"] == changed["revision"]
    for entry in (first, second):
        assert read_chapter(entry["id"])["revision"] == entry["revision"]
    unchanged_local = read(client, f"{inspection}/{baseline['second']['id']}")
    assert unchanged_local["current"]
    assert not accepted(client.get(f"{completion}/history/{frozen['id']}"))["current"]

    recovery_route = f"{snapshots}/{point['id']}/restore"
    recovery_request = {
        "mode": "new_branch",
        "branch_name": "还铃卷核对复本",
        "basis_sha256": preview["basis_sha256"],
        "idempotency_key": uid(),
    }
    fork = accepted(client.post(recovery_route, json=recovery_request), 201)
    other = f"/api/branches/{fork['branch_id']}"
    assert read_chapter(changed["id"])["content"] == changed["content"]
    forked_outline = accepted(client.get(other + "/outline-workspace"))
    assert not forked_outline["stale"]
    assert forked_outline["confirmed"]["setting_basis"]["restored_outline_id"] == outline_id
    assert forked_outline["confirmed"]["setting_basis"]["confirmation_id"] == fork["setting_id"] != setting_id
    mapped_card = fork["reference_sources"]["version_ids"][address["version_id"]]
    verified_card = forked_outline["confirmed"]["setting_basis"]["reference_cards"][0]
    assert verified_card == {**pinned, "version_id": mapped_card}
    assert mapped_card != address["version_id"]
    window = forked_outline["promises"][0]
    assert window["state"] == "resolved"
    assert window["plant_chapter_id"] == fork["chapters"][first["id"]]["id"]
    assert window["closes_chapter_id"] == fork["chapters"][fourth["id"]]["id"]
    assert window["stages"][1]["sources"][0]["chapter_id"] == window["plant_chapter_id"]
    assert window["stages"][2]["sources"][0]["chapter_id"] == window["closes_chapter_id"]
    for original in chapters:
        restored = read_chapter(fork["chapters"][original["id"]]["id"])
        assert restored["content"] == original["content"]
        assert sha256(restored["content"].encode()).hexdigest() == next(
            row["sha256"] for row in point["chapters"] if row["id"] == original["id"]
        )
    assert accepted(client.get(other + "/book-completion"))["latest"] is None
    assert client.get(other + f"/book-completion/{frozen['id']}/export").status_code == 404
    fork_package = accepted(client.get(other + "/manuscript-package"))
    assert fork_package["outline_history"]["adopted_revision_id"] == forked_outline["confirmed"]["id"]
    assert not fork_package["reference_history"].get("excerpts")
    assert any(row["id"] == mapped_card for row in fork_package["reference_history"]["versions"])
    assert SOURCE not in json.dumps(fork_package, ensure_ascii=False)
    export_path = f"{completion}/{frozen['id']}/export"

    def check_delivery(api):
        response = api.get(export_path)
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as historical:
            assert {name: historical.read(name) for name in historical.namelist()} == delivery_payload

    check_delivery(client)
    with Budget(settings).db() as ledger:
        assert [tuple(row) for row in ledger.execute(query)] == costs
    backup = accepted(client.post("/api/backups"))
    backup_path = Path(backup["path"])
    accepted(client.post(f"/api/backups/{backup['id']}/verify"))
    with ZipFile(backup_path) as archive:
        assert "usage-evidence.sqlite" in archive.namelist()
        assert all(b"test-not-real" not in archive.read(name) for name in archive.namelist())
    isolated = tmp_path / "closed-volume-workbench"
    unpack_verified(backup_path, isolated)
    with closing(connect(isolated / "usage-evidence.sqlite")) as ledger:
        assert list(ledger.execute(query)) == costs
    with TestClient(create_app(replace(settings, data_dir=isolated))) as recovered:
        recovered.headers["X-Ainovel-CSRF"] = accepted(recovered.get("/api/session"))["csrf"]
        assert accepted(recovered.post(recovery_route, json=recovery_request), 201) == fork
        assert accepted(recovered.get(f"{snapshots}/{point['id']}/preview"))["snapshot"] == point
        assert accepted(recovered.get(f"{route}/work-snapshots"))[0]["id"] == fork["recovery_snapshot_id"]
        assert accepted(recovered.get(candidate_url))["basis"] == old_candidate["basis"]
        assert accepted(recovered.get(f"{inspection}/{final['book']['id']}"))["current"] is False
        assert accepted(recovered.get(other + "/outline-workspace"))["promises"] == forked_outline["promises"]
        check_delivery(recovered)
