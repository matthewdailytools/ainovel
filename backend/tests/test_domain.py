import pytest

from ainovel.db import DomainError
from ainovel.domain import utf16_slice


def test_version_conflict_preserves_original(domain, story):
    first = domain.save(story["chapter_id"], 0, "沈砚写下第一行。")
    with pytest.raises(DomainError, match="新版本"):
        domain.save(story["chapter_id"], 0, "旧窗口不能覆盖。")
    assert domain.chapter(first["id"])["content"] == first["content"]
    history = domain.db.one("SELECT * FROM revisions WHERE chapter_id=? AND revision=1", (first["id"],))
    domain.save(first["id"], 1, "第二稿")
    restored = domain.restore(first["id"], 2, history["id"])
    assert restored["revision"] == 3 and restored["content"] == first["content"]


@pytest.mark.parametrize(
    "text,start,end,part", [("甲😀𠮷乙", 1, 5, "😀𠮷"), ("甲\n乙", 1, 2, "\n"), ("中文", 0, 2, "中文")]
)
def test_utf16(text, start, end, part):
    assert utf16_slice(text, start, end)[1] == part


@pytest.mark.parametrize("start,end", [(1, 2), (-1, 0), (0, 9), (3, 2)])
def test_invalid_selection(start, end):
    with pytest.raises(DomainError):
        utf16_slice("😀甲", start, end)


def candidate(domain, story, payload=None):
    domain.save(story["chapter_id"], 0, "甲😀乙")
    return domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 1,
        },
        "text_patch",
        payload or {"text": "新字", "original": "😀", "start": 1, "end": 3},
        {},
    )


def test_accept_is_atomic_and_idempotent(domain, story):
    artifact = candidate(domain, story)
    result = domain.accept(artifact["id"], "same-operation")
    assert domain.accept(artifact["id"], "same-operation") == result
    assert domain.accept(artifact["id"], "another-operation") == result
    assert domain.chapter(story["chapter_id"])["content"] == "甲新字乙"
    assert domain.chapter(story["chapter_id"])["revision"] == 2


def test_edit_while_generating_is_not_overwritten(domain, story):
    artifact = candidate(domain, story)
    domain.save(story["chapter_id"], 1, "作者继续写了")
    with pytest.raises(DomainError):
        domain.accept(artifact["id"], "cannot-replace")
    assert domain.chapter(story["chapter_id"])["content"] == "作者继续写了"


def test_bad_fact_rolls_back_adoption(domain, story):
    artifact = candidate(
        domain,
        story,
        {
            "text": "替换",
            "original": "😀",
            "start": 1,
            "end": 3,
            "memory": {
                "facts": [
                    {"subject": "沈砚", "kind": "event", "statement": "错误事实", "evidence": "原文不存在"}
                ]
            },
        },
    )
    with pytest.raises(DomainError):
        domain.accept(artifact["id"], "invalid-memory")
    assert domain.chapter(story["chapter_id"])["content"] == "甲😀乙"
    assert domain.artifact(artifact["id"])["status"] == "candidate"


def test_ten_chapter_state_invalidation(domain, story):
    ids = [story["chapter_id"]]
    for n in range(2, 11):
        ids.append(domain.create_chapter(story["branch_id"], f"第{n}章")["id"])
    for n, chapter_id in enumerate(ids, 1):
        row = domain.save(chapter_id, 0, f"第{n}天沈砚只剩{11 - n}枚铜钱。")
        with domain.db.connect(True) as tx:
            domain.add_fact_tx(
                tx,
                story["branch_id"],
                chapter_id,
                row["revision"],
                {
                    "subject": "沈砚",
                    "kind": "resource",
                    "statement": f"剩余{11 - n}枚铜钱",
                    "evidence": row["content"],
                },
                row["content"],
            )
    context = domain.context(story["branch_id"], ids[5])
    assert all(f["position"] < 6 for f in context["facts"])
    domain.save(ids[2], 1, "第三天铜钱丢失。")
    assert not domain.context(story["branch_id"], ids[9])["facts"] or all(
        f["position"] < 3 for f in domain.context(story["branch_id"], ids[9])["facts"]
    )
    assert domain.chapter(ids[9])["stale"] == 1
    assert len(domain.db.all("SELECT * FROM facts WHERE stale=1")) == 8


def test_export_restoration_matches(domain, story):
    domain.save(story["chapter_id"], 0, "原创正文\n第二段😀")
    assert "原创正文\n第二段😀" in domain.export(story["branch_id"])


def test_reextracting_memory_preserves_superseded_facts(domain, story):
    domain.save(story["chapter_id"], 0, "沈砚只剩一枚铜钱。")
    for index in range(2):
        artifact = domain.stage(
            {
                "novel_id": story["id"],
                "branch_id": story["branch_id"],
                "chapter_id": story["chapter_id"],
                "base_revision": 1,
            },
            "memory",
            {
                "facts": [
                    {
                        "subject": "沈砚",
                        "kind": "resource",
                        "statement": f"第{index}次确认一枚铜钱",
                        "evidence": "只剩一枚铜钱",
                    }
                ]
            },
            {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
        )
        domain.accept(artifact["id"], f"fact-extraction-{index}")
    facts = domain.db.all("SELECT * FROM facts ORDER BY rowid")
    assert len(facts) == 2 and [f["stale"] for f in facts] == [1, 0]
    assert all(f["source_revision"] == 1 for f in facts)
