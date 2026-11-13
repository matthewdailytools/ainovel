"""Exercise documented file/chapter limits on temporary data without model calls."""

import hashlib
import json
from time import perf_counter
import tracemalloc
from uuid import uuid4

from ainovel.manuscript_import import MAX_FILE


def test_20mib_plus_one_is_rejected_without_persisting_partial_preview(client, domain, story):
    before = domain.db.all("SELECT id FROM manuscript_imports")
    started = perf_counter()
    response = client.post(
        f"/api/branches/{story['branch_id']}/import-previews",
        files={"file": ("超界稿.txt", b"a" * (MAX_FILE + 1), "text/plain")},
        data={"encoding": "utf-8-sig"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "format"
    assert domain.db.all("SELECT id FROM manuscript_imports") == before
    assert domain.chapter(story["chapter_id"])["content"] == ""
    print(
        json.dumps({"file_bytes": MAX_FILE + 1, "rejected_ms": round((perf_counter() - started) * 1000, 2)})
    )


def test_exact_20mib_chinese_preview_explicit_skip_and_utf16_boundary(client, domain, story):
    first = "第一章 取证😀𠮷\n她保留原始记录。\n"
    prefix = (first + "第二章 大稿范围\n").encode("utf-8")
    # Few chapter boundaries and bounded line lengths avoid a manufactured O(n²) input.
    chunk = ("渡船复测记录" * 100 + "\n").encode("utf-8")
    remaining = MAX_FILE - len(prefix)
    count, tail = divmod(remaining, len(chunk))
    raw = prefix + chunk * count + b"x" * tail
    assert len(raw) == MAX_FILE
    before = domain.db.all("SELECT * FROM chapters")
    tracemalloc.start()
    started = perf_counter()
    try:
        response = client.post(
            f"/api/branches/{story['branch_id']}/import-previews",
            files={"file": ("整二十MiB中文稿.txt", raw, "text/plain")},
            data={"encoding": "utf-8-sig"},
        )
        preview_ms = (perf_counter() - started) * 1000
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status_code == 201, response.text
    row = response.json()
    assert domain.db.all("SELECT * FROM chapters") == before
    assert len(row["segments"]) == 2
    stored = domain.db.one(
        "SELECT raw_hash,length(raw) AS bytes FROM manuscript_imports WHERE id=?", (row["id"],)
    )
    assert stored["bytes"] == MAX_FILE and stored["raw_hash"] == hashlib.sha256(raw).hexdigest()
    first_end = len(first.encode("utf-16-le")) // 2
    assert row["segments"][0]["end"] == first_end
    assert row["segments"][1]["start"] == first_end
    read_url = f"/api/branches/{story['branch_id']}/import-previews/{row['id']}/read"
    # Start in the low surrogate of the first emoji; the entire read must fail.
    split_surrogate = len(first[: first.index("😀")].encode("utf-16-le")) // 2 + 1
    broken = client.post(read_url, json={"title": "错误边界", "start": split_surrogate, "end": first_end})
    assert broken.status_code == 400 and broken.json()["code"] == "import_range"
    valid = client.post(read_url, json={"title": "第一章", "start": 0, "end": first_end})
    assert valid.status_code == 200 and valid.json()["content"] == first
    confirm_url = f"/api/branches/{story['branch_id']}/import-previews/{row['id']}/confirm"
    mappings = [
        {"title": "取证", "start": 0, "end": first_end, "action": "append"},
        {"title": "明确不导入的大稿尾部", "start": first_end, "end": row["length_utf16"], "action": "skip"},
    ]
    payload = {
        "base_story_revision": row["base_story_revision"],
        "client_request_id": uuid4().hex,
        "mappings": mappings,
    }
    started = perf_counter()
    confirmed = client.post(confirm_url, json=payload)
    confirm_ms = (perf_counter() - started) * 1000
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert receipt["atomic"] is True
    assert [result["action"] for result in receipt["results"]] == ["append", "skip"]
    assert receipt["results"][1]["start"] == first_end
    assert receipt["results"][1]["end"] == row["length_utf16"]
    added = [
        chapter for chapter in domain.db.all("SELECT * FROM chapters") if chapter["id"] != story["chapter_id"]
    ]
    assert len(added) == 1 and added[0]["content"] == first
    assert client.post(confirm_url, json=payload).json() == receipt
    print(
        json.dumps(
            {
                "file_bytes": len(raw),
                "utf16_positions": row["length_utf16"],
                "preview_ms": round(preview_ms, 2),
                "confirm_selected_range_ms": round(confirm_ms, 2),
                "python_peak_traced_bytes": peak,
                "memory_scope": "tracemalloc Python allocations during multipart preview; not total RSS",
                "chapters_appended": 1,
                "skipped_ranges": 1,
            }
        )
    )


def test_250000_character_chapter_limit_preserves_non_bmp_and_rejects_plus_one(client, domain, story):
    text = "渡船\n" * 83_332 + "😀𠮷渡船"
    assert len(text) == 250_000
    url = f"/api/chapters/{story['chapter_id']}"
    started = perf_counter()
    response = client.put(url, json={"base_revision": 0, "content": text})
    save_ms = (perf_counter() - started) * 1000
    assert response.status_code == 200, response.text
    assert response.json()["content"] == text and response.json()["revision"] == 1
    restored = client.get(url)
    assert restored.status_code == 200 and restored.json()["content"] == text
    rejected = client.put(url, json={"base_revision": 1, "content": text + "尾"})
    assert rejected.status_code == 422
    assert domain.chapter(story["chapter_id"])["content"] == text
    assert domain.chapter(story["chapter_id"])["revision"] == 1
    history = client.get(url + "/history").json()
    assert len(history) == 2 and history[0]["content"] == text and history[1]["content"] == ""
    print(
        json.dumps(
            {
                "python_characters": len(text),
                "utf8_bytes": len(text.encode()),
                "utf16_positions": len(text.encode("utf-16-le")) // 2,
                "save_ms": round(save_ms, 2),
                "extra_character_status": rejected.status_code,
            }
        )
    )
