from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from ainovel.api import create_app
from ainovel.backups import unpack_verified
from ainovel.db import Database, dumps, uid
from ainovel.desktop_sync import DesktopSync, _network
from ainovel.domain import Domain
from ainovel.manuscript_exchange import PortableEvidence, export_manuscript, import_manuscript
from ainovel.workflow_archive import (
    export_workflow_history,
    validate_workflow_history,
    workflow_object,
)
from ainovel.workflow_inspection import _item, _sha
from test_workflow_archive import _members, _send_reports
from test_workflow_cloud import _complete, _grant, loopback as loopback_fixture

loopback = loopback_fixture
EXCERPT = "合成参考选段，只应留在本机。"


@pytest.fixture
def legacy(client, domain, story):
    branch = story["branch_id"]
    api = f"/api/branches/{branch}"
    inspection = api + "/workflow-inspections"
    completion = api + "/book-completion"
    chapter = domain.save(story["chapter_id"], 0, "学徒把铜铃交给师姐。")
    baseline = export_manuscript(domain, branch)
    _, passed = _send_reports(client, inspection, chapter)
    delivered = _complete(client, completion, chapter)
    zip_path = completion + f"/{delivered['id']}/export"
    zip_files = _members(client.get(zip_path))
    with domain.db.connect(True) as tx:
        frozen = json.loads(
            tx.execute("SELECT payload FROM artifacts WHERE id=?", (passed["id"],)).fetchone()[0]
        )
        item = frozen["items"][7]
        basis = {
            **item["basis"],
            "card_references": [
                {
                    "session_id": "fixture-session",
                    "card_id": "fixture-card",
                    "revision": 1,
                    "artifact_id": "fixture-writing",
                    "chapter_id": chapter["id"],
                    "chapter_revision": chapter["revision"],
                    "source": {
                        "title": "合成参考",
                        "book_id": "fixture-book",
                        "unit_id": "fixture-unit",
                        "file_hash": sha256(EXCERPT.encode()).hexdigest(),
                        "start": 0,
                        "end": len(EXCERPT),
                        "quote": EXCERPT,
                    },
                }
            ],
        }
        item.update(
            basis=basis,
            basis_sha256=_item(
                "08",
                item["summary"],
                basis,
                manifest=frozen["chapters"],
                scope=frozen["scope"],
            )["basis_sha256"],
        )
        frozen["basis_sha256"] = _sha(
            [
                frozen["scope"],
                frozen["scope_id"],
                [row["basis_sha256"] for row in frozen["items"]],
            ]
        )
        # Install the exact older signed fixture without changing application-time behavior.
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(frozen), passed["id"]))
        history = export_workflow_history(tx, branch)
    validate_workflow_history(history, branch, {row.id: row for row in baseline.revisions})
    document = next(
        item for item in client.get(api + "/manuscript-evidence").json() if item["id"] == passed["id"]
    )
    assert EXCERPT in document["body"]
    return SimpleNamespace(
        branch=branch,
        api=api,
        chapter=chapter,
        baseline=baseline,
        history=history,
        document=document,
        report_id=passed["id"],
        clean_ids={entry.id for entry in history.inspections if entry.id != passed["id"]},
        report_path=inspection + "/" + passed["id"],
        zip_path=zip_path,
        zip_files=zip_files,
    )


def assert_local_history(api, legacy):
    detail = api.get(legacy.report_path)
    assert detail.status_code == 200, detail.text
    frozen = next(entry for entry in legacy.history.inspections if entry.id == legacy.report_id).report
    assert detail.json()["basis_sha256"] == frozen.basis_sha256
    assert detail.json()["items"][7]["basis"] == frozen.items[7].basis
    assert _members(api.get(legacy.zip_path)) == legacy.zip_files


def test_legacy_native_and_imported_reports_stay_readable_without_leaking_into_default_export(
    client,
    settings,
    tmp_path,
    legacy,
):
    def blocked(api):
        for suffix in ("/manuscript-package", "/manuscript-backup"):
            rejected = api.get(legacy.api + suffix)
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["code"] == "manuscript_reference_scope"
            assert EXCERPT not in rejected.text

    blocked(client)
    assert_local_history(client, legacy)
    backup = Path(client.post("/api/backups").json()["path"])
    directory = tmp_path / "recovered-private-backup"
    unpack_verified(backup, directory)
    with TestClient(create_app(replace(settings, data_dir=directory))) as recovered:
        blocked(recovered)
        assert_local_history(recovered, legacy)

    for family, value in (
        ("formal", {"workflow_history": legacy.history}),
        ("projection", {"evidence": [PortableEvidence.model_validate(legacy.document)]}),
    ):
        with TestClient(create_app(replace(settings, data_dir=tmp_path / family))) as imported:
            imported.headers["X-Ainovel-CSRF"] = imported.get("/api/session").json()["csrf"]
            pack = legacy.baseline.model_copy(update=value)
            result = imported.post("/api/manuscript-packages/import", json=pack.model_dump())
            assert result.status_code == 201, result.text
            blocked(imported)
            if family == "formal":
                assert_local_history(imported, legacy)
            else:
                assert imported.get(legacy.api + "/manuscript-evidence").json() == [legacy.document]


def test_desktop_retires_unsafe_outbox_and_cloud_rejects_older_client_payloads(
    client,
    domain,
    tmp_path,
    loopback,
    legacy,
):
    branch = legacy.branch
    receiving_domain = Domain(Database(tmp_path / "clean-receiver.sqlite"))
    import_manuscript(receiving_domain, legacy.baseline.model_copy(update={"chapters": [], "revisions": []}))
    origin, _ = loopback
    writer, reader = _grant(loopback, branch, "writer"), _grant(loopback, branch, "reader")
    base = f"/api/sync/works/{branch}"
    emitted = []

    def transport(origin, token, method, target, payload, *, ledger_id=None):
        if method == "POST" and target.endswith("/push"):
            emitted.append(payload["object_id"])
            assert EXCERPT not in dumps(payload)
        return _network(origin, token, method, target, payload, ledger_id=ledger_id)

    left, right = DesktopSync(domain, transport), DesktopSync(receiving_domain)
    left.enable(branch, origin, writer)
    right.enable(branch, origin, reader)
    unsafe_report = next(entry for entry in legacy.history.inspections if entry.id == legacy.report_id)
    completion = legacy.history.completions[0]
    previous = {
        f"workflow-inspection:{unsafe_report.id}": workflow_object(branch, unsafe_report),
        f"workflow-completion:{completion.id}": workflow_object(branch, completion),
        f"evidence:{legacy.document['id']}": {
            "kind": "evidence",
            "format": "ainovel-manuscript-sync/1",
            "document": legacy.document,
        },
    }

    def request(object_id, payload):
        return {
            "object_id": object_id,
            "change_id": uid(),
            "parent_revision": None,
            "payload": payload,
            "tombstone": False,
        }

    with domain.db.connect(True) as tx:
        for object_id, value in previous.items():
            pending = request(object_id, value)
            tx.execute(
                "INSERT INTO desktop_sync_outbox VALUES(?,?,?,?,?,NULL)",
                (
                    branch,
                    pending["change_id"],
                    object_id,
                    dumps(pending),
                    sha256(dumps(value).encode()).hexdigest(),
                ),
            )
    changed = domain.save(legacy.chapter["id"], legacy.chapter["revision"], "保持师姐已经归还铜铃的正文。")
    done = left.run(branch, origin, writer)
    deferred = {row["object_id"]: row["reason"] for row in done["deferred"]}
    assert set(previous) <= set(deferred)
    assert all("历史体检含参考选段原句" in deferred[key] for key in previous)
    assert set(previous).isdisjoint(emitted)
    assert {f"workflow-inspection:{identifier}" for identifier in legacy.clean_ids} <= set(emitted)
    with domain.db.connect() as tx:
        assert not tx.execute("SELECT 1 FROM desktop_sync_outbox WHERE branch_id=?", (branch,)).fetchone()
    right.run(branch, origin, reader)
    assert receiving_domain.chapter(changed["id"])["content"] == changed["content"]
    received = export_manuscript(receiving_domain, branch).workflow_history.inspections
    assert {entry.id for entry in received} == legacy.clean_ids
    assert_local_history(client, legacy)

    with httpx.Client(trust_env=False, headers={"Authorization": f"Bearer {writer}"}) as older:
        for key, value in previous.items():
            response = older.post(origin + base + "/push", json=request(key, value))
            assert response.status_code == 409, response.text
            expected = (
                "sync_workflow_dependency"
                if value["kind"] == "workflow-completion"
                else "sync_workflow_reference_scope"
            )
            assert response.json()["code"] == expected
            assert EXCERPT not in response.text
        page = older.get(origin + base + "/pull?after=0&limit=100").json()
        assert all(row["object_id"] not in previous for row in page["changes"])
