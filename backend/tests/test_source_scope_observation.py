"""The new output shape cannot turn event timing into recipient restrictions."""

import asyncio
from copy import deepcopy

import pytest
from ainovel import authored_source_scope as authored
from ainovel import source_scope_observation as observation
from ainovel import workflow_plan_recipient_scope as core
from ainovel.db import DomainError, dumps
from ainovel.openai_schema import strict_schema
from ainovel.worker import Worker
from pydantic import ValidationError


def output(source):
    return {
        "input_sha256": core.sha(source),
        "units": [
            {
                "source_id": unit["source_id"],
                "classification": "not_scope_related",
                "observations": [],
                "reason": "此句的事件、时点或认知由后续全原意检查保留，不补接收范围。",
            }
            for unit in core.units(source)
        ],
        "note": "仅来源范围分类，不证明全情节或文学通过。",
    }


def destination(**changes):
    return {
        "kind": "named_destination",
        "supplier": "林舟",
        "recipient": "保管员",
        "transfer_object": "借用的木尺",
        "transfer_scope": "completed",
        "reason": "已指定本次行动双方，但没有排除其他人的接收范围。",
        **changes,
    }


def related(value, index, *items):
    value["units"][index].update(classification="scope_related", observations=list(items))
    return value


def restriction(kind, *, source_id="SCOPE0001", **changes):
    return {
        **destination(),
        "kind": kind,
        "restriction_source_id": source_id,
        "condition": "",
        **changes,
    }


def project(source, value):
    return observation.project(value, source, core.sha(source))


def test_ordinary_return_time_stays_in_source_without_restriction_slots():
    source = "查验结束时，林舟将借用的木尺归还保管员。"
    value = related(output(source), 0, destination())
    before = deepcopy(value)
    result = project(source, value)
    assert value == before
    assert result["observations"] == [
        {
            "observation_id": "O01",
            **destination(),
            "evidence": "SCOPE0001",
            "restriction_evidence": "",
            "condition": "",
        }
    ]
    assert result["unit_checks"][0]["observation_ids"] == ["O01"]
    assert not core.inventory_issues(
        result, core.sha(source), {u["source_id"]: u for u in core.units(source)}
    )
    request = observation.observation_request(source, token=core.sha(source), source_kind="author_plot")
    assert request["instruction"]["本次来源原文"] == source
    assert request["instruction"]["可引用原文"][0]["text"] == source


@pytest.mark.parametrize("field", ["condition", "restriction_source_id", "restriction_evidence", "evidence"])
def test_named_destination_structurally_rejects_restriction_or_manual_evidence(field):
    source = "林舟将木尺交给保管员。"
    value = related(output(source), 0, destination(**{field: "查验结束时"}))
    with pytest.raises(ValidationError):
        project(source, value)
    schema = observation.schema_for(source, core.sha(source))
    assert field not in schema["$defs"]["NamedDestination"]["properties"]


def test_permissions_requests_non_delivery_and_known_knowledge_are_distinct():
    source = (
        "林舟向管理员请求借阅目录。管理员只准在室内手抄目录。林舟未交出自己的测量纸。林舟不知道缺页原因。"
    )
    value = related(
        output(source),
        0,
        destination(
            recipient="管理员",
            transfer_object="借阅目录的请求",
            transfer_scope="completed",
            reason="请求已经提出，不表示请求的目录已取得。",
        ),
    )
    related(
        value,
        1,
        restriction(
            "action_permission",
            source_id="SCOPE0002",
            supplier="",
            recipient="",
            transfer_object="",
            transfer_scope="none",
            reason="只准手抄是行动／物品许可，不是实际交付或排他接收。",
        ),
    )
    result = project(source, value)
    assert [(item["kind"], item["transfer_scope"]) for item in result["observations"]] == [
        ("named_destination", "completed"),
        ("action_permission", "none"),
    ]
    assert [unit["status"] for unit in result["unit_checks"]] == [
        "covered",
        "covered",
        "not_scope_related",
        "not_scope_related",
    ]
    assert core.inventory_supported(result)
    assert result["observations"][0]["transfer_object"] == "借阅目录的请求"
    assert not any("测量纸" == item["transfer_object"] for item in result["observations"])


def test_specific_exclusion_with_none_stage_is_not_discarded_as_non_delivery():
    source = "林舟不得把测量纸交给访客。"
    value = related(
        output(source),
        0,
        restriction(
            "specific_recipient_exclusion",
            recipient="访客",
            transfer_object="测量纸",
            transfer_scope="none",
        ),
    )
    result = project(source, value)
    assert result["observations"][0]["kind"] == "specific_recipient_exclusion"
    assert result["observations"][0]["recipient"] == "访客"
    assert core.inventory_supported(result)


def test_unnamed_participants_do_not_become_guessed_people_or_explicit_unrestricted():
    source = "有人取走了桌上的副本，去向没有说明。"
    value = related(
        output(source),
        0,
        destination(
            supplier="",
            recipient="",
            transfer_object="桌上的副本",
            reason="原文指定普通取走事件，未指定身份或接收限制，不补姓名和不限制声明。",
        ),
    )
    result = project(source, value)
    assert core.inventory_supported(result)
    assert result["observations"][0]["supplier"] == result["observations"][0]["recipient"] == ""
    assert result["observations"][0]["kind"] == "named_destination"


def test_cross_sentence_restriction_projects_one_observation_and_both_real_anchors():
    source = "林舟把抄本交给管理员。仅限管理员阅读，公开之前不得转交他人。"
    value = related(
        output(source),
        0,
        restriction(
            "recipient_exclusivity",
            source_id="SCOPE0002",
            recipient="管理员",
            transfer_object="抄本",
            condition="公开之前",
        ),
    )
    related(value, 1)
    result = project(source, value)
    assert len(result["observations"]) == 1
    item = result["observations"][0]
    assert item["evidence"] == "SCOPE0001" and item["restriction_evidence"] == "SCOPE0002"
    assert item["condition"] == "公开之前"
    assert [unit["observation_ids"] for unit in result["unit_checks"]] == [["O01"], ["O01"]]


@pytest.mark.parametrize("classification", ["unknown", "not_scope_related"])
def test_cross_sentence_anchor_cannot_claim_unrelated_or_unknown_coverage(classification):
    source = "林舟把抄本交给管理员。仅限管理员阅读。"
    value = related(output(source), 0, restriction("recipient_exclusivity", source_id="SCOPE0002"))
    value["units"][1]["classification"] = classification
    with pytest.raises(ValueError, match="覆盖冲突"):
        project(source, value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("restriction_source_id", ""),
        ("restriction_source_id", "SCOPE9999"),
        ("condition", "不存在的条件"),
        ("transfer_scope", "none"),
    ],
)
def test_real_restrictions_require_local_evidence_continuous_condition_and_real_transfer(field, value):
    source = "公开之前，林舟仅向管理员交付抄本。"
    raw = related(output(source), 0, restriction("recipient_exclusivity", **{field: value}))
    with pytest.raises(ValueError):
        project(source, raw)


def test_condition_cannot_borrow_time_from_action_when_restriction_is_in_another_unit():
    source = "公开之前，林舟把抄本交给管理员。仅限管理员阅读。"
    raw = related(
        output(source),
        0,
        restriction(
            "recipient_exclusivity",
            source_id="SCOPE0002",
            condition="公开之前",
        ),
    )
    related(raw, 1)
    with pytest.raises(ValueError, match="限制原句"):
        project(source, raw)


@pytest.mark.parametrize("fault", ["missing", "reordered", "duplicated", "extra", "wrong_token"])
def test_every_original_unit_and_input_basis_are_mandatory(fault):
    source = "有人请求查目录。有人不知道旧事。"
    value = output(source)
    if fault == "missing":
        value["units"].pop()
    elif fault == "reordered":
        value["units"].reverse()
    elif fault == "duplicated":
        value["units"][1] = deepcopy(value["units"][0])
    elif fault == "extra":
        value["units"].append(deepcopy(value["units"][0]))
    else:
        value["input_sha256"] = "0" * 64
    assert observation.issues(value, source, core.sha(source))
    with pytest.raises(ValueError):
        project(source, value)


@pytest.mark.parametrize(
    "fault",
    [
        "empty_related",
        "unrelated_with_observation",
        "duplicate_observation",
        "group_blank_reason",
        "observation_blank_reason",
        "too_many_observations",
    ],
)
def test_conflicting_coverage_and_invalid_observations_are_never_repaired_by_projection(fault):
    source = "林舟把木尺交给保管员。"
    value = related(output(source), 0, destination())
    if fault == "empty_related":
        value["units"][0]["observations"] = []
    elif fault == "unrelated_with_observation":
        value["units"][0]["classification"] = "not_scope_related"
    elif fault == "duplicate_observation":
        value["units"][0]["observations"].append(destination())
    elif fault == "group_blank_reason":
        value["units"][0]["reason"] = "  "
    elif fault == "observation_blank_reason":
        value["units"][0]["observations"][0]["reason"] = "  "
    else:
        value["units"][0]["observations"] = [destination(transfer_object=str(i)) for i in range(25)]
    assert observation.issues(value, source, core.sha(source))


def test_multiple_transfers_get_stable_separate_ids_and_do_not_merge_copies():
    source = "林舟交一份抄本给甲，再将原件交给乙。甲随后转交自己的另一份抄本。"
    value = related(
        output(source),
        0,
        destination(recipient="甲", transfer_object="一份抄本"),
        destination(recipient="乙", transfer_object="原件"),
    )
    related(value, 1, destination(supplier="甲", recipient="", transfer_object="另一份抄本"))
    result = project(source, value)
    assert [item["observation_id"] for item in result["observations"]] == ["O01", "O02", "O03"]
    assert [item["transfer_object"] for item in result["observations"]] == ["一份抄本", "原件", "另一份抄本"]
    assert [unit["observation_ids"] for unit in result["unit_checks"]] == [["O01", "O02"], ["O03"]]


@pytest.mark.parametrize("count", [24, 25])
def test_existing_inventory_limit_applies_to_all_units_together(count):
    source = "".join(f"第{i}份抄本交给管理员。" for i in range(count))
    value = output(source)
    for index in range(count):
        related(value, index, destination(transfer_object=f"第{index}份抄本"))
    if count > 24:
        with pytest.raises(ValueError):
            project(source, value)
    else:
        result = project(source, value)
        assert len(result["observations"]) == 24
        assert result["observations"][-1]["observation_id"] == "O24"


@pytest.mark.parametrize("with_known_observation", [False, True])
def test_true_unknown_is_legal_output_without_guessing_or_grounded_repairs(with_known_observation):
    source = "甲给乙一份，另给丙一份；这份只许她看。"
    value = output(source)
    if with_known_observation:
        related(value, 0, destination(recipient="乙", transfer_object="一份"))
        related(
            value,
            1,
            destination(
                kind="unknown",
                supplier="",
                recipient="",
                transfer_object="",
                transfer_scope="none",
                reason="这份与她不能唯一绑定，保留范围歧义。",
            ),
        )
    else:
        value["units"][0]["classification"] = "unknown"
        value["units"][1]["classification"] = "unknown"
    original = deepcopy(value)
    result = project(source, value)
    assert value == original and not core.inventory_supported(result)
    assert observation.issues(value, source, core.sha(source)) == []
    assert not any(item["kind"] == "recipient_exclusivity" for item in result["observations"])


def test_schema_anyof_is_accepted_by_real_strict_adapter_and_all_fields_required():
    source = "林舟归还木尺。"
    schema = observation.schema_for(source, core.sha(source))
    strict = strict_schema(schema)
    assert "oneOf" not in dumps(schema) and "discriminator" not in dumps(schema)
    alternatives = schema["$defs"]["SourceUnit"]["properties"]["observations"]["items"]["anyOf"]
    assert len(alternatives) == 3
    ordinary = strict["$defs"]["NamedDestination"]
    assert set(ordinary["required"]) == set(ordinary["properties"])
    assert ordinary["additionalProperties"] is False
    restricted = strict["$defs"]["Restriction"]
    assert restricted["properties"]["restriction_source_id"]["enum"] == ["SCOPE0001"]


@pytest.mark.parametrize("source_kind", ["author_plot", "author_instruction", "workflow_plan"])
def test_source_only_request_uses_original_action_and_keeps_complete_utf16_source(source_kind):
    source = "🌊𠀀林舟归还木尺。\n他不知道原委，\r\n仍记下查阅时间。"
    request = observation.observation_request(source, token=core.sha(source), source_kind=source_kind)
    instruction = request["instruction"]
    assert instruction["动作"] == (core.ACTION if source_kind == "workflow_plan" else authored.ACTION)
    assert instruction["来源观察输出契约版本"] == observation.VERSION
    assert instruction["本次来源原文"] == source
    for unit in instruction["可引用原文"]:
        assert (
            source.encode("utf-16-le")[unit["start"] * 2 : unit["end"] * 2].decode("utf-16-le")
            == unit["text"]
        )
    assert request["schema"] is observation.SourceObservation
    assert "后续" in instruction["分组分类边界"] and "not_scope_related" in instruction["分组分类边界"]
    assert "旧" not in set(instruction)
    assert request["validator"](output(source)) == []
    assert request["repair_feedback"]()["来源观察输出契约版本"] == observation.VERSION


@pytest.mark.parametrize(
    "source,token,kind",
    [
        ("", "a" * 64, "author_plot"),
        ("  \n", "a" * 64, "workflow_plan"),
        ("原话。", "invalid", "author_plot"),
        ("原话。", "a" * 64, "unknown_kind"),
    ],
)
def test_bad_source_identity_is_rejected_before_any_grounded_call(source, token, kind):
    class NoCalls:
        async def grounded(self, **kwargs):
            pytest.fail("输入身份无效时不得派发")

    with pytest.raises((ValueError, DomainError)):
        asyncio.run(observation.observe(NoCalls(), source, token=token, source_kind=kind))


def test_observe_uses_existing_grounded_and_unknown_never_triggers_repair():
    source = "这份材料只许她看，所指不明。"
    raw = output(source)
    raw["units"][0]["classification"] = "unknown"

    class GroundedWorker:
        grounded = Worker.grounded

        def __init__(self):
            self.calls = []

        async def call(self, instruction, schema, context, max_output, **kwargs):
            self.calls.append((deepcopy(instruction), schema, context, kwargs))
            return schema.model_validate(deepcopy(raw)).model_dump()

    worker = GroundedWorker()
    result = asyncio.run(
        observation.observe(worker, source, token=core.sha(source), source_kind="author_plot")
    )
    assert len(worker.calls) == 1 and not core.inventory_supported(result)
    assert worker.calls[0][0]["本次来源原文"] == source
    assert worker.calls[0][1] is observation.SourceObservation
    assert raw["units"][0]["classification"] == "unknown"


def test_projection_rechecks_old_validator_even_if_grounded_stub_ignores_it(monkeypatch):
    source = "林舟把木尺交给保管员。"
    raw = related(output(source), 0, destination())
    calls = []
    original = core.inventory_issues

    def guarded(*args):
        calls.append(deepcopy(args[0]))
        return [*original(*args), "原校验器要求停止"]

    monkeypatch.setattr(core, "inventory_issues", guarded)

    class IgnoreValidator:
        async def grounded(self, **kwargs):
            return deepcopy(raw)

    with pytest.raises(DomainError, match="原范围契约"):
        asyncio.run(
            observation.observe(IgnoreValidator(), source, token=core.sha(source), source_kind="author_plot")
        )
    assert len(calls) == 1 and raw["units"][0]["observations"][0]["kind"] == "named_destination"
