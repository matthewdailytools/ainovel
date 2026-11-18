import pytest
from fastapi.testclient import TestClient

from ainovel import cloud_sync_server


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
