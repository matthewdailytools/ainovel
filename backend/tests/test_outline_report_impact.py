from io import BytesIO
from zipfile import ZipFile

from test_outline_workspace import chapter_plan, put_and_confirm
from test_workflow_inspection import author, item, read, report


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
            "ending": {"note": "旧尺已经归还。"},
            "characters": {"note": "学徒与守夜人当面交接。"},
            "promises": {"note": "本版本未声明其他承诺。"},
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
