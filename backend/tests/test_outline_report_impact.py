from io import BytesIO
from zipfile import ZipFile

import pytest

from test_outline_workspace import chapter_plan, put_and_confirm
from test_workflow_inspection import author, item, read, report, source


@pytest.mark.parametrize("level", ["unit", "volume"])
def test_pacing_confirmation_only_reopens_the_scopes_that_use_the_changed_arrangement(
    client, domain, story, level
):
    branch = story["branch_id"]
    outline = f"/api/branches/{branch}/outline-workspace"
    inspection = f"/api/branches/{branch}/workflow-inspections"
    first = domain.save(story["chapter_id"], 0, "守夜人发现一截失去记号的铜尺。")
    second = domain.save(domain.create_chapter(branch, "复查旧尺")["id"], 0, "守夜人查到铜尺记号的来历。")
    third = domain.save(domain.create_chapter(branch, "归还铜尺")["id"], 0, "守夜人把铜尺归还给学徒。")

    def unit(identifier, summary, chapter):
        return {
            "id": identifier,
            "title": chapter["title"],
            "summary": summary,
            "chapters": [{**chapter_plan(chapter["id"], "按本章线索推进"), "setting_ids": []}],
        }

    plan = {
        "summary": "查清铜尺来历。",
        "volumes": [
            {
                "id": "v1",
                "title": "找尺卷",
                "summary": "到卷末才兑现来历之谜。",
                "units": [
                    unit("u1", "保留疑问，等复查再兑现。", first),
                    unit("u2", "核清疑问并交代来历。", second),
                ],
            },
            {
                "id": "v2",
                "title": "归还卷",
                "summary": "完成铜尺归还。",
                "units": [unit("u3", "当面交付铜尺。", third)],
            },
        ],
    }
    confirmed = put_and_confirm(client, outline, {"base_revision": 0, "plan": plan})["confirmed"]
    scopes = {
        "first": ({"scope": "chapter", "scope_id": first["id"]}, first),
        "second": ({"scope": "chapter", "scope_id": second["id"]}, second),
        "volume": ({"scope": "volume", "scope_id": "v1"}, first),
        "other": ({"scope": "volume", "scope_id": "v2"}, third),
        "book": ({"scope": "book"}, first),
    }
    signed = {}
    for key, (scope, chapter) in scopes.items():
        preview = read(client, inspection, **scope)["preview"]
        signed[key] = report(
            client,
            inspection,
            preview,
            author_reviews=[author("10", chapter)],
            length_targets={"min_chars": 1, "expected_chapters": len(preview["chapters"])},
        )
        assert all(item(signed[key], identifier)["current_status"] == "green" for identifier in ("09", "10"))

    volume = plan["volumes"][0]
    arrangement = volume["units"][0] if level == "unit" else volume
    old_summary = arrangement["summary"]
    arrangement["summary"] = "在本阶段第一章交代铜尺来历并兑现疑问。"
    affected = {"first", "volume", "book"} | ({"second"} if level == "volume" else set())
    draft = client.put(outline, json={"base_revision": confirmed["revision"], "plan": plan})
    assert draft.status_code == 200, draft.text
    params = {"base_revision": draft.json()["draft"]["revision"]}
    effect = read(client, outline + "/confirmation-impact", **params)
    assert effect["outline_fields"] == ["organization"]
    assert effect["chapter_changes"] == []
    affected_reports = {row["id"]: set(row["check_ids"]) for row in effect["affected_reports"]}
    assert set(affected_reports) == {signed[key]["id"] for key in affected}
    for key in affected:
        checks = affected_reports[signed[key]["id"]]
        assert "10" in checks and "09" not in checks
        if key != "book":
            assert checks == {"10"}
    still_confirmed = read(client, inspection, **scopes["first"][0])["latest"]
    assert still_confirmed["current"]
    assert item(still_confirmed, "10")["basis"] == item(signed["first"], "10")["basis"]
    adopted = client.post(outline + "/confirm", json={**params, "impact_sha256": effect["basis_sha256"]})
    assert adopted.status_code == 200, adopted.text

    for key, (scope, _) in scopes.items():
        old = signed[key]
        history = read(client, f"{inspection}/{old['id']}")
        preview = read(client, inspection, **scope)["preview"]
        assert item(history, "09")["current_status"] == "green"
        assert item(preview, "09")["basis_sha256"] == item(old, "09")["basis_sha256"]
        assert item(history, "10")["status"] == "green"
        assert item(history, "10")["basis"] == item(old, "10")["basis"]
        changed = key in affected
        assert item(history, "10")["current_status"] == ("outdated" if changed else "green")
        assert (item(preview, "10")["basis_sha256"] != item(old, "10")["basis_sha256"]) == changed
        if key != "book":
            assert all(
                item(preview, row["check_id"])["basis_sha256"] == row["basis_sha256"]
                for row in old["items"]
                if row["check_id"] != "10"
            )

    def chapter_arrangement(check):
        (frozen_volume,) = check["basis"]["pacing_plan"]
        (frozen_unit,) = frozen_volume["units"]
        assert frozen_volume["id"] == "v1" and frozen_unit["id"] == "u1"
        assert frozen_unit["chapter_ids"] == [first["id"]]
        return frozen_unit if level == "unit" else frozen_volume

    first_scope, _ = scopes["first"]
    first_history = read(client, f"{inspection}/{signed['first']['id']}")
    assert chapter_arrangement(item(first_history, "10"))["summary"] == old_summary
    first_preview = read(client, inspection, **first_scope)["preview"]
    assert chapter_arrangement(item(first_preview, "10"))["summary"] == arrangement["summary"]
    rejected = client.post(inspection, json={**first_scope, "basis_sha256": signed["first"]["basis_sha256"]})
    assert rejected.status_code == 409 and rejected.json()["code"] == "inspection_changed"
    rechecked = report(client, inspection, first_preview, author_reviews=[author("10", first)])
    assert item(rechecked, "10")["current_status"] == "green"
    assert item(read(client, f"{inspection}/{signed['first']['id']}"), "10") == item(first_history, "10")


def test_outline_confirmation_forecasts_real_volume_book_and_delivery_impacts_without_writes(
    client, domain, story
):
    branch = story["branch_id"]
    root = f"/api/branches/{branch}"
    outline = root + "/outline-workspace"
    inspection = root + "/workflow-inspections"
    completion = root + "/book-completion"
    first = domain.save(story["chapter_id"], 0, "守夜人递出一把刻着裂纹的旧尺。")
    second = domain.create_chapter(branch, "核对账簿")
    second = domain.save(second["id"], 0, "学徒抄下账簿编号并归还旧尺。")
    chapters = {"v1": first, "v2": second}
    plan = {
        "summary": "寻找旧尺的来源。",
        "volumes": [
            {
                "id": key,
                "title": title,
                "summary": "以实际记录为据。",
                "units": [
                    {
                        "id": "unit-" + key,
                        "title": "查证",
                        "summary": "取得一项证据。",
                        "chapters": [
                            {**chapter_plan(chapters[key]["id"], "比对编号"), "setting_ids": []},
                        ],
                    }
                ],
            }
            for key, title in [("v1", "渡口卷"), ("v2", "账簿卷")]
        ],
    }
    original = put_and_confirm(client, outline, {"base_revision": 0, "plan": plan})["confirmed"]

    def sign(scope="book", scope_id=None):
        params = {"scope": scope, **({"scope_id": scope_id} if scope_id else {})}
        preview = read(client, inspection, **params)["preview"]
        selected = chapters[scope_id] if scope_id else first
        value = report(
            client,
            inspection,
            preview,
            author_reviews=[
                author(row["check_id"], selected) for row in preview["items"] if row["status"] == "pending"
            ],
            length_targets={"min_chars": 1, "expected_chapters": len(preview["chapters"])},
        )
        assert all(row["current_status"] == "green" for row in value["items"]), value
        return value

    first_report, second_report, book_report = sign("volume", "v1"), sign("volume", "v2"), sign()
    finished = client.post(
        completion + "/confirm",
        json={
            "basis_sha256": read(client, completion)["basis_sha256"],
            "read_note": "作者通读两个保存章节。",
            "author_decisions": {
                "reading": "verified",
                "ending": "verified",
                "characters": "verified",
                "promises": "author_decision",
            },
            "ending": {"note": "旧尺已经归还。", "sources": [source(second)]},
            "characters": {"note": "学徒与守夜人当面交接。", "sources": [source(second)]},
            "promises": {"note": "作者确认本版本未声明其他承诺。"},
            "intro": "学徒发现旧尺上的裂纹。",
            "spoiler_scope": "opening",
            "sample_chapter_ids": [first["id"]],
            "materials_reviewed": True,
        },
    )
    assert finished.status_code == 201, finished.text
    completion_id = finished.json()["id"]
    archive = f"{completion}/{completion_id}/export"

    def exported_materials():
        exported = client.get(archive)
        assert exported.status_code == 200, exported.text
        with ZipFile(BytesIO(exported.content)) as pack:
            return {name: pack.read(name) for name in pack.namelist()}

    frozen_materials = exported_materials()
    plan["volumes"][0]["units"][0]["chapters"][0]["goal"] = "追查旧尺裂纹"
    plan["new_promises"] = [
        {
            "title": "旧尺裂纹的来历",
            **{key: first["id"] for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")},
        }
    ]
    added = client.put(outline, json={"base_revision": original["revision"], "plan": plan})
    assert added.status_code == 200, added.text
    draft = added.json()["draft"]
    params = {"base_revision": draft["revision"]}

    def counts():
        with domain.db.connect() as tx:
            return [
                tx.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("outline_revisions", "story_records", "story_record_history", "artifacts")
            ]

    before = counts()
    previous_story = domain.branch(branch)["story_revision"]
    effect = read(client, outline + "/confirmation-impact", **params)
    assert read(client, outline + "/confirmation-impact", **params) == effect
    reports = {row["id"]: row for row in effect["affected_reports"]}
    assert set(reports) == {first_report["id"], book_report["id"], completion_id}
    assert reports[first_report["id"]]["scope_id"] == "v1"
    assert reports[book_report["id"]]["scope"] == "book"
    assert reports[completion_id]["kind"] == "book_completion"
    assert {"02", "07", "10"} <= set(reports[first_report["id"]]["check_ids"])
    assert all("09" not in row["check_ids"] for row in reports.values())
    assert counts() == before and domain.branch(branch)["story_revision"] == previous_story
    assert read(client, outline)["draft"] == draft
    assert read(client, completion)["latest"]["current"]

    concurrent = sign("volume", "v1")
    before = counts()
    stale = client.post(outline + "/confirm", json={**params, "impact_sha256": effect["basis_sha256"]})
    assert stale.status_code == 409 and stale.json()["code"] == "outline_impact_changed"
    assert counts() == before and read(client, outline)["confirmed"]["id"] == original["id"]
    assert read(client, outline)["draft"] == draft

    effect = read(client, outline + "/confirmation-impact", **params)
    reports = {row["id"]: row for row in effect["affected_reports"]}
    assert set(reports) == {first_report["id"], book_report["id"], completion_id, concurrent["id"]}
    accepted = client.post(outline + "/confirm", json={**params, "impact_sha256": effect["basis_sha256"]})
    assert accepted.status_code == 200, accepted.text
    assert [row["title"] for row in accepted.json()["promises"]] == ["旧尺裂纹的来历"]
    for signed in (first_report, second_report, book_report, concurrent):
        actual = read(client, f"{inspection}/{signed['id']}")
        expected_checks = reports.get(signed["id"], {}).get("check_ids", [])
        assert [
            row["check_id"] for row in actual["items"] if row["current_status"] == "outdated"
        ] == expected_checks
        assert item(actual, "09")["current_status"] == "green"
    assert not read(client, completion)["latest"]["current"]
    assert exported_materials() == frozen_materials
    assert all(domain.chapter(row["id"])["content"] == row["content"] for row in chapters.values())
