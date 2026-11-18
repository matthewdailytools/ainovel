import asyncio
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from ainovel.db import uid
from ainovel.worker import Worker
from test_conception_workbench import (
    confirm as confirm_settings,
    path as settings_path,
    save as save_settings,
)
from test_outline_workspace import chapter_plan, put_and_confirm
from test_project_brief import BRIEF
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


def test_two_volume_inspection_and_completion_share_one_versioned_manuscript(client, domain, settings, story):
    branch = story["branch_id"]
    route = f"/api/branches/{branch}"
    inspection = route + "/workflow-inspections"
    completion = route + "/book-completion"
    outlines = route + "/outline-workspace"
    records = route + "/story-records"
    chapters = [
        domain.save(
            story["chapter_id"] if number == 1 else domain.create_chapter(branch, f"第{number}章")["id"],
            0,
            content,
        )
        for number, content in enumerate(CONTENT, 1)
    ]
    first, second, third, fourth = chapters
    project = accepted(
        client.put(
            settings_path(branch) + "/project-brief",
            json={
                "base_revision": 0,
                "idempotency_key": "stage-three-project",
                "idea": "学徒凭铜铃刻痕找回师姐，随后归还师门信物。",
                "brief": {**BRIEF, "length": "两卷四章", "ending": "学徒找到师姐，将铜铃归还师门。"},
            },
        )
    )
    setting_route = settings_path(branch)
    state = client.get(setting_route).json()
    next(row for row in state["categories"] if row["id"] == "world")["items"] = [RULE]
    confirmed_setting = confirm_settings(client, setting_route, save_settings(client, setting_route, state))
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
                        "chapters": [chapter_plan(chapter["id"], chapter["content"]) for chapter in scoped],
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
    outline_id = put_and_confirm(
        client, outlines, {"base_revision": 0, "confirmation_id": setting_id, "plan": plan}
    )["confirmed"]["id"]

    class RuleModel:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            request = json.loads(prompt.split("\n只返回")[0])["任务"]
            if request["动作"] == "审查正文":
                assert request["正文"] == fourth["content"]
                assert not request["固定条件"]
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
            assert request["动作"] == "对照单条已确认设定与保存稿"
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
                        {"conception_snapshot_id": setting_id, "setting_item_id": RULE["id"]}
                        if hard_rule
                        else {}
                    ),
                    "account": "deepseek",
                    "client_request_id": uid(),
                },
            ),
            202,
        )
        assert asyncio.run(Worker(settings, RuleModel()).run_once())
        done = domain.task(task["id"])
        assert done["status"] == "succeeded", done["error"]
        return done["result"]["artifact_id"]

    for chapter in chapters:
        chapter_review(chapter)
    missing = read(client, inspection)
    assert missing["latest"] is None
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

    conflict = domain.save(third["id"], third["revision"], "阿芹在门外摇响铜铃，凭第二道刻痕找到师姐。")
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

    repaired = domain.save(
        conflict["id"], conflict["revision"], "阿芹在门外听不见铃声，回到门内摇铃才凭第二道刻痕找到师姐。"
    )
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
    data["basis_sha256"] = client.get(completion).json()["basis_sha256"]
    data["characters"]["sources"] = [source(repaired), source(fourth)]
    preview = accepted(client.post(completion + "/preview", json=data))
    assert all(row["status"] == "green" for row in preview["checks"])
    frozen = accepted(client.post(completion + "/confirm", json=data), 201)
    assert frozen["current"] and frozen["manifest"] == preview["manifest"]
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
