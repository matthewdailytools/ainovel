import asyncio
from io import BytesIO
import json
import sqlite3
from zipfile import ZipFile

import pytest

from ainovel.backups import create_backup, restore_backup, verify_backup
from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel.library import Library, parse_epub, public_url
from ainovel.runtime import alive, register, unregister


def epub(broken=False, malicious=False):
    memory = BytesIO()
    with ZipFile(memory, "w") as file:
        file.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>',
        )
        file.writestr(
            "OPS/package.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="one.xhtml"/><item id="two" href="two.xhtml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>',
        )
        file.writestr(
            "OPS/one.xhtml", "<html><body><script>恶意指令</script><p>😀渡船经过青石渡口。</p></body></html>"
        )
        if not broken:
            file.writestr("OPS/two.xhtml", "<p>原文第二节。</p>")
        if malicious:
            file.writestr("../outside.txt", "unsafe")
    return memory.getvalue()


@pytest.mark.parametrize("query", ["渡", "渡船", "青石渡口", "😀渡船"])
def test_chinese_exact_search_and_utf16_evidence(domain, settings, query):
    library = Library(domain.db, settings.data_dir)
    book = library.import_file("测试.epub", epub())
    result = library.search(query, book_id=book["id"])
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    excerpt = library.excerpt(hit["id"], hit["start"], hit["end"], query, "交通资料")
    assert excerpt["quote"] == query
    assert (
        "恶意指令"
        not in domain.db.one("SELECT content FROM source_units WHERE id=?", (hit["id"],))["content"]
    )
    assert not library.search("渡口青石")["hits"]


def test_partial_epub_and_bad_encoding_are_honest(domain, settings):
    library = Library(domain.db, settings.data_dir)
    partial = library.import_file("部分.epub", epub(broken=True))
    assert partial["status"] == "partial"
    assert json.loads(partial["coverage"])["parsed"] == 1
    assert json.loads(partial["coverage"])["expected"] == 2
    bad = library.import_file("编码.txt", "渡船".encode("gb18030"))
    assert bad["status"] == "parse_failed"
    assert (library.directory / bad["filename"]).is_file()
    assert library.import_file("正确.txt", "渡船".encode("gb18030"), encoding="gb18030")["status"] == "ready"


def test_epub_paths_and_network_allowlist():
    with pytest.raises(DomainError):
        parse_epub(epub(malicious=True))
    for url in [
        "http://127.0.0.1",
        "https://example.com",
        "https://zh.wikisource.org:8080",
        "https://user@zh.wikisource.org",
    ]:
        with pytest.raises(DomainError):
            asyncio.run(public_url(url))


def test_phrase_crossing_text_unit_boundary(domain, settings):
    library = Library(domain.db, settings.data_dir)
    library.import_file("跨单元.txt", ("甲" * 11999 + "青石渡口" + "乙" * 1000).encode())
    assert library.search("青石渡口")["hits"]


def test_fts_unavailable_keeps_search(domain, settings):
    domain.db.fts = False
    library = Library(domain.db, settings.data_dir)
    library.import_file("资料.md", "渡船与青石渡口".encode())
    assert len(library.search("青石渡口")["hits"]) == 1


def test_backup_actual_restore_and_ledger_not_rolled_back(domain, story, settings):
    library = Library(domain.db, settings.data_dir)
    library.import_file("资料.txt", "渡船".encode())
    domain.save(story["chapter_id"], 0, "原稿😀")
    budget = Budget(settings)
    snapshot = create_backup(settings, domain.db)
    assert verify_backup(settings.data_dir / "backups" / (snapshot["id"] + ".zip"))["integrity"] == "ok"
    domain.save(story["chapter_id"], 1, "新稿")
    budget.reserve(settings.providers["openai_1"], "after-backup", 100, 100)
    held = budget.report()[0]["reserved_unknown"]
    restore_backup(settings, domain.db, settings.data_dir / "backups" / (snapshot["id"] + ".zip"))
    assert domain.chapter(story["chapter_id"])["content"] == "原稿😀"
    assert Budget(settings).report()[0]["reserved_unknown"] == held > 0
    with sqlite3.connect(domain.db.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_formal_restore_refuses_live_service(domain, story, settings):
    import os

    Library(domain.db, settings.data_dir)
    domain.save(story["chapter_id"], 0, "当前稿保持不动")
    backup = create_backup(settings, domain.db)
    token = register(domain.db, "api")
    assert alive(os.getpid())
    with pytest.raises(DomainError, match="停止本机"):
        restore_backup(settings, domain.db, settings.data_dir / "backups" / (backup["id"] + ".zip"))
    assert domain.chapter(story["chapter_id"])["content"] == "当前稿保持不动"
    unregister(domain.db, token)


def test_interrupted_download_does_not_stay_running(settings, domain, monkeypatch):
    from fastapi.testclient import TestClient
    from ainovel.api import create_app

    Library(domain.db, settings.data_dir)
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO library_jobs(id,title,status,created,format) VALUES('old','测试','downloading',0,'epub')"
        )
    with TestClient(create_app(settings)) as client:
        jobs = client.get("/api/library/jobs").json()
        assert jobs[0]["status"] == "failed" and jobs[0]["error"]

    library = Library(domain.db, settings.data_dir)

    async def stopped(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(library, "fetch", stopped)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE library_jobs SET status='queued' WHERE id='old'")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(library.acquire("old"))
    assert domain.db.one("SELECT status FROM library_jobs WHERE id='old'")["status"] == "failed"


def test_evidence_bundle_matches_restored_manuscript_without_spending(settings, domain, story):
    import hashlib
    from pathlib import Path
    from ainovel.evidence_export import export_evidence

    domain.save(story["chapter_id"], 0, "只导出保存的原创稿😀")
    task = domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "account": "openai_1",
            "action": "writing.advise",
        },
    )
    result = export_evidence(settings, [task["id"]])
    folder = Path(result["folder"])
    evidence = json.loads((folder / "evidence.json").read_text("utf-8"))
    manuscript = (folder / (task["id"] + ".md")).read_bytes()
    assert evidence["tasks"][0]["manuscript_sha256"] == hashlib.sha256(manuscript).hexdigest()
    assert evidence["tasks"][0]["task"]["status"] == "queued"
    assert evidence["restore"]["integrity"] == "ok"
    assert "test-not-real" not in (folder / "evidence.json").read_text("utf-8")
    assert not (settings.data_dir / "usage.sqlite").exists()


def test_book_evidence_includes_related_manual_module_usage(settings, domain, story):
    from pathlib import Path
    from ainovel.evidence_export import export_evidence

    task = domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "account": "openai_1",
            "action": "writing.advise",
        },
    )
    manual = domain.queue(
        "module",
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "account": "openai_1",
            "action": "review.chapter",
        },
    )
    ledger = Budget(settings)
    call = ledger.reserve(settings.providers["openai_1"], manual["id"], 2, 1)
    ledger.settle(call, {"input": 2, "output": 1})
    result = export_evidence(settings, [task["id"]])
    entry = json.loads((Path(result["folder"]) / "evidence.json").read_text("utf-8"))["tasks"][0]
    assert len(entry["related_tasks"]) == 2
    assert entry["calls"][0]["task_id"] == manual["id"]
    assert entry["cost_by_currency"]["USD"]["settled"] == 0.000012


def test_readable_report_keeps_stale_review_and_unaccepted_candidate_separate(settings, domain, story):
    from pathlib import Path
    from ainovel.db import dumps
    from ainovel.evidence_export import export_evidence

    domain.save(story["chapter_id"], 0, "当前正文保留。")
    task = domain.queue(
        "module", {"novel_id": story["id"], "branch_id": story["branch_id"], "account": "openai_1"}
    )
    review = {
        "summary": "缺少交叉核对。",
        "checks": [],
        "findings": [
            {
                "chapter": 1,
                "severity": "blocker",
                "quote": "候选仍待查。",
                "reason": "情节未落实。",
                "repair": "补齐核对过程。",
            }
        ],
    }
    candidate = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 1,
        },
        "chapter",
        {"text": "候选仍待查。", "review": review},
        {},
        task["id"],
    )
    state = {
        "waiting_artifact": candidate["id"],
        "completion": {
            "closed": True,
            "summary": "这是旧版本的结论。",
            "findings": [],
            "promises": [{"condition": "核对完成", "result": "pass", "evidence": "旧稿"}],
            "source_versions": [{"id": story["chapter_id"], "revision": 0}],
        },
    }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=?,status='needs_review' WHERE id=?", (dumps(state), task["id"]))
    folder = Path(export_evidence(settings, [task["id"]])["folder"])
    manuscript = (folder / (task["id"] + ".md")).read_text("utf-8")
    report = (folder / (task["id"] + "-修订报告.md")).read_text("utf-8")
    pending = (folder / (task["id"] + "-待审候选.md")).read_text("utf-8")
    assert "当前正文保留。" in manuscript and "候选仍待查。" not in manuscript
    assert "历史来源" in report and "三层正式验收：False" in report
    assert "尚未采纳" in pending and "候选仍待查。" in pending
    assert "情节未落实。" in report and "补齐核对过程。" in report
