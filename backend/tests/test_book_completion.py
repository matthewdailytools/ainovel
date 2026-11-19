import asyncio
from hashlib import sha256
from io import BytesIO
import json
from time import gmtime
from types import SimpleNamespace
import zipfile
from zipfile import ZipFile

from ainovel import workflow_inspection
from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.worker import Worker
from test_conception_workbench import confirm, item, path, save
from test_project_brief import BRIEF, IDEA
from test_workflow_archive import _members, _send_reports
from test_workflow_cloud import _complete
from test_workflow_inspection import author, source


def test_finish_requires_six_checks_and_exports_immutable_package(client, domain, story, monkeypatch):
    current_method = workflow_inspection.COUNT_VERSION
    previous_method = "正文有效字符/1，排除 Markdown 标题行、常见排版标记、空白和不可见字符，计入标点"
    monkeypatch.setattr(workflow_inspection, "COUNT_VERSION", previous_method)
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒找到师姐，归还了铜铃。")
    endpoint = f"/api/branches/{branch}/book-completion"
    inspection = f"/api/branches/{branch}/workflow-inspections"
    initial = client.get(endpoint).json()
    evidence = author("01", chapter)["evidence"]
    data = {
        "basis_sha256": initial["basis_sha256"],
        "read_note": "作者已通读保存稿。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": "主冲突以归还铜铃解决。", "sources": evidence},
        "characters": {"note": "师姐与学徒重逢。作者裁定其后去向留白。", "sources": evidence},
        "promises": {"note": "铃和师姐的线索均在末章兑现。", "sources": evidence},
        "intro": "学徒循着一枚旧铃寻找师姐。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    blocked = client.post(endpoint + "/confirm", json=data)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "completion_blocked"
    configured = client.put(
        inspection + "/length-targets", json={"targets": {"min_chars": 1, "expected_chapters": 1}}
    )
    assert configured.status_code == 200, configured.text
    data["basis_sha256"] = client.get(endpoint).json()["basis_sha256"]
    missing_inspection = client.post(endpoint + "/preview", json=data)
    assert missing_inspection.status_code == 200, missing_inspection.text
    assert [row["id"] for row in missing_inspection.json()["checks"] if row["status"] != "green"] == [
        "inspection"
    ]
    assert client.post(endpoint + "/confirm", json=data).json()["code"] == "completion_blocked"
    preview = client.get(inspection).json()["preview"]
    created = client.post(
        inspection,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
            ],
            "length_targets": {"min_chars": 1, "expected_chapters": 1},
        },
    )
    assert created.status_code == 201, created.text
    assert all(row["current_status"] == "green" for row in created.json()["items"]), created.json()
    assert client.post(endpoint + "/confirm", json=data).json()["code"] == "completion_changed"
    data["basis_sha256"] = client.get(endpoint).json()["basis_sha256"]
    without_decisions = {key: value for key, value in data.items() if key != "author_decisions"}
    preview = client.post(endpoint + "/preview", json=without_decisions).json()
    assert [row["id"] for row in preview["checks"] if row["status"] == "pending"] == [
        "manuscript",
        "ending",
        "characters",
        "promises",
    ]
    assert client.post(endpoint + "/confirm", json=without_decisions).json()["code"] == "completion_blocked"
    for field, gate in (
        ("reading", "manuscript"),
        ("ending", "ending"),
        ("characters", "characters"),
        ("promises", "promises"),
    ):
        waiting = {**data, "author_decisions": {**data["author_decisions"], field: "pending"}}
        current = client.post(endpoint + "/preview", json=waiting).json()["checks"]
        assert [row["id"] for row in current if row["status"] != "green"] == [gate]
        assert client.post(endpoint + "/confirm", json=waiting).json()["code"] == "completion_blocked"
    for field, value, gate in [
        ("read_note", "", "manuscript"),
        ("ending", {"note": ""}, "ending"),
        ("characters", {"note": ""}, "characters"),
        ("promises", {"note": ""}, "promises"),
        ("materials_reviewed", False, "delivery"),
    ]:
        incomplete = {**data, field: value}
        check = client.post(endpoint + "/preview", json=incomplete).json()
        assert [row["id"] for row in check["checks"] if row["status"] != "green"] == [gate]
        assert client.post(endpoint + "/confirm", json=incomplete).status_code == 409
    for field in ("ending", "characters", "promises"):
        unsupported = {**data, field: {"note": "只有说明，没有当前稿件引文。"}}
        check = client.post(endpoint + "/preview", json=unsupported).json()
        item = next(row for row in check["checks"] if row["id"] == field)
        assert item["status"] == "pending"
        assert "当前正文引文" in item["detail"]
        assert client.post(endpoint + "/confirm", json=unsupported).json()["code"] == "completion_blocked"
    data["author_decisions"]["characters"] = "author_decision"
    data["characters"]["sources"] = []
    quote = "归还了铜铃。"
    data["ending"]["sources"] = [
        {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": len(chapter["content"]) - len(quote),
            "end": len(chapter["content"]),
            "quote": quote,
        }
    ]
    confirmed = client.post(endpoint + "/confirm", json=data)
    assert confirmed.status_code == 201, confirmed.text
    record = confirmed.json()
    assert record["review"]["author_decisions"] == data["author_decisions"]
    assert "作者裁定" in record["checks"][2]["detail"]
    assert client.post(endpoint + "/confirm", json=data).json()["id"] == record["id"]
    export = f"{endpoint}/{record['id']}/export"
    zip_now = 1_789_000_000
    monkeypatch.setattr(zipfile, "time", SimpleNamespace(time=lambda: zip_now, localtime=gmtime))
    response = client.get(export)
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["inspection_id"] == created.json()["id"]
        assert set(manifest["files"]) == {"正文.md", "设定册.md", "简介.md", "试读.md"}
        assert all(
            sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest["files"].items()
        )
        assert chapter["content"] in archive.read("正文.md").decode()

    monkeypatch.setattr(workflow_inspection, "COUNT_VERSION", current_method)
    recounted = client.get(endpoint).json()
    assert not recounted["latest"]["current"]
    assert recounted["basis_sha256"] != data["basis_sha256"]
    assert recounted["latest"]["checks"] == record["checks"]
    assert client.get(export).content == response.content

    length = client.get(inspection).json()["preview"]["length_target"]
    changed = client.put(
        inspection + "/length-targets",
        json={"base_target_id": length["id"], "targets": {"expected_chapters": 2}},
    )
    assert changed.status_code == 200, changed.text
    latest = client.get(endpoint).json()
    assert not latest["latest"]["current"]
    assert latest["basis_sha256"] != data["basis_sha256"]
    review = client.post(endpoint + "/preview", json={**data, "basis_sha256": latest["basis_sha256"]}).json()
    assert next(row for row in review["checks"] if row["id"] == "manuscript")["status"] == "red"
    assert (
        client.post(endpoint + "/confirm", json={**data, "basis_sha256": latest["basis_sha256"]}).status_code
        == 409
    )
    domain.save(chapter["id"], chapter["revision"], "学徒仍在找铃。")
    current = client.get(endpoint).json()
    assert not current["latest"]["current"]
    stale_evidence = client.post(
        endpoint + "/preview", json={**data, "basis_sha256": current["basis_sha256"]}
    )
    assert stale_evidence.status_code == 409 and stale_evidence.json()["code"] == "completion_evidence"
    recorded = client.get(f"{endpoint}/history/{record['id']}").json()
    assert recorded["review"]["author_decisions"] == data["author_decisions"]
    assert recorded["review"]["ending"]["sources"] == data["ending"]["sources"]
    zip_now += 4
    historical = client.get(export)
    assert historical.status_code == 200
    assert historical.content == response.content


def test_m7_uses_the_signed_text_length_basis_and_keeps_previous_delivery_readable(client, domain, story):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "😀师姐把铜铃交还主人。")
    project = client.put(
        path(branch) + "/project-brief",
        json={
            "base_revision": 0,
            "idempotency_key": "m7-length-brief",
            "idea": IDEA,
            "brief": BRIEF,
        },
    )
    assert project.status_code == 200, project.text
    endpoint = f"/api/branches/{branch}/book-completion"
    inspection = f"/api/branches/{branch}/workflow-inspections"
    data = {
        "basis_sha256": client.get(endpoint).json()["basis_sha256"],
        "read_note": "作者已通读保存稿。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "author_decision",
        },
        "ending": {"note": "铃已归还。", "sources": [source(chapter)]},
        "characters": {"note": "师姐主动完成交接。", "sources": [source(chapter)]},
        "promises": {"note": "作者确认没有另设伏笔。"},
        "intro": "学徒寻找一枚铜铃。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    preview = client.post(endpoint + "/preview", json=data).json()
    assert preview["checks"][0]["status"] == "pending"
    assert "第 09 项签认篇幅" in preview["checks"][0]["detail"]
    assert client.post(endpoint + "/confirm", json=data).json()["code"] == "completion_blocked"
    revision = client.get(inspection).json()["preview"]
    length_note = "作者确认由一卷两章缩为一章，范围覆盖全部内容。"
    adjudication = client.post(
        inspection,
        json={
            "basis_sha256": revision["basis_sha256"],
            "author_reviews": [
                author(row["check_id"], chapter)
                | ({"basis_note": BRIEF["length"], "reason": length_note} if row["check_id"] == "09" else {})
                for row in revision["items"]
                if row["status"] == "pending"
            ],
        },
    )
    assert adjudication.status_code == 201, adjudication.text
    length = next(row for row in adjudication.json()["items"] if row["check_id"] == "09")
    assert length["basis"]["project_length"] == BRIEF["length"]
    assert length["author_review"]["reason"] == length_note
    data["basis_sha256"] = client.get(endpoint).json()["basis_sha256"]
    confirmed = client.post(endpoint + "/confirm", json=data)
    assert confirmed.status_code == 201, confirmed.text
    record = confirmed.json()
    assert record["checks"][0]["status"] == "green"
    assert "第 09 项中的作者核对" in record["checks"][0]["detail"]
    assert record["manifest"]["inspection_id"] == adjudication.json()["id"]
    assert record["manifest"]["length_target"] is None
    identifier = record["id"]
    export = f"{endpoint}/{identifier}/export"
    frozen = client.get(export).content

    # Simulate a prior-policy confirmation with a note and no excerpt.
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE artifacts SET basis=json_set(basis,'$.review.ending.sources',json('[]')) WHERE id=?",
            (identifier,),
        )
    current = client.get(endpoint).json()
    assert current["history"][0]["current"] is False
    assert current["latest"]["current"] is False
    assert current["basis_sha256"] == data["basis_sha256"]
    history = client.get(f"{endpoint}/history/{identifier}").json()
    assert history["basis_reconciles"] and not history["current"]
    assert history["review"]["ending"]["sources"] == []
    assert client.get(export).content == frozen
    new = client.post(endpoint + "/confirm", json=data)
    assert new.status_code == 201 and new.json()["id"] != identifier
    assert client.get(export).content == frozen


def test_m7_rejects_chapter_with_only_formatting_without_a_length_target(client, domain, story):
    branch = story["branch_id"]
    content = "## 第一章\r\n***\r> - "
    chapter = domain.save(story["chapter_id"], 0, content)
    endpoint = f"/api/branches/{branch}/book-completion"
    state = client.get(endpoint).json()
    data = {
        "basis_sha256": state["basis_sha256"],
        "read_note": "作者通读。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": {"note": "核对终局。"},
        "characters": {"note": "核对人物去向。"},
        "promises": {"note": "核对伏笔。"},
        "intro": "故事简介。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    preview = client.post(endpoint + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    assert preview.json()["manifest"]["length_target"] is None
    manuscript = next(row for row in preview.json()["checks"] if row["id"] == "manuscript")
    assert manuscript["status"] == "red"
    assert "0 个有效字符" in manuscript["detail"] and "空稿" in manuscript["detail"]
    rejected = client.post(endpoint + "/confirm", json=data)
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "completion_blocked"
    assert client.get(f"/api/chapters/{chapter['id']}").json()["content"] == content


def test_exported_catalogue_keeps_each_adopted_setting_on_its_frozen_revision(
    client, domain, story, tmp_path
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒找到师姐，归还了铜铃。")
    completion = f"/api/branches/{branch}/book-completion"
    inspection = f"/api/branches/{branch}/workflow-inspections"
    conception = path(branch)
    _send_reports(client, inspection, chapter)
    initial = _complete(client, completion, chapter)
    initial_path = completion + f"/{initial['id']}/export"
    original = _members(client.get(initial_path))
    assert initial["manifest"]["format"] == "book-completion/1"

    state = client.get(conception).json()
    categories = {category["id"]: category for category in state["categories"]}
    categories["characters"]["items"] = [
        item("keeper", "守铃人阿陶"),
        {**item("draft-person", "待定人物"), "adoption_state": "pending"},
    ]
    categories["relationships"]["items"] = [item("sisters", "师承关系")]
    state["categories"].extend(
        [
            {"id": "props", "title": "信物", "items": [item("red-key", "赤钥")], "skipped": False},
            {"id": "unused", "title": "未用设定", "items": [item("blue-key", "蓝钥")], "skipped": True},
        ]
    )
    adopted = confirm(client, conception, save(client, conception, state))["confirmation"]["id"]
    _send_reports(client, inspection, chapter)
    classified = _complete(client, completion, chapter)
    classified_path = completion + f"/{classified['id']}/export"
    frozen = _members(client.get(classified_path))
    manifest = classified["manifest"]
    assert manifest["format"] == "book-completion/2" and manifest["setting_id"] == adopted
    assert [entry["id"] for entry in manifest["settings_catalogue"]] == ["keeper", "sisters", "red-key"]
    catalogue = frozen["设定册.md"].decode()
    assert all(title in catalogue for title in ("守铃人阿陶", "人物关系 · 师承关系", "信物 · 赤钥"))
    assert "待定人物" not in catalogue and "蓝钥" not in catalogue
    assert sha256(frozen["设定册.md"]).hexdigest() == manifest["files"]["设定册.md"]

    state = client.get(conception).json()
    first = next(row for row in state["categories"] if row["id"] == "characters")
    first["items"][0]["detail"] = "阿陶已经把铜铃交回师门。"
    confirm(client, conception, save(client, conception, state), "confirmed-after-delivery")
    assert not client.get(completion).json()["latest"]["current"]
    assert _members(client.get(classified_path)) == frozen
    assert _members(client.get(initial_path)) == original

    history = export_manuscript(domain, branch)
    isolated = Domain(Database(tmp_path / "completion-with-settings.sqlite"))
    import_manuscript(isolated, history)
    restored = export_manuscript(isolated, branch).workflow_history.completions
    assert {row.id: row.materials["设定册.md"].encode() for row in restored} == {
        initial["id"]: original["设定册.md"],
        classified["id"]: frozen["设定册.md"],
    }


def test_completion_preparation_uses_exact_sources_and_keeps_author_gate(client, domain, settings, story):
    branch = story["branch_id"]
    opening = domain.save(story["chapter_id"], 0, "😀学徒拾起那只哑铃。")
    second = domain.create_chapter(branch, "归还")
    ending = domain.save(second["id"], 0, "师姐解下铃绳，归还铜铃。")
    endpoint = f"/api/branches/{branch}/book-completion"
    basis = client.get(endpoint).json()["basis_sha256"]
    queued = client.post(
        endpoint + "/preparation",
        json={"basis_sha256": basis, "account": "deepseek", "client_request_id": "delivery-draft-one"},
    )
    assert queued.status_code == 202, queued.text

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            task = json.loads(prompt.split("\n只返回")[0])["任务"]
            if task["动作"] == "起草完本交付简介":
                assert task["可用正文"] == [opening["content"]]
                assert ending["content"] not in prompt
                payload = {"intro": "学徒从哑铃的来历追起。"}
            else:
                assert task["动作"] == "准备完本收束核对建议"
                last = task["正文依据"][-1]
                assert last["text"] == ending["content"]
                payload = {
                    "ending": {"note": "核对归还是否收束。", "source_ids": [last["source_id"]]},
                    "characters": {"note": "缺少后续依据。", "source_ids": []},
                    "promises": {"note": "", "source_ids": []},
                }
            return {"text": json.dumps(payload), "usage": {"input": 1, "output": 1}, "call_id": task["动作"]}

    assert asyncio.run(Worker(settings, Model()).run_once())
    state = client.get(endpoint + "/preparation").json()["task"]
    assert state["status"] == "succeeded", state["error"]
    assert state["candidate"]["current"]
    assert state["candidate"]["closures"]["ending"]["sources"] == [
        {
            "chapter_id": ending["id"],
            "revision": ending["revision"],
            "start": 0,
            "end": len(ending["content"]),
            "quote": ending["content"],
        }
    ]
    assert state["candidate"]["closures"]["characters"]["note"] == ""
    assert client.get(endpoint).json()["latest"] is None
    data = {
        "basis_sha256": basis,
        "preparation_id": state["artifact_id"],
        **state["candidate"]["closures"],
        "read_note": "",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "intro": state["candidate"]["intro"],
        "spoiler_scope": "opening",
        "sample_chapter_ids": [opening["id"]],
        "materials_reviewed": False,
    }
    assert client.post(endpoint + "/confirm", json=data).json()["code"] == "completion_blocked"
    domain.save(opening["id"], opening["revision"], "学徒把铃留在门外。")
    assert not client.get(endpoint + "/preparation").json()["task"]["candidate"]["current"]
    data["basis_sha256"] = client.get(endpoint).json()["basis_sha256"]
    assert client.post(endpoint + "/preview", json=data).json()["code"] == "completion_preparation_stale"


def test_prepared_introduction_cannot_be_relabelled_with_a_different_delivery_scope(
    client, domain, settings, story
):
    branch = story["branch_id"]
    opening = domain.save(story["chapter_id"], 0, "学徒从木匣里拾起铜铃。")
    second = domain.create_chapter(branch, "揭晓")
    ending = "末章揭晓，铃匠就是失散的师姐。"
    second = domain.save(second["id"], 0, ending)
    endpoint = f"/api/branches/{branch}/book-completion"
    _send_reports(client, f"/api/branches/{branch}/workflow-inspections", opening)
    basis = client.get(endpoint).json()["basis_sha256"]
    queued = client.post(
        endpoint + "/preparation",
        json={
            "basis_sha256": basis,
            "account": "deepseek",
            "client_request_id": "whole-book-intro",
            "spoiler_scope": "whole",
            "sample_chapters": 2,
        },
    )
    assert queued.status_code == 202, queued.text

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            task = json.loads(prompt.split("\n只返回")[0])["任务"]
            if task["动作"] == "准备完本收束核对建议":
                payload = {
                    "ending": {
                        "note": "核对末章的身份揭晓。",
                        "source_ids": [task["正文依据"][-1]["source_id"]],
                    },
                    "characters": {"note": "", "source_ids": []},
                    "promises": {"note": "", "source_ids": []},
                }
            else:
                assert task["剧透范围"] == "全书" and ending in task["可用正文"]
                payload = {"intro": ending}
            return {"text": json.dumps(payload), "usage": {"input": 1, "output": 1}, "call_id": task["动作"]}

    assert asyncio.run(Worker(settings, Model()).run_once())
    prepared = client.get(endpoint + "/preparation").json()["task"]
    assert prepared["status"] == "succeeded", prepared
    candidate = prepared["candidate"]
    assert candidate["spoiler_scope"] == "whole"
    assert candidate["sample_chapter_ids"] == [opening["id"], second["id"]]
    request = {
        "basis_sha256": basis,
        "preparation_id": prepared["artifact_id"],
        "read_note": "作者已通读。",
        "author_decisions": {
            "reading": "verified",
            "ending": "verified",
            "characters": "verified",
            "promises": "verified",
        },
        "ending": candidate["closures"]["ending"],
        "characters": {"note": "师姐与学徒重逢。", "sources": [source(second)]},
        "promises": {"note": "铃匠身份已揭晓。", "sources": [source(second)]},
        "intro": " " + candidate["intro"] + "\n",
        "spoiler_scope": "whole",
        "sample_chapter_ids": [opening["id"], second["id"]],
        "materials_reviewed": True,
    }
    for changed in ({"spoiler_scope": "opening"}, {"sample_chapter_ids": [opening["id"]]}):
        for route in ("preview", "confirm"):
            refused = client.post(f"{endpoint}/{route}", json={**request, **changed})
            assert refused.status_code == 409, refused.text
            assert refused.json()["code"] == "completion_preparation_scope"
    assert client.get(endpoint).json()["latest"] is None

    matched = client.post(endpoint + "/confirm", json=request)
    assert matched.status_code == 201, matched.text
    original = matched.json()
    original_url = f"{endpoint}/{original['id']}/export"
    original_intro = _members(client.get(original_url))["简介.md"].decode()
    assert "剧透范围：含全书内容" in original_intro and ending in original_intro

    edited_request = {
        **request,
        "intro": "一名学徒带着失主留下的铜铃走出老街。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [opening["id"]],
    }
    review = client.post(endpoint + "/preview", json={**edited_request, "materials_reviewed": False})
    assert review.status_code == 200, review.text
    assert next(row for row in review.json()["checks"] if row["id"] == "delivery")["status"] == "pending"
    assert (
        client.post(endpoint + "/confirm", json={**edited_request, "materials_reviewed": False}).status_code
        == 409
    )
    edited = client.post(endpoint + "/confirm", json=edited_request)
    assert edited.status_code == 201, edited.text
    assert edited.json()["manifest"]["ai_preparation_id"] == prepared["artifact_id"]
    edited_url = f"{endpoint}/{edited.json()['id']}/export"
    parts = _members(client.get(edited_url))
    assert "剧透范围：仅开篇" in parts["简介.md"].decode()
    assert ending not in parts["简介.md"].decode() + parts["试读.md"].decode()
    assert _members(client.get(original_url))["简介.md"].decode() == original_intro
