"""Wire-only union adaptation, source/domain invariants and zero-fee preflight."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
import hashlib
import json

import httpx
from pydantic import BaseModel, ValidationError
import pytest

from ainovel.author_contract import AuthorIntentFields
from ainovel.budget import Budget
from ainovel.character_repair import CharacterRepair
from ainovel.character_review import CharacterReview
from ainovel.db import DomainError
from ainovel.evidence import source_schema, source_units
from ainovel.models import Completion, Memory, Plan, Review, RevisionPatch, SegmentCompletion
from ainovel.openai_schema import VERSION
from ainovel.plot_audit import AuthorPlot, ScopedAuthorPlot, PlotAudit, FlowObservations
from ainovel.providers import ModelClient, strict_schema
from ainovel.reference import Analysis, Application, Comparison
from ainovel.scene_order import linked_scene_issues
from ainovel.story_extraction import Extraction, ExtractionWithScenes, ExtractionWithLinkedScenes
from ainovel.targeted_revision import TargetedRecheck
from ainovel.worker import Worker
from test_budget_provider import stream
from test_extraction_scene_diagnostics import units_for, proposals
from test_scene_order import scene


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def nodes(schema):
    yield schema
    for key in ("properties", "$defs"):
        for item in schema.get(key, {}).values():
            yield from nodes(item)
    if "items" in schema:
        yield from nodes(schema["items"])
    for key in ("anyOf", "oneOf"):
        for item in schema.get(key, []):
            yield from nodes(item)


# All schemas passed to Worker.call/grounded in the current implementation.
GROUNDED = [
    AuthorIntentFields,
    Plan,
    Review,
    Memory,
    RevisionPatch,
    Completion,
    SegmentCompletion,
    Analysis,
    Application,
    Comparison,
    CharacterReview,
    CharacterRepair,
    AuthorPlot,
    ScopedAuthorPlot,
    PlotAudit,
    FlowObservations,
    TargetedRecheck,
    Extraction,
    ExtractionWithScenes,
    ExtractionWithLinkedScenes,
]


@pytest.mark.parametrize("model", GROUNDED, ids=lambda model: model.__name__)
def test_actual_project_grounded_schema_inventory_preserves_sources_and_domain(model):
    original = model.model_json_schema()
    contract = source_schema(original, source_units("😀𠮷来到渡口。\n只核对本章。", "C001P"))
    before = deepcopy(contract)
    wire = strict_schema(contract)
    assert contract == before and model.model_json_schema() == original
    assert not any("oneOf" in node or "discriminator" in node or "default" in node for node in nodes(wire))
    for node in nodes(wire):
        if node.get("type") == "object":
            assert node["required"] == list(node.get("properties", {}))
            assert node["additionalProperties"] is False
    for name, definition in contract.get("$defs", {}).items():
        if "quote" in definition.get("properties", {}):
            assert (
                wire["$defs"][name]["properties"]["quote"]
                == strict_schema(
                    {"type": "object", "properties": {"quote": definition["properties"]["quote"]}}
                )["properties"]["quote"]
            )
    if model in (Extraction, ExtractionWithScenes, ExtractionWithLinkedScenes):
        assert len(wire["$defs"]["ProposedRecord"]["properties"]["record"]["anyOf"]) == 5
    if model is ExtractionWithLinkedScenes:
        assert len(wire["$defs"]["LinkedSceneProposal"]["properties"]["order_basis"]["anyOf"]) == 2
        assert "oneOf" in contract["$defs"]["LinkedSceneProposal"]["properties"]["order_basis"]


def tagged():
    return {
        "type": "object",
        "properties": {
            "result": {
                "discriminator": {
                    "propertyName": "kind",
                    "mapping": {"event": "#/$defs/Event", "unknown": "#/$defs/Unknown"},
                },
                "oneOf": [{"$ref": "#/$defs/Event"}, {"$ref": "#/$defs/Unknown"}],
            }
        },
        "$defs": {
            "Event": {
                "type": "object",
                "properties": {"kind": {"type": "string", "const": "event"}, "quote": {"type": "string"}},
                "required": ["kind", "quote"],
            },
            "Unknown": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": ["unknown"]}, "reason": {"type": "string"}},
                "required": ["kind", "reason"],
            },
        },
    }


def test_nested_aliases_and_pointer_escapes_preserve_provable_tags():
    value = tagged()
    value["$defs"]["Event/Origin~"] = value["$defs"].pop("Event")
    value["$defs"]["Alias"] = {"$ref": "#/$defs/Event~1Origin~0"}
    result = value["properties"]["result"]
    result["oneOf"][0]["$ref"] = result["discriminator"]["mapping"]["event"] = "#/$defs/Alias"
    value["properties"] = {
        "nested": {"type": "array", "items": {"type": "object", "properties": {"value": result}}}
    }
    wire = strict_schema(value)
    union = wire["properties"]["nested"]["items"]["properties"]["value"]
    assert len(union["anyOf"]) == 2 and "discriminator" not in union


def test_schema_keyword_field_names_and_literal_defaults_are_not_walked_as_schemas():
    value = {
        "type": "object",
        "properties": {
            name: {"type": "string", "default": "literal", "description": name}
            for name in ("oneOf", "discriminator", "default", "properties", "$defs")
        },
        "default": {"oneOf": "not a schema", "discriminator": {"arbitrary": True}},
    }
    wire = strict_schema(value)
    assert list(wire["properties"]) == list(value["properties"])
    assert wire["required"] == list(value["properties"])
    assert all(item["description"] == name for name, item in wire["properties"].items())


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("string", {}),
        ("integer", {}),
        ("number", {}),
        ("boolean", {}),
        ("object", {"properties": {"answer": {"type": "string"}}}),
        ("array", {"items": {"type": "string"}}),
    ],
)
def test_documented_nullable_type_arrays_preserve_wire_shape(kind, extra):
    value = {"type": "object", "properties": {"optional": {"type": [kind, "null"], **extra}}}
    wire = strict_schema(value)
    assert wire["properties"]["optional"]["type"] == [kind, "null"]
    if kind not in {"object", "array"}:
        assert encoded(wire) == encoded(legacy_strict(value))
    if kind == "object":
        assert wire["properties"]["optional"]["required"] == ["answer"]
        assert wire["properties"]["optional"]["additionalProperties"] is False


def bad_schema(damage):
    value = tagged()
    union = value["properties"]["result"]
    if damage == "untagged":
        union.pop("discriminator")
    elif damage == "overlap":
        value["$defs"]["Unknown"]["properties"]["kind"] = {"type": "string", "const": "event"}
    elif damage == "optional_tag":
        value["$defs"]["Event"]["required"] = ["quote"]
    elif damage == "numeric_tag":
        value["$defs"]["Event"]["properties"]["kind"] = {"type": "integer", "const": 1}
    elif damage == "missing_tag":
        value["$defs"]["Event"]["properties"].pop("kind")
    elif damage == "mapping_mismatch":
        union["discriminator"]["mapping"]["event"] = "#/$defs/Unknown"
    elif damage == "remote_ref":
        union["oneOf"][0]["$ref"] = "https://example.invalid/schema"
    elif damage == "missing_ref":
        union["oneOf"][0]["$ref"] = "#/$defs/Absent"
    elif damage == "cyclic_alias":
        value["$defs"]["Event"] = {"$ref": "#/$defs/Event"}
    elif damage == "ref_sibling":
        union["oneOf"][0]["properties"] = {}
    elif damage == "root_union":
        value = {"$defs": value["$defs"], **union}
    elif damage == "mixed_union":
        union["anyOf"] = [{"type": "null"}]
    elif damage == "dynamic_properties":
        value["additionalProperties"] = {"type": "string"}
    elif damage == "empty_schema":
        value = {}
    else:
        value["properties"]["result"] = {"type": "object", damage: {}}
    return value


UNSUPPORTED = [
    "untagged",
    "overlap",
    "optional_tag",
    "numeric_tag",
    "missing_tag",
    "mapping_mismatch",
    "remote_ref",
    "missing_ref",
    "cyclic_alias",
    "ref_sibling",
    "root_union",
    "mixed_union",
    "dynamic_properties",
    "empty_schema",
    "allOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
    "patternProperties",
    "prefixItems",
    "unknownKeyword",
]


@pytest.mark.parametrize("damage", UNSUPPORTED)
def test_unproven_or_unsupported_schemas_stop_before_reservation_and_http(settings, monkeypatch, damage):
    ledger = Budget(settings)

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported schema must not reserve or dispatch")

    monkeypatch.setattr(ledger, "reserve", forbidden)
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(forbidden)).generate(
                "openai_1", "unsupported-" + damage, "系统", "本章", schema=bad_schema(damage)
            )
        )
    assert caught.value.code == "schema_unsupported"
    assert ledger.report()[0]["calls"] == []


def legacy_strict(value):
    if isinstance(value, list):
        return [legacy_strict(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: legacy_strict(item) for key, item in value.items() if key != "default"}
    if result.get("type") == "object":
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    return result


@pytest.mark.parametrize("model", [Memory, Plan, Review, Analysis, CharacterReview])
def test_existing_nonunion_wire_stays_byte_identical(model):
    schema = model.model_json_schema()
    assert encoded(strict_schema(schema)) == encoded(legacy_strict(schema))


def completed(text='{"answer":"完成"}'):
    return httpx.Response(
        200,
        content=stream(
            [
                {"type": "response.output_text.delta", "delta": text},
                {
                    "type": "response.completed",
                    "response": {"status": "completed", "usage": {"input_tokens": 21, "output_tokens": 7}},
                },
            ]
        ),
    )


def test_actual_wire_body_drives_hashes_byte_window_and_money_gates(settings):
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return completed()

    ledger = Budget(settings)
    client = ModelClient(settings, ledger, httpx.MockTransport(handler))
    schema = ExtractionWithLinkedScenes.model_json_schema()
    output = asyncio.run(
        client.generate("openai_1", "converted-body", '只读"正文"', "😀𠮷", 17, schema=schema)
    )
    raw = bodies[-1]
    wire = json.loads(raw)["text"]["format"]["schema"]
    metadata = output["request_metadata"]
    assert metadata == {
        "wire_schema_version": VERSION,
        "wire_schema_sha256": digest(wire),
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "request_bytes": len(raw),
    }
    provider = settings.providers["openai_1"]
    expected = int(
        (
            Decimal(len(raw) + 2048) * provider.input_rate + Decimal(17) * provider.output_rate
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    assert ledger.report()[0]["calls"][0]["reserved"] == expected
    settings.providers[provider.id] = replace(
        provider,
        max_input_bytes=len(raw),
        context_window_tokens=len(raw) + 17 + 4096,
        context_source="verified test boundary",
    )
    asyncio.run(client.generate(provider.id, "exact-boundary", '只读"正文"', "😀𠮷", 17, schema=schema))
    assert bodies[-1] == raw
    for settings_change in (
        {"max_input_bytes": len(raw) - 1},
        {"context_window_tokens": len(raw) + 17 + 4095},
    ):
        settings.providers[provider.id] = replace(settings.providers[provider.id], **settings_change)
        before = deepcopy(ledger.report())
        with pytest.raises(DomainError, match="未截断或付费调用"):
            asyncio.run(
                client.generate(provider.id, "below-boundary", '只读"正文"', "😀𠮷", 17, schema=schema)
            )
        assert ledger.report() == before and len(bodies) == 2
        settings.providers[provider.id] = replace(
            provider,
            max_input_bytes=len(raw),
            context_window_tokens=len(raw) + 17 + 4096,
            context_source="verified test boundary",
        )


def test_http400_remains_zero_settled_without_retry_and_retains_only_safe_wire_metadata(settings):
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx.Response(400, json={"error": {"message": "Unsupported schema"}})

    ledger = Budget(settings)
    with pytest.raises(DomainError) as caught:
        asyncio.run(
            ModelClient(settings, ledger, httpx.MockTransport(handler)).generate(
                "openai_1",
                "explicit-http400",
                "private system",
                "private prose",
                schema=ExtractionWithLinkedScenes.model_json_schema(),
            )
        )
    assert len(bodies) == 1 and not caught.value.repairable
    assert ledger.report()[0]["settled"] == 0 and ledger.report()[0]["reserved_unknown"] == 0
    metadata = caught.value.provider_details["request_metadata"]
    assert metadata["request_bytes"] == len(bodies[0])
    assert metadata["request_sha256"] == hashlib.sha256(bodies[0]).hexdigest()
    assert "private" not in json.dumps(metadata) and "schema" not in metadata


def test_wire_conversion_does_not_replace_local_structural_or_evidence_validation():
    schema = ExtractionWithLinkedScenes.model_json_schema()
    strict_schema(schema)
    value = {
        "records": [],
        "unknowns": [],
        "coverage": "本章",
        "scenes": [
            {
                "title": "渡口",
                "source_ids": ["S1"],
                "order_basis": {"kind": "unknown", "reason": "缺少时点依据"},
            }
        ],
    }
    for basis in (
        {"kind": "invented", "refs": []},
        {"kind": "unknown", "reason": ""},
        {"kind": "event_refs", "refs": []},
    ):
        with pytest.raises(ValidationError):
            ExtractionWithLinkedScenes.model_validate(
                {**value, "scenes": [{**value["scenes"][0], "order_basis": basis}]}
            )
    units = units_for("渡船")
    items = proposals(units, (3,))
    items[0]["record"]["status"] = "planned"
    assert linked_scene_issues([scene(units)], units, {}, items)


class Answer(BaseModel):
    answer: str


def worker_for(settings, domain, story, model):
    domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "account": "openai_1",
            "action": "writing.advise",
        },
    )
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    return worker


def test_worker_schema_preflight_precedes_dispatch_but_follows_completed_semantic_cache(
    settings, domain, story, monkeypatch
):
    calls = []

    class Offline:
        async def generate(self, *args, **kwargs):
            calls.append(kwargs)
            return {
                "text": '{"answer":"旧付费结果"}',
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-paid",
            }

    worker = worker_for(settings, domain, story, Offline())
    instruction = {"动作": "仅离线核对语义缓存"}
    invalid_contract = bad_schema("untagged")
    with pytest.raises(DomainError, match="oneOf缺少"):
        asyncio.run(worker.call(instruction, Answer, contract=invalid_contract))
    assert not calls and not domain.db.all("SELECT * FROM task_events WHERE kind='step_dispatched'")
    # Simulate a pre-upgrade completed paid step, without dispatching a model.
    original = strict_schema
    monkeypatch.setattr("ainovel.providers.strict_schema", lambda value: {"type": "object", "properties": {}})
    first = asyncio.run(worker.call(instruction, Answer, contract=invalid_contract))
    monkeypatch.setattr("ainovel.providers.strict_schema", original)
    assert asyncio.run(worker.call(instruction, Answer, contract=invalid_contract)) == first
    assert len(calls) == 1
    event = json.loads(
        domain.db.one("SELECT payload FROM task_events WHERE kind='step_dispatched'")["payload"]
    )
    assert event["request"]["wire_schema_version"] == VERSION
    assert len(event["request"]["wire_schema_sha256"]) == 64
