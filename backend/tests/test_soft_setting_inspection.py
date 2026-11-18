import asyncio
import json

import pytest

from ainovel.db import Database, DomainError
from ainovel.domain import Domain
from ainovel.manuscript_exchange import ManuscriptPackage, export_manuscript, import_manuscript
from ainovel.worker import Worker
from test_conception_workbench import confirm, save
from test_outline_workspace import chapter_plan, put_and_confirm
from test_workflow_inspection import author, item, read, report


def test_soft_setting_deviations_require_a_source_backed_author_decision_or_a_new_review(
    client, domain, settings, story, tmp_path
):
    chapter = domain.save(story["chapter_id"], 0, "😀铜铃在门外响了。\n学徒未损寿元。\n铜铃已经碎裂。")
    branch = story["branch_id"]
    root = f"/api/branches/{branch}"
    conception = root + "/conception"
    inspection = root + "/workflow-inspections"
    world = read(client, conception)
    rules = {
        "sound": ("铃声倾向", "铜铃通常在门内响。", "soft"),
        "cost": ("代价倾向", "学徒通常消耗寿元。", "soft"),
        "material": ("铜铃材质", "铜铃不可打碎。", "hard"),
    }
    next(row for row in world["categories"] if row["id"] == "world")["items"] = [
        {
            "id": key,
            "title": title,
            "detail": detail,
            "setting_policy": {"strength": strength},
            "adoption_state": "adopted",
        }
        for key, (title, detail, strength) in rules.items()
    ]
    authority = confirm(client, conception, save(client, conception, world))["confirmation"]["id"]
    put_and_confirm(
        client,
        root + "/outline-workspace",
        {
            "base_revision": 0,
            "confirmation_id": authority,
            "plan": {
                "summary": "学徒核对铜铃规则。",
                "volumes": [
                    {
                        "id": "v",
                        "title": "拾铃",
                        "summary": "铜铃有异。",
                        "units": [
                            {
                                "id": "u",
                                "title": "试铃",
                                "summary": "核对三条设定。",
                                "chapters": [
                                    {**chapter_plan(chapter["id"], "试铃"), "setting_ids": list(rules)}
                                ],
                            }
                        ],
                    }
                ],
            },
        },
    )

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])["任务"]
            assert frame["适用规则"]["strength"] == expected_strength
            source = frame["可引用原文"][line]
            return {
                "text": json.dumps(
                    {"result": verdict, "reason": "正文对账。", "evidence": source["source_id"]}
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": f"soft-setting-{task_id}",
            }

    expected_strength, line, verdict = "", 0, ""

    def check_rule(key, result, line_index):
        nonlocal expected_strength, line, verdict
        expected_strength, line, verdict = rules[key][2], line_index, result
        queued = client.post(
            "/api/modules",
            json={
                "novel_id": story["id"],
                "branch_id": branch,
                "chapter_id": chapter["id"],
                "base_revision": chapter["revision"],
                "account": "deepseek",
                "action": "review.chapter",
                "instruction": "据保存稿核对设定。",
                "conception_snapshot_id": authority,
                "setting_item_id": key,
            },
        )
        assert queued.status_code == 202, queued.text
        assert asyncio.run(Worker(settings, Model()).run_once())
        task = domain.task(queued.json()["id"])
        assert task["status"] == "succeeded", task["error"]
        result = read(client, f"/api/artifacts/{task['result']['artifact_id']}")
        excerpt = result["payload"]["evidence"]
        return result["id"], {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": excerpt["start"],
            "end": excerpt["end"],
            "quote": excerpt["text"],
        }

    def preview():
        return read(client, inspection)["preview"]

    def completion_consistency():
        return next(
            row["status"]
            for row in read(client, root + "/book-completion")["checks"]
            if row["id"] == "inspection"
        )

    pending = preview()
    assert item(pending, "05")["status"] == "pending"
    first_id, first_source = check_rule("sound", "fail", 0)
    assert item(preview(), "05")["status"] == "yellow"
    second_id, second_source = check_rule("cost", "fail", 1)
    current = preview()
    assert item(current, "05")["status"] == "yellow"
    yellow = report(client, inspection, current)
    assert item(yellow, "05")["current_status"] == "yellow"
    assert completion_consistency() == "pending"
    decision = {
        **author("05", chapter),
        "reason": "本段铃声和寿元处理保留为这两条软设定的例外，其他设定已核对。",
        "evidence": [first_source, second_source],
        "soft_deviation_report_ids": [second_id, first_id],
    }
    for invalid in [
        {**decision, "soft_deviation_report_ids": []},
        {**decision, "soft_deviation_report_ids": [first_id]},
        {**decision, "soft_deviation_report_ids": [first_id, "wrong-report"]},
        {**decision, "evidence": [first_source]},
    ]:
        rejected = client.post(
            inspection,
            json={"basis_sha256": current["basis_sha256"], "author_reviews": [invalid]},
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["code"] == "inspection_soft_deviation"
    wrong_check = client.post(
        inspection,
        json={"basis_sha256": current["basis_sha256"], "author_reviews": [{**decision, "check_id": "07"}]},
    )
    assert wrong_check.status_code == 422

    signed = report(
        client,
        inspection,
        current,
        author_reviews=[
            decision,
            *(author(row["check_id"], chapter) for row in current["items"] if row["status"] == "pending"),
        ],
        length_targets={"min_chars": 1},
    )
    assert item(signed, "05")["current_status"] == "green"
    assert item(signed, "05")["author_review"]["soft_deviation_report_ids"] == [second_id, first_id]
    assert completion_consistency() == "green"
    assert item(read(client, f"{inspection}/{yellow['id']}"), "05")["status"] == "yellow"
    package = export_manuscript(domain, branch)
    tampered = package.model_dump()
    archived_green = next(
        row for row in tampered["workflow_history"]["inspections"] if row["id"] == signed["id"]
    )
    archived_green["report"]["items"][4]["author_review"]["soft_deviation_report_ids"] = [first_id]
    damaged = ManuscriptPackage.model_validate(tampered)
    rejected_db = Domain(Database(tmp_path / "rejected-soft-audit.sqlite"))
    with pytest.raises(DomainError, match="软设定裁定无效"):
        import_manuscript(rejected_db, damaged)
    assert not rejected_db.db.all("SELECT id FROM novels")
    restore = Domain(Database(tmp_path / "restored-soft-audit.sqlite"))
    import_manuscript(restore, package)
    restored = export_manuscript(restore, branch)
    previous = next(row for row in restored.workflow_history.inspections if row.id == signed["id"])
    assert previous.report.items[4].author_review.soft_deviation_report_ids == [second_id, first_id]

    check_rule("material", "fail", 2)
    current = preview()
    assert item(current, "05")["status"] == "red"
    rejected = client.post(
        inspection,
        json={"basis_sha256": current["basis_sha256"], "author_reviews": [decision]},
    )
    assert rejected.status_code == 409
    hard = report(client, inspection, current, author_reviews=[author("05", chapter)])
    assert item(hard, "05")["status"] == "red"
    assert completion_consistency() == "red"
    previous = item(read(client, f"{inspection}/{signed['id']}"), "05")
    assert previous["status"] == "green" and previous["current_status"] == "outdated"
    assert previous["basis_sha256"] == item(signed, "05")["basis_sha256"]

    chapter = domain.save(
        chapter["id"], chapter["revision"], "铜铃在门内响了。\n学徒消耗了寿元。\n铜铃完好。"
    )
    for line_index, key in enumerate(rules):
        check_rule(key, "pass", line_index)
    revised = preview()
    assert item(revised, "05")["status"] == "green"
    updated = item(report(client, inspection, revised), "05")
    assert updated["basis_sha256"] != previous["basis_sha256"]
    assert "author_review" not in updated
