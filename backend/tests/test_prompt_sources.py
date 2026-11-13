from copy import deepcopy

import pytest

from ainovel.db import DomainError, dumps
from ainovel.prompt_encoding import encode_prompt, expand_prompt
from ainovel.prompt_sources import compact_sources, expand_sources


def frame():
    text = "先查原始记录。😀𠮷\n" * 1200
    quote = text[6:800]
    source = {
        "chapter_id": "old",
        "revision": 3,
        "start": len(text[:6].encode("utf-16-le")) // 2,
        "end": len(text[:800].encode("utf-16-le")) // 2,
        "quote": quote,
    }
    row = {"id": "event", "revision": 2, "payload": {"sources": [source], "story_time": "昨夜"}}
    return {
        "任务": {"正文": text},
        "上下文": {
            "chapter": {"id": "new", "content": text, "base_revision": 0},
            "recent": [{"id": "old", "revision": 3, "content": text}],
            "longform": {
                "characters": [deepcopy(row)],
                "events": [deepcopy(row)],
                "states": [],
                "uncertain": [deepcopy(row)],
            },
            "chapter_scenes": {"scenes": [{"entry": {"events": []}, "exit": {"events": [deepcopy(row)]}}]},
        },
    }


def test_complete_source_objects_and_temporal_membership_roundtrip_without_mutation():
    original = frame()
    before = deepcopy(original)
    encoded = encode_prompt(original["任务"], original["上下文"])
    assert expand_prompt(encoded) == original == before
    assert len(encoded["来源引用表"]) == 1
    source = next(iter(encoded["来源引用表"].values()))
    assert "quote_ref" in source and "quote" not in source
    assert source["revision"] == 3 and source["chapter_id"] == "old"
    assert encoded["上下文"]["chapter_scenes"]["scenes"][0]["entry"]["events"] == []
    assert len(encoded["上下文"]["chapter_scenes"]["scenes"][0]["exit"]["events"]) == 1
    assert len(dumps(encoded).encode()) < len(dumps(original).encode())


@pytest.mark.parametrize("change", ["version", "quote", "range", "identity"])
def test_mismatched_source_never_aliases_to_a_different_recent_text(change):
    original = frame()
    source = original["上下文"]["longform"]["events"][0]["payload"]["sources"][0]
    if change == "version":
        source["revision"] += 1
    elif change == "quote":
        source["quote"] = "另一个版本的证据" * 100
    elif change == "range":
        source["end"] += 1
    else:
        source["chapter_id"] = "other"
    encoded = compact_sources(original)
    assert expand_sources(encoded) == original
    literal = [
        row
        for row in encoded["来源引用表"].values()
        if row.get("quote") == source["quote"]
        and row["revision"] == source["revision"]
        and row["chapter_id"] == source["chapter_id"]
    ]
    assert literal


@pytest.mark.parametrize("change", ["version", "hash", "range", "negative", "missing_id"])
def test_damaged_reference_cannot_expand_as_valid_evidence(change):
    encoded = compact_sources(frame())
    source = next(iter(encoded["来源引用表"].values()))
    if change == "version":
        source["revision"] += 1
    elif change == "hash":
        source["quote_ref"]["sha256"] = "wrong"
    elif change == "range":
        source["end"] += 1
    elif change == "negative":
        source["quote_ref"]["recent_index"] = -1
    else:
        encoded["上下文"]["longform"]["events"][0] = {"source_ref": "missing"}
    with pytest.raises(DomainError):
        expand_sources(encoded)
