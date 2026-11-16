"""Anonymous original source; all native calls use MockTransport in isolated data."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import httpx

from ainovel import authored_source_scope as authored
from ainovel import authored_source_classification as classification
from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.authored_source_fixture import classification_preservation
from helpers.author_clarification_fixture import answer as ordinary_answer
from test_openai_schema import completed
from test_workflow_plan_source_roles import deepseek_completed

SOURCE = (
    "林舟只把账页交给师傅，不转给其他人。\n"
    "林舟向掌柜购买铜印，掌柜加价，交易失败。\n"
    "铜印和货款都未交付，两人不动手。\n"
    "林舟向管理员请求查看公开目录，尚未获准。\n"
    "管理员明确不知道缺页原因。\n"
    "查看目录不等于管理员已经知道全部调查。\n"
    "林舟向修理人借来旧尺，约定初查结束后归还。"
)
TAG = "authored-classification-e2e"
BODY = (
    "林舟把账页推到师傅手边，等他看清缺页的位置才松手。桌边没有第三个人，他也没有向外人提起纸上的事。"
    "到了街口，掌柜临时加了铜印的价钱。林舟收回钱袋，铜印仍留在柜里，谁也没有付钱交货，更没有动手。"
    "他随后走进资料馆，向管理员提出查阅公开目录的请求。管理员尚未答应，目录也没有交到他手里。"
    "管理员只承认目录缺页，坦白说自己不知道原因。林舟没有因此认定她明白自己此前的全部调查。"
    "离开资料馆后，林舟向修理人借来一把旧尺，把初查结束后归还的约定记在纸上。他没有把借尺说成铜印买卖成功，"
    "也没有说尺已归还；尺上的旧刻痕与柜里留下的铜印，仍是两件不同的东西。"
)


def observation(task, *, unknown=False):
    units = task["可引用原文"]
    specs = [
        (0, "recipient_exclusivity", "林舟", "师傅", "账页", "completed", ""),
        (1, "named_destination", "掌柜", "林舟", "铜印", "attempted", ""),
        (3, "named_destination", "林舟", "管理员", "查看公开目录的请求", "completed", ""),
        (6, "action_permission", "修理人", "林舟", "旧尺", "completed", "初查结束后归还"),
    ]
    observations = []
    for i, (u, kind, sender, recipient, obj, stage, condition) in enumerate(specs, 1):
        observations.append(
            {
                "observation_id": f"O{i:02}",
                "kind": "unknown" if unknown and i == 2 else kind,
                "supplier": sender,
                "recipient": recipient,
                "transfer_object": obj,
                "transfer_scope": stage,
                "evidence": units[u]["source_id"],
                "restriction_evidence": units[u]["source_id"]
                if kind in {"recipient_exclusivity", "action_permission"}
                else "",
                "condition": condition,
                "reason": "分别保留真实排他、失败尝试、请求本身与借用归还，不将剧情未知当来源歧义。",
            }
        )
    return {
        "input_sha256": task["input_sha256"],
        "observations": observations,
        "unit_checks": [
            {
                "source_id": unit["source_id"],
                "status": "covered" if i in [0, 1, 3, 6] else "not_scope_related",
                "observation_ids": [f"O{[0, 1, 3, 6].index(i) + 1:02}"] if i in [0, 1, 3, 6] else [],
                "reason": "全部单元保留；not_scope_related只免范围观察，不免完整原意核对。",
            }
            for i, unit in enumerate(units)
        ],
        "note": "确定性工程分类，不是文学或真实模型质量认可。",
    }


def interpretation(task, *, ambiguous=False):
    rows = []
    bound = {
        0: ("O01", "林舟", "师傅", "账页", "completed", "only"),
        1: ("O02", "掌柜", "林舟", "铜印", "attempted", "unspecified"),
        3: ("O03", "林舟", "管理员", "查看公开目录的请求", "completed", "unspecified"),
        6: ("O04", "修理人", "林舟", "旧尺", "completed", "unspecified"),
    }
    for i, unit in enumerate(task["可引用原文"]):
        spec = bound.get(i, (None, "", "", "", "none", "unspecified"))
        oid, sender, recipient, obj, stage, scope = spec
        rows.append(
            {
                "author_evidence": unit["source_id"],
                "actor": "管理员" if i in {4, 5} else "林舟",
                "action": "按原句保留",
                "object": unit["text"],
                "direction": "" if stage == "none" else f"{sender}向{recipient}",
                "result": unit["text"],
                "certainty": "ambiguous" if ambiguous and i == 0 else "explicit",
                "supplier": sender,
                "recipient": recipient,
                "transfer_object": obj,
                "transfer_scope": stage,
                "recipient_scope": scope,
                "scope_condition": "",
                "scope_observation_ids": [oid] if oid else [],
                "scope_binding_status": "supported",
                "scope_binding_reason": "保留同一原句的角色、物品、阶段、否定和范围；普通未知不补答案。",
            }
        )
    return {"items": rows, "note": "全部原句都在P保留，包括无接收范围的否定与认知。"}


def native(
    worker,
    *,
    observe_unknown=False,
    ambiguous=False,
    preserve="pass",
    classification_result="pass",
    timeout_action=None,
    transform=None,
):
    frames = []

    def handler(request):
        wire = json.loads(request.content)
        prompt = wire.get("input", wire.get("messages"))[1]["content"]
        frame = json.loads(prompt.split("\n只返回")[0])
        frames.append(deepcopy(frame))
        task = frame["任务"]
        if task["动作"] == timeout_action:
            raise httpx.ReadTimeout("isolated source result unknown", request=request)
        if task["动作"] == authored.ACTION:
            value = observation(task, unknown=observe_unknown)
        elif task["动作"] == "仅依据作者原话整理本场景情节":
            value = interpretation(task, ambiguous=ambiguous)
        elif task["动作"] == classification.ACTION:
            value = classification_preservation(task, preserve, classification_result)
        elif task["动作"] == "按章纲写完整正文":
            value = BODY
        else:
            value = ordinary_answer(frame, preserve=preserve)
        if transform:
            value = transform(frame, value)
        if isinstance(value, str):
            return completed(value) if "input" in wire else deepseek_completed(value)
        return completed(dumps(value)) if "input" in wire else deepseek_completed(value)

    worker.client = ModelClient(worker.settings, worker.budget, httpx.MockTransport(handler))
    return frames


def source_unverified(
    settings,
    domain,
    story,
    *,
    client=None,
    account="deepseek",
    policy=authored.VERSION,
    client_request_id="classification-known-source-stop",
):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="segment",
        brief=TAG,
        chapters=1,
        plots=[{"number": 1, "title": "匿名分类验证", "plot": SOURCE, "fixed": ["不动手"]}],
        account=account,
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id=client_request_id,
    ).model_dump(exclude_none=True)
    with patch.object(authored, "NEW_TASK_VERSION", policy):
        if client:
            response = client.post("/api/workflows", json=payload)
            assert response.status_code == 202, response.text
            task = response.json()
        else:
            task = domain.queue("workflow", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == task["id"]
    frames = native(worker, observe_unknown=True)
    try:
        asyncio.run(worker.workflow())
    except DomainError as error:
        assert error.code == "author_source_unverified", error.message
        worker.checkpoint(worker.task["result"], "blocked_runtime", error.message)
    else:
        raise AssertionError("fixture must stop on known semantic unknown")
    assert len(frames) == 1
    return worker, domain.task(task["id"]), frames
