"""Anonymous, truly ambiguous pronoun; native providers are MockTransport only."""

import asyncio
from copy import deepcopy
import json

import httpx
from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from ainovel import authored_source_scope as authored
from ainovel import author_source_clarification as bridge
from test_openai_schema import completed
from test_workflow_plan_source_roles import deepseek_completed

SOURCE = "掌柜和伙计都站在门内，他把账页交给来客。交接后两人继续等候，不离开门口。"
ADDITION = "本章交给来客的是伙计；掌柜只在旁等候。"
BODY = "伙计双手托平账页，等来客接稳，才慢慢松开指头。纸角还留在柜沿的阴影里，墨迹已经干了。掌柜没有催促，只把一只空茶碗移回柜内。门外的脚步经过又远去，两个人仍在门内等候，既没有跟出去，也没有再取别的账册。来客把账页折起时，伙计看着折痕避开纸边的旧印，才把手收回袖中。掌柜抬眼看了一下门缝，随即转回原处。柜台上空出一块窄窄的位置，原来压住纸角的小木块还在，两人谁也没有动它。门内安静下来，伙计靠着柜台，掌柜站在旁边，继续等候。"


def answer(frame, *, preserve="pass", phase_unknown=False):
    instruction = frame["任务"]
    action = instruction.get("动作")
    if action == authored.ACTION:
        text = instruction["作者本次情节原话"]
        clarified = "补充澄清" in text
        units = instruction["可引用原文"]
        return {
            "input_sha256": instruction["input_sha256"],
            "observations": [
                {
                    "observation_id": "O01",
                    "kind": "named_destination",
                    "supplier": "伙计" if clarified else "",
                    "recipient": "来客",
                    "transfer_object": "账页",
                    "transfer_scope": "completed",
                    "evidence": units[0]["source_id"],
                    "restriction_evidence": "",
                    "condition": "",
                    "reason": "初次他可回指两人；明确补充才据原话确认伙计，命名来客不表示排他。",
                }
            ],
            "unit_checks": [
                {
                    "source_id": u["source_id"],
                    "status": "covered" if i == 0 else "not_scope_related",
                    "observation_ids": ["O01"] if i == 0 else [],
                    "reason": "只观察原句的交付与范围；补充的主语说明和继续等候不新增接收范围。",
                }
                for i, u in enumerate(units)
            ],
            "note": "工程样例的明确已知语义，不评价文学。",
        }
    if action == "仅依据作者原话整理本场景情节":
        clarified = "补充澄清" in instruction["作者原话"]
        return {
            "items": [
                {
                    "author_evidence": "AUTHOR0001",
                    "actor": "伙计" if clarified else "他",
                    "action": "交给",
                    "object": "账页",
                    "direction": "交给来客",
                    "result": "交接完成，两人继续等候",
                    "certainty": "explicit" if clarified else "ambiguous",
                    "supplier": "伙计" if clarified else "",
                    "recipient": "来客",
                    "transfer_object": "账页",
                    "transfer_scope": "completed",
                    "recipient_scope": "unspecified",
                    "scope_condition": "",
                    "scope_observation_ids": ["O01"],
                    "scope_binding_status": "supported",
                    "scope_binding_reason": "首次主体回指真实不明；补充明确后保持原交付阶段和收件方。",
                }
            ],
            "note": "原话解释提案，非新增作者约束。",
        }
    if action == bridge.ACTION:
        result = {
            "source_checks": [
                {
                    "source_id": u["source_id"],
                    "result": preserve,
                    "evidence_ids": [u["source_id"]],
                    "reason": "原两人、交付完成、继续等候与否定均保持。",
                }
                for u in instruction["原话连续单元"]
            ],
            "addition_checks": [
                {
                    "source_id": u["source_id"],
                    "classification": "clarification"
                    if preserve == "pass"
                    else "direction_change"
                    if preserve == "fail"
                    else "unknown",
                    "evidence_ids": [u["source_id"]],
                    "reason": "仅明确他回指谁，不改变原结果或新增事件。",
                }
                for u in instruction["本次补充单元"]
            ],
            "fixed_checks": [
                {
                    "source_id": u["source_id"],
                    "result": "pass",
                    "evidence_ids": [u["source_id"]],
                    "reason": "保留原约定，非履约或文学判定。",
                }
                for u in instruction["本章及全书固定条件"]
            ],
            "author_contract_check": {"result": "pass", "reason": "未改变既有创作约定。"},
            "summary": "仅示例已知的原意兼容结果。",
        }
        return result
    if action == "按章纲写完整正文":
        return BODY
    if action == "审查正文":
        return {
            "checks": [
                {"condition": c, "result": "pass", "evidence": instruction["可引用原文"][0]["source_id"]}
                for c in instruction["固定条件"]
            ],
            "findings": [],
            "summary": "工程通过，非文学评价。",
        }
    if action == "逐项对照作者情节解释与本次正文":
        return {
            "checks": [
                {
                    "item_id": i["id"],
                    "result": "pass",
                    "observed": "伙计交付账页，二人仍等候。",
                    "evidence": instruction["可引用原文"][0]["source_id"],
                    "reason": "显式补充与原角色行动保持。",
                    "repair": "",
                }
                for i in instruction["独立整理的解释提案"]
            ],
            "summary": "已知工程输出。",
        }
    if action == "只从本次正文独立提取物品与信息流向":
        return {
            "flows": [
                {
                    "object_id": "O01",
                    "supplier_id": "E01",
                    "recipient_id": "E02",
                    "stage": "completed",
                    "certainty": "explicit",
                    "evidence": instruction["可引用原文"][0]["source_id"],
                    "note": "伙计交付来客。",
                }
            ],
            "note": "工程替身。",
        }
    from helpers.flow_entailment_fixture import flow_entailment_output

    if output := flow_entailment_output(instruction):
        return output
    if action == "从正文提取事实候选与章节摘要":
        return {"facts": [], "summary": "伙计交付账页，二人继续等候。"}
    raise AssertionError(action)


def native(worker, *, preserve="pass", timeout_action=None, observe_unknown=False):
    sent = []

    def handler(request):
        wire = json.loads(request.content)
        prompt = wire.get("input", wire.get("messages"))[1]["content"]
        frame = json.loads(prompt.split("\n只返回")[0])
        sent.append(deepcopy(frame))
        if frame["任务"].get("动作") == timeout_action:
            raise httpx.ReadTimeout("offline unknown source check", request=request)
        value = answer(frame, preserve=preserve)
        if observe_unknown and frame["任务"].get("动作") == authored.ACTION:
            value["observations"] = []
            for row in value["unit_checks"]:
                row.update(
                    status="unknown", observation_ids=[], reason="完整返回的来源语义未知，非网络/计费未知。"
                )
        if isinstance(value, str):
            return completed(value) if "input" in wire else deepseek_completed(value)
        return completed(dumps(value)) if "input" in wire else deepseek_completed(value)

    worker.client = ModelClient(worker.settings, worker.budget, httpx.MockTransport(handler))
    return sent


def source_unverified(
    settings, domain, story, *, client=None, chapters=3, number=2, account="deepseek", observe_unknown=False
):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="assisted",
        completion_scope="segment",
        brief="固定章节安排，后续规划保持。",
        chapters=chapters,
        plots=[
            {
                "number": n,
                "title": f"原创章{n}",
                "plot": SOURCE if n == number else "先测量再保留原记录，后续才核验。",
                "fixed": ["两人不离开门口"] if n == number else [],
            }
            for n in range(1, chapters + 1)
        ],
        account=account,
        min_chars=200,
        max_chars=600,
        adoption="manual",
        budget_limit=2,
        plot_contract_check=True,
        client_request_id="initial-ambiguous-author-source",
    ).model_dump(exclude_none=True)
    if client is not None:
        response = client.post("/api/workflows", json=payload)
        assert response.status_code == 202, response.text
        task = response.json()
    else:
        task = domain.queue("workflow", payload, story["branch_id"])
    branch = task["branch_id"]
    for n in range(1, chapters + 1):
        rows = domain.db.all(
            "SELECT * FROM chapters WHERE branch_id=? AND position=? AND archived=0", (branch, n)
        )
        chapter = rows[0] if rows else domain.create_chapter(branch, f"原创章{n}", n)
        if n < number:
            domain.save(chapter["id"], chapter["revision"], "前章已保存：伙计整理账页，掌柜守在门内。")
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == task["id"]
    worker.task["result"].update(
        plan={"chapters": deepcopy(task["payload"]["plots"]), "ending": "后续核验，未提前结局。"},
        next_chapter=number,
        repair_round=1,
        expected_story_revision=domain.branch(branch)["story_revision"],
    )
    worker.checkpoint(worker.task["result"])
    sent = native(worker, observe_unknown=observe_unknown)
    try:
        asyncio.run(worker.workflow())
    except DomainError as error:
        assert error.code == "author_source_unverified", error.message
        worker.checkpoint(worker.task["result"], "blocked_runtime", error.message)
    else:
        raise AssertionError("true pronoun ambiguity must stop before prose")
    assert len(sent) == (1 if observe_unknown else 2) and not domain.db.all(
        "SELECT * FROM artifacts WHERE task_id=?", (task["id"],)
    )
    return worker, domain.task(task["id"]), sent
