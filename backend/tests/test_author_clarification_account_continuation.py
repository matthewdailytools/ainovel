"""Anonymous formal continuation reproduction, with body known not sent."""

import asyncio
from copy import deepcopy
from decimal import Decimal
import json

import httpx
import pytest

from ainovel import authored_source_scope as authored
from ainovel import author_source_clarification as bridge
from ainovel.db import DomainError, dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.author_clarification_fixture import source_unverified, answer, native
from test_author_source_clarification import ready_request
from test_openai_schema import completed
from test_workflow_plan_source_roles import deepseek_completed


@pytest.mark.parametrize(
    "source_account,target_account", [("deepseek", "openai_1"), ("openai_1", "openai_2")]
)
def test_explicit_account_continuation_after_ready_source_and_known_unsent_body(
    settings, domain, story, client, source_account, target_account
):
    _, parent, _ = source_unverified(settings, domain, story, account=source_account)
    body = ready_request(client, parent)
    response = client.post(f"/api/tasks/{parent['id']}/author-source-clarifications", json=body)
    assert response.status_code == 202, response.text
    writer = Worker(settings)
    writer.task = writer.claim()
    writer.attempt = writer.task["attempt"]
    provider = settings.providers[source_account].kind
    old_limit = settings.limits[provider]
    sent = []

    def handler(request):
        wire = json.loads(request.content)
        frame = json.loads(wire.get("input", wire.get("messages"))[1]["content"].split("\n只返回")[0])
        sent.append(deepcopy(frame))
        output = answer(frame)
        if frame["任务"].get("动作") == bridge.ACTION:
            settings.limits[provider] = Decimal("0")
        return completed(dumps(output)) if "input" in wire else deepseek_completed(output)

    writer.client = ModelClient(settings, writer.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError) as blocked:
        asyncio.run(writer.workflow())
    assert "预算" in blocked.value.message
    writer.checkpoint(writer.task["result"], "blocked_runtime", blocked.value.message)
    settings.limits[provider] = old_limit
    stopped = domain.task(writer.task["id"])
    assert stopped["result"][bridge.CHECKS]["status"] == "ready"
    assert stopped["result"][authored.RECEIPTS]["chapter:2"]["state"] == "ready"
    assert not any(f["任务"].get("动作") == "按章纲写完整正文" for f in sent)
    with writer.budget.db() as tx:
        old_calls = [dict(row) for row in tx.execute("SELECT * FROM calls ORDER BY id")]
    result = client.post(
        f"/api/tasks/{stopped['id']}/continue",
        json={
            "account": target_account,
            "budget_limit": 2.0,
            "acknowledge_unknown": True,
            "reason": "来源保持已完成，正文已证未发送；显式更换已配置账户继续。",
            "client_request_id": "independent-clarification-account-switch",
        },
    )
    assert result.status_code == 202, result.text
    child_id = result.json()["id"]
    checker = Worker(settings)
    actual = native(checker)
    assert asyncio.run(checker.run_once())
    child = domain.task(child_id)
    assert child["status"] == "needs_review", child["error"]
    assert actual and actual[0]["任务"]["动作"] == "按章纲写完整正文"
    assert not any(
        frame["任务"]["动作"] in {authored.ACTION, bridge.ACTION, "仅依据作者原话整理本场景情节"}
        for frame in actual
    )
    assert child["result"][bridge.RECEIPT] == stopped["result"][bridge.RECEIPT]
    with checker.budget.db() as tx:
        new_calls = [dict(row) for row in tx.execute("SELECT * FROM calls ORDER BY id")]
    old_ids = {r["id"] for r in old_calls}
    assert [r for r in new_calls if r["id"] in old_ids] == old_calls
    assert all(
        r["account"] == target_account and r["status"] == "settled"
        for r in new_calls
        if r["id"] not in old_ids
    )
    assert len(new_calls) > len(old_calls)
