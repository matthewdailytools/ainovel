from ainovel.domain import utf16_slice
from ainovel.evidence import resolve_field, source_units, source_schema
from ainovel.models import Memory, Review, RevisionPatch


def test_source_id_restores_exact_frozen_original_without_paraphrasing():
    text = "他把😀木尺放好。\n\n𠮷字刻在旧尺边。"
    units = source_units(text, "C001P")
    for unit in units:
        assert utf16_slice(text, unit["start"], unit["end"])[1] == unit["text"]
    evidence = {"evidence": units[0]["source_id"]}
    resolve_field(evidence, "evidence", {unit["source_id"]: unit for unit in units})
    assert evidence["evidence"] == "他把😀木尺放好。"
    wrong = {"evidence": "沈砚把😀木尺放好。"}
    assert resolve_field(wrong, "evidence", {unit["source_id"]: unit for unit in units}) is None
    assert wrong["evidence"] not in text


def test_native_contract_allows_one_real_source_and_bounded_revision():
    units = source_units("甲😀。\n乙𠮷。", "REF")
    schema = source_schema(Review.model_json_schema(), units)
    choices = schema["$defs"]["ReviewCheck"]["properties"]["evidence"]["enum"]
    assert choices == [unit["source_id"] for unit in units] + [""]
    assert "REF0001 REF0002" not in choices
    memory = source_schema(Memory.model_json_schema(), units)
    assert "" not in memory["$defs"]["MemoryFact"]["properties"]["evidence"]["enum"]
    patch = source_schema(RevisionPatch.model_json_schema(), units, "delete")
    assert patch["properties"]["edits"]["maxItems"] == 1
    assert patch["$defs"]["TextEdit"]["properties"]["replacement"]["enum"] == [""]


def test_grouped_source_fragments_keep_utf16_offsets():
    text = "甲😀\n乙𠮷\n\n" * 150
    units = source_units(text, "VOL", offset=30, group_size=300)
    for unit in units:
        assert utf16_slice(text, unit["start"] - 30, unit["end"] - 30)[1] == unit["text"]
    assert "".join(unit["text"] for unit in units) == text
