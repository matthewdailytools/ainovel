"""Explicit account output space binds the wire, budget, trace and paid-step reuse."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import json

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel.providers import ModelClient
from test_budget_provider import stream
from test_openai_schema import Answer, completed, worker_for
from test_provider_limits import load_config
from test_request_trace import events


@pytest.mark.parametrize("finish,done", [("stop", True), (None, False)])
def test_empty_output_without_a_length_signal_is_not_labelled_as_output_exhaustion(settings, finish, done):
    ledger = Budget(settings)
    response = stream(
        [
            {
                "choices": [{"delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30},
            }
        ],
        done,
    )
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(
                settings, ledger, httpx.MockTransport(lambda _: httpx.Response(200, content=response))
            ).generate("deepseek", "empty-without-length", "system", "prompt")
        )
    assert caught.value.code == "incomplete" and not caught.value.repairable
    assert "达到本次输出上限" not in str(caught.value)
    assert ledger.report()[1]["reserved_unknown"] == 0


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "16384"])
@pytest.mark.parametrize("field", ["structured_output_min_tokens", "text_output_min_tokens"])
def test_invalid_floor_is_rejected_at_configuration_boundary(settings, value, field):
    with pytest.raises(ValueError, match=field):
        replace(settings.providers["deepseek"], **{field: value})


def test_floor_is_explicit_account_local_and_does_not_change_old_public_shape(tmp_path, monkeypatch):
    loaded = load_config(
        tmp_path, monkeypatch, "structured_output_min_tokens=16384\ntext_output_min_tokens=24576"
    )
    first, second = (loaded.providers[name] for name in ("first", "second"))
    assert first.structured_output_min_tokens == 16384
    assert first.public()["limits"]["structured_output_min_tokens"] == 16384
    assert first.text_output_min_tokens == 24576 and first.output_limit(8192) == 24576
    assert "structured_output_min_tokens" not in second.public()["limits"]
    assert "text_output_min_tokens" not in second.public()["limits"]
    assert second.output_limit(4096, structured=True) == 4096
    assert str(loaded.limits["deepseek"]) == "80"


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
@pytest.mark.parametrize(
    "requested,structured,text_floor,expected",
    [
        (4096, True, None, 16384),
        (32768, True, None, 32768),
        (2048, False, None, 2048),
        (8192, False, 16384, 16384),
        (32768, False, 16384, 32768),
    ],
)
def test_wire_uses_the_explicit_floor_for_each_output_kind(
    settings, account, requested, structured, text_floor, expected
):
    settings.providers[account] = replace(
        settings.providers[account], structured_output_min_tokens=16384, text_output_min_tokens=text_floor
    )
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        if account != "deepseek":
            return completed()
        return httpx.Response(
            200,
            content=stream(
                [
                    {
                        "choices": [{"delta": {"content": '{"answer":"完成"}'}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
                    }
                ],
                True,
            ),
        )

    result = asyncio.run(
        ModelClient(settings, Budget(settings), httpx.MockTransport(handler)).generate(
            account,
            "wire-floor",
            "system",
            "prompt",
            requested,
            schema=Answer.model_json_schema() if structured else None,
        )
    )
    assert result["text"] == '{"answer":"完成"}'
    assert captured[0]["max_tokens" if account == "deepseek" else "max_output_tokens"] == expected


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
@pytest.mark.parametrize("structured", [False, True])
def test_increased_space_is_reserved_before_dispatch_and_stays_within_task_budget(
    settings, account, structured
):
    settings.providers[account] = replace(
        settings.providers[account], structured_output_min_tokens=16384, text_output_min_tokens=16384
    )
    sent = []
    ledger = Budget(settings)
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(lambda r: sent.append(r))).generate(
                account,
                "floor-budget",
                "system",
                "prompt",
                4096,
                run_limit=Decimal("0.1"),
                schema=Answer.model_json_schema() if structured else None,
            )
        )
    assert caught.value.code == "budget" and sent == []
    assert all(not row["calls"] for row in ledger.report())


@pytest.mark.parametrize("structured", [False, True])
def test_increased_space_obeys_model_window_before_any_reservation(settings, structured):
    settings.providers["deepseek"] = replace(
        settings.providers["deepseek"],
        structured_output_min_tokens=16384,
        text_output_min_tokens=16384,
        context_window_tokens=15000,
        context_source="explicit test window",
    )
    ledger = Budget(settings)
    sent = []
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(lambda r: sent.append(r))).generate(
                "deepseek",
                "floor-window",
                "system",
                "prompt",
                4096,
                schema=Answer.model_json_schema() if structured else None,
            )
        )
    assert caught.value.code == "context_limit" and sent == []
    assert all(not row["calls"] for row in ledger.report())


def test_actual_space_binds_worker_trace_and_cache_without_invalidating_plain_prose(settings, domain, story):
    account = "openai_1"
    settings.providers[account] = replace(settings.providers[account], structured_output_min_tokens=16384)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return completed()

    worker = worker_for(
        settings, domain, story, ModelClient(settings, Budget(settings), httpx.MockTransport(handler))
    )
    request = {"动作": "结构化输出额度验证"}
    for _ in range(2):
        assert asyncio.run(worker.call(request, Answer, max_output=4096)) == {"answer": "完成"}
    assert len(sent) == 1 and sent[0]["max_output_tokens"] == 16384
    assert events(domain, "step_dispatched")[0]["request"]["max_output_tokens"] == 16384
    settings.providers[account] = replace(settings.providers[account], structured_output_min_tokens=32768)
    assert asyncio.run(worker.call(request, Answer, max_output=4096)) == {"answer": "完成"}
    assert len(sent) == 2 and sent[-1]["max_output_tokens"] == 32768
    traces = events(domain, "step_dispatched")
    assert traces[0]["fingerprint"] != traces[1]["fingerprint"]
    assert traces[-1]["request"]["account"]["limits"]["structured_output_min_tokens"] == 32768
    asyncio.run(worker.call(request, max_output=2048))
    assert sent[-1]["max_output_tokens"] == 2048
    settings.providers[account] = replace(settings.providers[account], text_output_min_tokens=16384)
    asyncio.run(worker.call(request, max_output=2048))
    assert sent[-1]["max_output_tokens"] == 16384
    assert events(domain, "step_dispatched")[-1]["request"]["max_output_tokens"] == 16384


def test_floor_does_not_increase_finite_repair_rounds_or_expose_thinking(settings, domain, story):
    settings.providers["openai_1"] = replace(
        settings.providers["openai_1"], structured_output_min_tokens=16384
    )
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=stream(
                [
                    {
                        "type": "response.incomplete",
                        "response": {
                            "status": "incomplete",
                            "incomplete_details": {"reason": "max_output_tokens"},
                            "usage": {"input_tokens": 20, "output_tokens": 16384},
                        },
                    }
                ]
            ),
        )

    ledger = Budget(settings)
    worker = worker_for(settings, domain, story, ModelClient(settings, ledger, httpx.MockTransport(handler)))
    with pytest.raises(DomainError):
        asyncio.run(worker.call({"动作": "验证有限格式修正"}, Answer, max_output=4096))
    assert len(sent) == 3 and {row["max_output_tokens"] for row in sent} == {16384}
    assert ledger.report()[0]["reserved_unknown"] == 0
    assert len(ledger.report()[0]["calls"]) == 3
    assert not domain.db.all("SELECT * FROM task_steps")
