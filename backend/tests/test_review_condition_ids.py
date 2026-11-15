"""Stable condition references preserve long author text and every review judgment."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.conditional_review import ConditionalReview, KEY
from ainovel.book_applicability import KEY as APPLICABILITY_KEY
from ainovel.db import DomainError, dumps
from ainovel.evidence import source_schema, source_units
from ainovel.providers import strict_schema
from ainovel.review_condition_ids import condition_table, id_contract, resolve_checks
from ainovel.worker import Worker
from test_workflow_scope import workflow_payload


LONG = "【原创器具约定😀𠮷】" + "每次量船板须保留原刻线、工具所有者和记录顺序，不把准备测量写成已经量完；" * 6
CONDITIONAL = "若涉及交还旧锤，须核对保管者与实际交还时点。"
TEXT = "林舟把工具袋放在台上，先核对原刻线与保管人，只留下待量的三处标记。😀𠮷"


class IdModel:
    def __init__(self, *, mutate=None, literal=False, legacy_ids=False):
        self.instructions = []
        self.mutate = mutate
        self.literal = literal
        self.legacy_ids = legacy_ids

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        outer = json.loads(prompt.split("\n只返回")[0])["任务"]
        self.instructions.append(deepcopy(outer))
        instruction = outer
        while "原始任务" in instruction:
            instruction = instruction["原始任务"]
        table = instruction.get("固定条件编号表")
        checks = []
        for index, condition in enumerate(instruction["固定条件"]):
            reference = table[index]["condition_id"] if table and not self.literal else condition
            if not table and index == 0:
                reference = "器具使用须保留原刻线和保管者。"
            checks.append(
                {
                    "condition": reference,
                    "result": "unknown" if index == 0 else "not_triggered",
                    "evidence": "",
                    "scope": "book",
                    "applicability": "uncertain" if index == 0 else "not_triggered",
                    "chapter_obligation": "not_required",
                    "explanation": "未涉及旧锤交还，整书仍待核对；器具条件保留未知，不改判通过。",
                }
            )
        if self.legacy_ids:
            checks = [
                {"condition": f"FIX{index:03}", "result": "unknown", "evidence": ""}
                for index, _ in enumerate(instruction["固定条件"], 1)
            ]
        output = {"checks": checks, "findings": [], "summary": f"第{len(self.instructions)}次独立核对。"}
        if self.mutate:
            self.mutate(output, len(self.instructions), instruction)
        return {
            "text": dumps(output),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-condition-id",
        }


def review_worker(settings, domain, story, model, *, fixed=None, legacy=False):
    payload = workflow_payload(story)
    payload["fixed"] = fixed if fixed is not None else [LONG, CONDITIONAL]
    payload["plots"][0]["fixed"] = []
    task = domain.queue("workflow", payload, story["branch_id"])
    # Isolate the ID contract on an already-frozen policy-1 task. New default
    # applicability-observer + ID orchestration is covered by its integration tests.
    result = {
        key: value
        for key, value in task["result"].items()
        if key != APPLICABILITY_KEY and (not legacy or key != KEY)
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(result), task["id"]))
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    context = worker.context(story["branch_id"], story["chapter_id"])
    return worker, task, context, payload


def test_numbered_contract_prevents_long_condition_paraphrase_and_roundtrips_verbatim(
    settings, domain, story
):
    model = IdModel()
    worker, _, context, payload = review_worker(settings, domain, story, model)
    review = asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert [row["condition"] for row in review["checks"]] == [LONG, CONDITIONAL]
    assert [row["result"] for row in review["checks"]] == ["unknown", "not_triggered"]
    assert len(model.instructions) == 1
    assert model.instructions[0]["固定条件"] == [LONG, CONDITIONAL]
    assert model.instructions[0]["固定条件检查格式版本"] == "fixed-check-id/1"
    assert review["condition_id_version"] == "fixed-check-id/1"
    assert [row["condition_id"] for row in review["condition_id_map"]] == ["FIX001", "FIX002"]
    assert [row["condition"] for row in review["condition_id_map"]] == [LONG, CONDITIONAL]
    assert review["condition_check_ids"] == [
        {"condition_id": "FIX001", "input_kind": "id"},
        {"condition_id": "FIX002", "input_kind": "id"},
    ]
    events = domain.db.all("SELECT payload FROM task_events WHERE kind='step_dispatched'")
    request = json.loads(events[0]["payload"])["request"]
    assert request["review_check_version"] == request["review_condition_id_version"] == "fixed-check-id/1"
    assert domain.chapter(story["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT * FROM artifacts")


@pytest.mark.parametrize(
    "defect", ["unknown", "partial", "paraphrase", "order", "duplicate", "extra", "missing"]
)
def test_unrecognized_or_reordered_references_never_rewrite_originals_or_judgments(
    settings, domain, story, defect
):
    def mutate(output, call, instruction):
        checks = output["checks"]
        if defect == "unknown":
            checks[0]["condition"] = "FIX999"
        elif defect == "partial":
            checks[0]["condition"] = "FIX001：" + LONG[:12]
        elif defect == "paraphrase":
            checks[0]["condition"] = LONG[:-1]
        elif defect == "order":
            checks.reverse()
        elif defect == "duplicate":
            checks[1]["condition"] = "FIX001"
        elif defect == "extra":
            checks.append(deepcopy(checks[0]))
        else:
            checks.pop()

    model = IdModel(mutate=mutate)
    worker, task, context, payload = review_worker(settings, domain, story, model)
    with pytest.raises(DomainError):
        asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert len(model.instructions) == 3
    assert all(instruction["固定条件"] == [LONG, CONDITIONAL] for instruction in model.instructions)
    assert all("固定条件编号修正要求" in instruction for instruction in model.instructions[1:])
    assert domain.task(task["id"])["payload"]["fixed"] == [LONG, CONDITIONAL]
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert not domain.db.all("SELECT * FROM artifacts")


def test_wrong_ids_remain_wrong_even_when_two_conditions_have_identical_original_text(
    settings, domain, story
):
    def mutate(output, call, instruction):
        output["checks"][0]["condition"], output["checks"][1]["condition"] = "FIX002", "FIX001"
        for check in output["checks"]:
            check.update(result="unknown", evidence="")

    model = IdModel(mutate=mutate)
    worker, _, context, payload = review_worker(settings, domain, story, model, fixed=[LONG, LONG])
    with pytest.raises(DomainError, match="错序或重复"):
        asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert not domain.db.all("SELECT * FROM artifacts")


def test_verbatim_compatibility_does_not_enable_paraphrases(settings, domain, story):
    model = IdModel(literal=True)
    worker, _, context, payload = review_worker(settings, domain, story, model)
    review = asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert [row["condition"] for row in review["checks"]] == [LONG, CONDITIONAL]
    assert [row["input_kind"] for row in review["condition_check_ids"]] == ["verbatim", "verbatim"]
    assert review["checks"][0]["result"] == "unknown"


def test_original_text_that_looks_like_another_id_is_not_guessed_by_position(settings, domain, story):
    model = IdModel(literal=True)
    worker, _, context, payload = review_worker(settings, domain, story, model, fixed=["FIX002", CONDITIONAL])
    with pytest.raises(DomainError):
        asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert not domain.db.all("SELECT * FROM artifacts")


def test_numbering_does_not_allow_a_nonconditional_id_to_skip_its_condition(settings, domain, story):
    def mutate(output, call, instruction):
        output["checks"][0].update(
            result="not_triggered", applicability="not_triggered", chapter_obligation="not_required"
        )

    model = IdModel(mutate=mutate)
    worker, _, context, payload = review_worker(settings, domain, story, model)
    with pytest.raises(DomainError, match="not_triggered"):
        asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert not domain.db.all("SELECT * FROM artifacts")


@pytest.mark.parametrize("kind", ["grounding", "structure"])
def test_format_repairs_keep_ids_unknown_and_the_original_text(settings, domain, story, kind):
    def mutate(output, call, instruction):
        if call == 1:
            if kind == "grounding":
                output["checks"][0]["condition"] = "FIX999"
            else:
                output.pop("findings")

    model = IdModel(mutate=mutate)
    worker, _, context, payload = review_worker(settings, domain, story, model)
    review = asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert len(model.instructions) == 2
    assert "固定条件编号修正要求" in model.instructions[1]
    assert [check["condition"] for check in review["checks"]] == [LONG, CONDITIONAL]
    assert [check["result"] for check in review["checks"]] == ["unknown", "not_triggered"]


def test_legacy_review_never_receives_or_resolves_fixed_ids(settings, domain, story):
    model = IdModel(legacy_ids=True)
    worker, _, context, payload = review_worker(settings, domain, story, model, legacy=True)
    with pytest.raises(DomainError):
        asyncio.run(worker.review(TEXT, payload["fixed"], context, 1, {"本章": payload["plots"][0]}))
    assert len(model.instructions) == 3
    assert all("固定条件编号表" not in instruction for instruction in model.instructions)
    assert all(
        instruction["固定条件检查格式版本"] == "fixed-check-shape/2" for instruction in model.instructions
    )


def test_empty_fixed_list_retains_a_valid_schema_and_an_empty_report(settings, domain, story):
    model = IdModel()
    worker, _, context, payload = review_worker(settings, domain, story, model, fixed=[])
    review = asyncio.run(worker.review(TEXT, [], context, 1, {"本章": payload["plots"][0]}))
    assert review["checks"] == review["condition_id_map"] == review["condition_check_ids"] == []


def wire_contract(fixed=None, assessment=None):
    fixed = [LONG, CONDITIONAL] if fixed is None else fixed
    origins = [{"condition": condition, "scope": "book"} for condition in fixed]
    table = condition_table(fixed, origins, [CONDITIONAL], assessment=assessment)
    original = source_schema(ConditionalReview.model_json_schema(), source_units(TEXT, "C001P"))
    original["properties"]["checks"].update(minItems=len(fixed), maxItems=len(fixed))
    contract = id_contract(original, table)
    return original, contract, table


@pytest.mark.parametrize("fixed", [[], [LONG, CONDITIONAL]])
def test_nested_anyof_compiles_to_openai_wire_without_losing_source_enums_or_mutating_schema(fixed):
    original, contract, table = wire_contract(fixed)
    before = deepcopy(contract)
    wire = strict_schema(contract)
    assert contract == before
    assert original["properties"]["checks"]["items"] == {"$ref": "#/$defs/ConditionalReviewCheck"}
    items = wire["properties"]["checks"]["items"]
    branches = items.get("anyOf", [items])
    assert all(branch["type"] == "object" and branch["additionalProperties"] is False for branch in branches)
    assert all(branch["properties"]["evidence"]["enum"] == ["C001P0001", ""] for branch in branches)
    assert not any(key in json.dumps(wire) for key in ('"prefixItems"', '"allOf"', '"oneOf"'))
    if table:
        assert [branch["properties"]["condition"]["enum"] for branch in branches] == [
            ["FIX001"],
            ["FIX002"],
            ["FIX002"],
        ]
        not_triggered = branches[-1]["properties"]
        assert not_triggered["result"]["enum"] == ["not_triggered"]
        assert not_triggered["scope"]["enum"] == ["book"]
        assert not_triggered["applicability"]["enum"] == ["not_triggered"]
        assert not_triggered["chapter_obligation"]["enum"] == ["not_required"]
    else:
        assert "anyOf" not in items and "enum" not in items["properties"]["condition"]


@pytest.mark.parametrize(
    "trigger,obligation,allowed",
    [
        ("not_triggered", "not_required", {"fail", "unknown", "not_triggered"}),
        ("triggered", "not_required", {"pass", "fail", "unknown"}),
        ("triggered", "required", {"pass", "fail", "unknown"}),
        ("uncertain", "not_required", {"fail", "unknown"}),
        ("not_triggered", "uncertain", {"fail", "unknown"}),
        ("not-a-valid-trigger", "not_required", {"fail", "unknown"}),
    ],
)
def test_optional_independent_assessment_restricts_both_wire_and_runtime_without_reclassifying_results(
    trigger, obligation, allowed
):
    assessment = {
        "FIX001": {"trigger": "triggered", "chapter_obligation": "required"},
        "FIX002": {"trigger": trigger, "chapter_obligation": obligation},
    }
    _, contract, table = wire_contract(assessment=assessment)
    assert set(table[1]["allowed_results"]) == allowed
    branches = strict_schema(contract)["properties"]["checks"]["items"]["anyOf"]
    wire_allowed = {
        result
        for branch in branches
        if branch["properties"]["condition"]["enum"] == ["FIX002"]
        for result in branch["properties"]["result"]["enum"]
    }
    assert wire_allowed == allowed
    for result in ["pass", "fail", "unknown", "not_triggered"]:
        checks = [{"condition": "FIX001", "result": "unknown"}, {"condition": "FIX002", "result": result}]
        issues, actual = resolve_checks(checks, table)
        assert bool(issues) is (result not in allowed)
        assert checks[1]["result"] == result
        assert checks[1]["condition"] == CONDITIONAL
        assert actual[1]["condition_id"] == "FIX002"


def test_optional_assessment_only_restricts_eligible_ids_and_does_not_expand_other_conditions():
    assessment = {
        identifier: {"trigger": "not_triggered", "chapter_obligation": "not_required"}
        for identifier in ("FIX001", "FIX002")
    }
    _, _, table = wire_contract(assessment=assessment)
    assert table[0]["allowed_results"] == ["pass", "fail", "unknown"]
    assert set(table[1]["allowed_results"]) == {"fail", "unknown", "not_triggered"}
