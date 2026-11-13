import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError
from ainovel.plot_audit import audit_plot, independent_flows
from test_reference_flow import Model, SOURCE, reference as reference, run, update

AUTHOR = "沈砚从货商手中取得铜环，但最终交易失败。"
TEXT = "沈砚把铜环带给货商，想换取铜料。双方未成交。"


def test_missing_author_plot_does_not_spend_or_infer_it_from_candidate():
    worker = Grounded()
    audited = asyncio.run(audit_plot(worker, "  ", TEXT, 1))
    assert audited["status"] == "unverified"
    assert not worker.requests and not audited["findings"]


class Grounded:
    def __init__(self, certainty="explicit", result="fail", invalid=None):
        self.certainty, self.result, self.invalid = certainty, result, invalid
        self.requests = []

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        self.requests.append(deepcopy({"instruction": instruction, "context": context}))
        if len(self.requests) == 1:
            value = {
                "items": [
                    {
                        "author_evidence": "AUTHOR0001",
                        "actor": "沈砚",
                        "action": "取得",
                        "object": "铜环",
                        "direction": "",
                        "result": "交易失败",
                        "certainty": self.certainty,
                    }
                ],
                "note": "解释提案",
            }
            if self.invalid == "author":
                value["items"][0]["author_evidence"] = TEXT
        else:
            value = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": self.result,
                        "observed": "沈砚提供铜环并想取得铜料",
                        "evidence": "PLOT001P0001" if self.result != "unknown" else "",
                        "reason": "对象流向与作者原话不同",
                        "repair": "保持沈砚取得铜环的方向",
                    }
                ],
                "summary": "逐项核对",
            }
            if self.invalid == "candidate":
                value["checks"][0]["evidence"] = AUTHOR
            if self.invalid == "duplicate":
                value["checks"].append(deepcopy(value["checks"][0]))
        value = schema.model_validate(value).model_dump()
        if issues := validate(value):
            raise DomainError("evidence", str(issues))
        return value


@pytest.mark.parametrize(
    "certainty,result,status,severity",
    [
        ("explicit", "fail", "failed", "blocker"),
        ("ambiguous", "fail", "unverified", "suggestion"),
        ("explicit", "unknown", "unverified", None),
        ("explicit", "pass", "model_pass", None),
    ],
)
def test_author_only_parse_then_grounded_comparison(certainty, result, status, severity):
    worker = Grounded(certainty, result)
    audited = asyncio.run(audit_plot(worker, AUTHOR, TEXT, 1))
    assert audited["status"] == status
    assert audited["items"][0]["author_evidence"] == AUTHOR
    assert TEXT not in str(worker.requests[0])
    assert TEXT in str(worker.requests[1])
    if severity:
        assert audited["findings"][0]["severity"] == severity
        assert audited["findings"][0]["quote"] == TEXT
    else:
        assert not audited["findings"]


@pytest.mark.parametrize("invalid", ["author", "candidate", "duplicate"])
def test_evidence_cannot_cross_roles_or_duplicate_requirements(invalid):
    with pytest.raises(DomainError, match="原话|正文|重复"):
        asyncio.run(audit_plot(Grounded(invalid=invalid), AUTHOR, TEXT, 1))


class Integrated(Model):
    def __init__(self):
        super().__init__()
        self.audits = []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        action = value["任务"].get("动作")
        if action == "按确认参考应用方案原创试写":
            return {"text": TEXT, "usage": {"input": 1, "output": 1}, "call_id": "offline"}
        if action == "仅依据作者原话整理本场景情节":
            self.audits.append(value)
            output = {
                "items": [
                    {
                        "author_evidence": "AUTHOR0001",
                        "actor": "沈砚",
                        "action": "取得",
                        "object": "铜环",
                        "direction": "货商交付铜环给沈砚",
                        "supplier": "货商",
                        "recipient": "沈砚",
                        "transfer_object": "铜环",
                        "transfer_scope": "attempted",
                        "result": "交易失败",
                        "certainty": "explicit",
                    }
                ],
                "note": "AI解释，未成为故事事实",
            }
        elif action == "逐项对照作者情节解释与本次正文":
            self.audits.append(value)
            output = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": "pass",
                        "observed": "沈砚带铜环换铜料",
                        "evidence": "PLOT001P0001",
                        "reason": "对象及流向反转",
                        "repair": "按原方向写",
                    }
                ],
                "summary": "发现情节不符",
            }
        elif action == "只从本次正文独立提取物品与信息流向":
            self.audits.append(value)
            output = {
                "flows": [
                    {
                        "object_id": "O01",
                        "supplier_id": "E01",
                        "recipient_id": "E02",
                        "stage": "offered",
                        "certainty": "explicit",
                        "evidence": "FLOW0001",
                        "note": "沈砚提供铜环",
                    }
                ],
                "note": "仅看正文，未见作者预期",
            }
        else:
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        return {
            "text": json.dumps(output, ensure_ascii=False),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline",
        }


@pytest.mark.parametrize("enabled", [False, True])
def test_optional_audit_is_frozen_in_task_and_does_not_adopt(
    client, settings, domain, story, reference, enabled
):
    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot=AUTHOR,
        fixed=["交易失败"],
        application_plan="保留作者指定交易方向",
    )
    before = domain.chapter(story["chapter_id"])
    model = Integrated()
    candidate, _ = run(client, settings, domain, row["id"], "draft", model=model, plot_contract_check=enabled)
    assert domain.chapter(story["chapter_id"]) == before
    assert not domain.db.all("SELECT * FROM facts")
    assert candidate["payload"]["review_passed"] is not enabled
    if enabled:
        assert len(model.audits) == 3
        assert TEXT not in str(model.audits[0]) and SOURCE not in str(model.audits[0])
        assert set(model.audits[0]["上下文"]) == {"scope"}
        assert set(model.audits[1]["上下文"]) == {"scope"}
        assert AUTHOR not in str(model.audits[2]) and SOURCE not in str(model.audits[2])
        assert set(model.audits[2]["上下文"]) == {"scope"}
        audit = candidate["payload"]["reference_comparison"]["plot_audit"]
        assert audit["status"] == "failed"
        assert audit["checks"][0]["guard_override"]["code"] == "reverse_transfer"
        assert candidate["payload"]["review"]["findings"][0]["quote"] == TEXT
    else:
        assert not model.audits
        assert "plot_audit" not in candidate["payload"]["reference_comparison"]


@pytest.mark.parametrize("change,code", [("cancel", "cancelled"), ("author_plot", "reference_changed")])
def test_change_after_author_parse_does_not_dispatch_comparison(
    client, settings, domain, story, reference, change, code
):
    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot=AUTHOR,
        application_plan="保持原交易方向",
    )

    class CancelAfterParse(Integrated):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            result = await super().generate(account, task_id, system, prompt, max_output, **kwargs)
            if len(self.audits) == 1:
                if change == "cancel":
                    assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200
                else:
                    update(client, domain, row, plot="作者已明确改为交换木料，不再是原情节。")
            return result

    model = CancelAfterParse()
    before = domain.chapter(story["chapter_id"])
    with pytest.raises(DomainError) as cancelled:
        run(client, settings, domain, row["id"], "draft", model=model, plot_contract_check=True)
    assert cancelled.value.code == code
    assert len(model.audits) == 1
    assert domain.chapter(story["chapter_id"]) == before
    assert not domain.db.all("SELECT * FROM artifacts WHERE kind='chapter_draft'")


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("reverse", "fail"),
        ("forward", "pass"),
        ("none", "unknown"),
        ("planned", "unknown"),
        ("both", "pass"),
        ("other_object", "unknown"),
    ],
)
def test_direction_guard_compares_roles_not_model_pass_claim(scenario, expected):
    reverse = {
        "object_id": "O01",
        "supplier_id": "E01",
        "recipient_id": "E02",
        "stage": "offered",
        "certainty": "explicit",
        "evidence": "FLOW0001",
        "note": "独立观察",
    }
    forward = {**reverse, "supplier_id": "E02", "recipient_id": "E01"}
    flows = {
        "reverse": [reverse],
        "forward": [forward],
        "none": [],
        "planned": [{**reverse, "stage": "planned"}],
        "both": [reverse, forward],
        "other_object": [{**reverse, "object_id": "other"}],
    }[scenario]

    class Observations:
        async def grounded(self, instruction, schema, context, validate, **kwargs):
            assert AUTHOR not in str(instruction)
            assert "作者预期" not in instruction
            value = schema.model_validate({"flows": flows, "note": "独立流向"}).model_dump()
            assert not validate(value)
            return value

    checks = [
        {
            "item_id": "P01",
            "result": "pass",
            "evidence": TEXT,
            "observed": "模型声称一致",
            "reason": "模型声称一致",
            "repair": "",
        }
    ]
    items = [
        {
            "id": "P01",
            "certainty": "explicit",
            "transfer_scope": "attempted",
            "transfer_object": "铜环",
            "supplier": "货商",
            "recipient": "沈砚",
        }
    ]
    asyncio.run(independent_flows(Observations(), items, TEXT, checks))
    assert checks[0]["result"] == expected
