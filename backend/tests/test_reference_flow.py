import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from ainovel.db import DomainError, dumps
from ainovel.library import Library
from ainovel.reference import overlap_report, session
from ainovel.worker import Worker

SOURCE = "😀渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。"
TEXT = "她指着闸门外露的螺栓，请管事翻出维修记录。记录未能取到，她没有动手修门，只把测得的磨损方向记在纸上。"


@pytest.fixture
def reference(client, domain, settings, story):
    library = Library(domain.db, settings.data_dir)
    book = library.import_file("自拟参考.txt", SOURCE.encode())
    unit = domain.db.one("SELECT * FROM source_units WHERE book_id=?", (book["id"],))
    result = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": unit["id"],
            "start": 0,
            "end": len(SOURCE.encode("utf-16-le")) // 2,
            "quote": SOURCE,
            "goal": "有限信息下取得局部进展",
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


class Model:
    def __init__(self):
        self.requests = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        self.requests.append(deepcopy(value))
        task = value["任务"]
        action = task.get("动作")
        if action == "拆解所选原文写法":
            result = {
                "cards": [
                    {
                        "id": "",
                        "observation": "交涉只取得部分信息",
                        "interpretation": "可能留下持续期待",
                        "technique": "让局部进展改变下一步行动",
                        "conditions": "信息有限且角色能采取行动",
                        "exceptions": "不要求每次付出相同代价",
                        "evidence": "REF0001",
                    }
                ],
                "coverage_note": "只覆盖选段",
            }
        elif action == "将参考写法迁移到本书":
            result = {
                "text": "检查闸门外露磨损，因责任争议拿不到记录，保留测量后另找见证者。",
                "rule_applications": ["用可见证据支持下一步选择"],
                "conflicts": [],
            }
        elif action == "按确认参考应用方案原创试写":
            return {"text": TEXT, "usage": {"input": 1, "output": 1}, "call_id": "offline"}
        elif action == "参考写法对照检查":
            result = {
                "rules": [
                    {
                        "rule_id": c["id"],
                        "result": "pass",
                        "evidence": "REFCHECK0001",
                        "reason": "留下可行动的测量结果",
                    }
                    for c in task["选定规则"]
                ],
                "concrete_plot_similarities": [],
                "summary": "原文用于写法比较，不作原创保证",
            }
        elif action == "审查正文":
            result = {
                "checks": [
                    {"condition": c, "result": "pass", "evidence": "C001P0001"} for c in task["固定条件"]
                ],
                "findings": [],
                "summary": "保留固定结果",
            }
        else:
            raise AssertionError(action)
        return {"text": dumps(result), "usage": {"input": 1, "output": 1}, "call_id": "offline"}


def run(client, settings, domain, identifier, action, model=None, **extra):
    row = session(domain, identifier)
    response = client.post(
        f"/api/reference-sessions/{identifier}/actions",
        json={
            "base_revision": row["revision"],
            "action": action,
            "account": "deepseek",
            "client_request_id": uuid4().hex,
            **extra,
        },
    )
    assert response.status_code == 202, response.text
    worker = Worker(settings, model or Model())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    asyncio.run(worker.module())
    task = domain.task(response.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    return domain.artifact(task["result"]["artifact_id"]), worker


def update(client, domain, row, **changes):
    p = session(domain, row["id"])["payload"]
    values = {
        k: p[k]
        for k in (
            "goal",
            "cards",
            "selected_ids",
            "plot",
            "fixed",
            "application_plan",
            "branch_id",
            "chapter_id",
        )
    }
    values.update(changes)
    result = client.patch(
        f"/api/reference-sessions/{row['id']}",
        json={"base_revision": session(domain, row["id"])["revision"], **values},
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_full_reference_chain_preserves_source_and_manuscript(client, domain, settings, story, reference):
    branch = domain.branch(story["branch_id"])
    adopted = client.post(
        f"/api/branches/{branch['id']}/definitions",
        json={
            "kind": "intent",
            "text": "本书禁止发生战斗",
            "intent_contract": {"prohibitions": ["不战斗"]},
            "base_story_revision": branch["story_revision"],
        },
    )
    assert adopted.status_code == 201, adopted.text
    original = domain.chapter(story["chapter_id"])
    analysis, analysis_worker = run(client, settings, domain, reference["id"], "analyze")
    assert analysis["payload"]["cards"][0]["evidence"] == SOURCE
    assert "chapter" not in analysis_worker.client.requests[0]["上下文"]
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot="检查外露结构，但拿不到记录、不修门",
        fixed=["不修门"],
    )
    plan, plan_worker = run(client, settings, domain, row["id"], "plan")
    row = update(client, domain, row, application_plan=plan["payload"]["text"])
    candidate, worker = run(client, settings, domain, row["id"], "draft")
    assert candidate["kind"] == "chapter_draft" and candidate["payload"]["text"] == TEXT
    assert candidate["payload"]["reference_comparison"]["rules"][0]["result"] == "pass"
    assert domain.chapter(story["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM facts")
    comparison, check_worker = run(client, settings, domain, row["id"], "check", candidate_id=candidate["id"])
    assert comparison["payload"]["rules"][0]["result"] == "pass"
    for instance in (analysis_worker, plan_worker, worker, check_worker):
        assert instance.client.requests
        for sent in instance.client.requests:
            assert sent["上下文"]["author_contract"]["intent_contract"]["prohibitions"] == ["不战斗"]
    generation = worker.client.requests[0]
    assert SOURCE not in dumps(generation)
    assert generation["上下文"]["genre_profile"]["catalog_version"]
    result = client.post(
        f"/api/artifacts/{candidate['id']}/accept",
        json={"idempotency_key": uuid4().hex, "candidate_revision": 0},
    )
    assert result.status_code == 200, result.text
    assert domain.chapter(story["chapter_id"])["content"] == TEXT
    assert (
        domain.db.one(
            "SELECT content FROM revisions WHERE chapter_id=? AND revision=0", (story["chapter_id"],)
        )["content"]
        == ""
    )


def test_reference_edit_invalidates_queued_work_before_spending(client, domain, settings, reference):
    response = client.post(
        f"/api/reference-sessions/{reference['id']}/actions",
        json={
            "base_revision": 0,
            "action": "analyze",
            "account": "deepseek",
            "client_request_id": uuid4().hex,
        },
    )
    assert response.status_code == 202
    update(client, domain, reference, goal="改为研究对白")
    worker = Worker(settings, Model())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError, match="参考会话已改变"):
        asyncio.run(worker.module())
    assert not worker.client.requests


def test_source_selection_utf16_and_conflicting_edits_are_rejected(client, domain, reference):
    source = reference["payload"]["source"]
    result = client.post(
        "/api/reference-sessions",
        json={"unit_id": source["unit_id"], "start": 1, "end": 3, "quote": "wrong", "goal": "研究"},
    )
    assert result.status_code >= 400
    row = update(client, domain, reference, goal="先研究信息差")
    result = client.patch(
        f"/api/reference-sessions/{row['id']}",
        json={"base_revision": 0, "goal": "旧窗口", "cards": [], "selected_ids": []},
    )
    assert result.status_code == 409


def test_standalone_library_analysis_does_not_require_author_novel(client, domain, settings, reference):
    row = update(client, domain, reference, branch_id=None, chapter_id=None)
    _, worker = run(client, settings, domain, row["id"], "analyze")
    assert worker.task["branch_id"] != reference["payload"]["branch_id"]
    hidden = domain.db.one("SELECT novel_id FROM reference_workspace")
    assert hidden["novel_id"] not in [n["id"] for n in client.get("/api/novels").json()]


def test_generic_module_cannot_bypass_reference_session_route(client, story):
    result = client.post(
        "/api/modules",
        json={
            "action": "reference.analyze",
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "instruction": "绕过",
            "account": "deepseek",
        },
    )
    assert result.status_code == 400


def test_exact_overlap_reports_utf16_and_explicit_scope():
    same = "连续重复的具体文字需要双方原文定位"
    report = overlap_report("😀" + same, "甲" + same)
    assert report["hits"][0]["source_start"] == 2
    assert report["hits"][0]["candidate_start"] == 1
    assert "仅比较本次选段" in report["coverage"]


def test_session_task_history_is_scoped_and_survives_reopen(client, domain, settings, reference):
    artifact, worker = run(client, settings, domain, reference["id"], "analyze")
    tasks = client.get(f"/api/reference-sessions/{reference['id']}/tasks").json()
    assert [t["id"] for t in tasks] == [worker.task["id"]]
    assert tasks[0]["status"] == "succeeded"
    assert tasks[0]["result"]["artifact_id"] == artifact["id"]
    source = reference["payload"]["source"]
    other = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": source["unit_id"],
            "start": source["start"],
            "end": source["end"],
            "quote": source["quote"],
            "goal": "另一会话",
        },
    ).json()
    assert client.get(f"/api/reference-sessions/{other['id']}/tasks").json() == []
    row = update(client, domain, reference, goal="修改学习目标")
    assert row["payload"]["history"][0]["results"]["analyze"] == artifact["id"]
    assert client.get(f"/api/artifacts/{artifact['id']}").status_code == 200


def test_reference_backup_restores_snapshot_and_history(domain, settings, reference, client, tmp_path):
    from ainovel.backups import create_backup, unpack_verified, verify_backup
    from ainovel.db import Database
    from ainovel.domain import Domain

    before = update(client, domain, reference, goal="备份中的学习目标")
    backup = create_backup(settings, domain.db)
    path = settings.data_dir / "backups" / (backup["id"] + ".zip")
    assert verify_backup(path)["integrity"] == "ok"
    update(client, domain, before, goal="备份后的临时修改")
    target = tmp_path / "isolated-restore"
    target.mkdir()
    unpack_verified(path, target)
    restored_domain = Domain(Database(target / "manuscripts.sqlite"))
    restored = session(restored_domain, reference["id"])
    assert restored == before
    assert restored["payload"]["source"]["quote"] == SOURCE
    assert (
        restored_domain.db.one("SELECT name FROM extension_migrations WHERE name='reference-sessions/1'")[
            "name"
        ]
        == "reference-sessions/1"
    )
