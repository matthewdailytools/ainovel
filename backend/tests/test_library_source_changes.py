"""Imported text and reference evidence remain snapshots when originals change."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from ainovel.backups import create_backup, verify_backup
import ainovel.backups as backups
from ainovel.budget import Budget
from ainovel.db import DomainError
from ainovel.library import Library
from ainovel.reference import session
from test_reference_flow import Model, SOURCE, TEXT, run, update


REPLACEMENT = "😀新资料改为铁路站台，旧渡口和掌柜均不在这份文本中。"


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def create_session(client, story, unit_id, text):
    response = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": unit_id,
            "start": 0,
            "end": utf16_length(text),
            "quote": text,
            "goal": "学习信息有限时推进交涉的写法",
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def imported_reference(client, domain, settings, story):
    library = Library(domain.db, settings.data_dir)
    book = library.import_file("同名资料.txt", SOURCE.encode("utf-8"))
    unit = domain.db.one("SELECT * FROM source_units WHERE book_id=?", (book["id"],))
    excerpt = library.excerpt(unit["id"], 0, utf16_length(SOURCE), SOURCE, "交通交涉", story["id"])
    reference = create_session(client, story, unit["id"], SOURCE)
    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    reference = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot="查看闸门外露磨损，记录未能取得，不动手修门。",
        fixed=["不修门"],
        application_plan="用有限信息改变下一步行动，不借参考事件作为本书事实。",
    )
    domain.save(story["chapter_id"], 0, TEXT)
    report, _ = run(client, settings, domain, reference["id"], "check", check_saved=True)
    return library, book, unit, excerpt, reference, report


@pytest.mark.parametrize("change", ["delete", "replace"])
def test_managed_original_changes_do_not_rebind_read_search_excerpts_or_reference_checks(
    client, domain, settings, story, change
):
    library, book, unit, excerpt, reference, report = imported_reference(client, domain, settings, story)
    old_report = deepcopy(report)
    old_source = deepcopy(reference["payload"]["source"])
    original = settings.data_dir / "sources" / book["filename"]
    if change == "delete":
        original.unlink()
    else:
        original.write_bytes(REPLACEMENT.encode("utf-8"))
        assert hashlib.sha256(original.read_bytes()).hexdigest() != book["file_hash"]

    assert domain.db.one("SELECT * FROM books WHERE id=?", (book["id"],)) == book
    assert domain.db.one("SELECT * FROM source_units WHERE id=?", (unit["id"],)) == unit
    read = client.get(f"/api/library/units/{unit['id']}")
    assert read.status_code == 200
    assert read.json()["content"] == SOURCE
    assert read.json()["file_hash"] == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    hits = client.get("/api/library/search", params={"q": "渡口", "book_id": book["id"]}).json()["hits"]
    assert len(hits) == 1 and hits[0]["id"] == unit["id"] and hits[0]["file_hash"] == book["file_hash"]
    assert not client.get("/api/library/search", params={"q": "铁路站台", "book_id": book["id"]}).json()[
        "hits"
    ]
    assert client.get(f"/api/library/books/{book['id']}/units").json()[0]["id"] == unit["id"]
    assert (
        next(row for row in client.get("/api/library/excerpts").json() if row["id"] == excerpt["id"])["quote"]
        == SOURCE
    )

    # The frozen quote still resolves; the new bytes cannot masquerade as that unit.
    fresh = create_session(client, story, unit["id"], SOURCE)
    assert fresh["payload"]["source"] == old_source
    bad = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": unit["id"],
            "start": 0,
            "end": utf16_length(REPLACEMENT),
            "quote": REPLACEMENT,
            "goal": "不能把新原件文字绑定到旧正文单元",
        },
    )
    assert bad.status_code == 409
    model = Model()
    new_report, _ = run(client, settings, domain, reference["id"], "check", model=model, check_saved=True)
    assert any(request["任务"].get("参考选段") == SOURCE for request in model.requests)
    assert all(REPLACEMENT not in json.dumps(request, ensure_ascii=False) for request in model.requests)
    assert session(domain, reference["id"])["payload"]["source"] == old_source
    assert domain.artifact(report["id"]) == old_report
    assert new_report["id"] != report["id"]
    assert new_report["basis"]["reference_session"]["payload"]["source"] == old_source
    assert domain.chapter(story["chapter_id"])["content"] == TEXT
    with Budget(settings).db() as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_reimport_same_title_new_bytes_creates_distinct_book_without_replacing_old_sources(
    client, domain, settings, story
):
    library, book, unit, excerpt, reference, report = imported_reference(client, domain, settings, story)
    old_report = deepcopy(report)
    old_session_source = deepcopy(reference["payload"]["source"])
    second = library.import_file("同名资料.txt", REPLACEMENT.encode("utf-8"))
    newer = domain.db.one("SELECT * FROM source_units WHERE book_id=?", (second["id"],))
    assert book["title"] == second["title"]
    assert book["id"] != second["id"] and book["filename"] != second["filename"]
    assert book["file_hash"] != second["file_hash"]
    assert newer["id"] != unit["id"]
    assert (library.directory / book["filename"]).read_bytes() == SOURCE.encode("utf-8")
    assert (library.directory / second["filename"]).read_bytes() == REPLACEMENT.encode("utf-8")
    assert client.get(f"/api/library/units/{unit['id']}").json()["content"] == SOURCE
    assert client.get(f"/api/library/units/{newer['id']}").json()["content"] == REPLACEMENT
    assert {row["book_id"] for row in library.search("铁路站台")["hits"]} == {second["id"]}
    assert {row["book_id"] for row in library.search("掌柜先问")["hits"]} == {book["id"]}
    assert domain.db.one("SELECT * FROM excerpts WHERE id=?", (excerpt["id"],))["unit_id"] == unit["id"]
    newer_reference = create_session(client, story, newer["id"], REPLACEMENT)
    assert newer_reference["payload"]["source"]["book_id"] == second["id"]
    assert newer_reference["payload"]["source"]["file_hash"] == second["file_hash"]
    assert session(domain, reference["id"])["payload"]["source"] == old_session_source
    assert domain.artifact(report["id"]) == old_report


def test_restore_rejects_replaced_source_even_with_self_consistent_zip_manifest(
    client, domain, settings, story
):
    library, book, *_ = imported_reference(client, domain, settings, story)
    original = library.directory / book["filename"]
    before = domain.db.all("SELECT * FROM books")
    backup = create_backup(settings, domain.db)
    with ZipFile(backup["path"]) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    source_name = "sources/" + book["filename"]
    entries[source_name] = REPLACEMENT.encode("utf-8")
    manifest = json.loads(entries["manifest.json"])
    manifest["files"][source_name] = hashlib.sha256(entries[source_name]).hexdigest()
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    changed = settings.data_dir / "self-consistent-manifest.zip"
    with ZipFile(changed, "w") as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
    # Even an internally consistent manifest cannot replace the imported book hash.
    with pytest.raises(DomainError) as failure:
        verify_backup(changed)
    assert failure.value.code == "backup_hash"
    assert domain.db.all("SELECT * FROM books") == before
    assert original.read_bytes() == SOURCE.encode("utf-8")
    assert domain.chapter(story["chapter_id"])["content"] == TEXT


def test_deleted_original_prevents_backup_creation_without_changing_imported_snapshot(
    client, domain, settings, story
):
    library, book, unit, *_ = imported_reference(client, domain, settings, story)
    (library.directory / book["filename"]).unlink()
    with pytest.raises(DomainError) as failure:
        create_backup(settings, domain.db)
    assert failure.value.code == "backup_source_missing"
    assert book["title"] in failure.value.message and "导入快照仍保留" in failure.value.message
    assert not list((settings.data_dir / "backups").glob("*.zip"))
    assert not list((settings.data_dir / "backups").glob("*.partial"))
    assert domain.db.one("SELECT * FROM source_units WHERE id=?", (unit["id"],)) == unit
    assert domain.chapter(story["chapter_id"])["content"] == TEXT


@pytest.mark.parametrize("change", ["delete", "replace"])
def test_backup_api_rejects_changed_original_before_publishing_zip(client, domain, settings, story, change):
    library, book, unit, *_ = imported_reference(client, domain, settings, story)
    original = library.directory / book["filename"]
    if change == "delete":
        original.unlink()
    else:
        original.write_bytes(REPLACEMENT.encode("utf-8"))
    before = domain.db.all("SELECT * FROM books")
    result = client.post("/api/backups")
    assert result.status_code == 409
    assert result.json()["code"] == (
        "backup_source_missing" if change == "delete" else "backup_source_changed"
    )
    assert book["title"] in result.json()["message"] and "导入快照仍保留" in result.json()["message"]
    assert not list((settings.data_dir / "backups").glob("*.zip"))
    assert not list((settings.data_dir / "backups").glob("*.partial"))
    assert domain.db.all("SELECT * FROM books") == before
    assert client.get(f"/api/library/units/{unit['id']}").json()["content"] == SOURCE
    assert domain.chapter(story["chapter_id"])["content"] == TEXT


def test_source_changes_during_bounded_read_cannot_publish_a_mismatched_archive(
    domain, settings, monkeypatch
):
    library = Library(domain.db, settings.data_dir)
    raw = b"A" * (backups.COPY_BLOCK * 3)
    book = library.import_file("流中变化.txt", raw)
    original = library.directory / book["filename"]
    changed = raw[: backups.COPY_BLOCK + 1] + b"B" + raw[backups.COPY_BLOCK + 2 :]
    native_open = Path.open
    sizes = []
    opened = []

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size):
            assert 0 < size <= backups.COPY_BLOCK
            sizes.append(size)
            value = self.stream.read(size)
            if len(sizes) == 1:
                original.write_bytes(changed)
            return value

    def open_source(path, mode="r", *args, **kwargs):
        stream = native_open(path, mode, *args, **kwargs)
        if path == original and mode == "rb":
            opened.append(path)
            return Reader(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_source)
    try:
        result = create_backup(settings, domain.db)
    except DomainError as failure:
        assert failure.code == "backup_source_changed"
        assert not list((settings.data_dir / "backups").glob("*.zip"))
    else:
        # Some systems keep the old opened stream coherent after a path write.
        # Publishing is safe only when the actual archived bytes remain original.
        with ZipFile(result["path"]) as archive:
            assert archive.read("sources/" + book["filename"]) == raw
        assert verify_backup(Path(result["path"]))["integrity"] == "ok"
    assert len(opened) == 1 and len(sizes) > 1
    with native_open(original, "rb") as current:
        assert current.read() == changed
    assert not list((settings.data_dir / "backups").glob("*.partial"))
    assert (
        domain.db.one("SELECT file_hash FROM books WHERE id=?", (book["id"],))["file_hash"]
        == hashlib.sha256(raw).hexdigest()
    )


def test_grown_original_stops_at_import_bound_without_publishing(domain, settings, monkeypatch):
    library = Library(domain.db, settings.data_dir)
    book = library.import_file("变大的原件.txt", b"snapshot")
    monkeypatch.setattr(backups, "MAX_FILE", backups.COPY_BLOCK * 2)
    (library.directory / book["filename"]).write_bytes(b"X" * (backups.MAX_FILE + 1))
    with pytest.raises(DomainError) as failure:
        create_backup(settings, domain.db)
    assert failure.value.code == "backup_source_changed"
    assert not list((settings.data_dir / "backups").glob("*.zip"))
    assert not list((settings.data_dir / "backups").glob("*.partial"))


def test_archive_io_failure_is_not_mislabeled_as_source_change_and_preserves_usage(
    domain, settings, monkeypatch
):
    library = Library(domain.db, settings.data_dir)
    book = library.import_file("保留资料.txt", SOURCE.encode("utf-8"))
    budget = Budget(settings)
    budget.reserve(settings.providers["openai_1"], "isolated-unknown", 2, 1)
    before = budget.report()
    native_open = ZipFile.open

    def disk_failure(archive, name, mode="r", *args, **kwargs):
        if mode == "w" and name.startswith("sources/"):
            raise OSError("simulated archive storage failure")
        return native_open(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "open", disk_failure)
    with pytest.raises(OSError, match="archive storage failure"):
        create_backup(settings, domain.db)
    assert budget.report() == before
    assert (library.directory / book["filename"]).read_bytes() == SOURCE.encode("utf-8")
    assert not list((settings.data_dir / "backups").glob("*.zip"))
    assert not list((settings.data_dir / "backups").glob("*.partial"))


def test_preexisting_partial_is_not_deleted_when_exclusive_creation_fails(domain, settings, monkeypatch):
    Library(domain.db, settings.data_dir)
    folder = settings.data_dir / "backups"
    folder.mkdir()
    sentinel = folder / ("a" * 32 + ".partial")
    sentinel.write_bytes(b"previous unfinished backup remains")
    monkeypatch.setattr(backups, "uid", lambda: "a" * 32)
    with pytest.raises(FileExistsError):
        create_backup(settings, domain.db)
    assert sentinel.read_bytes() == b"previous unfinished backup remains"
    assert not list(folder.glob("*.zip"))


@pytest.mark.parametrize(
    "route",
    [
        "/api/library/books",
        "/api/library/search",
        "/api/library/books/{book_id}/units",
        "/api/library/units/{unit_id}",
    ],
)
def test_existing_api_contract_describes_import_snapshot_without_new_response_fields(client, route):
    description = client.get("/openapi.json").json()["paths"][route]["get"]["description"]
    assert "导入" in description
    assert "快照" in description or "file_hash" in description
