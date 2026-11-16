"""Original archived automatic source for explicit scope-only correction tests."""

import asyncio
from copy import deepcopy
import json

from ainovel.db import dumps
from ainovel.worker import Worker
from ainovel.workflow_plan_preparation import prepare
from helpers.historical_plan_task import queue_historical_plan
from test_workflow_scope import workflow_payload
from test_workflow_plan_preparation import PreparationModel

SOURCE = "林舟抄一份测绘记录寄往青石测量所请求校核，回执尚未到。师傅只准携带另抄的草图，不准把原件带走。此事暂不告知顾平。"
BODY = (
    "BODY_ONLY_SENTINEL😀。"
    + "林舟寄出测绘记录抄本，回执尚未到。他保留原件，另抄草图供外出携带，也没有告知顾平。" * 6
)


def old_contract(text=SOURCE):
    return {
        "items": [
            {
                "actor": "林舟",
                "action": "抄写并寄出",
                "object": "测绘记录抄本",
                "direction": "林舟寄给青石测量所",
                "result": "已寄出，回执未到",
                "certainty": "explicit",
                "author_evidence": text,
                "transfer_object": "测绘记录抄本",
                "supplier": "林舟",
                "recipient": "青石测量所",
                "transfer_scope": "completed",
                "recipient_scope": "only",
                "scope_condition": "寄往青石测量所请求校核",
            }
        ],
        "note": "ARCHIVED_INTERPRETATION_SENTINEL；仅为原创历史解释夹具，scope错误保留待纠偏。",
    }


class ArchivedModel(PreparationModel):
    def __init__(self, note=None):
        super().__init__()
        self.note = note

    async def generate(self, account, task_id, system, prompt, max_output, **kw):
        frame = json.loads(prompt.split("\n只返回")[0])
        i = frame["任务"]
        if i.get("动作") == "写前解释AI工作章纲":
            self.calls += 1
            result = old_contract()
            if self.note is not None:
                result["note"] = self.note
            result["items"][0]["author_evidence"] = i["可引用原文"][0]["source_id"]
            return {"text": dumps(result), "usage": {"input": 1, "output": 1}, "call_id": "archived-original"}
        return await super().generate(account, task_id, system, prompt, max_output, **kw)


def stopped(settings, domain, story, *, account="openai_1", note=None):
    payload = workflow_payload(story, mode="automatic", adoption="manual")
    payload.update(plot_contract_check=True, budget_limit=5, account=account, brief="FUTURE_BRIEF_SENTINEL")
    task = queue_historical_plan(domain, payload, story["branch_id"], open_details=False)
    worker = Worker(settings, ArchivedModel(note))
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    plot = {"number": 1, "title": "寄出抄本", "plot": SOURCE, "fixed": ["回执尚未到，不认定记录伪造。"]}
    worker.task["result"].update(plan={"chapters": [plot], "ending": "FUTURE_PLAN_SENTINEL"}, next_chapter=1)
    worker.checkpoint(worker.task["result"])
    asyncio.run(prepare(worker, plot, domain.chapter(story["chapter_id"])))
    parent = domain.task(task["id"])
    candidate = domain.stage(
        {**payload, "branch_id": story["branch_id"], "chapter_id": story["chapter_id"], "base_revision": 0},
        "chapter_draft",
        {
            "text": BODY,
            "fixed": payload["fixed"] + plot["fixed"],
            "review_passed": False,
            "review": {"summary": "OLD_REVIEW_SENTINEL"},
            "plot_audit": {
                "version": "author-plot-audit/2",
                "source_kind": "workflow_plan",
                "items": old_contract()["items"],
                "status": "unverified",
                "summary": "OLD_BODY_CHECK_SENTINEL",
            },
            "memory": {},
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
        parent["id"],
    )
    state = {
        **deepcopy(parent["result"]),
        "next_chapter": 1,
        "waiting_artifact": candidate["id"],
        "latest_artifact": candidate["id"],
        "repair_round": 1,
    }
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='needs_review',pause=1,result=? WHERE id=?", (dumps(state), parent["id"])
        )
    return domain.task(parent["id"]), candidate


def inventory_output(i, *, kind="named_destination", unknown=False):
    units = i["可引用原文"]
    observations = [
        {
            "observation_id": "O01",
            "kind": "unknown" if unknown else kind,
            "supplier": "林舟",
            "recipient": "青石测量所",
            "transfer_object": "测绘记录抄本",
            "transfer_scope": "completed",
            "evidence": units[0]["source_id"],
            "restriction_evidence": "" if kind == "named_destination" else units[0]["source_id"],
            "condition": "",
            "reason": "原始工程样例：命名收件方没有排除其他接收者；语义结论仍须第二阶段独立核对。",
        }
    ]
    return {
        "input_sha256": i["input_sha256"],
        "observations": observations,
        "unit_checks": [
            {
                "source_id": u["source_id"],
                "status": "covered" if n == 0 else "not_scope_related",
                "observation_ids": ["O01"] if n == 0 else [],
                "reason": "确定性原创夹具，专项范围语义由调用者显式提供。",
            }
            for n, u in enumerate(units)
        ],
        "note": "仅离线工程替身，不能证明中文语义或文学质量。",
    }


def preservation_output(i, *, verdict="pass", sufficient="yes"):
    return {
        "item_checks": [
            {
                "item_id": r["item_id"],
                "result": verdict,
                "evidence": i["可引用原文"][0]["source_id"],
                "reason": "独立原文与全部事项核对的工程替身；非来源事实认证。",
                "scope_only_fix_sufficient": sufficient,
            }
            for r in i["原全部P提案"]
        ],
        "fixed_checks": [
            {
                "condition_id": r["condition_id"],
                "result": verdict,
                "evidence": r["condition_id"],
                "reason": "保持原固定条件，不要求后章提前兑现。",
            }
            for r in i["原固定条件"]
        ],
        "summary": "独立保持工程替身。",
    }
