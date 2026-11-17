"""Anonymous object naming guidance; independent evidence still determines support."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from ainovel import candidate_recheck
from ainovel.db import DomainError, dumps
from ainovel.flow_entailment import (
    ACTION,
    FIELDS,
    KEY,
    OBJECT_GUIDANCE_DETAIL,
    OBJECT_KEY,
    OBJECT_VERSION,
    VERSION,
)
from ainovel.plot_audit import VERSION as PLOT_VERSION, independent_flows
from test_source_interpretation_correction import stopped_source as stopped_source

POSITIVE = "匠人从旧工具箱取出一把尺壳磨旧的钢卷尺，递给周宁。周宁接过卷尺，放进随身袋，答应用完归还。"
OTHER = "匠人把钢卷尺收回工具箱，另递给周宁一根铅笔。周宁接过铅笔，放进随身袋。"
READING = "周宁站在岸边，只看见固定旧潮尺的红针指着1.55；没有人把量尺交给他。"


class BodyModel:
    def __init__(self, *, marker=OBJECT_VERSION, observed_object="named", verdict="supported"):
        self.task = {
            "kind": "candidate_recheck",
            "result": {
                "plot_contract_version": PLOT_VERSION,
                KEY: VERSION,
                "candidate_recheck_snapshot": {} if marker is None else {OBJECT_KEY: marker},
            },
        }
        self.observed_object, self.verdict = observed_object, verdict
        self.frames = []

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        self.frames.append(deepcopy(instruction))
        unit = instruction["可引用原文"][0]["source_id"]
        if instruction["动作"] == "只从本次正文独立提取物品与信息流向":
            objects = instruction["待识别物品名称"]
            entities = {name: key for key, name in instruction["实体别名对照"].items()}
            value = {
                "flows": [
                    {
                        "object_id": next(iter(objects)) if self.observed_object == "named" else "other",
                        "supplier_id": entities["匠人"],
                        "recipient_id": entities["周宁"],
                        "stage": "completed",
                        "certainty": "explicit",
                        "evidence": unit,
                        "note": "只据本次正文提出对象与实际取得提案，仍须独立核实。",
                    }
                ],
                "note": "匿名工程提案，不代表真实模型已识别。",
            }
        else:
            assert instruction["动作"] == ACTION
            value = {
                "input_sha256": instruction["input_sha256"],
                "checks": [
                    {
                        "flow_id": row["flow_id"],
                        "result": self.verdict,
                        "verified_fields": list(FIELDS) if self.verdict == "supported" else [],
                        "evidence": unit,
                        "reason": "独立回全文核物品本体/同指与交付，不采用旧结论。",
                    }
                    for row in instruction["待核对观察提案"]
                ],
                "participants": [],
                "instances": [],
                "note": "匿名独立结果。",
            }
        result = schema.model_validate(value).model_dump()
        issues = validate(result)
        assert issues == []
        return result


def compare(model, text=POSITIVE):
    item = {
        "id": "P01",
        "author_evidence": "借旧尺。",
        "actor": "周宁",
        "action": "借旧尺",
        "object": "旧尺",
        "direction": "匠人→周宁",
        "result": "借得后归还",
        "certainty": "explicit",
        "transfer_object": "旧尺",
        "supplier": "匠人",
        "recipient": "周宁",
        "transfer_scope": "completed",
        "recipient_scope": "unspecified",
        "scope_condition": "用完归还",
    }
    check = {
        "item_id": "P01",
        "result": "pass",
        "observed": "原检查声称一致",
        "evidence": text,
        "reason": "需独立核实",
        "repair": "",
    }
    before = deepcopy(item)
    result = asyncio.run(independent_flows(model, [item], text, [check], version=PLOT_VERSION))
    assert item == before
    return result, check


def test_same_borrowed_object_concrete_name_needs_both_independent_supported_steps():
    model = BodyModel()
    report, check = compare(model)
    assert check["result"] == "pass"
    assert len(model.frames) == 2
    assert report["source_entailment"]["checks"][0]["result"] == "supported"
    for frame in model.frames:
        assert frame["物品辨认执行模板"] == OBJECT_VERSION
        assert frame["物品同指与内容边界"] == OBJECT_GUIDANCE_DETAIL
        assert "不能单独证明" in frame["物品同指与内容边界"]
        assert all(key not in frame for key in ("作者原话", "预期方向", "旧报告", "固定条件"))
        assert "借旧尺。" not in json.dumps(frame, ensure_ascii=False)


@pytest.mark.parametrize(
    "text,observed,verdict",
    [
        (OTHER, "other", "supported"),
        (OTHER, "named", "contradicted"),
        (READING, "named", "unknown"),
        (POSITIVE, "named", "unknown"),
        (POSITIVE, "other", "supported"),
    ],
)
def test_other_instance_reading_or_missing_independent_support_cannot_pass(text, observed, verdict):
    model = BodyModel(observed_object=observed, verdict=verdict)
    report, check = compare(model, text)
    assert check["result"] == "unknown"
    if observed == "other":
        assert len(model.frames) == 1
        assert report["flows"][0]["object_id"] == "other"
        assert report["source_entailment"]["status"] == "not_applicable"
    else:
        assert len(model.frames) == 2
        assert report["source_entailment"]["supported_observations"] == []


def test_unmarked_old_requests_keep_the_exact_legacy_frames():
    model = BodyModel(marker=None)
    compare(model)
    assert len(model.frames) == 2
    assert all(
        "物品辨认执行模板" not in frame and "物品同指与内容边界" not in frame for frame in model.frames
    )


def test_unknown_template_marker_refuses_before_any_model_frame():
    model = BodyModel(marker="future/9")
    with pytest.raises(DomainError, match="冻结执行模板"):
        compare(model)
    assert model.frames == []


def test_new_recheck_freezes_guidance_and_historical_nonce_preserves_absence(client, domain, stopped_source):
    _parent, source = stopped_source
    body = {
        "candidate_revision": 0,
        "account": "openai_1",
        "client_request_id": "anonymous-object-new-recheck",
    }
    response = client.post(f"/api/artifacts/{source['id']}/recheck", json=body)
    assert response.status_code == 202, response.text
    current = response.json()
    snapshot = current["result"]["candidate_recheck_snapshot"]
    assert snapshot[OBJECT_KEY] == OBJECT_VERSION
    assert candidate_recheck.validate(SimpleNamespace(task=current, domain=domain)) == snapshot
    old_snapshot = candidate_recheck.freeze(
        domain,
        current["payload"],
        conditional_policy=snapshot.get("conditional_book_policy_version"),
        applicability_version=snapshot.get("book_applicability_version"),
        flow_entailment_version=snapshot[KEY],
    )
    assert OBJECT_KEY not in old_snapshot
    old = deepcopy(current)
    old["result"]["candidate_recheck_snapshot"] = old_snapshot
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(old["result"]), old["id"]))
    assert candidate_recheck.validate(SimpleNamespace(task=old, domain=domain)) == old_snapshot
    before = domain.db.all("SELECT * FROM tasks")
    replay = client.post(f"/api/artifacts/{source['id']}/recheck", json=body)
    assert replay.status_code == 202 and replay.json() == old
    assert domain.db.all("SELECT * FROM tasks") == before
