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


def test_unknown_timeout_is_not_repairable_or_retried(settings):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("not disclosed")

    ledger = Budget(settings)
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(handler)).generate(
                "openai_1", "unknown-timeout", "system", "prompt"
            )
        )
    assert caught.value.code == "unknown_timeout"
    assert len(calls) == 1 and ledger.report()[0]["reserved_unknown"] > 0
