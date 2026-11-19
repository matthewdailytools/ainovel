import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from ainovel import manuscript_backup
from ainovel.api import create_app
from ainovel.manuscript_backup import BACKUP_IMPORT_PATH, BACKUP_MEDIA_TYPE, BackupReader
from ainovel.manuscript_exchange import export_manuscript
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_project_brief import BRIEF, IDEA
from test_reference_flow import reference as reference_fixture, update
from test_reference_sync import archive, card
from test_workflow_archive import _members, _send_reports
from test_workflow_cloud import _complete


reference = reference_fixture
BINARY = {"Content-Type": "application/octet-stream"}


def framed(rows):
    return raw_frames(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") for row in rows
    )


def raw_frames(rows):
    parts = [b"ainovel-web-backup/1\n"]
    digests = []
    for record in rows:
        digest = sha256(record).hexdigest().encode("ascii")
        parts.append(digest + b" " + record + b"\n")
        digests.append(digest)
    parts.append(b"sha256 " + sha256(b"".join(digests)).hexdigest().encode("ascii") + b"\n")
    return b"".join(parts)


def _identify(client):
    client.headers["X-Ainovel-CSRF"] = client.get("/api/session").json()["csrf"]


def test_portable_restore_state_reinstalls_native_facts_and_preserves_cleared_targets(
    client, settings, domain, story
):
    chapter_id = story["chapter_id"]
    branch = f"/api/branches/{story['branch_id']}"
    quote = "𠮷所记😀\r\n铜铃"
    saved = domain.save(chapter_id, 0, f"前言{quote}已归还。")
    created = client.post(
        "/api/facts",
        json={
            "chapter_id": chapter_id,
            "source_revision": saved["revision"],
            "subject": "学徒",
            "kind": "event",
            "statement": "归还了铜铃。",
            "evidence": quote,
        },
    )
    assert created.status_code == 201, created.text
    inspection = branch + "/workflow-inspections"
    configured = client.put(
        inspection + "/length-targets",
        json={"scope": "book", "targets": {"min_chars": 50, "expected_chapters": 1}},
    )
    assert configured.is_success, configured.text
    download = client.get(branch + "/manuscript-backup")
    assert download.is_success, download.text
    rows = [json.loads(line.split(b" ", 1)[1]) for line in download.content.splitlines()[1:-1]]
    proof = next(row[2]["evidence"] for row in rows if row[:2] == ["restore_state.facts", "item"])
    assert proof["start"] == 2 and proof["end"] == 12
    assert proof["quote_sha256"] == sha256(quote.encode()).hexdigest()
    rows.append(
        [
            "restore_state.length_targets",
            "item",
            {
                "scope": "chapter",
                "scope_id": chapter_id,
                "targets": None,
            },
        ]
    )

    with TestClient(create_app(replace(settings, data_dir=settings.data_dir / "restore-state"))) as receiver:
        _identify(receiver)
        wrong = deepcopy(rows)
        evidence = next(row[2]["evidence"] for row in wrong if row[:2] == ["restore_state.facts", "item"])
        evidence["start"] = 3
        rejected = receiver.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(wrong))
        assert rejected.status_code == 400 and rejected.json()["code"] == "manuscript_restore_state"
        archived_rows = deepcopy(rows)
        next(row[2] for row in archived_rows if row[:2] == ["chapters", "item"])["archived"] = 1
        rejected = receiver.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(archived_rows))
        assert rejected.status_code == 400 and rejected.json()["code"] == "manuscript_restore_state"
        assert receiver.get("/api/novels").json() == []
        accepted = receiver.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(rows))
        assert accepted.status_code == 201, accepted.text
        facts = receiver.get(branch + "/facts").json()
        assert len(facts) == 1
        assert {
            key: facts[0][key] for key in ("source_revision", "evidence", "evidence_start", "evidence_end")
        } == {
            "source_revision": saved["revision"],
            "evidence": quote,
            "evidence_start": proof["start"],
            "evidence_end": proof["end"],
        }
        report = receiver.get(inspection).json()["preview"]
        assert next(row for row in report["items"] if row["check_id"] == "09")["status"] == "red"
        state = receiver.get(branch + "/manuscript-package").json()["restore_state"]
        assert state["facts"][0]["evidence"] == proof
        assert state["length_targets"] == [
            {
                "scope": "book",
                "scope_id": None,
                "targets": {"min_chars": 50, "max_chars": None, "expected_chapters": 1},
            },
            {"scope": "chapter", "scope_id": chapter_id, "targets": None},
        ]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE chapters SET archived=1 WHERE id=?", (chapter_id,))
    assert client.get(branch + "/facts").json()[0]["evidence"] == quote
    assert client.get(branch + "/manuscript-package").json()["restore_state"]["facts"] == []


def test_checkpoints_keep_exact_sources_and_portable_links_through_independent_backup(
    client, settings, domain, story
):
    branch_id = story["branch_id"]
    branch = f"/api/branches/{branch_id}"
    first_id = story["chapter_id"]
    second_id = domain.create_chapter(branch_id, "第二卷章序")["id"]
    original = domain.save(first_id, 0, "😀卷末，学徒把铜铃交给师姐。")
    second = domain.save(second_id, 0, "师姐来到旧宅门前。")
    project = client.put(
        branch + "/conception/project-brief",
        json={
            "base_revision": 0,
            "idempotency_key": "portable-checkpoint-fixture",
            "idea": IDEA,
            "brief": BRIEF,
        },
    )
    assert project.is_success, project.text
    setting_id = setting_confirmation(client, branch_id)
    plan = {
        "summary": "查找铃声来源。",
        "volumes": [
            {
                "id": f"volume-{number}",
                "title": f"第 {number} 卷",
                "summary": "追踪铜铃的去向。",
                "units": [
                    {
                        "id": f"unit-{number}",
                        "title": "去向",
                        "summary": "寻找手持铜铃的人。",
                        "chapters": [chapter_plan(chapter, "找到师姐")],
                    }
                ],
            }
            for number, chapter in enumerate((first_id, second_id), 1)
        ],
    }
    outline = put_and_confirm(
        client,
        branch + "/outline-workspace",
        {"base_revision": 0, "confirmation_id": setting_id, "plan": plan},
    )["confirmed"]
    route = branch + "/work-snapshots"
    inspection_route = branch + "/workflow-inspections"
    inspected = client.get(inspection_route).json()["preview"]
    report = client.post(inspection_route, json={"basis_sha256": inspected["basis_sha256"]})
    assert report.status_code == 201, report.text
    checkpoints = {}
    for scope, scope_id in (("book", None), ("volume", "volume-1"), ("chapter", first_id)):
        saved = client.post(
            route, json={"label": f"原始恢复点 {scope}", "scope": scope, "scope_id": scope_id}
        )
        assert saved.status_code == 201, saved.text
        checkpoints[scope] = saved.json()
    assert checkpoints["book"]["reports"]["workflow_inspection"] == report.json()["id"]
    changed = domain.save(first_id, original["revision"], "改稿后，学徒仍在门外。")
    download = client.get(branch + "/manuscript-backup")
    assert download.status_code == 200, download.text
    reader = BackupReader()
    reader.feed(download.content)
    package = reader.package()
    entries = {row.id: row.model_dump() for row in package.snapshot_history.entries}
    assert checkpoints["chapter"]["id"] not in entries
    book_id, volume_id = [checkpoints[scope]["id"] for scope in ("book", "volume")]
    book = json.loads(entries[book_id]["body"])
    volume = json.loads(entries[volume_id]["body"])
    assert book["scoped_chapter_ids"] == [first_id, second_id]
    assert volume["scoped_chapter_ids"] == [first_id]
    assert volume["source_manifest_sha256"] == checkpoints["volume"]["manifest_sha256"]
    assert volume["shared"] == {
        "project_id": project.json()["id"],
        "settings_id": setting_id,
        "outline_id": outline["id"],
    }
    assert volume["reports"] == {"workflow_inspection": report.json()["id"]}
    assert original["content"] not in entries[volume_id]["body"]

    rows = [json.loads(line.split(b" ", 1)[1]) for line in download.content.split(b"\n")[1:-2]]
    item_index = next(
        index
        for index, row in enumerate(rows)
        if row[:2] == ["snapshot_history.entries", "item"] and row[2]["id"] == volume_id
    )
    with TestClient(
        create_app(replace(settings, data_dir=settings.data_dir / "portable-checkpoints"))
    ) as target:
        _identify(target)
        unrelated = target.post("/api/novels", json={"title": "独立存档保留作品"}).json()["id"]
        for field, value in (
            ("branch_id", "foreign-branch"),
            ("scoped_chapter_ids", [second_id]),
            ("shared", {**volume["shared"], "outline_id": "missing-outline"}),
            ("chapters", [{**volume["chapters"][0], "sha256": "0" * 64}, volume["chapters"][1]]),
            ("workspace", {"draft": "不会作为归档字段写入"}),
        ):
            forged = deepcopy(rows)
            body = json.dumps({**volume, field: value}, ensure_ascii=False)
            forged[item_index][2].update(body=body, sha256=sha256(body.encode()).hexdigest())
            rejected = target.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(forged))
            assert rejected.status_code == 400 and rejected.json()["code"] == "snapshot_source"
        corrupted = deepcopy(rows)
        corrupted[item_index][2]["body"] += " "
        rejected = target.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(corrupted))
        assert rejected.status_code == 400 and rejected.json()["code"] == "snapshot_source"
        repeated = [*rows, rows[item_index]]
        rejected = target.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(repeated))
        assert rejected.status_code == 400 and rejected.json()["code"] == "snapshot_source"
        assert [row["id"] for row in target.get("/api/novels").json()] == [unrelated]

        accepted = target.post(BACKUP_IMPORT_PATH, headers=BINARY, content=download.content)
        assert accepted.status_code == 201, accepted.text
        archived = branch + "/portable-checkpoints"
        assert {row["id"] for row in target.get(archived).json()} == set(entries)
        assert target.get(f"{archived}/{volume_id}").json() == volume
        assert target.get(f"{route}/{volume_id}/preview").status_code == 404
        chapter_route = f"{archived}/{volume_id}/chapters/"
        assert target.get(chapter_route + first_id).json() == {
            "title": original["title"],
            "snapshot": {"revision": original["revision"], "content": original["content"]},
            "current": {"revision": changed["revision"], "content": changed["content"], "archived": 0},
        }
        assert target.get(chapter_route + second_id).status_code == 404
        assert (
            target.get(f"{archived}/{book_id}/chapters/{second_id}").json()["snapshot"]["content"]
            == second["content"]
        )
        assert target.get(f"{branch}/workflow-inspections/{report.json()['id']}").is_success
        reexport = target.get(branch + "/manuscript-backup")
        assert reexport.status_code == 200, reexport.text
        read_again = BackupReader()
        read_again.feed(reexport.content)
        returned = {row.id: row.model_dump() for row in read_again.package().snapshot_history.entries}
        assert {key: returned[key] for key in entries} == entries


def test_large_backup_replaces_neither_existing_work_nor_the_ordinary_upload_limit(
    client, settings, domain, story
):
    branch = f"/api/branches/{story['branch_id']}"
    lead = "渡口敲过三巡更，学徒依次收好铜铃和写满行踪的纸。"
    names = []
    for number in range(1, 37):
        chapter = (
            domain.chapter(story["chapter_id"])
            if number == 1
            else domain.create_chapter(story["branch_id"], f"第{number}章")
        )
        body = f"序号{number}\n" + lead * 9900 + f"\n第{number}章底稿。"
        names.append((chapter["id"], body))
        assert domain.save(chapter["id"], 0, body)["revision"] == 1
    downloaded = client.get(branch + "/manuscript-backup")
    assert downloaded.status_code == 200
    assert downloaded.headers["Content-Type"] == BACKUP_MEDIA_TYPE
    assert downloaded.headers["Content-Disposition"].endswith('.ainovel-backup"')
    assert downloaded.headers["Cache-Control"] == "no-store"
    assert int(downloaded.headers["Content-Length"]) == len(downloaded.content) > 24 * 1024 * 1024
    assert (
        client.post(
            "/api/manuscript-packages/import",
            headers={"Content-Length": str(len(downloaded.content))},
            json={},
        ).status_code
        == 413
    )

    with TestClient(create_app(replace(settings, data_dir=settings.data_dir / "independent"))) as receiver:
        _identify(receiver)
        unrelated = receiver.post("/api/novels", json={"title": "原有独立作品"}).json()
        unrelated_chapter = receiver.post(
            f"/api/branches/{unrelated['branch_id']}/chapters", json={"title": "保留章"}
        ).json()
        assert receiver.put(
            f"/api/chapters/{unrelated_chapter['id']}", json={"base_revision": 0, "content": "原有稿需保留。"}
        ).is_success

        denied = receiver.post(
            BACKUP_IMPORT_PATH, headers={**BINARY, "X-Ainovel-CSRF": "wrong"}, content=downloaded.content[:90]
        )
        assert denied.status_code == 403 and denied.json()["code"] == "csrf"
        truncated = receiver.post(BACKUP_IMPORT_PATH, headers=BINARY, content=downloaded.content[:-11])
        assert truncated.status_code == 400 and truncated.json()["code"] == "manuscript_backup"
        assert [row["id"] for row in receiver.get("/api/novels").json()] == [unrelated["id"]]
        restored = receiver.post(BACKUP_IMPORT_PATH, headers=BINARY, content=downloaded.content)
        assert restored.status_code == 201, restored.text
        assert restored.json() == {"id": story["id"], "branch_id": story["branch_id"]}
        assert len(receiver.get(branch + "/chapters").json()) == 36
        assert receiver.get(f"/api/chapters/{unrelated_chapter['id']}").json()["content"] == "原有稿需保留。"
        for chapter, body in (names[0], names[-1]):
            history = f"/api/chapters/{chapter}/history"
            assert receiver.get(history).json() == client.get(history).json()
            restored_body = receiver.get(f"/api/chapters/{chapter}").json()["content"]
            assert sha256(restored_body.encode()).digest() == sha256(body.encode()).digest()
        assert receiver.get(branch + "/manuscript-backup").content == downloaded.content


def test_reader_accepts_split_utf8_and_rejects_a_verified_frame_after_the_seal(client, domain, story):
    chapter = story["chapter_id"]
    domain.save(chapter, 0, "铜铃😀\n仍在门口。")
    package = export_manuscript(domain, story["branch_id"]).model_dump(mode="json")
    entries = []
    for name, value in package.items():
        if isinstance(value, list):
            entries.append([name, "list", None])
            entries.extend([name, "item", item] for item in value)
        else:
            entries.append([name, "value", value])
    accepted = framed(entries)
    reader = BackupReader()
    for index in range(0, len(accepted), 7):
        reader.feed(accepted[index : index + 7])
    assert reader.package().model_dump() == package
    assert (
        client.post(BACKUP_IMPORT_PATH, headers=BINARY, content=accepted).json()["code"]
        == "manuscript_conflict"
    )
    frames_after_seal = client.post(
        BACKUP_IMPORT_PATH, headers=BINARY, content=accepted + accepted.splitlines()[1] + b"\n"
    )
    assert frames_after_seal.json()["code"] == "manuscript_backup"
    absent_list = next(index for index, entry in enumerate(entries) if entry[:2] == ["revisions", "list"])
    rejected = client.post(
        BACKUP_IMPORT_PATH, headers=BINARY, content=framed(entries[:absent_list] + entries[absent_list + 1 :])
    )
    assert rejected.json()["code"] == "manuscript_backup"
    wrong_parent = next(
        entry for entry in entries if entry[:2] == ["revisions", "item"] and entry[2]["revision"] == 1
    )
    wrong_parent[2]["parent_id"] = "forged-parent"
    wrong_history = client.post(BACKUP_IMPORT_PATH, headers=BINARY, content=framed(entries))
    assert wrong_history.json()["code"] == "manuscript_history"
    assert domain.chapter(chapter)["content"] == "铜铃😀\n仍在门口。"


@pytest.mark.parametrize(
    "raw",
    [
        b'["novel","value",{"title":"\xff"}]',
        b'["novel","value",{"title":"private-deep-structure","extra":'
        + b"[" * 2000
        + b"0"
        + b"]" * 2000
        + b"}]",
    ],
)
def test_invalid_utf8_and_excessive_json_depth_return_a_sanitized_error(client, raw):
    result = client.post(BACKUP_IMPORT_PATH, headers=BINARY, content=raw_frames([raw]))
    assert result.status_code == 400 and result.json()["code"] == "manuscript_backup"
    assert "private-deep-structure" not in result.text
    assert client.get("/api/novels").json() == []


def test_escaped_lone_surrogate_is_rejected_before_import(client):
    content = raw_frames(
        [
            b'["format","value","ainovel-manuscript/1"]',
            b'["novel","value",{"id":"bad","title":"\\ud800","brief":"","created":1}]',
            b'["branch","value",{"id":"bad-branch","novel_id":"bad","name":"draft","mode":"assisted","story_revision":0,"created":1}]',
            b'["chapters","list",null]',
            b'["revisions","list",null]',
        ]
    )
    result = client.post(BACKUP_IMPORT_PATH, headers=BINARY, content=content)
    assert result.status_code == 400 and result.json()["code"] == "manuscript_backup"
    assert client.get("/api/novels").json() == []


def test_stream_limit_applies_without_content_length_and_download_fails_before_sending(
    client, settings, domain, story, monkeypatch
):
    monkeypatch.setattr(manuscript_backup, "MAX_BACKUP_BYTES", 512)

    async def chunked():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://testserver"
        ) as api:
            session = (await api.get("/api/session")).json()
            api.headers["X-Ainovel-CSRF"] = session["csrf"]

            async def incomplete():
                yield b"ainovel-web-backup/1\n"
                yield b"a" * 250
                yield b"b" * 260

            result = await api.post(BACKUP_IMPORT_PATH, headers=BINARY, content=incomplete())
            assert "Content-Length" not in result.request.headers
            assert result.status_code == 413 and result.json()["code"] == "file_limit"

    asyncio.run(chunked())
    branch = story["branch_id"]
    failure = client.get(f"/api/branches/{branch}/manuscript-backup")
    assert failure.status_code == 413 and failure.json()["code"] == "file_limit"
    assert "content-disposition" not in failure.headers
    assert client.post(BACKUP_IMPORT_PATH, json={}).status_code == 415

    domain.save(story["chapter_id"], 0, "铜铃带回渡口。" * 200)
    monkeypatch.setattr(
        manuscript_backup, "_prepare_download", lambda *_: pytest.fail("large source reached serialization")
    )
    failure = client.get(f"/api/branches/{branch}/manuscript-backup")
    assert failure.status_code == 413 and failure.json()["code"] == "file_limit"


def test_backup_restores_frozen_inspection_completion_and_only_explicit_reference_excerpt(
    client, settings, domain, story, reference
):
    update(client, domain, reference, cards=[card("😀渡口")])
    branch_id = story["branch_id"]
    version = archive(domain, branch_id).versions[-1]
    branch = f"/api/branches/{branch_id}"
    saved = domain.save(story["chapter_id"], 0, "学徒把铜铃交给师姐。")
    _, inspection = _send_reports(client, branch + "/workflow-inspections", saved)
    completion = _complete(client, branch + "/book-completion", saved)
    delivery = branch + f"/book-completion/{completion['id']}/export"
    expected = _members(client.get(delivery))

    default = client.get(branch + "/manuscript-backup")
    assert default.status_code == 200
    assert "😀渡口".encode() not in default.content
    opted_in = client.get(
        branch + "/manuscript-backup", params={"include_reference_evidence_for": version.id}
    )
    assert opted_in.status_code == 200 and "😀渡口".encode() in opted_in.content

    with TestClient(
        create_app(replace(settings, data_dir=settings.data_dir / "history-receiver"))
    ) as restored:
        _identify(restored)
        result = restored.post(BACKUP_IMPORT_PATH, headers=BINARY, content=opted_in.content)
        assert result.status_code == 201, result.text
        evidence = restored.get(branch + f"/workflow-inspections/{inspection['id']}")
        assert evidence.status_code == 200
        assert evidence.json()["basis_sha256"] == inspection["basis_sha256"]
        assert _members(restored.get(delivery)) == expected
        assert "😀渡口".encode() not in restored.get(branch + "/manuscript-backup").content
        selected = restored.get(
            branch + "/manuscript-backup", params={"include_reference_evidence_for": version.id}
        )
        assert selected.status_code == 200 and "😀渡口".encode() in selected.content
