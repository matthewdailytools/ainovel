"""Real storage/API scale checks with synthetic prose and no model spending."""

import json
from time import perf_counter
from uuid import uuid4

import pytest

from ainovel.story_graph import SaveRecord, graph, query_context, save_record


@pytest.mark.parametrize("count", [36, 100])
def test_large_import_context_export_and_early_edit(client, domain, story, count):
    branch = story["branch_id"]
    # Unique per-chapter text avoids confusing duplicate-content safeguards.
    chapters = [
        f"第{number}章 第{(number - 1) // 12 + 1}卷渡口\n"
        + (f"第{number}次核对水位，宁禾仍等证人。😀𠮷\n" * 150)
        for number in range(1, count + 1)
    ]
    text = "".join(chapters)
    start = perf_counter()
    response = client.post(
        f"/api/branches/{branch}/import-previews",
        files={"file": ("原创规模样本.txt", text.encode(), "text/plain")},
        data={"encoding": "utf-8-sig"},
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    assert len(preview["segments"]) == count
    assert len(domain.db.all("SELECT id FROM chapters")) == 1
    body = {
        "base_story_revision": preview["base_story_revision"],
        "client_request_id": uuid4().hex,
        "mappings": [
            {key: segment[key] for key in ("title", "start", "end")} | {"action": "append"}
            for segment in preview["segments"]
        ],
    }
    url = f"/api/branches/{branch}/import-previews/{preview['id']}/confirm"
    confirmed = client.post(url, json=body)
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert client.post(url, json=body).json() == receipt
    ids = [row["chapter_id"] for row in receipt["results"]]
    assert [domain.chapter(identifier)["content"] for identifier in ids] == chapters
    imported_ms = (perf_counter() - start) * 1000
    started = perf_counter()
    context = domain.context(branch, ids[-1])
    context_ms = (perf_counter() - started) * 1000
    assert [row["id"] for row in context["recent"]] == ids[-3:-1]
    assert context["chapter"]["content"] == chapters[-1]
    exported = domain.export(branch)
    assert all(chapter in exported for chapter in chapters)
    original = domain.chapter(ids[0])
    domain.save(ids[0], original["revision"], original["content"] + "作者在此补充核验依据。")
    assert all(domain.chapter(identifier)["stale"] for identifier in ids[1:])
    refreshed = domain.context(branch, ids[-1])["recent"]
    assert [row["id"] for row in refreshed] == [story["chapter_id"], ids[0]]
    assert refreshed[-1]["revision"] == original["revision"] + 1
    assert all(not row["stale"] for row in refreshed)
    print(
        json.dumps(
            {
                "chapters": count,
                "utf8_bytes": len(text.encode()),
                "import_ms": round(imported_ms, 2),
                "context_ms": round(context_ms, 2),
                "context_bytes": len(json.dumps(context, ensure_ascii=False).encode()),
            }
        )
    )


def test_hundred_events_cross_volume_narrative_visibility_and_invalidation(domain, story):
    branch = story["branch_id"]

    def add(payload):
        save_record(
            domain,
            branch,
            SaveRecord(base_story_revision=domain.branch(branch)["story_revision"], record=payload),
        )
        return graph(domain, branch)["records"][-1]

    character = add({"kind": "character", "title": "宁禾", "aliases": ["小宁"]})
    chapters, events = [], []
    start = perf_counter()
    for number in range(1, 101):
        chapter = domain.create_chapter(branch, f"第{number}章")
        chapter = domain.save(chapter["id"], 0, f"宁禾核对第{number}道水闸。😀𠮷")
        source = {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": 0,
            "end": len(chapter["content"].encode("utf-16-le")) // 2,
            "quote": chapter["content"],
        }
        event = add(
            {
                "kind": "event",
                "title": f"核对第{number}道闸",
                "volume": f"卷{(number - 1) // 12 + 1}",
                "status": "actual",
                "story_order": number,
                "participants": [character["id"]],
                "sources": [source],
            }
        )
        chapters.append(chapter)
        events.append(event)
    setup_ms = (perf_counter() - start) * 1000
    started = perf_counter()
    context = query_context(domain, branch, chapters[49]["id"], 100, "character")
    query_ms = (perf_counter() - started) * 1000
    serialized = json.dumps(context, ensure_ascii=False)
    # A later story-time query must never reveal not-yet-narrated evidence.
    visible = {row["id"] for row in context["events"]}
    assert visible == {row["id"] for row in events[:49]}
    before = domain.chapter(chapters[0]["id"])
    domain.save(before["id"], before["revision"], before["content"] + "原文变更。")
    snapshot = graph(domain, branch)
    assert all(row["invalid_reasons"] for row in snapshot["records"] if row["kind"] == "event")
    print(
        json.dumps(
            {
                "events": 100,
                "setup_ms": round(setup_ms, 2),
                "query_ms": round(query_ms, 2),
                "context_bytes": len(serialized.encode()),
            }
        )
    )
