from dataclasses import replace
from json import dumps, loads
from socket import socket
from threading import Event, Thread

from fastapi.testclient import TestClient
import httpx
import pytest
from uvicorn import Config, Server

from ainovel.api import create_app
from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, _network
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.workflow_archive import workflow_object
from test_workflow_archive import _members, _send_reports
from test_workflow_inspection import author, source


@pytest.fixture
def loopback(tmp_path):
    ready = Event()
    secret = "provision-for-workflow-test-12345678"

    class LocalServer(Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)
            ready.set()

    cloud = create_sync_app(tmp_path / "cloud-workflow.sqlite", secret)
    server = LocalServer(Config(cloud, log_level="error", ws="none"))
    with socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            assert ready.wait(10) and server.started
            yield origin, secret
        finally:
            server.should_exit = True
            worker.join(10)
            assert not worker.is_alive()


def _grant(loopback, work, device):
    origin, secret = loopback
    response = _network(origin, secret, "POST", f"/api/sync/works/{work}/devices", {"device_id": device})
    return response["token"]


def _complete(client, endpoint, chapter):
    response = client.post(
        endpoint + "/confirm",
        json={
            "basis_sha256": client.get(endpoint).json()["basis_sha256"],
            "read_note": "作者已通读保存稿。",
            "ending": {"note": "归还铜铃解决主冲突。", "sources": [source(chapter)]},
            "characters": {"note": "师姐与学徒重逢。"},
            "promises": {"note": "疑问都在末章交待。"},
            "intro": "学徒循着旧铃寻找师姐。",
            "spoiler_scope": "opening",
            "sample_chapter_ids": [chapter["id"]],
            "materials_reviewed": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_formal_archive_is_immutable_and_pulls_into_independent_desktop_history(
    client, domain, settings, story, tmp_path, loopback
):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "😀学徒找到师姐，归还了铜铃。")
    baseline = export_manuscript(domain, branch)
    other_settings = replace(settings, data_dir=tmp_path / "archive-receiver")
    target = Domain(Database(other_settings.data_dir / "manuscripts.sqlite"))
    import_manuscript(target, baseline)
    inspection = f"/api/branches/{branch}/workflow-inspections"
    completion = f"/api/branches/{branch}/book-completion"
    red, book = _send_reports(client, inspection, chapter)
    completed = _complete(client, completion, chapter)
    pointer = completion + f"/{completed['id']}/export"
    original_zip = _members(client.get(pointer))
    archive = export_manuscript(domain, branch).workflow_history
    red_entry = next(entry for entry in archive.inspections if entry.id == red["id"])
    passing = next(entry for entry in archive.inspections if entry.id == book["id"])
    frozen = archive.completions[0]
    origin, _ = loopback
    authored = _grant(loopback, branch, "writer")
    receiving = _grant(loopback, branch, "reader")
    sync = f"/api/sync/works/{branch}"

    def raw_push(entry, change_id, **override):
        payload = workflow_object(branch, entry)
        body = {
            "change_id": change_id,
            "object_id": f"{payload['kind']}:{entry.id}",
            "parent_revision": None,
            "payload": payload,
            "tombstone": False,
            **override,
        }
        with httpx.Client(trust_env=False) as request:
            response = request.post(
                origin + sync + "/push", headers={"Authorization": f"Bearer {authored}"}, json=body
            )
        result = response.json()
        if not response.is_success:
            raise DomainError(result["code"], result["message"], response.status_code)
        return result

    with pytest.raises(DomainError, match="缺原章稿修订") as missing_revision:
        raw_push(passing, "missing-source")
    assert missing_revision.value.code == "sync_workflow_dependency"
    with pytest.raises(DomainError, match="缺原全书体检单") as missing_inspection:
        raw_push(frozen, "missing-inspection")
    assert missing_inspection.value.code == "sync_workflow_dependency"

    dropped = []

    def drop_after_completion(origin, token, method, target, payload):
        reply = _network(origin, token, method, target, payload)
        if (
            target.endswith("/push")
            and payload["object_id"].startswith("workflow-completion:")
            and not dropped
        ):
            dropped.append(payload)
            raise DomainError("sync_transport", "测试：交付回执丢失。", 502)
        return reply

    left, right = DesktopSync(domain, drop_after_completion), DesktopSync(target)
    left.enable(branch, origin, authored)
    right.enable(branch, origin, receiving)
    with pytest.raises(DomainError, match="回执丢失"):
        left.run(branch, origin, authored)
    assert left._state(branch)["pending"] >= 1
    assert left.run(branch, origin, authored)["pending"] == 0
    lost = dropped[0]
    replay = _network(origin, authored, "POST", sync + "/push", lost)
    assert replay["change_id"] == lost["change_id"]
    events = _network(origin, authored, "GET", sync + "/pull?after=0&limit=50")["changes"]
    actual = [row for row in events if row["object_id"].startswith("workflow-")]
    assert [row["object_id"] for row in actual] == [
        f"workflow-inspection:{red['id']}",
        f"workflow-inspection:{book['id']}",
        f"workflow-completion:{completed['id']}",
    ]
    assert actual[1]["payload"]["entry_json"] == workflow_object(branch, passing)["entry_json"]
    first_source = passing.chapter_bindings[0].revision_id
    assert (
        next(row for row in events if row["object_id"] == f"revision:{first_source}")["sequence"]
        < actual[0]["sequence"]
    )
    with pytest.raises(DomainError, match="不能改写") as modified:
        raw_push(passing.model_copy(update={"created": passing.created + 1}), "edited-history")
    assert modified.value.code == "sync_workflow"
    with pytest.raises(DomainError, match="不能删除"):
        raw_push(passing, "deleted-history", payload=None, tombstone=True)
    claimed_branch = {**workflow_object(branch, passing), "branch_id": "different-work"}
    with pytest.raises(DomainError, match="归属不匹配"):
        raw_push(passing, "different-work-record", payload=claimed_branch)
    scrambled = workflow_object(branch, passing)
    scrambled["entry_json"] = dumps(loads(scrambled["entry_json"]), sort_keys=True, ensure_ascii=False)
    with pytest.raises(DomainError, match="检查依据"):
        raw_push(passing, "sorted-nested-basis", payload=scrambled)
    wrong_m6 = frozen.manifest.model_copy(update={"inspection_id": red_entry.id})
    with pytest.raises(DomainError, match="通过体检单"):
        raw_push(frozen.model_copy(update={"id": "bad-completion", "manifest": wrong_m6}), "nonpassing-m6")
    wrong_basis = frozen.manifest.model_copy(update={"story_revision": frozen.manifest.story_revision + 1})
    with pytest.raises(DomainError, match="冻结完本依据"):
        raw_push(
            frozen.model_copy(update={"id": "changed-basis", "manifest": wrong_basis}), "unverified-basis"
        )

    with TestClient(create_app(other_settings)) as other:
        other.headers["X-Ainovel-CSRF"] = other.get("/api/session").json()["csrf"]
        local_preview = other.get(inspection).json()["preview"]
        local = other.post(inspection, json={"basis_sha256": local_preview["basis_sha256"]}).json()
        assert not right.run(branch, origin, receiving)["deferred"]
        assert other.get(inspection).json()["latest"]["id"] == local["id"]
        cloud_report = other.get(inspection + "/" + red["id"]).json()
        assert cloud_report["source_device_id"] == "writer" and cloud_report["origin"] == "cloud"
        assert not cloud_report["current"]
        original = next(item for item in cloud_report["items"] if item["check_id"] == "03")
        assert original["status"] == "red" and original["author_review"]["evidence"] == [source(chapter)]
        assert original["current_status"] == "outdated"
        restored = other.get(completion + f"/history/{completed['id']}").json()
        assert restored["origin"] == "cloud" and restored["source_device_id"] == "writer"
        assert not restored["current"] and other.get(completion).json()["latest"] is None
        assert _members(other.get(pointer)) == original_zip
        with target.db.connect() as tx:
            assert (
                tx.execute("SELECT id FROM artifacts WHERE kind='workflow_inspection'").fetchall()[0][0]
                == local["id"]
            )
            assert not tx.execute("SELECT 1 FROM artifacts WHERE kind='book_completion'").fetchone()
        assert other.get(inspection + "/" + book["id"]).json()["revalidation_request"]
        target.save(chapter["id"], chapter["revision"], "接收后由另一台设备改稿。")
        assert _members(other.get(pointer)) == original_zip


def test_oversized_workflow_archive_defers_without_blocking_unrelated_chapters(
    client, domain, story, loopback
):
    branch = story["branch_id"]
    opener = domain.save(story["chapter_id"], 0, "学徒沿铃声找到师姐。")
    chapter_ids = [opener["id"]]
    for position in (2, 3):
        chapter = domain.create_chapter(branch, f"第 {position} 章")
        domain.save(chapter["id"], 0, "学徒沿着河岸继续向前。" * 19_000)
        chapter_ids.append(chapter["id"])
    inspection = f"/api/branches/{branch}/workflow-inspections"
    completion = f"/api/branches/{branch}/book-completion"
    preview = client.get(inspection).json()["preview"]
    response = client.post(
        inspection,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                author(item["check_id"], opener) for item in preview["items"] if item["status"] == "pending"
            ],
        },
    )
    assert response.status_code == 201, response.text
    completed = _complete(client, completion, opener)
    token = _grant(loopback, branch, "large-work")
    origin, _ = loopback
    desktop = DesktopSync(domain)
    desktop.enable(branch, origin, token)
    state = desktop.run(branch, origin, token)
    deferred = {row["object_id"]: row["reason"] for row in state["deferred"]}
    assert "超过" in deferred[f"workflow-completion:{completed['id']}"]
    assert not state["pending"]
    events = _network(origin, token, "GET", f"/api/sync/works/{branch}/pull?after=0&limit=50")
    object_ids = {row["object_id"] for row in events["changes"]}
    assert {f"chapter:{identifier}" for identifier in chapter_ids} <= object_ids
    assert f"workflow-inspection:{response.json()['id']}" in object_ids
    assert f"workflow-completion:{completed['id']}" not in object_ids
    assert client.get(completion).json()["latest"]["id"] == completed["id"]
