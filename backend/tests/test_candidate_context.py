import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.models import ModuleInput
from ainovel.text_context import text_context
from ainovel.prompt_encoding import expand_prompt
from ainovel.prompt_encoding import encode_prompt
from ainovel.worker import Worker


ORIGINAL = "她到了渡口。\n\n柜上共有十枚铜钱，她交出去三枚，便只剩八枚。\n\n她没有买货，也没有与人动手。"
QUOTE = "柜上共有十枚铜钱，她交出去三枚，便只剩八枚。"
CANDIDATE = ORIGINAL.replace("八枚", "七枚")


class OfflineModel:
    def __init__(self, answer):
        self.answer, self.requests = answer, []

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        request = json.loads(prompt.split("\n只返回")[0])
        self.requests.append(deepcopy(request))
        output = self.answer(request, len(self.requests))
        return {"text": dumps(output), "usage": {"input": 1, "output": 1}, "call_id": "offline-only"}


def claimed_worker(settings, domain, story, model, text=ORIGINAL):
    chapter = domain.save(story["chapter_id"], 0, text)
    domain.queue(
        "module",
        ModuleInput(
            novel_id=story["id"],
            branch_id=story["branch_id"],
            chapter_id=chapter["id"],
            base_revision=chapter["revision"],
            action="writing.advise",
            instruction="隔离回归，不调用真实模型。",
            account="openai_1",
        ).model_dump(),
    )
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    return worker, worker.context(story["branch_id"], chapter["id"]), chapter


def repair_unit(text=ORIGINAL, quote=QUOTE):
    start = len(text[: text.index(quote)].encode("utf-16-le")) // 2
    return {
        "source_id": "EDIT0001",
        "text": quote,
        "start": start,
        "end": start + len(quote.encode("utf-16-le")) // 2,
    }


def test_candidate_context_separates_unsaved_content_from_saved_revision():
    context = {
        "branch": {"id": "b", "story_revision": 8},
        "chapter": {"id": "c", "revision": 3, "content": ORIGINAL},
    }
    original = deepcopy(context)
    candidate = text_context(context, CANDIDATE, "chapter_review")
    assert context == original
    assert candidate["chapter"]["content"] == CANDIDATE
    assert "revision" not in candidate["chapter"]
    assert candidate["chapter"]["base_revision"] == 3
    assert candidate["chapter"]["content_status"] == "unadopted_candidate_under_review"
    assert candidate["text_target"]["content_sha256"] == hashlib.sha256(CANDIDATE.encode()).hexdigest()
    assert (
        candidate["source_version_basis"]["saved_content_sha256"]
        == hashlib.sha256(ORIGINAL.encode()).hexdigest()
    )
    assert (
        text_context(candidate, CANDIDATE, "memory")["source_version_basis"]
        == candidate["source_version_basis"]
    )
    assert text_context(context, ORIGINAL, "review")["chapter"]["revision"] == 3


def test_large_review_wire_reference_keeps_candidate_and_saved_source_separate(settings, domain, story):
    original = "渡船原稿，尚未修正。😀𠮷\n" * 1600
    candidate = original.replace("尚未修正", "已完成修正")

    def answer(wire, number):
        assert "content" not in wire["上下文"]["chapter"]
        assert wire["上下文"]["chapter"]["content_ref"]["path"] == ["任务", "正文"]
        restored = expand_prompt(wire)
        assert restored["任务"]["正文"] == candidate
        assert restored["上下文"]["chapter"]["content"] == candidate
        assert restored["上下文"]["chapter"]["content_status"] == "unadopted_candidate_under_review"
        assert (
            restored["上下文"]["source_version_basis"]["saved_content_sha256"]
            == hashlib.sha256(original.encode()).hexdigest()
        )
        assert all(unit["text"] in candidate for unit in restored["任务"]["可引用原文"])
        return {"checks": [], "findings": [], "summary": "只核对同一候选"}

    model = OfflineModel(answer)
    worker, context, chapter = claimed_worker(settings, domain, story, model, original)
    result = asyncio.run(worker.review(candidate, [], context, 1))
    assert not result["findings"]
    assert len(model.requests) == 1
    assert domain.chapter(chapter["id"])["content"] == original
    assert context["chapter"]["content"] == original


def test_lossless_wire_encoding_change_reuses_completed_generation_but_not_changed_input(
    settings, domain, story, monkeypatch
):
    text = "渡船测量记录😀𠮷。\n" * 1800
    model = OfflineModel(lambda *_: {"answer": "already paid output"})
    worker, context, _ = claimed_worker(settings, domain, story, model, text)
    instruction = {"动作": "只测试既有步骤复用", "正文": text}
    monkeypatch.setattr(
        "ainovel.worker.encode_prompt", lambda instruction, context: {"任务": instruction, "上下文": context}
    )
    first = asyncio.run(worker.call(instruction, context=context))
    monkeypatch.setattr("ainovel.worker.encode_prompt", encode_prompt)
    assert asyncio.run(worker.call(instruction, context=context)) == first
    assert len(model.requests) == 1
    asyncio.run(worker.call({**instruction, "作者新增条件": "保持未知"}, context=context))
    assert len(model.requests) == 2


@pytest.mark.parametrize("operation", ["review", "memory"])
def test_review_and_memory_use_candidate_content_in_every_body_location(settings, domain, story, operation):
    def answer(request, number):
        assert request["任务"]["正文"] == CANDIDATE
        target = request["上下文"]
        assert target["chapter"]["content"] == CANDIDATE
        assert target["chapter"]["base_revision"] == 1 and "revision" not in target["chapter"]
        assert target["source_version_basis"]["chapter_revision"] == 1
        assert target["branch"]["story_revision"] == context["branch"]["story_revision"]
        assert all("八枚" not in row["text"] for row in request["任务"]["可引用原文"])
        if operation == "memory":
            return {"facts": [], "summary": "余数已经修正为七枚。"}
        return {"checks": [], "findings": [], "summary": "只核对本次候选，旧稿问题已经修正。"}

    model = OfflineModel(answer)
    worker, context, chapter = claimed_worker(settings, domain, story, model)
    if operation == "review":
        asyncio.run(worker.review(CANDIDATE, [], context, 1, {"原报告": "旧稿写八枚，需要修正。"}))
    else:
        asyncio.run(worker.memory(CANDIDATE, context))
    assert domain.chapter(chapter["id"])["content"] == ORIGINAL
    assert context["chapter"]["content"] == ORIGINAL


@pytest.mark.parametrize("bad", ["outside_scope", "whole_chapter", "literal_newline"])
def test_local_revision_rejects_scope_escape_and_new_literal_newlines(settings, domain, story, bad):
    def answer(request, number):
        unit = request["任务"]["可替换原文"][0]
        original, replacement = unit["source_id"], QUOTE.replace("八枚", "七枚")
        if number == 1:
            if bad == "outside_scope":
                original, replacement = "她到了渡口。", "她又去了别处。"
            elif bad == "whole_chapter":
                original, replacement = ORIGINAL, CANDIDATE
            else:
                replacement += "\\n\\n"
        return {"edits": [{"original": original, "replacement": replacement}], "reason": "只改铜钱余数。"}

    model = OfflineModel(answer)
    worker, context, chapter = claimed_worker(settings, domain, story, model)
    result = asyncio.run(
        worker.revise_chapter(ORIGINAL, {}, {"findings": []}, context, 1, 200, repair_units=[repair_unit()])
    )
    assert result == CANDIDATE and "\\n" not in result
    assert len(model.requests) == 2
    assert all(
        len(r["任务"]["可替换原文"]) == 1 and r["任务"]["可替换原文"][0]["text"] == QUOTE
        for r in model.requests
    )
    assert domain.chapter(chapter["id"])["content"] == ORIGINAL


def test_existing_code_escape_is_preserved_without_decoding(settings, domain, story):
    quote = "纸上记着字面\\n，余数是八枚。"
    text = "她打开账本。\n\n" + quote + "\n\n她随后合上账本。"
    model = OfflineModel(
        lambda req, _: {
            "edits": [{"original": "EDIT0001", "replacement": quote.replace("八枚", "七枚")}],
            "reason": "不触碰已有代码字面值。",
        }
    )
    worker, context, _ = claimed_worker(settings, domain, story, model, text)
    result = asyncio.run(
        worker.revise_chapter(text, {}, {}, context, 1, 200, repair_units=[repair_unit(text, quote)])
    )
    assert result == text.replace("八枚", "七枚") and "字面\\n" in result
    assert len(model.requests) == 1


@pytest.mark.parametrize("scope", ["empty", "whole_chapter", "wrong_position"])
def test_invalid_local_scope_never_falls_back_to_whole_chapter_or_calls_model(settings, domain, story, scope):
    model = OfflineModel(lambda *_: pytest.fail("Invalid scope must stop before dispatch."))
    worker, context, _ = claimed_worker(settings, domain, story, model)
    units = (
        []
        if scope == "empty"
        else [repair_unit(ORIGINAL, ORIGINAL)]
        if scope == "whole_chapter"
        else [{**repair_unit(), "start": 0}]
    )
    with pytest.raises(DomainError):
        asyncio.run(worker.revise_chapter(ORIGINAL, {}, {}, context, 1, 200, repair_units=units))
    assert not model.requests


def test_overlapping_quote_occurrences_are_not_a_unique_repair_scope(settings, domain, story):
    text = "哈哈哈哈。她随后离开。"
    model = OfflineModel(lambda *_: pytest.fail("Ambiguous quote must stop before dispatch."))
    worker, context, _ = claimed_worker(settings, domain, story, model, text)
    with pytest.raises(DomainError):
        asyncio.run(
            worker.revise_chapter(text, {}, {}, context, 1, 200, repair_units=[repair_unit(text, "哈哈哈")])
        )
    assert not model.requests


@pytest.mark.parametrize("result", ["unknown", "pass"])
def test_negative_condition_without_quotable_evidence_stays_unknown_or_blocks(
    settings, domain, story, result
):
    model = OfflineModel(
        lambda *_: {
            "checks": [{"condition": "不增加恋爱", "result": result, "evidence": ""}],
            "findings": [],
            "summary": "全文未见相关描写，但没有能定位的代表段落。",
        }
    )
    worker, context, _ = claimed_worker(settings, domain, story, model)
    if result == "unknown":
        review = asyncio.run(worker.review(CANDIDATE, ["不增加恋爱"], context, 1))
        assert review["checks"][0]["result"] == "unknown" and len(model.requests) == 1
    else:
        with pytest.raises(DomainError, match="两轮引用修正"):
            asyncio.run(worker.review(CANDIDATE, ["不增加恋爱"], context, 1))
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (worker.task["id"],))
