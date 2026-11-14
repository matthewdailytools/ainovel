"""The same whitespace match must not establish facts, repairs or reference rules."""

import asyncio
import json
from uuid import uuid4

import pytest

from ainovel.db import DomainError, dumps
from ainovel.library import Library
from ainovel.plot_audit import audit_plot
from ainovel.reference import session
from ainovel.targeted_revision import run_targeted_revision
from ainovel.worker import Worker
from test_memory_evidence import MemoryModel, prepared
from test_plot_audit import AUTHOR, Grounded
from test_reference_flow import Model
from test_targeted_revision import RevisionWorker, reviewed as reviewed


@pytest.mark.parametrize("quote", ["\n", "\u3000"])
def test_whitespace_cannot_support_new_memory_fact(settings, domain, story, quote, monkeypatch):
    monkeypatch.setattr("test_memory_evidence.TEXT", "甲记下时刻。\n\n乙收回旧钥匙。\n" + quote)
    model = MemoryModel(quote, repair=False)
    worker, _, chapter, _ = prepared(settings, domain, story, model)
    with pytest.raises(DomainError) as caught:
        asyncio.run(worker.module())
    assert caught.value.code == "evidence" and len(model.requests) == 2
    failures = [
        json.loads(row["payload"])
        for row in domain.db.all("SELECT payload FROM task_events WHERE kind='citation_repair' ORDER BY id")
    ]
    assert len(failures) == 3 and all("facts[1].evidence" in str(row["issues"]) for row in failures)
    assert domain.db.all("SELECT * FROM facts") == []
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.chapter(chapter["id"]) == chapter


@pytest.mark.parametrize("result", ["pass", "fail", "unknown"])
def test_plot_comparison_rejects_whitespace_evidence_even_when_present_in_prose(result):
    class WhitespaceComparison(Grounded):
        async def grounded(self, instruction, schema, context, validate, **kwargs):
            def checked(value):
                if "checks" in value:
                    value["checks"][0]["evidence"] = " "
                return validate(value)

            return await super().grounded(instruction, schema, context, checked, **kwargs)

    worker = WhitespaceComparison(result=result)
    with pytest.raises(DomainError, match="正文证据"):
        asyncio.run(audit_plot(worker, AUTHOR, "沈砚 记下条件，交易仍未完成。", 1))
    assert len(worker.requests) == 2


def test_whitespace_cannot_confirm_a_targeted_problem_was_repaired(domain, reviewed):
    class WhitespaceRecheck(RevisionWorker):
        async def grounded(self, instruction, schema, context, validator, max_output):
            value = {
                "results": [
                    {"finding_index": f["index"], "result": "pass", "reason": "声称已修复", "evidence": "\n"}
                    for f in instruction["原问题"]
                ]
            }
            if issues := validator(value):
                raise DomainError("evidence", "；".join(issues))
            return value

    request, report, chapter = reviewed
    with pytest.raises(DomainError, match="复检证据"):
        asyncio.run(
            run_targeted_revision(
                WhitespaceRecheck(domain), request, domain.context(request["branch_id"], chapter["id"])
            )
        )
    assert domain.chapter(chapter["id"]) == chapter
    assert domain.artifact(report["id"]) == report
    assert len(domain.db.all("SELECT * FROM artifacts")) == 1


def reference_with_space(client, domain, settings, story):
    text = "甲 乙。"
    book = Library(domain.db, settings.data_dir).import_file("原创引用样例.txt", text.encode())
    unit = domain.db.one("SELECT * FROM source_units WHERE book_id=?", (book["id"],))
    response = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": unit["id"],
            "start": 0,
            "end": len(text),
            "quote": text,
            "goal": "仅依据这段观察写法。",
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reference_analysis_does_not_publish_a_whitespace_rule(client, domain, settings, story):
    class WhitespaceAnalysis(Model):
        async def generate(self, *args, **kwargs):
            response = await super().generate(*args, **kwargs)
            value = json.loads(response["text"])
            value["cards"][0]["evidence"] = " "
            return {**response, "text": dumps(value)}

    source = reference_with_space(client, domain, settings, story)
    response = client.post(
        f"/api/reference-sessions/{source['id']}/actions",
        json={
            "base_revision": source["revision"],
            "action": "analyze",
            "account": "deepseek",
            "client_request_id": uuid4().hex,
        },
    )
    assert response.status_code == 202, response.text
    model = WhitespaceAnalysis()
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(response.json()["id"])["status"] == "blocked_runtime"
    assert len(model.requests) == 2  # Identical final input reuses a completed paid step.
    assert len(domain.db.all("SELECT * FROM task_events WHERE kind='citation_repair'")) == 3
    assert session(domain, source["id"]) == source
    assert domain.db.all("SELECT * FROM artifacts") == []


@pytest.mark.parametrize("evidence,expected", [(" ", 400), ("甲 乙。", 200)])
def test_manual_reference_rule_requires_actual_text_without_trimming_valid_quote(
    client, domain, settings, story, evidence, expected
):
    source = reference_with_space(client, domain, settings, story)
    response = client.patch(
        f"/api/reference-sessions/{source['id']}",
        json={
            "base_revision": source["revision"],
            "goal": "本段写法",
            "cards": [
                {
                    "id": "manual",
                    "observation": "原文观察",
                    "interpretation": "作用解释",
                    "technique": "可迁移写法",
                    "conditions": "使用边界",
                    "exceptions": "例外",
                    "evidence": evidence,
                }
            ],
            "selected_ids": ["manual"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
        },
    )
    assert response.status_code == expected, response.text
    current = session(domain, source["id"])
    if expected == 400:
        assert current == source
    else:
        assert current["payload"]["cards"][0]["evidence"] == evidence
