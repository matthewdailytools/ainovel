import asyncio
from dataclasses import replace
from decimal import Decimal
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel.providers import ModelClient
from ainovel.settings import Settings


def test_two_keys_share_twenty_dollars_and_restart(settings):
    ledger = Budget(settings)
    expensive = replace(settings.providers["openai_1"], input_rate=Decimal("10000000"))
    call = ledger.reserve(expensive, "t", 1, 1)
    ledger.settle(call)  # Unknown consumes $10, even when switching account/restarting.
    second = Budget(settings)
    with pytest.raises(DomainError, match="共享预算"):
        second.reserve(replace(expensive, id="openai_2"), "t2", 2, 1)
    report = second.report()
    assert report[0]["limit"] == "20"
    assert report[0]["reserved_unknown"] > 10
    assert report[1]["remaining"] == 80


def test_config_cannot_raise_authorized_cap(tmp_path, monkeypatch):
    path = tmp_path / "settings.toml"
    path.write_text("[budgets]\nopenai=500\ndeepseek=800\n")
    monkeypatch.setenv("AINOVEL_CONFIG", str(path))
    assert Settings.load(tmp_path).limits == {"openai": Decimal("20"), "deepseek": Decimal("80")}


def test_per_task_limit_unknown_price_and_settlement(settings):
    budget = Budget(settings)
    provider = settings.providers["openai_1"]
    with pytest.raises(DomainError):
        budget.reserve(replace(provider, input_rate=None), "t", 10, 100)
    with pytest.raises(DomainError):
        budget.reserve(provider, "t", 1000, 1000, 0.001)
    call = budget.reserve(provider, "t", 100, 100)
    budget.settle(call, {"input": 20, "output": 10})
    first = budget.report()[0]["settled"]
    budget.settle(call, known_no_charge=True)
    assert first == budget.report()[0]["settled"] > 0


@pytest.mark.parametrize("account", ["openai_1", "openai_2", "deepseek"])
def test_oversized_chinese_context_stops_before_network_or_fee_reservation(settings, account):
    budget = Budget(settings)
    before = budget.report()
    attempted = []

    def transport(request):
        attempted.append(request)
        raise AssertionError("Oversized input must not reach the provider")

    model = ModelClient(settings, budget, httpx.MockTransport(transport))
    # Count UTF-8 bytes, including non-BMP characters, rather than Python characters.
    prompt = "渡船😀𠮷" * 12_000
    assert len(prompt) < settings.providers[account].max_input_bytes
    assert len(prompt.encode()) > settings.providers[account].max_input_bytes
    with pytest.raises(DomainError) as error:
        asyncio.run(model.generate(account, "scale-input-limit", "仅处理给定原创正文", prompt))
    assert error.value.code == "context_limit"
    assert not attempted
    assert budget.report() == before


def stream(events, deepseek=False):
    return "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events).encode() + (
        b"data: [DONE]\n\n" if deepseek else b""
    )


@pytest.mark.parametrize("account", ["openai_1", "openai_2", "deepseek"])
def test_real_adapter_sse_contract(settings, account):
    deepseek = account == "deepseek"
    events = (
        [
            {"choices": [{"delta": {"content": "甲😀"}, "finish_reason": None}]},
            {
                "choices": [{"delta": {"content": "乙"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        ]
        if deepseek
        else [
            {"type": "response.output_text.delta", "delta": "甲😀乙"},
            {
                "type": "response.completed",
                "response": {"status": "completed", "usage": {"input_tokens": 20, "output_tokens": 10}},
            },
        ]
    )

    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        assert request.headers["authorization"] == "Bearer test-not-real"
        return httpx.Response(200, content=stream(events, deepseek))

    ledger = Budget(settings)
    result = asyncio.run(
        ModelClient(settings, ledger, httpx.MockTransport(handler)).generate(account, "t", "系统", "正文")
    )
    assert result["text"] == "甲😀乙" and result["usage"]["output"] == 10
    assert ledger.report()[1 if deepseek else 0]["reserved_unknown"] == 0


@pytest.mark.parametrize("effort", [None, "", "none", "low", "high", "max"])
def test_deepseek_explicit_thinking_wire_usage_and_public_mode(settings, effort):
    provider = replace(settings.providers["deepseek"], reasoning=effort)
    settings.providers["deepseek"] = provider
    enabled = effort in ("low", "high", "max")
    deltas = []
    events = [
        {"choices": [{"delta": {"reasoning_content": "内部推理不作为正文返回"}}]},
        {"choices": [{"delta": {"content": "只返回最终正文😀"}, "finish_reason": "stop"}]},
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 23},
            },
        },
    ]

    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled" if enabled else "disabled"}
        assert body.get("reasoning_effort") == (effort if enabled else None)
        assert body["max_tokens"] == 4096
        assert "temperature" not in body and "tools" not in body
        return httpx.Response(200, content=stream(events, True))

    async def on_text(value):
        deltas.append(value)

    ledger = Budget(settings)
    result = asyncio.run(
        ModelClient(settings, ledger, httpx.MockTransport(handler)).generate(
            "deepseek", "thinking-usage", "系统", "正文", 4096, on_text=on_text
        )
    )
    assert result["text"] == "只返回最终正文😀" and deltas == ["只返回最终正文😀"]
    assert result["usage"] == {"input": 20, "output": 30}
    assert ledger.report()[1]["settled"] == pytest.approx((20 * 2 + 30 * 8) / 1_000_000)
    public = provider.public()
    assert "test-not-real" not in json.dumps(public)
    if enabled:
        assert public["thinking_mode"] == "enabled" and public["reasoning_effort"] == effort
    else:
        assert "thinking_mode" not in public and "reasoning_effort" not in public


@pytest.mark.parametrize("effort", ["medium", "typo", True, 4])
def test_deepseek_unknown_thinking_effort_is_rejected_before_usage(settings, effort):
    ledger = Budget(settings)
    before = ledger.report()
    with pytest.raises(ValueError, match="reasoning_effort"):
        replace(settings.providers["deepseek"], reasoning=effort)
    assert ledger.report() == before


def test_deepseek_thinking_alias_uses_existing_shared_budget(settings):
    ledger = Budget(settings)
    provider = replace(settings.providers["deepseek"], input_rate=Decimal("40000000"))
    first = ledger.reserve(provider, "original-deepseek", 1, 1)
    ledger.settle(first)
    with pytest.raises(DomainError, match="共享预算"):
        ledger.reserve(replace(provider, id="deepseek_thinking", reasoning="high"), "thinking", 2, 1)
    assert ledger.report()[1]["limit"] == "80"


@pytest.mark.parametrize("code", [401, 429, 500])
def test_provider_errors_never_retry_or_switch(settings, code):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(code, text="deliberately not logged")

    ledger = Budget(settings)
    with pytest.raises(DomainError):
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(handler)).generate("openai_1", "t", "s", "p")
        )
    assert len(seen) == 1
    assert (ledger.report()[0]["reserved_unknown"] > 0) == (code == 500)


def test_incomplete_stream_and_cancellation_hold_reservation(settings):
    response = httpx.Response(200, content=stream([{"type": "response.output_text.delta", "delta": "部分"}]))
    ledger = Budget(settings)
    with pytest.raises(DomainError):
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(lambda _: response)).generate(
                "openai_1", "t", "s", "p"
            )
        )
    assert ledger.report()[0]["reserved_unknown"] > 0
    ledger2 = Budget(settings)
    with pytest.raises(DomainError):
        asyncio.run(
            ModelClient(settings, ledger2).generate("openai_1", "not-sent", "s", "p", cancelled=lambda: True)
        )
    assert ledger2.report()[0]["calls"][0]["charged"] == 0


def test_explicit_output_limit_settles_known_usage_and_preserves_partial(settings):
    events = [
        {"type": "response.output_text.delta", "delta": "部分JSON"},
        {
            "type": "response.incomplete",
            "response": {
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 30, "output_tokens": 100},
            },
        },
    ]
    ledger = Budget(settings)
    client = ModelClient(
        settings, ledger, httpx.MockTransport(lambda _: httpx.Response(200, content=stream(events)))
    )
    with pytest.raises(DomainError) as caught:
        asyncio.run(client.generate("openai_1", "explicit-limit", "system", "prompt"))
    assert caught.value.repairable and caught.value.known_usage
    assert caught.value.partial_text == "部分JSON"
    assert ledger.report()[0]["reserved_unknown"] == 0
    assert ledger.report()[0]["settled"] > 0


def test_provider_failed_event_keeps_sanitized_reason_and_does_not_retry(settings):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=stream(
                [
                    {
                        "type": "response.failed",
                        "response": {
                            "status": "failed",
                            "service_tier": "default",
                            "error": {"code": "server_error", "message": "upstream failed test-not-real"},
                            "usage": None,
                        },
                    }
                ]
            ),
        )

    ledger = Budget(settings)
    client = ModelClient(settings, ledger, httpx.MockTransport(handler))
    with pytest.raises(DomainError) as caught:
        asyncio.run(client.generate("openai_1", "failed-terminal", "system", "prompt"))
    assert len(requests) == 1 and requests[0]["service_tier"] == "default"
    assert caught.value.code == "provider_error" and not caught.value.repairable
    assert caught.value.provider_details["provider_code"] == "server_error"
    assert "test-not-real" not in str(caught.value) and "[REDACTED]" in str(caught.value)
    assert ledger.report()[0]["reserved_unknown"] > 0


@pytest.mark.parametrize("account,effort", [("openai_1", "none"), ("deepseek", "none"), ("deepseek", "high")])
def test_unknown_timeout_is_not_repairable_or_retried(settings, account, effort):
    settings.providers[account] = replace(settings.providers[account], reasoning=effort)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("not disclosed")

    ledger = Budget(settings)
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(handler)).generate(
                account, "unknown-timeout", "system", "prompt"
            )
        )
    assert caught.value.code == "unknown_timeout"
    assert len(calls) == 1 and ledger.report()[int(account == "deepseek")]["reserved_unknown"] > 0


def test_thinking_only_output_limit_settles_usage_without_returning_internal_text(settings):
    settings.providers["deepseek"] = replace(settings.providers["deepseek"], reasoning="high")
    ledger = Budget(settings)
    events = [
        {
            "choices": [
                {"delta": {"reasoning_content": "内部文本不得进正文或异常片段"}, "finish_reason": "length"}
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30},
        }
    ]
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(
                settings,
                ledger,
                httpx.MockTransport(lambda _: httpx.Response(200, content=stream(events, True))),
            ).generate("deepseek", "thinking-length", "系统", "正文", 30)
        )
    assert caught.value.code == "incomplete" and caught.value.known_usage and caught.value.repairable
    assert "达到本次输出上限" in str(caught.value)
    assert caught.value.provider_details == {
        "reason": "max_output_tokens",
        "max_output_tokens": 30,
        "received_terminal": True,
        "visible_text_chars": 0,
    }
    assert not getattr(caught.value, "partial_text", "")
    assert "内部文本" not in str(caught.value)
    assert ledger.report()[1]["reserved_unknown"] == 0 and ledger.report()[1]["settled"] > 0


def test_same_deepseek_key_can_be_configured_as_explicit_separate_thinking_account(tmp_path, monkeypatch):
    config = tmp_path / "thinking.toml"
    config.write_text(
        """[accounts.deepseek]
kind="deepseek"
key_env="DEEPSEEK_API_KEY"
model="test-model"
reasoning_effort="none"
[accounts.deepseek_thinking]
kind="deepseek"
key_env="DEEPSEEK_API_KEY"
model="test-model"
reasoning_effort="high"
[pricing."test-model"]
currency="CNY"
input_per_million="2"
output_per_million="8"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AINOVEL_CONFIG", str(config))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-duplicate-key")
    loaded = Settings.load(tmp_path)
    original, thinking = (loaded.providers[name] for name in ("deepseek", "deepseek_thinking"))
    assert original.key == thinking.key and original.reasoning == "none" and thinking.reasoning == "high"
    assert original.kind == thinking.kind == "deepseek" and loaded.limits["deepseek"] == Decimal("80")
