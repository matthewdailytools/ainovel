"""Anonymous known-returned interpret stops, using real provider transports."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import httpx

from ainovel import authored_source_classification as classification
from ainovel import authored_source_execution as execution
from ainovel import authored_source_scope as authored
from ainovel.db import dumps
from ainovel.models import WorkflowInput
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.author_clarification_fixture import answer as downstream_answer
from helpers.authored_source_fixture import classification_preservation
from test_openai_schema import completed
from test_workflow_plan_source_roles import deepseek_completed

SOURCE = (
    "林舟记录窗边公开刻度，保留自己的原始记录。\n"
    "林舟向管理员请求查看公开目录，尚未获准。\n"
    "林舟不向管理员讲述或展示刚才记下的刻度记录。"
)
BODY = (
    SOURCE.replace("\n", "")
    + ("林舟把自己的记录收回袋中，站在柜外等候。他没有越过柜台，也没有翻开尚未获准查看的目录。") * 6
)
INTERPRET = "仅依据作者原话整理本场景情节"


def observation(task):
    units = task["可引用原文"]
    assert len(units) == 3
    observations = []
    for number, index, kind, obj, stage in (
        (1, 1, "named_destination", "查看公开目录的请求", "completed"),
        (2, 2, "specific_recipient_exclusion", "刻度记录", "none"),
    ):
        observations.append(
            {
                "observation_id": f"O{number:02}",
                "kind": kind,
                "supplier": "林舟",
                "recipient": "管理员",
                "transfer_object": obj,
                "transfer_scope": stage,
                "evidence": units[index]["source_id"],
                "restriction_evidence": units[index]["source_id"] if number == 2 else "",
                "condition": "",
                "reason": "请求已提出但尚未获准；特定管理员排除不等于排他接收。",
            }
        )
    return {
        "input_sha256": task["input_sha256"],
        "observations": observations,
        "unit_checks": [
            {
                "source_id": unit["source_id"],
                "status": "not_scope_related" if index == 0 else "covered",
                "observation_ids": [] if index == 0 else [f"O{index:02}"],
                "reason": "记录不建立传递；请求与未披露的原意分别保留。",
            }
            for index, unit in enumerate(units)
        ],
        "note": "匿名已知源语义，用于真实阶段持久化，不作文学质量判断。",
    }


def interpretation(task, *, invalid, attempt):
    units = task["可引用原文"]
    assert len(units) == 3
    items = []
    for index, unit in enumerate(units):
        sender, recipient, obj, stage, ids = (
            ("", "", "", "none", [])
            if index == 0
            else ("林舟", "管理员", "查看公开目录的请求", "completed", ["O01"])
            if index == 1
            else ("林舟", "管理员", "刻度记录", "none", ["O02"])
        )
        items.append(
            {
                "author_evidence": unit["source_id"],
                "actor": "林舟",
                "action": unit["text"],
                "object": obj or "原始记录",
                "direction": "林舟向管理员提出请求" if index == 1 else "",
                "result": unit["text"],
                "certainty": "explicit",
                "supplier": sender,
                "recipient": recipient,
                "transfer_object": obj,
                "transfer_scope": stage,
                "recipient_scope": "only" if invalid and index == 2 else "unspecified",
                "scope_condition": "",
                "scope_observation_ids": ids,
                "scope_binding_status": "supported",
                "scope_binding_reason": "第三项只排除管理员接收记录，不能改成排他接收；请求不等于许可。",
            }
        )
    return {
        "items": items,
        # Distinct genuine returned frames must create distinct repair requests.
        "note": f"匿名解释返回{attempt}，原行动、否定与范围保持；非正文或作者文学认可。",
    }


def native(worker, *, failures=0, timeout_interpret=False, malformed_interpret=False, response_tag="new"):
    frames = []
    interpret_count = 0

    def handler(request):
        nonlocal interpret_count
        wire = json.loads(request.content)
        prompt = wire.get("input", wire.get("messages"))[1]["content"]
        frame = json.loads(prompt.split("\n只返回")[0])
        frames.append(deepcopy(frame))
        task = frame["任务"]
        action = task["动作"]
        if action == authored.ACTION:
            value = observation(task)
        elif action == INTERPRET:
            interpret_count += 1
            if timeout_interpret:
                raise httpx.ReadTimeout("anonymous diagnostic retry result unknown", request=request)
            value = (
                "匿名错误根类型，必须走真实结构修正。"
                if malformed_interpret
                else interpretation(
                    task,
                    invalid=interpret_count <= failures,
                    attempt=f"{response_tag}-{interpret_count}",
                )
            )
        elif malformed_interpret and action == "仅修正结构化产物格式":
            value = "匿名结构修正仍为错误根类型，不得追加第三次线缆调用。"
        elif action == classification.ACTION:
            value = classification_preservation(task)
        elif action == "按章纲写完整正文":
            value = BODY
        elif action == "逐项对照作者情节解释与本次正文":
            value = {
                "checks": [
                    {
                        "item_id": item["id"],
                        "result": "pass",
                        "observed": "记录仍由林舟保存；请求已提出，尚未获准，也未披露记录。",
                        "evidence": task["可引用原文"][0]["source_id"],
                        "reason": "匿名已知原义保持，不把禁止披露改成实际交付。",
                        "repair": "",
                    }
                    for item in task["独立整理的解释提案"]
                ],
                "summary": "确定性工程输出，不代表真实模型质量。",
            }
        elif action == "只从本次正文独立提取物品与信息流向":
            value = {"flows": [], "note": "没有发生材料交付；请求另在原情节核对保留。"}
        elif action == "从正文提取事实候选与章节摘要":
            value = {"facts": [], "summary": "请求尚未获准，未披露自己的记录。"}
        else:
            value = downstream_answer(frame)
        text = value if isinstance(value, str) else dumps(value)
        return completed(text) if "input" in wire else deepseek_completed(value)

    worker.client = ModelClient(worker.settings, worker.budget, httpx.MockTransport(handler))
    return frames


def known_interpret_failure(
    settings,
    domain,
    story,
    *,
    client=None,
    client_request_id="anonymous-known-interpret-diagnostic-stop",
    account="deepseek",
):
    """Make a true four-response stop under only the historical label rendering."""
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="whole_book",
        brief="匿名十二章诊断恢复专项，不是文学评估。",
        chapters=12,
        plots=[
            {
                "number": number,
                "title": f"匿名第{number}章",
                "plot": SOURCE if number == 1 else f"第{number}章继续整理原有记录，不新增披露。",
                "fixed": ["不得向管理员披露刻度记录。"],
            }
            for number in range(1, 13)
        ],
        account=account,
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id=client_request_id,
    ).model_dump(exclude_none=True)
    if client:
        response = client.post("/api/workflows", json=payload)
        assert response.status_code == 202, response.text
        queued = response.json()
    else:
        queued = domain.queue("workflow", payload, story["branch_id"])
    worker = Worker(settings)
    frames = native(worker, failures=3, response_tag="legacy")
    current_binding_issues = authored.binding_issues

    def legacy_labels(value, inventory, **kwargs):
        return current_binding_issues(value, inventory, legacy_diagnostic_labels=True)

    with patch.object(authored, "binding_issues", legacy_labels):
        assert asyncio.run(worker.run_once())
    current = domain.task(queued["id"])
    assert current["status"] == "blocked_runtime", current["error"]
    assert current["result"][authored.KEY] == authored.CURRENT_VERSION
    assert current["result"][execution.KEY] == execution.VERSION
    record = current["result"][authored.RECEIPTS]["chapter:1"]
    assert record["phases"]["observe"]["status"] == "completed"
    assert record["phases"]["interpret"]["status"] == "started"
    assert "response" not in record["phases"]["interpret"]
    assert len(frames) == 4
    assert len(domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (current["id"],))) == 4
    with worker.budget.db() as usage:
        calls = [dict(row) for row in usage.execute("SELECT * FROM calls WHERE task_id=?", (current["id"],))]
        assert len(calls) == 4 and all(
            row["status"] == "settled" and row["charged"] is not None for row in calls
        )
    return worker, current, frames
