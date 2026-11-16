"""The scope browser fixture must never register against daily or provider data."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from tempfile import TemporaryDirectory
import pytest
from fastapi import FastAPI
from ainovel.api import create_app
from ainovel.settings import Settings, Provider

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/e2e_prepared_scope_fixture.py"
spec = spec_from_file_location("private_scope_e2e_fixture", SCRIPT)
fixture = module_from_spec(spec)
spec.loader.exec_module(fixture)


def safe(location):
    return Settings(
        Path(location),
        {
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
        {"openai": Decimal("20"), "deepseek": Decimal("80")},
        test_mode=True,
        port=8897,
    )


@pytest.mark.parametrize(
    "bad", ["not_test", "different_data", "wrong_directory", "live_port", "provider_url", "provider_key"]
)
def test_fixture_rejects_nonowned_settings(bad, tmp_path):
    with TemporaryDirectory(prefix="ainovel-browser-tests-") as location:
        settings = safe(location)
        if bad == "not_test":
            settings.test_mode = False
        elif bad == "different_data":
            settings.data_dir = tmp_path
        elif bad == "wrong_directory":
            location = str(tmp_path)
            settings.data_dir = tmp_path
        elif bad == "live_port":
            settings.port = 8766
        elif bad == "provider_url":
            settings.providers["offline"] = replace(
                settings.providers["offline"], base_url="https://api.deepseek.com"
            )
        elif bad == "provider_key":
            settings.providers["offline"] = replace(settings.providers["offline"], key="real-looking-key")
        app = FastAPI()
        with pytest.raises(RuntimeError, match="owned disposable"):
            fixture.register_prepared_scope_fixture(app, settings, location)
        assert all(getattr(r, "path", "") != "/test/prepared-scope/fixtures" for r in app.routes)


def test_production_create_app_never_registers_fixture(tmp_path):
    app = create_app(safe(tmp_path))
    assert all("/test/prepared-scope" not in getattr(r, "path", "") for r in app.routes)
