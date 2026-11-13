"""Chapter-bound segment checks keep their origin when equal quotations recur."""

from copy import deepcopy
import asyncio
import re

import pytest
from pydantic import ValidationError

from ainovel.db import DomainError
from ainovel.evidence import completion_sources
from ainovel.models import SegmentCompletion
from ainovel.worker import Worker


PLAN = {
    "chapters": [
        {"number": 1, "title": "出发", "plot": "准备行装", "fixed": []},
        {"number": 2, "title": "交涉", "plot": "交易失败", "fixed": ["不战斗"]},
    ]
}
PAYLOAD = {"completion_scope": "segment", "brief": "本批交涉，后续才收束全书", "fixed": ["保留记录"]}


class Capture:
    completion_report = Worker.completion_report

    def __init__(self, evidence):
        self.evidence = evidence
        self.instructions = []
        self.issues = []

    def author_snapshot(self):
        return {"intent_contract": {}}

    def genre_snapshot(self):
        return {}

    async def grounded(self, instruction, schema, context, validator, max_output):
        self.instructions.append(deepcopy(instruction))
        units = instruction["可引用原文"]
        promises = []
        for condition in instruction["必须逐项核对的本批约定"]:
            match = re.match(r"第(\d+)章", condition)
            chapter = int(match[1]) if match else 1
            unit = next(row for row in units if row["chapter"] == chapter)
            value = unit["source_id"]
            if condition == "第2章情节：交易失败":
                value = self.evidence(units)
            promises.append({"condition": condition, "result": "pass", "evidence": value})
        report = schema.model_validate(
            {"complete": True, "promises": promises, "findings": [], "summary": "仅当前范围"}
        ).model_dump()
        self.issues = validator(report)
        assert all("evidence_chapter" not in item for item in report["promises"])
        assert all("evidence_source" not in item for item in report["promises"])
        if self.issues:
            raise DomainError("citation", "；".join(self.issues))
        return report


def chapters(first, second):
    return [
        {"id": "one", "position": 1, "revision": 2, "content": first},
        {"id": "two", "position": 2, "revision": 3, "content": second},
    ]


@pytest.mark.parametrize(
    ("first", "second", "evidence", "valid"),
    [
        ("她整理行囊。", "交易失败。", lambda units: units[0]["source_id"], False),
        ("她整理行囊。", "交易失败。", lambda units: "她整理行囊。", False),
        ("她记下读数。", "她记下读数。", lambda units: "她记下读数。", False),
        ("她记下读数。", "她记下读数。", lambda units: units[1]["source_id"], True),
        ("她整理行囊。", "交易失败。交易失败。", lambda units: "交易失败。", False),
        ("她整理行囊。", "交易失败。", lambda units: "交易失败。", True),
    ],
)
def test_segment_plot_evidence_must_be_unambiguous_and_belong_to_target_chapter(
    first, second, evidence, valid
):
    worker = Capture(evidence)
    if valid:
        report = asyncio.run(worker.completion_report(chapters(first, second), PAYLOAD, PLAN, "本次批次"))
        target = next(item for item in report["promises"] if item["condition"] == "第2章情节：交易失败")
        assert target["evidence_chapter"] == 2
        assert report["closed"] is False and report["complete"] is True
    else:
        with pytest.raises(DomainError, match="第2章"):
            asyncio.run(worker.completion_report(chapters(first, second), PAYLOAD, PLAN, "本次批次"))
        assert worker.issues


def test_segment_aggregation_preserves_verified_chapter_when_quotations_are_equal():
    worker = Capture(lambda units: next(unit["source_id"] for unit in units if unit["chapter"] == 2))
    frozen = chapters("她记下读数。", "她记下读数。")
    first = asyncio.run(worker.completion_report(frozen, PAYLOAD, PLAN, "全文分段1"))
    report = asyncio.run(worker.completion_report(frozen, PAYLOAD, PLAN, "本次批次", [first]))
    target = next(item for item in report["promises"] if item["condition"] == "第2章情节：交易失败")
    assert target["evidence_chapter"] == 2
    assert {unit["chapter"] for unit in worker.instructions[-1]["可引用原文"]} == {1, 2}
    derived = next(
        item
        for item in worker.instructions[-1]["分段全文报告"][0]["promises"]
        if item["condition"] == "第2章情节：交易失败"
    )
    selected = next(
        unit for unit in worker.instructions[-1]["可引用原文"] if unit["source_id"] == derived["evidence"]
    )
    assert selected["chapter"] == 2


def test_segment_aggregation_keeps_selected_later_duplicate_utf16_span():
    repeated = "😀" + "甲" * 599
    frozen = chapters("她整理行囊。", repeated * 2)
    worker = Capture(
        lambda units: next(unit["source_id"] for unit in units if unit["chapter"] == 2 and unit["start"] > 0)
    )
    first = asyncio.run(worker.completion_report(frozen, PAYLOAD, PLAN, "全文分段1"))
    target = next(item for item in first["promises"] if item["condition"] == "第2章情节：交易失败")
    assert target["evidence_source"] == {
        "chapter": 2,
        "chapter_id": "two",
        "revision": 3,
        "start": 601,
        "end": 1202,
    }
    original = deepcopy(first)
    units, reports = completion_sources(frozen, [first])
    grouped = next(item for item in reports[0]["promises"] if item["condition"] == target["condition"])
    selected = next(unit for unit in units if unit["source_id"] == grouped["evidence"])
    assert selected["start"] == 601 and selected["end"] == 1202
    assert selected["text"] == repeated
    assert first == original


@pytest.mark.parametrize(
    "change", [{"revision": 9}, {"chapter_id": "missing"}, {"start": 1}, {"end": 1}, {"start": True}]
)
def test_segment_aggregation_rejects_stale_or_invalid_verified_source_metadata(change):
    frozen = chapters("她整理行囊。", "😀交易失败。")
    worker = Capture(lambda units: next(unit["source_id"] for unit in units if unit["chapter"] == 2))
    report = asyncio.run(worker.completion_report(frozen, PAYLOAD, PLAN, "全文分段1"))
    target = next(item for item in report["promises"] if item["condition"] == "第2章情节：交易失败")
    target["evidence_source"].update(change)
    with pytest.raises(ValueError):
        completion_sources(frozen, [report])


def test_model_cannot_supply_backend_source_metadata():
    with pytest.raises(ValidationError):
        SegmentCompletion.model_validate(
            {
                "complete": True,
                "promises": [
                    {
                        "condition": "本章",
                        "result": "pass",
                        "evidence": "C2",
                        "evidence_source": {"chapter": 2},
                    }
                ],
                "findings": [],
                "summary": "禁止模型自报来源",
            }
        )
