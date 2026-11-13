"""Account limits are explicit and guard the exact wire body before reservation."""

import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel.providers import ModelClient
from ainovel.settings import Settings


def load_config(tmp_path, monkeypatch, limits=""):
    config = tmp_path / "limits.toml"
    config.write_text(
        '[accounts.first]\nkind="deepseek"\nkey_env="LIMIT_TEST_KEY"\n'
        'model="deepseek-flash"\n' + limits + "\n"
        '[accounts.second]\nkind="deepseek"\nkey_env="OTHER_LIMIT_TEST_KEY"\n'
        'model="deepseek-flash"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("ainovel.settings.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("AINOVEL_CONFIG", str(config))
    monkeypatch.setenv("LIMIT_TEST_KEY", "test-not-real")
    return Settings.load(tmp_path)


def test_limits_default_does_not_guess_model_window(tmp_path, monkeypatch):
    loaded = load_config(tmp_path, monkeypatch)
    assert loaded.providers["first"].limits() == {
        "max_input_bytes": 120_000,
        "context_window_tokens": None,
        "context_source": "",
        "context_safety_tokens": 4096,
    }


def test_larger_limit_requires_explicit_window_source_and_stays_account_local(tmp_path, monkeypatch):
    source = "https://api-docs.deepseek.com/quick_start/pricing/"
    loaded = load_config(
        tmp_path,
        monkeypatch,
        'max_input_bytes=512000\ncontext_window_tokens=1000000\ncontext_source="' + source + '"',
    )
    first, second = loaded.providers["first"], loaded.providers["second"]
    assert first.max_input_bytes == 512_000 and first.context_window_tokens == 1_000_000
    assert first.context_source == source
    assert second.max_input_bytes == 120_000 and second.context_window_tokens is None
    public = first.public()
    assert public["limits"] == first.limits()
    assert "test-not-real" not in json.dumps(public)
    assert str(loaded.limits["openai"]) == "20" and str(loaded.limits["deepseek"]) == "80"


@pytest.mark.parametrize(
    "limits",
    [
        "max_input_bytes=0",
        "max_input_bytes=-1",
        "max_input_bytes=true",
        "max_input_bytes=false",
        "max_input_bytes=120000.0",
        'max_input_bytes="120000"',
        "max_input_bytes=inf",
        "max_input_bytes=nan",
        "context_window_tokens=0",
        "context_window_tokens=-1",
        "context_window_tokens=true",
        "context_window_tokens=false",
        "context_window_tokens=1000000.0",
        'context_window_tokens="1000000"',
        "context_window_tokens=inf",
        "context_window_tokens=nan",
        "context_source=true",
        "context_source=123",
        "max_input_bytes=120001",
        'max_input_bytes=120001\ncontext_source="verified document"',
        "max_input_bytes=120001\ncontext_window_tokens=1000000",
        'max_input_bytes=120001\ncontext_window_tokens=1000000\ncontext_source="  "',
    ],
)
def test_config_rejects_invalid_or_unsupported_limit_increases(tmp_path, monkeypatch, limits):
    with pytest.raises(ValueError, match="max_input_bytes|context_window_tokens|context_source"):
        load_config(tmp_path, monkeypatch, limits)


def test_smaller_byte_limit_needs_no_assumed_model_window(tmp_path, monkeypatch):
    provider = load_config(tmp_path, monkeypatch, "max_input_bytes=64000").providers["first"]
    assert provider.max_input_bytes == 64_000 and provider.context_window_tokens is None


@pytest.mark.parametrize("field", ["max_input_bytes", "context_window_tokens"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, float("inf"), float("nan"), "100"])
def test_direct_provider_construction_also_validates_limits(settings, field, value):
    with pytest.raises(ValueError):
        replace(settings.providers["deepseek"], **{field: value})


def response(account):
    if account == "deepseek":
        events = [
            {
                "choices": [{"delta": {"content": "完成"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 2},
            }
        ]
    else:
        events = [
            {"type": "response.output_text.delta", "delta": "完成"},
            {
                "type": "response.completed",
                "response": {"status": "completed", "usage": {"input_tokens": 20, "output_tokens": 2}},
            },
        ]
    content = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)
    if account == "deepseek":
        content += "data: [DONE]\n\n"
    return httpx.Response(200, content=content.encode("utf-8"))


@pytest.mark.parametrize("account", ["openai_1", "openai_2", "deepseek"])
def test_exact_utf8_body_and_window_boundary_before_fees(settings, account):
    wire = []
    system, prompt = '只读"基准"\\\n', '渡船😀𠮷\n"原文"\\尾'
    max_output = 31

    def handler(request):
        wire.append(request.content)
        body = json.loads(request.content)
        inputs = body["messages"] if account == "deepseek" else body["input"]
        assert inputs[0]["content"] == system and inputs[1]["content"] == prompt
        assert "渡船😀𠮷".encode() in request.content
        assert request.headers["content-type"] == "application/json"
        return response(account)

    budget = Budget(settings)
    model = ModelClient(settings, budget, httpx.MockTransport(handler))
    asyncio.run(model.generate(account, "measure-body", system, prompt, max_output))
    request_size = len(wire[0])
    assert request_size > len((system + prompt).encode())

    original = settings.providers[account]
    settings.providers[account] = replace(
        original,
        context_window_tokens=request_size + max_output + 4096,
        context_source="verified test window",
    )
    asyncio.run(model.generate(account, "window-exact", system, prompt, max_output))
    assert wire[1] == wire[0]

    settings.providers[account] = replace(
        settings.providers[account], context_window_tokens=request_size + max_output + 4096 - 1
    )
    before = budget.report()
    with pytest.raises(DomainError) as caught:
        asyncio.run(model.generate(account, "window-short", system, prompt, max_output))
    assert caught.value.code == "context_limit" and len(wire) == 2
    assert budget.report() == before

    settings.providers[account] = replace(original, max_input_bytes=request_size)
    asyncio.run(model.generate(account, "bytes-exact", system, prompt, max_output))
    assert wire[2] == wire[0]
    settings.providers[account] = replace(original, max_input_bytes=request_size - 1)
    before = budget.report()
    with pytest.raises(DomainError) as caught:
        asyncio.run(model.generate(account, "bytes-short", system, prompt, max_output))
    assert caught.value.code == "context_limit" and len(wire) == 3
    assert budget.report() == before


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
def test_explicit_larger_limit_transmits_complete_context_within_window(settings, account):
    prompt = "渡船😀𠮷" * 12_000
    requests = []

    def handler(request):
        body = json.loads(request.content)
        inputs = body["messages"] if account == "deepseek" else body["input"]
        assert inputs[1]["content"] == prompt and len(request.content) > 120_000
        requests.append(request)
        return response(account)

    settings.providers[account] = replace(
        settings.providers[account],
        max_input_bytes=512_000,
        context_window_tokens=1_000_000,
        context_source="verified test window",
    )
    result = asyncio.run(
        ModelClient(settings, Budget(settings), httpx.MockTransport(handler)).generate(
            account, "larger-complete-input", "系统", prompt
        )
    )
    assert result["text"] == "完成" and len(requests) == 1


def test_schema_and_json_escaping_count_toward_real_body_limit(settings):
    provider = settings.providers["openai_1"]
    settings.providers[provider.id] = replace(provider, max_input_bytes=2000)
    schema = {"type": "object", "properties": {"result": {"type": "string", "description": "依据" * 1000}}}
    budget = Budget(settings)
    before = budget.report()
    transport = httpx.MockTransport(lambda request: pytest.fail("must stop before transport"))
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, budget, transport).generate(
                provider.id, "schema-overhead", "s", "p", schema=schema
            )
        )
    assert caught.value.code == "context_limit" and budget.report() == before


@pytest.mark.parametrize("gate", ["bytes", "budget"])
def test_configured_model_window_keeps_byte_and_money_gates(settings, gate):
    settings.providers["deepseek"] = replace(
        settings.providers["deepseek"],
        max_input_bytes=2000 if gate == "bytes" else 512_000,
        context_window_tokens=1_000_000,
        context_source="verified test window",
    )
    budget = Budget(settings)
    before = budget.report()
    transport = httpx.MockTransport(lambda request: pytest.fail("must stop before transport"))
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, budget, transport).generate(
                "deepseek", "preserve-other-gates", "s", "正文" * 1000, run_limit=0.001
            )
        )
    assert caught.value.code == ("context_limit" if gate == "bytes" else "budget")
    assert budget.report() == before


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.0, float("inf"), "2"])
def test_output_bound_must_be_positive_integer_before_reservation(settings, value):
    budget = Budget(settings)
    before = budget.report()
    transport = httpx.MockTransport(lambda request: pytest.fail("must stop before transport"))
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, budget, transport).generate(
                "openai_1", "invalid-output-bound", "s", "p", max_output=value
            )
        )
    assert caught.value.code == "configuration" and budget.report() == before
