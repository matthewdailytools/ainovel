"""The new same-source browser fixture refuses daily/provider settings."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from tempfile import TemporaryDirectory
import pytest
from fastapi import FastAPI
from ainovel.api import create_app
from ainovel.settings import Settings, Provider

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/e2e_author_source_recheck_fixture.py"
spec = spec_from_file_location("private_author_source_recheck_e2e_fixture", SCRIPT)
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
        port=8898,
    )


@pytest.mark.parametrize(
    "bad", ["not_test", "different_data", "wrong_directory", "live_port", "provider_url", "provider_key"]
)
def test_same_source_fixture_rejects_nonowned_settings(bad, tmp_path):
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
            settings.providers["offline"] = replace(
                settings.providers["offline"], key="not-an-offline-fixture"
            )
        app = FastAPI()
        with pytest.raises(RuntimeError, match="owned disposable"):
            fixture.register_author_source_recheck_fixture(app, settings, location)
        assert all(
            getattr(route, "path", "") != "/test/author-source-recheck/fixtures" for route in app.routes
        )


def test_production_create_app_never_registers_same_source_fixture(tmp_path):
    app = create_app(safe(tmp_path))
    assert all("/test/author-source-recheck" not in getattr(route, "path", "") for route in app.routes)


def routing():
    script = SCRIPT.parent / "e2e_authored_scope_fixture.py"
    spec = spec_from_file_location("same_source_stage_routing", script)
    result = module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_scope_observation_never_consumes_classification_or_other_action():
    route = routing()
    for action in [route.CLASSIFICATION_ACTION, "其他独立阶段"]:
        assert (
            route.source_observation(
                {"动作": action, "authored_source_scope_version": "authored-source-scope/2"}
            )
            is None
        )
    assert route.source_classification({"动作": route.OBSERVE_ACTION}) is None
    instruction = {
        "动作": route.CLASSIFICATION_ACTION,
        "input_sha256": "a" * 64,
        "全部原话连续单元": [{"source_id": "SCOPE0001", "text": "交易失败。"}],
        "完整P解释（AI提案非新条件）": {"items": [{}]},
        "本章及全书固定条件": [],
    }
    result = route.source_classification(instruction)
    assert result["source_checks"][0]["classification_result"] == "pass"
    assert result["source_checks"][0]["result"] == "pass"
    assert result["item_checks"][0]["item_id"] == "P01"


@pytest.mark.parametrize("version", ["authored-source-scope/1", "authored-source-scope/2"])
def test_exclusive_engineering_sentinel_retains_its_same_observation_binding(version):
    route = routing()
    value = {"items": [{"recipient_scope": "only"}], "note": "明确工程原句，不模拟通用语义。"}
    output = route.bind_interpretation(
        {
            "动作": "仅依据作者原话整理本场景情节",
            "authored_source_scope_version": version,
            "作者原话": route.EXCLUSIVE_SENTINEL,
        },
        value,
    )
    assert output["items"] == [
        {
            "recipient_scope": "only",
            "scope_observation_ids": ["O01"],
            "scope_binding_status": "supported",
            "scope_binding_reason": "保持固定原创样例的同一读数、双方、阶段及本章范围。",
        }
    ]


def test_old_and_new_exclusive_observation_outputs_stay_identical():
    route = routing()

    def observe(version):
        return route.source_observation(
            {
                "动作": route.OBSERVE_ACTION,
                "authored_source_scope_version": version,
                "作者本次情节原话": route.EXCLUSIVE_SENTINEL,
                "input_sha256": "b" * 64,
                "可引用原文": [{"source_id": "SCOPE0001"}],
            }
        )

    assert observe("authored-source-scope/1") == observe("authored-source-scope/2")
