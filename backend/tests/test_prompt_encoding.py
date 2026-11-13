from copy import deepcopy

import pytest

from ainovel.db import DomainError, dumps
from ainovel.prompt_encoding import encode_prompt, expand_prompt


@pytest.mark.parametrize("key", ["正文", "候选正文", "本次候选"])
def test_large_target_prose_has_one_canonical_body_and_roundtrips_exactly(key):
    text = "她查阅记录。😀𠮷\n" * 2000
    instruction = {key: text, "可引用原文": [{"source_id": "P1", "text": text[:100]}]}
    context = {
        "chapter": {
            "id": "chapter",
            "content": text,
            "base_revision": 2,
            "content_status": "unadopted_candidate_under_review",
        },
        "source_version_basis": {"chapter_revision": 2, "saved_content_sha256": "saved-original-hash"},
        "longform": {"states": [], "coverage": "未知不补齐"},
    }
    original = deepcopy({"任务": instruction, "上下文": context})
    encoded = encode_prompt(instruction, context)
    assert expand_prompt(encoded) == original
    assert {"任务": instruction, "上下文": context} == original
    assert "content" not in encoded["上下文"]["chapter"]
    assert encoded["上下文"]["chapter"]["content_ref"]["path"] == ["任务", key]
    assert encoded["任务"][key] == text
    assert len(dumps(encoded).encode()) < len(dumps(original).encode()) - 30000
    assert encoded["上下文"]["source_version_basis"] == context["source_version_basis"]


def test_small_requests_and_different_baseline_or_nested_instruction_are_not_aliased():
    for instruction, context in [
        ({"正文": "短正文"}, {"chapter": {"content": "短正文"}}),
        ({"正文": "新候选" * 10000}, {"chapter": {"content": "旧原稿"}}),
        ({"原始任务": {"正文": "原文" * 10000}}, {"chapter": {"content": "原文" * 10000}}),
    ]:
        assert encode_prompt(instruction, context) == {"任务": instruction, "上下文": context}


@pytest.mark.parametrize("change", ["hash", "length", "path"])
def test_reference_integrity_rejects_wrong_hash_utf16_and_unknown_paths(change):
    text = "正文😀𠮷" * 10000
    encoded = encode_prompt({"正文": text}, {"chapter": {"content": text}})
    ref = encoded["上下文"]["chapter"]["content_ref"]
    if change == "hash":
        ref["sha256"] = "wrong"
    elif change == "length":
        ref["utf16_length"] -= 1
    else:
        ref["path"] = ["上下文", "secret"]
    with pytest.raises(DomainError):
        expand_prompt(encoded)
