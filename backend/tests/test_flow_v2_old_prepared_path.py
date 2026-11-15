"""Explicit new prose recheck preserves a historical wrong source-only field."""

import asyncio
from copy import deepcopy
import json

from ainovel.db import dumps
from ainovel.flow_entailment import ACTION, FIELDS, KEY, LEGACY_VERSION, VERSION
from ainovel.worker import Worker
from helpers.historical_plan_task import queue_historical_plan
from helpers.plan_execution_fixture import plan_execution_output
from test_candidate_recheck import queue_recheck
from test_workflow_plan_preparation import PreparationModel
from test_workflow_scope import workflow_payload

SOURCE = "林舟把检测记录抄一份寄往设备核验站请求核查，回执尚未收到。"
TEXT = (
    "林舟把检测记录抄件寄往设备核验站请求核查，回执未到。"
    "修理师傅陈伯看着林舟另抄一份随身副本，再递还林舟；这不是已寄出的那份。"
    + "铺门边的风翻动记录，林舟把两份材料的去向分别记清。"
    * 8
)


class Model(PreparationModel):
    def __init__(self, recheck=False):
        super().__init__()
        self.actions = []
        self.recheck = recheck

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        action = instruction["动作"]
        self.actions.append(action)
        if self.recheck:
            assert action not in {
                "阶段批次规划",
                "写前解释AI工作章纲",
                "独立核对AI工作章纲保持",
                "按章纲写完整正文",
            }
        if action == "阶段批次规划":
            output = {
                "chapters": [{"number": 1, "title": "寄件", "plot": SOURCE, "fixed": []}],
                "ending": "后续核查",
            }
        elif action == "写前解释AI工作章纲":
            output = plan_execution_output(instruction)
            output["items"][0].update(
                actor="林舟",
                action="抄写并寄出",
                object="检测记录",
                direction="林舟向设备核验站",
                result="已寄出；回执未到",
                certainty="explicit",
                transfer_object="检测记录的抄件",
                supplier="林舟",
                recipient="设备核验站",
                transfer_scope="completed",
                recipient_scope="only",
                scope_condition="寄往设备核验站请求核查",
                open_fields=[],
                open_reason="",
            )
        elif action == "按章纲写完整正文":
            output = TEXT
        elif action == "逐项对照作者情节解释与本次正文":
            output = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": "pass",
                        "observed": "林舟寄出一份抄件，另一份后来随身。",
                        "evidence": instruction["可引用原文"][0]["source_id"],
                        "reason": "原创隔离替身，只核对当前正文，不认可旧来源only。",
                        "repair": "",
                    }
                ],
                "summary": "正文工程核对",
            }
        elif action == "只从本次正文独立提取物品与信息流向":
            ids = {name: identifier for identifier, name in instruction["实体别名对照"].items()}
            oid = next(iter(instruction["待识别物品名称"]))
            evidence = instruction["可引用原文"][0]["source_id"]
            output = {
                "flows": [
                    {
                        "object_id": oid,
                        "supplier_id": ids["林舟"],
                        "recipient_id": ids["设备核验站"],
                        "stage": "completed",
                        "certainty": "explicit",
                        "evidence": evidence,
                        "note": "寄出抄件。",
                    },
                    {
                        "object_id": oid,
                        "supplier_id": "unknown",
                        "recipient_id": ids["林舟"],
                        "stage": "completed",
                        "certainty": "explicit",
                        "evidence": evidence,
                        "note": "师傅递回另抄的随身副本，非已寄那份。",
                    },
                ],
                "note": "原创工程观察",
            }
        elif action == ACTION:
            evidence = instruction["可引用原文"][0]["source_id"]
            output = {
                "input_sha256": instruction["input_sha256"],
                "checks": [
                    {
                        "flow_id": f["flow_id"],
                        "result": "supported",
                        "verified_fields": list(FIELDS),
                        "evidence": evidence,
                        "reason": "原文支持两次交接，不把表外编号当已知身份。",
                    }
                    for f in instruction["待核对观察提案"]
                ],
                "note": "原创工程蕴含检查",
            }
            if instruction["流向来源蕴含版本"] == VERSION:
                sender = next(k for k, v in instruction["实体别名对照"].items() if v == "林舟")
                output.update(
                    participants=[
                        {
                            "flow_id": "F02",
                            "side": "supplier_id",
                            "status": "known_other",
                            "label": "修理师傅陈伯",
                            "excluded_entity_ids": [sender],
                            "evidence": evidence,
                            "reason": "陈伯与林舟在连续原文中为两人；不猜陈伯是否任职核验站。",
                        }
                    ],
                    instances=[
                        {
                            "left_flow_id": "F01",
                            "right_flow_id": "F02",
                            "result": "different",
                            "kind": "physical",
                            "evidence": evidence,
                            "reason": "明确另抄且不是已寄那份，不声明信息内容不同。",
                        }
                    ],
                )
        elif action == "从正文提取事实候选与章节摘要":
            output = {"facts": [], "summary": "抄件寄出、回执未到，未采纳任何事实。"}
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.calls += 1
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": f"original-flow-fixture-{self.calls}",
        }


def test_new_explicit_v2_recheck_uses_old_frozen_only_without_reinterpreting_or_adopting(
    settings, domain, story, client
):
    payload = workflow_payload(story, mode="automatic")
    payload.update(plot_contract_check=True, client_request_id="historical-old-flow-only")
    queued = queue_historical_plan(domain, payload, story["branch_id"])
    assert queued["result"][KEY] == LEGACY_VERSION
    asyncio.run(Worker(settings, Model()).run_once())
    parent = domain.task(queued["id"])
    assert parent["status"] == "needs_review", parent["error"]
    candidate = domain.artifact(parent["result"]["waiting_artifact"])
    assert candidate["candidate_revision"] == 0 and candidate["payload"]["text"] == TEXT
    original = deepcopy(parent)
    prepared = deepcopy(parent["result"]["plot_preparations"]["1"]["prepared_contract"])
    assert prepared["items"][0]["recipient_scope"] == "only" and "只" not in SOURCE
    assert candidate["payload"]["plot_audit"]["checks"][0]["result"] == "unknown"
    chapter = domain.chapter(candidate["chapter_id"])
    response = queue_recheck(client, candidate, client_request_id="explicit-new-v2-same-r0")
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["result"][KEY] == VERSION
    assert task["result"]["candidate_recheck_snapshot"]["prepared_contract"] == prepared
    model = Model(recheck=True)
    asyncio.run(Worker(settings, model).run_once())
    final = domain.task(task["id"])
    assert final["status"] == "succeeded", final["error"]
    saved_parent = domain.task(parent["id"])
    assert {k: v for k, v in saved_parent.items() if k != "updated"} == {
        k: v for k, v in original.items() if k != "updated"
    }
    assert saved_parent["updated"] >= original["updated"]
    audit_events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='candidate_rechecked'",
        (parent["id"],),
    )
    assert len(audit_events) == 1
    assert json.loads(audit_events[0]["payload"])["previous_candidate"] == candidate["payload"]
    assert domain.chapter(candidate["chapter_id"]) == chapter
    updated = domain.artifact(candidate["id"])
    assert updated["candidate_revision"] == 0 and updated["payload"]["text"] == TEXT
    assert updated["payload"]["plot_audit"]["checks"][0]["result"] == "pass"
    assert updated["payload"]["plot_audit"]["items"][0]["recipient_scope"] == "only"
    assert updated["payload"]["previous_plot_audit"] == candidate["payload"]["plot_audit"]
    assert original["result"][KEY] == LEGACY_VERSION
    assert original["result"]["plot_preparations"]["1"]["original_plot"] == SOURCE
    assert sum(a == ACTION for a in model.actions) == 1
    assert sum(a == "只从本次正文独立提取物品与信息流向" for a in model.actions) == 1
    assert not domain.db.all("SELECT * FROM facts")
    again = queue_recheck(client, candidate, client_request_id="explicit-new-v2-same-r0")
    assert again.status_code == 202 and again.json() == final
