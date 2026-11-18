from contextlib import closing
from hashlib import sha256
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ainovel import cloud_sync_server
from ainovel.cloud_ledger import LEDGER_HEADER
from ainovel.cloud_sync import SyncStore, create_sync_app
from ainovel.cloud_sync_backup import create_backup, restore_backup, verify_backup
from test_cloud_sync import ADMIN, WORK, grant, parent, push


@pytest.mark.parametrize(
    ("argv", "problem"),
    [
        (["--host", "0.0.0.0"], "非回环地址必须配置 TLS"),
        (["--host", "::"], "非回环地址必须配置 TLS"),
        (["--host", "sync.example.com"], "明确的 IPv4 或 IPv6"),
        (["--port", "65536"], "监听端口"),
        (["--tls-keyfile", "unused.pem"], "TLS 证书和私钥须同时提供"),
    ],
)
def test_invalid_transport_stops_before_opening_storage(argv, problem, monkeypatch, capsys):
    monkeypatch.setattr(
        cloud_sync_server, "sync_app_from_env", lambda: pytest.fail("unexpected storage access")
    )
    with pytest.raises(SystemExit) as stopped:
        cloud_sync_server.main(argv)
    assert stopped.value.code == 2
    assert problem in capsys.readouterr().err


def test_invalid_tls_material_stops_before_opening_storage(tmp_path, monkeypatch, capsys):
    certificate = tmp_path / "invalid-certificate.pem"
    private_key = tmp_path / "invalid-private-key.pem"
    certificate.write_text("not a certificate", encoding="utf-8")
    private_key.write_text("local synthetic key content", encoding="utf-8")
    monkeypatch.setattr(
        cloud_sync_server, "sync_app_from_env", lambda: pytest.fail("unexpected storage access")
    )
    with pytest.raises(SystemExit) as stopped:
        cloud_sync_server.main(
            ["--host", "0.0.0.0", "--tls-certfile", str(certificate), "--tls-keyfile", str(private_key)]
        )
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert "TLS 文件" in error
    assert str(private_key) not in error and "synthetic key" not in error


def test_local_loopback_uses_isolated_sync_factory_without_proxy_headers(tmp_path, monkeypatch):
    ledger = tmp_path / "sync.sqlite"
    monkeypatch.setenv("AINOVEL_SYNC_DB", str(ledger))
    monkeypatch.setenv("AINOVEL_SYNC_PROVISION_TOKEN", "loopback-test-only-provision-value")
    monkeypatch.setenv("AINOVEL_SYNC_BROWSER_ORIGINS", "https://writer.example")

    def verify(server):
        config = server.config
        assert config.host == "127.0.0.1"
        assert config.port == 41234
        assert config.ssl is None
        assert not config.proxy_headers and not config.access_log
        with TestClient(config.loaded_app) as client:
            health = client.get("/api/sync/health")
            assert health.status_code == 200 and health.json()["status"] == "ready"
            assert client.get("/api/sync/works/sample/pull").status_code == 401

    monkeypatch.setattr(cloud_sync_server.uvicorn.Server, "run", verify)
    cloud_sync_server.main(["--port", "41234"])
    assert ledger.is_file()


def test_health_checks_real_sqlite_without_creating_or_disclosing_storage(tmp_path):
    ledger = tmp_path / "sync # database.sqlite"
    saved = tmp_path / "isolated-copy.sqlite"
    with TestClient(create_sync_app(ledger, ADMIN["Authorization"].split()[1])) as client:
        assert client.get("/api/sync/health").status_code == 200
        ledger.replace(saved)
        missing = client.get("/api/sync/health")
        assert missing.status_code == 503 and not ledger.exists()
        assert missing.json() == {"code": "sync_storage", "message": "同步存储暂不可读。"}
        ledger.write_text("synthetic replacement for the database", encoding="utf-8")
        invalid = client.get("/api/sync/health")
        assert invalid.status_code == 503 and invalid.json() == missing.json()
        assert invalid.headers["Cache-Control"] == "no-store"
        bad_storage = client.post(WORK + "/devices", headers=ADMIN, json={"device_id": "desk"})
        assert bad_storage.status_code == 503 and bad_storage.json() == invalid.json()
        assert ledger.read_text(encoding="utf-8") == "synthetic replacement for the database"
        saved.replace(ledger)
        ready = client.get("/api/sync/health")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready", "format": "ainovel-sync/1"}


@pytest.mark.parametrize(
    ("statement", "endpoint"),
    [
        ("DROP TABLE sync_changes", "/pull"),
        ("ALTER TABLE sync_devices RENAME COLUMN token_sha256 TO unexpected", "/checkpoint"),
        ("DROP TABLE sync_attachments", "/attachments"),
    ],
)
def test_readiness_and_affected_requests_report_ledger_schema_damage(tmp_path, statement, endpoint):
    ledger = tmp_path / "invalid-schema.sqlite"
    with TestClient(create_sync_app(ledger, ADMIN["Authorization"].split()[1])) as client:
        device = grant(client, "desk")
        assert client.get(WORK + endpoint, headers=device).status_code == 200
        with closing(sqlite3.connect(ledger)) as tx, tx:
            tx.execute(statement)
        health = client.get("/api/sync/health")
        request = client.get(WORK + endpoint, headers=device)
        assert request.status_code == health.status_code == 503
        assert request.json() == health.json() == {"code": "sync_storage", "message": "同步存储暂不可读。"}
        assert "invalid-schema" not in health.text and "invalid-schema" not in request.text


def test_requests_leave_restore_path_available_and_recovered_service_preserves_history(tmp_path):
    ledger = tmp_path / "同步稿 # ledger.sqlite"
    parked = tmp_path / "saved-original.sqlite"
    archive = tmp_path / "restore-point.sqlite"
    content = "隔离恢复附件".encode()
    digest = sha256(content).hexdigest()
    attachment = f"{WORK}/attachments/{digest}"
    provisioning = ADMIN["Authorization"].split()[1]
    with TestClient(create_sync_app(ledger, provisioning)) as client:
        desk = grant(client, "desk")
        checkpoint = client.get(WORK + "/checkpoint", headers=desk).json()
        original = {**desk, LEDGER_HEADER: checkpoint["ledger_id"]}
        first = push(client, original, parent("saved", payload={"text": "恢复点的正文。"}))
        assert client.post(
            f"{WORK}/attachments",
            headers=original,
            json={"name": "恢复演练.txt", "sha256": digest, "size": len(content)},
        ).is_success
        assert (
            client.post(
                f"{attachment}/parts/0",
                headers={**original, "Content-Type": "application/octet-stream"},
                content=content,
            ).status_code
            == 204
        )
        assert client.post(f"{attachment}/seal", headers=original).is_success
        archive_manifest = create_backup(ledger, archive)
        parked_digest = sha256(ledger.read_bytes()).hexdigest()
        ledger.replace(parked)
        sidecars = ("-wal", "-shm", "-journal")
        for suffix in sidecars:
            source = ledger.with_name(ledger.name + suffix)
            if source.exists():
                source.replace(parked.with_name(parked.name + suffix))

        # An online device polls first. No runtime request should preempt the independent restore path.
        for response in (
            client.get(WORK + "/checkpoint", headers=original),
            client.get(WORK + "/pull", headers=original),
            client.post(WORK + "/push", headers=original, json=parent("retry-later", "saved")),
            client.get(attachment + "/content", headers=original),
            client.get("/api/sync/health"),
        ):
            assert response.status_code == 503, response.text
            assert response.json()["code"] == "sync_storage"
            assert response.headers["Cache-Control"] == "no-store"
            assert ledger.name not in response.text and provisioning not in response.text
        assert not any(ledger.with_name(ledger.name + suffix).exists() for suffix in ("", *sidecars))

        restore_backup(archive, ledger, expected_sha256=archive_manifest["sha256"])
        assert client.get("/api/sync/health").status_code == 200
        assert client.get(WORK + "/checkpoint", headers=original).status_code == 401

    with TestClient(create_sync_app(ledger, provisioning)) as recovered:
        new_desk = grant(recovered, "desk")
        new_checkpoint = recovered.get(WORK + "/checkpoint", headers=new_desk).json()
        assert new_checkpoint["cursor"] == 1 and new_checkpoint["ledger_id"] != checkpoint["ledger_id"]
        current = {**new_desk, LEDGER_HEADER: new_checkpoint["ledger_id"]}
        assert recovered.get(WORK + "/pull", headers=current).json()["changes"] == [first]
        assert recovered.get(WORK + "/receipts/saved", headers=current).json() == first
        assert recovered.get(attachment + "/content", headers=current).content == content
        assert push(recovered, current, parent("retry-later", "saved"))["sequence"] == 2
    assert sha256(parked.read_bytes()).hexdigest() == parked_digest
    assert verify_backup(archive) == archive_manifest


def test_transient_sqlite_pages_are_configured_for_memory(tmp_path):
    store = SyncStore(tmp_path / "sync.sqlite")
    with store.transaction() as tx:
        assert tx.execute("PRAGMA temp_store").fetchone()[0] == 2


def test_sync_storage_errors_do_not_mask_integrity_violations(tmp_path):
    store = SyncStore(tmp_path / "sync.sqlite")
    with pytest.raises(sqlite3.IntegrityError), store.transaction(write=True) as tx:
        tx.execute("INSERT INTO sync_works(id) VALUES('same'), ('same')")
    with store.transaction() as tx:
        assert tx.execute("SELECT count(*) FROM sync_works").fetchone()[0] == 0
