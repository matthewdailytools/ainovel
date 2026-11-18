from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.db import Database, DomainError
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.workflow_archive import (
    WorkflowHistory,
    validate_workflow_history,
    validate_workflow_object,
)
from ainovel.workflow_inspection import _item, _sha
from test_workflow_inspection import author, save_inspection_story_record, source


def _members(response):
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _send_reports(client, inspection, chapter):
    scoped = {"scope": "chapter", "scope_id": chapter["id"]}
    preview = client.get(inspection, params=scoped).json()["preview"]
    local = client.post(
        inspection,
        json={
            **scoped,
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [author("03", chapter, "red")],
        },
    )
    assert local.status_code == 201, local.text
    resolved = client.post(
        inspection,
        json={
            **scoped,
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [author("03", chapter, "green")],
        },
    )
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["items"][2]["current_status"] == "green"
    preview = client.get(inspection).json()["preview"]
    passed = client.post(
        inspection,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                author(item["check_id"], chapter) for item in preview["items"] if item["status"] == "pending"
            ],
        },
    )
    assert passed.status_code == 201, passed.text
    return local.json(), passed.json()


def test_restored_workflow_proofs_remain_historical_until_native_author_confirmation(
    client, domain, settings, story, tmp_path
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "😀学徒找到师姐，归还了铜铃。")
    inspection = f"/api/branches/{branch}/workflow-inspections"
    completion = f"/api/branches/{branch}/book-completion"
    red, book = _send_reports(client, inspection, chapter)
    decision = {
        "basis_sha256": client.get(completion).json()["basis_sha256"],
        "read_note": "作者已通读保存稿。",
        "ending": {"note": "归还铜铃解决主冲突。", "sources": [source(chapter)]},
        "characters": {"note": "师姐与学徒重逢。"},
        "promises": {"note": "疑问都在末章交待。"},
        "intro": "学徒循着旧铃寻找师姐。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    confirmed = client.post(completion + "/confirm", json=decision)
    assert confirmed.status_code == 201, confirmed.text
    record = confirmed.json()
    pointer = completion + f"/{record['id']}/export"
    original_zip = _members(client.get(pointer))
    pack = export_manuscript(domain, branch)
    history = pack.workflow_history
    revisions = {row.id: row for row in pack.revisions}
    validate_workflow_history(history, branch, revisions)
    encoded = history.model_dump()
    wrong = history.model_dump()
    wrong["inspections"][0]["chapter_bindings"][0]["revision_id"] = "other-work-source"
    with pytest.raises(DomainError, match="准确正文修订"):
        validate_workflow_history(WorkflowHistory.model_validate(wrong), branch, revisions)
    wrong = history.model_dump()
    wrong["completions"][0]["materials"]["正文.md"] = "置换正文"
    with pytest.raises(DomainError, match="校验失败"):
        validate_workflow_history(WorkflowHistory.model_validate(wrong), branch, revisions)
    wrong["completions"][0]["manifest"]["files"]["正文.md"] = sha256("置换正文".encode()).hexdigest()
    with pytest.raises(DomainError, match="冻结稿件"):
        validate_workflow_history(WorkflowHistory.model_validate(wrong), branch, revisions)
    wrong["completions"][0]["materials"]["../../other.md"] = wrong["completions"][0]["materials"].pop(
        "正文.md"
    )
    wrong["completions"][0]["manifest"]["files"]["../../other.md"] = wrong["completions"][0]["manifest"][
        "files"
    ].pop("正文.md")
    with pytest.raises(DomainError, match="材料名称"):
        validate_workflow_history(WorkflowHistory.model_validate(wrong), branch, revisions)

    target_settings = replace(settings, data_dir=tmp_path / "restored-workflow")
    target = Domain(Database(target_settings.data_dir / "manuscripts.sqlite"))
    with TestClient(create_app(target_settings)) as other:
        other.headers["X-Ainovel-CSRF"] = other.get("/api/session").json()["csrf"]
        archive_path = f"/api/branches/{branch}/manuscript-package"
        exported = client.get(archive_path)
        assert exported.status_code == 200, exported.text
        original_pack = exported.json()
        corrupt = {**original_pack, "workflow_history": wrong}
        rejected = other.post("/api/manuscript-packages/import", json=corrupt)
        assert rejected.status_code == 409 and rejected.json()["code"] == "completion_integrity"
        with target.db.connect() as tx:
            assert not tx.execute("SELECT 1 FROM novels").fetchone()
        imported = other.post("/api/manuscript-packages/import", json=original_pack)
        assert imported.status_code == 201, imported.text
        with target.db.connect() as tx:
            assert not tx.execute(
                "SELECT 1 FROM artifacts WHERE kind IN ('workflow_inspection','book_completion')"
            ).fetchone()
        recovered = other.get(archive_path).json()["workflow_history"]
        assert {i["id"]: i for i in recovered["inspections"]} == {i["id"]: i for i in encoded["inspections"]}
        check = other.get(inspection).json()
        assert check["latest"] is None
        assert check["history"][0]["id"] == book["id"]
        frozen = other.get(inspection + "/" + red["id"]).json()
        verdict = next(item for item in frozen["items"] if item["check_id"] == "03")
        assert verdict["status"] == "red" and verdict["current_status"] == "outdated"
        assert verdict["author_review"]["evidence"] == [source(chapter)]
        assert frozen["scope"] == "chapter" and frozen["scope_id"] == chapter["id"]
        assert not frozen["current"]
        details = other.get(completion + f"/history/{record['id']}").json()
        assert details["origin"] == "manuscript_package" and not details["current"]
        assert details["review"]["ending"]["sources"] == [source(chapter)]
        assert other.get(completion).json()["latest"] is None
        assert _members(other.get(pointer)) == original_zip
        native_input = other.get(inspection + "/" + book["id"]).json()["revalidation_request"]
        assert native_input
        current_m6 = other.post(inspection, json=native_input)
        assert current_m6.status_code == 201, current_m6.text
        assert current_m6.json()["current"] and current_m6.json()["id"] != book["id"]
        current_input = other.get(completion + f"/history/{record['id']}").json()["revalidation_request"]
        assert current_input and current_input["basis_sha256"] != decision["basis_sha256"]
        current_m7 = other.post(completion + "/confirm", json=current_input)
        assert current_m7.status_code == 201, current_m7.text
        assert current_m7.json()["current"] and current_m7.json()["id"] != record["id"]

        dual = other.get(archive_path).json()["workflow_history"]
        assert {row["id"] for row in dual["inspections"]} == {
            *(row["id"] for row in encoded["inspections"]),
            current_m6.json()["id"],
        }
        assert {row["id"] for row in dual["completions"]} == {record["id"], current_m7.json()["id"]}
        third = Domain(Database(tmp_path / "workflow-third.sqlite"))
        import_manuscript(third, type(pack).model_validate(other.get(archive_path).json()))
        with third.db.connect() as tx:
            assert tx.execute("SELECT COUNT(*) FROM manuscript_workflow_history").fetchone()[0] == 6
            assert not tx.execute("SELECT 1 FROM artifacts WHERE kind='book_completion'").fetchone()

        target.save(chapter["id"], chapter["revision"], "后来作者重写了最后一场。")
        assert other.get(completion + f"/history/{record['id']}").json()["revalidation_request"] is None
        assert not other.get(completion).json()["latest"]["current"]
        assert _members(other.get(pointer)) == original_zip


def test_state_conflict_archive_uses_frozen_chapters_and_refuses_changed_verdict_or_quote(
    client, domain, story
):
    branch_id = story["branch_id"]
    branch = f"/api/branches/{branch_id}"
    route = branch + "/workflow-inspections"
    first = domain.save(story["chapter_id"], 0, "😀沈砚左手骨折。")
    second = domain.create_chapter(branch_id, "第二章")
    second = domain.save(second["id"], 0, "隔雾看去，沈砚左手完好。")
    person = save_inspection_story_record(client, branch, {"kind": "character", "title": "沈砚"})
    event = save_inspection_story_record(
        client,
        branch,
        {"kind": "event", "title": "渡口交接", "status": "actual", "sources": [source(first)]},
    )
    for chapter, value in ((first, "骨折"), (second, "完好")):
        save_inspection_story_record(
            client,
            branch,
            {
                "kind": "state",
                "title": value,
                "character_id": person["id"],
                "event_id": event["id"],
                "category": "injury",
                "key": "左手伤势",
                "value": value,
                "audience": "author",
                "truth": "actual",
                "sources": [source(chapter)],
            },
        )
    preview = client.get(route).json()["preview"]
    created = client.post(route, json={"basis_sha256": preview["basis_sha256"]})
    assert created.status_code == 201, created.text
    assert created.json()["items"][2]["status"] == "red"
    domain.save(story["chapter_id"], first["revision"], "沈砚处理了左手的伤。")
    package = export_manuscript(domain, branch_id)
    history = package.workflow_history
    originals = {row.id: row for row in package.revisions}
    frozen = history.inspections[0]
    assert validate_workflow_object(frozen, branch_id, originals.get, None) == frozen
    assert frozen.report.items[2].basis["state_conflicts"][0]["event_id"] == event["id"]

    green = history.model_dump()
    green["inspections"][0]["report"]["items"][2]["status"] = "green"
    with pytest.raises(DomainError, match="须保留红项"):
        validate_workflow_history(WorkflowHistory.model_validate(green), branch_id, originals)

    bad_quote = history.model_dump()
    report = bad_quote["inspections"][0]["report"]
    check = report["items"][2]
    side = check["basis"]["state_conflicts"][0]["sides"][0]
    # Change both witnesses and recalculate hashes to exercise the exact revision/selection check.
    side["sources"][0]["start"] += 1
    for row in check["basis"]["records"]:
        if row["id"] == side["record_id"]:
            row["sources"][0]["start"] = side["sources"][0]["start"]
    check["basis_sha256"] = _item(
        "03", "", check["basis"], manifest=report["chapters"], scope=report["scope"]
    )["basis_sha256"]
    report["basis_sha256"] = _sha(
        [report["scope"], report["scope_id"], [item["basis_sha256"] for item in report["items"]]]
    )
    with pytest.raises(DomainError, match="第 03 项冲突.*原文范围无效"):
        validate_workflow_history(WorkflowHistory.model_validate(bad_quote), branch_id, originals)
