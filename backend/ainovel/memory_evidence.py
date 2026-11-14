"""Precise, bounded feedback for the existing single-source memory contract."""

import re

from .evidence import resolve_field

MEMORY_EVIDENCE_VERSION = "memory-single-source/1"


def memory_evidence_issues(result, text, sources):
    identifiers = list(sources)
    hint = (
        f"合法单编号例如{identifiers[0]}；本批集合为{identifiers[0]}—{identifiers[-1]}"
        f"（共{len(identifiers)}项，以可引用原文列表为准）。"
        if identifiers
        else "本批没有合法来源编号，不得编造证据。"
    )
    issues = []
    for index, fact in enumerate(result["facts"]):
        resolve_field(fact, "evidence", sources)
        value = fact["evidence"]
        if value.strip() and value in text:
            continue
        location = f"facts[{index}].evidence"
        if len(re.findall(r"\bMEM[0-9]+\b", value)) > 1:
            issues.append(
                f"{location} 检测到多个编号；一个字段只接受单个source_id或一段连续逐字原文，"
                "不能用逗号、空格、范围或拼接多段代替。"
                + hint
                + "请核实整条陈述的支持范围；复合陈述需在原文中核实或合理拆分，"
                "不能盲选第一编号、拼非连续引文或仅为通过删除事实。"
            )
        elif re.fullmatch(r"MEM[0-9]+", value):
            issues.append(f"{location} 编号不属于本批合法集合；必须使用一个完整合法编号。{hint}")
        else:
            issues.append(
                f"{location} 不是单个合法source_id，也不在本章连续逐字原文中；"
                f"概括、改写和跨章引文不能充当证据。{hint}"
            )
    return issues
