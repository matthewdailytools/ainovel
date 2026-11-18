from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.outline_workspace import read_outline
from test_outline_workspace import chapter_plan
from test_workflow_inspection import author, item, read, report, source


def test_author_required_promise_controls_inspection_completion_and_portable_history(
    client, domain, story, tmp_path
):
    branch = story["branch_id"]
    root = f"/api/branches/{branch}"
    outline = root + "/outline-workspace"
    inspections = root + "/workflow-inspections"
    completion = root + "/book-completion"
    first = domain.save(story["chapter_id"], 0, "门外的铜铃发出一声闷响。")
    last = domain.create_chapter(branch, "还铃")
    window = {
        "plant_chapter_id": first["id"],
        "opens_chapter_id": last["id"],
        "closes_chapter_id": last["id"],
    }
    plan = {
        "summary": "追查铜铃来历，最终归还。",
        "volumes": [
            {
                "id": "volume",
                "title": "上下两章",
                "summary": "初见与归还。",
                "units": [
                    {
                        "id": "unit",
                        "title": "归还铜铃",
                        "summary": "找回失主。",
                        "chapters": [
                            {**chapter_plan(c["id"], c["title"]), "setting_ids": []} for c in [first, last]
                        ],
                    }
                ],
            }
        ],
        "new_promises": [
            {**window, "title": "归还铜铃", "fulfillment": "author_required"},
            {**window, "title": "门环纹样"},
        ],
    }

    def save(plan, revision):
        response = client.put(outline, json={"base_revision": revision, "plan": plan})
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["requires_promise_confirmation"]
        return {"base_revision": value["draft"]["revision"]}

    def confirm(data):
        blocked = client.post(outline + "/confirm", json=data)
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "outline_required_promises"
        response = client.post(outline + "/confirm", json={**data, "acknowledge_required_promises": True})
        assert response.status_code == 200, response.text
        return response.json()

    draft = save(plan, 0)
    assert not read(client, outline)["promises"]
    adopted = confirm(draft)
    adopted_outline = adopted["confirmed"]
    assert adopted_outline["setting_basis"]["required_promises_confirmation"] == "author"
    required, ordinary = [row["promise_id"] for row in adopted["promises"]]
    assert adopted["promises"][0]["fulfillment_met"] is False
    assert adopted["promises"][0]["due"] is None
    assert "fulfillment" not in adopted["promises"][1]
    assert item(read(client, inspections)["preview"], "07")["status"] == "yellow"
    last = domain.save(last["id"], 0, "学徒认出铃纹，将铜铃交还师姐。")

    def record(identifier, *stages):
        current = read(client, root + "/story-graph")
        existing = next(row for row in current["records"] if row["id"] == identifier)
        changed = client.put(
            root + f"/story-records/{identifier}",
            json={
                "base_story_revision": current["story_revision"],
                "base_revision": existing["revision"],
                "record": {**existing["payload"], "stages": [{"status": "planned"}, *stages]},
            },
        )
        assert changed.status_code == 200, changed.text

    def check_promise():
        return next(row for row in read(client, completion)["checks"] if row["id"] == "promises")

    assert item(read(client, inspections)["preview"], "07")["status"] == "red"
    record(required, {"status": "abandoned", "note": "作者决定先略过归还。"})
    record(ordinary, {"status": "retained", "note": "门环纹样留作续篇背景。"})
    preview = read(client, inspections)["preview"]
    warning = item(preview, "07")
    assert warning["status"] == "red"
    assert warning["basis"]["outline_id"] == adopted_outline["id"]
    assert check_promise()["status"] == "red"
    assert "归还铜铃" in check_promise()["detail"] and "门环纹样" not in check_promise()["detail"]
    assert (
        item(report(client, inspections, preview, author_reviews=[author("07", last)]), "07")["status"]
        == "red"
    )

    record(required, {"status": "resolved", "sources": [source(first)]})
    assert item(read(client, inspections)["preview"], "07")["status"] == "red"
    record(required, {"status": "resolved", "sources": [source(last)]})
    preview = read(client, inspections)["preview"]
    assert item(preview, "07")["status"] == "green"
    completed_report = report(
        client,
        inspections,
        preview,
        author_reviews=[
            author(check["check_id"], last) for check in preview["items"] if check["status"] == "pending"
        ],
        length_targets={"min_chars": 1, "expected_chapters": 2},
    )
    data = {
        "basis_sha256": read(client, completion)["basis_sha256"],
        "read_note": "作者通读两个保存章节。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": "铜铃已归还。", "sources": [source(last)]},
        "characters": {"note": "学徒与师姐重逢。", "sources": [source(last)]},
        "promises": {"note": "按窗口兑现了铜铃承诺。", "sources": [source(last)]},
        "intro": "学徒寻找铜铃的失主。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [first["id"]],
        "materials_reviewed": True,
    }
    finished = client.post(completion + "/confirm", json=data)
    assert finished.status_code == 201, finished.text
    download = f"{completion}/{finished.json()['id']}/export"
    archive_zip = client.get(download).content
    package = export_manuscript(domain, branch)
    portable = package.outline_history.versions[0]
    assert portable.plan.promises[0].fulfillment == "author_required"
    restore = Domain(Database(tmp_path / "restore-required.sqlite"))
    assert import_manuscript(restore, package)["outline_restore"]["status"] == "adopted"
    restored = read_outline(restore, branch)
    assert restored["confirmed"]["setting_basis"]["required_promises_confirmation"] == "formal_source"
    assert restored["promises"][0]["fulfillment_met"] is True

    domain.save(last["id"], last["revision"], "学徒转身离开，没有归还铜铃。")
    assert item(read(client, inspections)["preview"], "07")["status"] == "red"
    previous = item(read(client, f"{inspections}/{completed_report['id']}"), "07")
    assert previous["status"] == "green" and previous["current_status"] == "outdated"
    old = next(row for row in previous["basis"]["records"] if row["id"] == required)
    assert old["stages"][-1]["sources"] == [source(last)]
    assert not read(client, completion)["latest"]["current"]
    assert check_promise()["status"] == "red"
    assert client.get(download).content == archive_zip

    record(required, {"status": "abandoned", "note": "经核对保留为后续题材空间。"})
    changed_plan = adopted_outline["plan"]
    del changed_plan["promises"][0]["fulfillment"]
    draft = save(changed_plan, adopted_outline["revision"])
    assert check_promise()["status"] == "red"
    updated = confirm(draft)["confirmed"]
    assert updated["id"] != adopted_outline["id"]
    assert item(read(client, inspections)["preview"], "07")["status"] == "green"
    historical_outline = read(client, f"{outline}/revisions/{adopted_outline['revision']}")
    assert historical_outline["plan"]["promises"][0]["fulfillment"] == "author_required"
