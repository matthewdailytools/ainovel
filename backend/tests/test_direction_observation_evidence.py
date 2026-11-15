"""Direction guards retain unknown identities and distinguish offers from delivery."""

import asyncio
from copy import deepcopy

import pytest

from ainovel.db import DomainError
from ainovel.plot_audit import LEGACY_VERSION, VERSION, audit_plot, independent_flows
from ainovel.plot_identity_context import VERSION as IDENTITY_VERSION


SOURCE = (
    "林舟向渡口货商换旧铜环，对方加价后交易失败，双方均未交付；林舟另从渡口一位修船匠借得旧尺，约定后日归还。"
)
TEXT = (
    "渡口货商赵掌柜把旧铜环摆到柜上，向林舟报了新价。林舟摇头，钱没付，铜环仍在柜里。"
    "渡口西边的吴伯正在修船摊给舢板捻缝。他从箱里拿出旧尺，递给林舟。林舟接住旧尺，约定后日归还。"
)


def source_items():
    common = {
        "author_evidence": SOURCE,
        "actor": "林舟",
        "certainty": "explicit",
        "recipient": "林舟",
        "recipient_scope": "unspecified",
        "scope_condition": "",
    }
    return [
        {
            **common,
            "id": "P01",
            "action": "换取失败",
            "object": "旧铜环",
            "direction": "货商向林舟提议给出旧铜环",
            "result": "交易失败，双方未交付",
            "transfer_object": "旧铜环",
            "supplier": "货商",
            "transfer_scope": "attempted",
        },
        {
            **common,
            "id": "P02",
            "action": "借得",
            "object": "旧尺",
            "direction": "修船匠向林舟借出旧尺",
            "result": "取得旧尺并约定归还",
            "transfer_object": "旧尺",
            "supplier": "修船匠",
            "transfer_scope": "completed",
        },
    ]


def model_checks():
    return [
        {
            "item_id": item["id"],
            "result": "pass",
            "observed": "隔离替身的原始逐项结论",
            "evidence": TEXT,
            "reason": "仅用于工程回归，不能代替模型质量验收。",
            "repair": "",
        }
        for item in source_items()
    ]


class Observations:
    def __init__(self, *, unknown=(), ruler_stage="completed", mutate=None, version=VERSION):
        self.task = {
            "payload": {"source_identity_context": version == VERSION},
            "result": {"plot_contract_version": version},
        }
        if version == VERSION:
            self.task["result"]["source_identity_context_version"] = IDENTITY_VERSION
        self.unknown = unknown
        self.ruler_stage = ruler_stage
        self.mutate = mutate
        self.frames = []

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        self.frames.append((deepcopy(instruction), deepcopy(context)))
        action = instruction["动作"]
        source = instruction["可引用原文"][0]["source_id"]
        if action == "仅从情节原话补全角色称谓限定":
            value = {
                "labels": [
                    {
                        "entity_id": eid,
                        "label": "渡口一位修船匠" if name == "修船匠" else name,
                        "status": "supported",
                        "evidence": source,
                        "reason": "只补充原话的角色限定，不添加正文姓名或方向。",
                    }
                    for eid, name in instruction["原角色名称表"].items()
                ]
            }
        elif action == "独立核对原话角色称谓限定":
            value = {
                "checks": [
                    {
                        "entity_id": row["entity_id"],
                        "result": "pass",
                        "evidence": source,
                        "reason": "仅原话核实。",
                    }
                    for row in instruction["限定提案"]
                ]
            }
        elif action == "只从本次正文独立提取物品与信息流向":
            entities = {name: eid for eid, name in instruction["实体别名对照"].items()}
            objects = {name: oid for oid, name in instruction["待识别物品名称"].items()}
            value = {
                "flows": [
                    {
                        "object_id": objects[obj],
                        "supplier_id": "unknown" if index in self.unknown else entities[supplier],
                        "recipient_id": entities["林舟"],
                        "stage": stage,
                        "certainty": "explicit",
                        "evidence": source,
                        "note": "只按本次正文及无方向名称表生成的隔离观察。",
                    }
                    for index, (obj, supplier, stage) in enumerate(
                        [
                            ("旧铜环", "货商", "offered"),
                            (
                                "旧尺",
                                "渡口一位修船匠" if "渡口一位修船匠" in entities else "修船匠",
                                self.ruler_stage,
                            ),
                        ]
                    )
                ],
                "note": "角色对应不能核实时保留unknown，不借原情节或旧报告补齐。",
            }
            if self.mutate:
                self.mutate(value)
        else:
            raise AssertionError(action)
        result = schema.model_validate(value).model_dump()
        issues = validate(result)
        if issues:
            raise DomainError("evidence", "；".join(issues))
        return result


def compare(worker, *, version=VERSION):
    items, checks = source_items(), model_checks()
    before = deepcopy(items)
    result = asyncio.run(independent_flows(worker, items, TEXT, checks, version=version, source_text=SOURCE))
    assert items == before
    return result, checks


def test_verified_source_labels_do_not_make_unmapped_prose_suppliers_match():
    result, checks = compare(Observations(unknown=(0, 1)))
    assert result["source_identity_context"]["unverified_ids"] == []
    assert all(row["result"] == "pass" for row in result["source_identity_context"]["checks"])
    assert [row["result"] for row in checks] == ["unknown", "unknown"]
    assert all(row["original_model_check"]["result"] == "pass" for row in checks)
    assert all("独立且同向" in row["reason"] for row in checks)
    assert [flow["stage"] for flow in result["flows"]] == ["offered", "completed"]
    assert all(flow["supplier_id"] == "unknown" for flow in result["flows"])


def test_actual_mapped_direction_and_stage_need_no_identity_guess_or_source_rewrite():
    worker = Observations()
    result, checks = compare(worker)
    assert [row["result"] for row in checks] == ["pass", "pass"]
    assert all("original_model_check" not in row for row in checks)
    source_context = result["source_identity_context"]
    assert "渡口一位修船匠" in source_context["entities"].values()
    observation, context = worker.frames[-1]
    assert observation["本次正文"] == TEXT
    assert SOURCE not in str(observation) and SOURCE not in str(context)
    assert "supplier" not in observation and "recipient" not in observation
    assert "transfer_scope" not in observation and "原始逐项结论" not in str(observation)
    assert "赵掌柜" not in str(source_context) and "吴伯" not in str(source_context)


@pytest.mark.parametrize("index", [0, 1])
def test_one_unmapped_participant_only_holds_its_own_item(index):
    _, checks = compare(Observations(unknown=(index,)))
    assert checks[index]["result"] == "unknown"
    assert checks[1 - index]["result"] == "pass"


def test_reverse_direction_is_a_failure_with_exact_observation_evidence():
    def reverse(value):
        for flow in value["flows"]:
            flow["supplier_id"], flow["recipient_id"] = flow["recipient_id"], flow["supplier_id"]

    result, checks = compare(Observations(mutate=reverse))
    assert [row["result"] for row in checks] == ["fail", "fail"]
    assert all(row["guard_override"]["code"] == "reverse_transfer" for row in checks)
    assert all(row["evidence"] == TEXT for row in checks)
    assert all(row["evidence"] == TEXT for row in result["flows"])


@pytest.mark.parametrize("stage", ["planned", "refused", "unknown"])
def test_a_future_refused_or_unknown_ruler_transfer_is_not_completed(stage):
    _, checks = compare(Observations(ruler_stage=stage))
    assert checks[0]["result"] == "pass"
    assert checks[1]["result"] == "unknown"
    assert checks[1]["original_model_check"]["result"] == "pass"


@pytest.mark.parametrize("stage", ["offered", "requested"])
def test_an_offer_or_request_alone_does_not_prove_a_required_completed_transfer(stage):
    _, checks = compare(Observations(ruler_stage=stage))
    assert checks[0]["result"] == "pass"
    assert checks[1]["result"] == "unknown"
    assert checks[1]["original_model_check"]["result"] == "pass"


@pytest.mark.parametrize("defect", ["quote", "entity_id", "object_id", "blank_quote"])
def test_invalid_independent_observation_cannot_be_used_to_repair_a_guard(defect):
    def mutate(value):
        row = value["flows"][1]
        if defect == "quote":
            row["evidence"] = "旧稿里有的给尺话，本章并不存在。"
        elif defect == "entity_id":
            row["supplier_id"] = "E999"
        elif defect == "object_id":
            row["object_id"] = "O999"
        else:
            row["evidence"] = ""

    with pytest.raises(DomainError):
        compare(Observations(mutate=mutate))


@pytest.mark.parametrize("stage", ["offered", "requested", "completed"])
def test_reverse_offer_does_not_prove_delivery_but_actual_reverse_delivery_still_fails(stage):
    def reverse_ruler(value):
        flow = value["flows"][1]
        flow["supplier_id"], flow["recipient_id"] = flow["recipient_id"], flow["supplier_id"]

    _, checks = compare(Observations(ruler_stage=stage, mutate=reverse_ruler))
    assert checks[0]["result"] == "pass"
    assert checks[1]["result"] == ("fail" if stage == "completed" else "unknown")
    assert checks[1]["original_model_check"]["result"] == "pass"


@pytest.mark.parametrize("stage", ["offered", "requested"])
def test_legacy_stage_matching_and_observation_prompt_remain_unchanged(stage):
    worker = Observations(ruler_stage=stage, version=LEGACY_VERSION)
    observed, checks = compare(worker, version=LEGACY_VERSION)
    assert checks[1]["result"] == "pass"
    assert "role_observation_version" not in observed
    assert "角色流向观察版本" not in worker.frames[-1][0]
    assert "独立角色映射" not in worker.frames[-1][0]


class PromptWorker(Observations):
    async def grounded(self, instruction, schema, context, validate, **kwargs):
        action = instruction["动作"]
        if action not in {"仅依据作者原话整理本场景情节", "逐项对照作者情节解释与本次正文"}:
            return await super().grounded(instruction, schema, context, validate, **kwargs)
        self.frames.append((deepcopy(instruction), deepcopy(context)))
        source = instruction["可引用原文"][0]["source_id"]
        if action == "仅依据作者原话整理本场景情节":
            items = [{k: v for k, v in item.items() if k != "id"} for item in source_items()]
            for item in items:
                item["author_evidence"] = source
                if self.task["result"]["plot_contract_version"] == LEGACY_VERSION:
                    item.pop("recipient_scope")
                    item.pop("scope_condition")
            value = {"items": items, "note": "独立原话解释，未读取候选。"}
        else:
            checks = model_checks()
            for check in checks:
                check["evidence"] = source
            value = {"checks": checks, "summary": "隔离对照输出。"}
        result = schema.model_validate(value).model_dump()
        issues = validate(result)
        if issues:
            raise DomainError("evidence", "；".join(issues))
        return result


@pytest.mark.parametrize("version", [LEGACY_VERSION, VERSION])
def test_stage_and_identity_observation_guidance_are_versioned_only_for_v2(version):
    worker = PromptWorker(version=version)
    report = asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert report["status"] == "model_pass"
    compare_frame = next(
        instruction
        for instruction, _ in worker.frames
        if instruction["动作"] == "逐项对照作者情节解释与本次正文"
    )
    observe_frame = worker.frames[-1][0]
    interpret_frame = worker.frames[0][0]
    assert "情节阶段对照版本" not in interpret_frame
    if version == VERSION:
        assert report["comparison_guidance_version"] == "stage-aware-comparison/1"
        assert compare_frame["情节阶段对照版本"] == "stage-aware-comparison/1"
        assert "planned" in compare_frame["按冻结事项区分行动阶段"]
        assert "completed" in compare_frame["按冻结事项区分行动阶段"]
        assert "被拦停" in compare_frame["按冻结事项区分行动阶段"]
        assert "同一个最终判断" in compare_frame["最终判断一致性"]
        assert report["direction_guard"]["role_observation_version"] == "source-role-observation/1"
        assert observe_frame["角色流向观察版本"] == "source-role-observation/1"
        assert "不能仅因姓名不同一律填unknown" in observe_frame["独立角色映射"]
        assert "索取者是recipient" in observe_frame["独立角色映射"]
        assert SOURCE not in str(observe_frame)
        assert "独立整理的解释提案" not in observe_frame
    else:
        assert "comparison_guidance_version" not in report
        assert "情节阶段对照版本" not in compare_frame
        assert "按冻结事项区分行动阶段" not in compare_frame
        assert "最终判断一致性" not in compare_frame
        assert "role_observation_version" not in report["direction_guard"]
        assert "角色流向观察版本" not in observe_frame
