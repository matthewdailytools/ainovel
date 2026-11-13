from copy import deepcopy

import pytest

from ainovel.db import DomainError
from ainovel.evidence import source_units
from ainovel.prompt_encoding import encode_prompt, expand_prompt
from ainovel.prompt_units import compact_units, expand_units


@pytest.mark.parametrize("key", ["正文", "原稿"])
def test_whole_body_including_blank_lines_and_non_bmp_is_preserved_in_verbatim_units(key):
    text = "\n\n" + ("先核对渡船😀𠮷。\n\n\t" * 2200) + "\n  \n"
    units = source_units(text, "P")
    instruction = {key: text, "可引用原文": units, "锁定结果": ["不成交"]}
    context = {"chapter": {"content": text, "base_revision": 3}, "longform": {"states": [], "conflicts": []}}
    before = deepcopy({"任务": instruction, "上下文": context})
    encoded = encode_prompt(instruction, context)
    assert isinstance(encoded["任务"][key], dict)
    assert encoded["任务"]["可引用原文"] == units
    assert expand_prompt(encoded) == before == {"任务": instruction, "上下文": context}


@pytest.mark.parametrize("change", ["overlap", "wrong_quote", "duplicate_id", "half_surrogate"])
def test_invalid_units_do_not_replace_body(change):
    text = "渡船😀𠮷。\n" * 1000
    units = source_units(text, "P")
    if change == "overlap":
        units[1]["start"] = 0
    elif change == "wrong_quote":
        units[0]["text"] = "不同版本"
    elif change == "duplicate_id":
        units[1]["source_id"] = units[0]["source_id"]
    else:
        units[0]["end"] = 3
    original = {"任务": {"正文": text, "可替换原文": units}, "上下文": {}}
    assert compact_units(original, ["正文"]) == original


@pytest.mark.parametrize("change", ["hash", "gap", "order", "length", "path", "range"])
def test_tampering_cannot_expand_as_valid_body(change):
    text = "渡船😀𠮷。\n" * 1000
    encoded = compact_units(
        {"任务": {"正文": text, "可引用原文": source_units(text, "P")}, "上下文": {}}, ["正文"]
    )
    ref = encoded["任务"]["正文"]["body_units_ref"]
    if change == "hash":
        ref["sha256"] = "wrong"
    elif change == "gap":
        ref["gaps"][0] = "被添加"
    elif change == "order":
        ref["order"].reverse()
    elif change == "length":
        ref["utf16_length"] -= 1
    elif change == "path":
        ref["path"] = ["任务", "其他内容"]
    else:
        encoded["任务"]["可引用原文"][0]["end"] += 1
    with pytest.raises(DomainError):
        expand_units(encoded)
