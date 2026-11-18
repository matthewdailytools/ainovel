import asyncio
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.worker import Worker
from test_conception_workbench import confirm, item, path, save
from test_workflow_archive import _members, _send_reports
from test_workflow_cloud import _complete
from test_workflow_inspection import author


def test_finish_requires_six_checks_and_exports_immutable_package(client, domain, story):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒找到师姐，归还了铜铃。")
    endpoint = f"/api/branches/{branch}/book-completion"
    inspection = f"/api/branches/{branch}/workflow-inspections"
    initial = client.get(endpoint).json()
    data = {
        "basis_sha256": initial["basis_sha256"],
        "read_note": "作者已通读保存稿。",
        "ending": {"note": "主冲突以归还铜铃解决。"},
        "characters": {"note": "师姐与学徒重逢。"},
        "promises": {"note": "铃和师姐的线索均在末章兑现。"},
        "intro": "学徒循着一枚旧铃寻找师姐。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    blocked = client.post(endpoint + "/confirm", json=data)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "completion_blocked"
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
    confirmed = client.post(endpoint + "/confirm", json=data)
    assert confirmed.status_code == 201, confirmed.text
    record = confirmed.json()
    assert client.post(endpoint + "/confirm", json=data).json()["id"] == record["id"]
    export = f"{endpoint}/{record['id']}/export"
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
    assert not client.get(endpoint).json()["latest"]["current"]
    with ZipFile(BytesIO(client.get(export).content)) as archive:
        assert chapter["content"] in archive.read("正文.md").decode()


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
