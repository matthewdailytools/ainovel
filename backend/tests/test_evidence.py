from ainovel.domain import utf16_slice
from ainovel.evidence import resolve_field, source_units, source_schema, completion_sources
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


def test_completion_batch_reads_middle_of_every_chapter_in_full():
    chapters = [
        {"id": str(n), "position": n, "revision": 3, "content": "开头" * 600 + "😀中段矛盾" + "结尾" * 600}
        for n in range(1, 7)
    ]
    units, reports = completion_sources(chapters)
    assert reports == []
    for chapter in chapters:
        selected = [unit for unit in units if unit["chapter_id"] == chapter["id"]]
        assert "".join(unit["text"] for unit in selected) == chapter["content"]
        assert all(unit["revision"] == 3 for unit in selected)


def test_completion_aggregation_keeps_middle_evidence_and_original_reports():
    chapter = {
        "id": "chapter",
        "position": 1,
        "revision": 2,
        "content": "前" * 1000 + "😀中段矛盾" + "后" * 1000,
    }
    report = {
        "promises": [{"condition": "信息边界", "result": "fail", "evidence": "😀中段矛盾"}],
        "findings": [{"chapter": 1, "quote": "😀中段矛盾", "reason": "原文矛盾", "severity": "blocker"}],
    }
    units, reports = completion_sources([chapter], [report])
    assert report["findings"][0]["quote"] == "😀中段矛盾"
    source_id = reports[0]["findings"][0]["quote"]
    source = next(unit for unit in units if unit["source_id"] == source_id)
    assert source["revision"] == 2 and source["chapter_id"] == chapter["id"]
    assert utf16_slice(chapter["content"], source["start"], source["end"])[1] == "😀中段矛盾"
    assert reports[0]["promises"][0]["result"] == "fail"
