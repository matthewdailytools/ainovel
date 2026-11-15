"""Disposable browser fixtures never become product routes or touch ordinary data."""

from dataclasses import replace
from decimal import Decimal
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from ainovel.api import create_app
from ainovel.settings import Provider

SPEC = importlib.util.spec_from_file_location(
    "isolated_flow_policy_fixture", Path(__file__).resolve().parents[2] / "scripts/e2e_flow_policy_fixture.py"
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def offline(settings, location):
    return replace(
        settings,
        data_dir=Path(location),
        providers={
            "offline": Provider(
                "offline",
                "deepseek",
                "offline-fixture",
                "https://example.invalid",
                "offline-not-real",
                Decimal("2"),
                Decimal("8"),
            )
        },
        port=8892,
    )


def test_production_factory_does_not_register_flow_fixture(settings):
    assert not any(route.path.startswith("/test/flow-policy") for route in create_app(settings).routes)


@pytest.mark.parametrize("change", ["production", "different_path", "not_temporary", "real_provider"])
def test_guard_rejects_non_isolated_inputs_before_reading_app(settings, tmp_path, change):
    with TemporaryDirectory(prefix="ainovel-browser-tests-") as location:
        cfg = offline(settings, location)
        path = location
        if change == "production":
            cfg = replace(cfg, test_mode=False)
        elif change == "different_path":
            path = tmp_path
        elif change == "not_temporary":
            cfg = replace(cfg, data_dir=tmp_path)
            path = tmp_path
        else:
            cfg = replace(cfg, providers=settings.providers)
        with pytest.raises(RuntimeError):
            FIXTURE.register_flow_policy_fixture(object(), cfg, path)


def test_native_seed_has_actual_adoption_maintenance_and_stable_nonce(settings):
    with TemporaryDirectory(prefix="ainovel-browser-tests-") as location:
        cfg = offline(settings, location)
        app = create_app(cfg)
        FIXTURE.register_flow_policy_fixture(app, cfg, location)
        with TestClient(app, base_url="http://127.0.0.1:8892") as client:
            request = {"client_request_id": "native-safe-flow-fixture"}
            assert client.post("/test/flow-policy/fixtures", json=request).status_code == 403
            client.headers["X-Ainovel-CSRF"] = client.get("/api/session").json()["csrf"]
            seeded = client.post("/test/flow-policy/fixtures", json=request)
            assert seeded.status_code == 201, seeded.text
            fixture = seeded.json()
            assert client.post("/test/flow-policy/fixtures", json=request).json() == fixture
            proof = client.get(f"/test/flow-policy/fixtures/{fixture['fixture_id']}/proof").json()
            assert all(proof["protected"].values()) and proof["old_calls_unchanged"]
            assert proof["current"]["chapter"]["revision"] == 1
            assert (
                proof["current"]["candidate"]["status"]
                == proof["current"]["structure"]["status"]
                == "accepted"
            )
            assert len(proof["current"]["receipts"]) == 2
            assert proof["current"]["task"]["result"]["structure_history"][-1]["policy"] == "author_confirmed"
            preview = client.get(f"/api/tasks/{fixture['parent_id']}/continue-flow-policy-preview")
            assert preview.status_code == 200, preview.text
            assert preview.json()["budget"]["remaining_run_limit"] == 4.9
            assert proof["transport_sends"] == []
