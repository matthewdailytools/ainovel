"""The new explicit body task retains book provenance and chapter obligations."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.book_applicability import ACTION as APP_ACTION
from ainovel.db import dumps
from ainovel.worker import Worker
from test_source_interpretation_correction import (
    CorrectedRecheckModel,
    recheck_basis,
    run_source,
    stopped_source as source_fixture,
)

stopped_source = source_fixture


class ConditionalCorrectedModel(CorrectedRecheckModel):
    def __init__(self, chapter_result):
        super().__init__()
        self.chapter_result = chapter_result
        self.review_rows = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        if instruction.get("动作") != "审查正文":
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        self.actions.append("审查正文")
        self.review_rows = deepcopy(instruction["固定条件编号表"])
        quote = instruction["可引用原文"][0]["source_id"]
        checks = []
        for row in self.review_rows:
            conditional = "若涉及" in row["condition"]
            result = (
                "not_triggered"
                if conditional
                else self.chapter_result
                if row["scope"] == "chapter"
                else "pass"
            )
            checks.append(
                {
                    "condition": row["condition_id"],
                    "result": result,
                    "scope": row["scope"],
                    "applicability": "not_triggered" if conditional else "unconditional",
                    "chapter_obligation": "required" if row["scope"] == "chapter" else "not_required",
                    "evidence": "" if conditional or result == "unknown" else quote,
                    "explanation": "本章未触发的全书约定保留整书待核，本章必达独立核对，不提前兑现后章。",
                }
            )
        return {
            "text": dumps({"checks": checks, "findings": [], "summary": "隔离工程条件适用性核对"}),
            "usage": {"input": 1, "output": 1},
            "call_id": "conditional-corrected-review",
        }


@pytest.mark.parametrize("chapter_result", ["pass", "unknown"])
def test_explicit_corrected_worker_retains_book_absence_pending_and_chapter_must(
    settings, domain, client, stopped_source, chapter_result
):
    parent, candidate = stopped_source
    mother = "若涉及母亲，其去向和生死保持未证实；后章才核对完整去向。"
    ending = "全书末章才公开有依据的责任归属。"
    request = deepcopy(parent["payload"])
    request["fixed"] = [mother, ending]
    payload = deepcopy(candidate["payload"])
    payload["fixed"] = request["fixed"] + request["plots"][0]["fixed"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(request), parent["id"]))
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))
    candidate = domain.artifact(candidate["id"])
    source_task, _ = run_source(settings, domain, client, candidate)
    assert source_task["result"]["source_correction_state"] == "ready"
    assert not source_task["payload"].get("plot_contract_check")
    receipt_id = source_task["result"]["artifact_id"]
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "conditional-corrected-body-request",
            "receipt_id": receipt_id,
            "preview_basis_sha256": recheck_basis(client, candidate, receipt_id),
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["payload"]["plot_contract_check"] is True
    model = ConditionalCorrectedModel(chapter_result)
    asyncio.run(Worker(settings, model).run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    report = domain.artifact(task["result"]["artifact_id"])["payload"]
    review = report["review"]
    assert [row["scope"] for row in model.review_rows] == ["book", "book", "chapter"]
    assert model.actions.index(APP_ACTION) < model.actions.index("审查正文")
    assert review["checks"][0]["result"] == "not_triggered"
    assert review["checks"][2]["result"] == chapter_result
    assert review["book_applicability"]["checks"][0]["trigger"] == "not_triggered"
    assert [row["condition"] for row in review["book_checks_pending"]] == [mother, ending]
    assert all(row["status"] == "whole_book_pending" for row in review["book_checks_pending"])
    assert report["review_passed"] is (chapter_result == "pass")
    assert not any(action == "用局部替换修订正文" for action in model.actions)
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
