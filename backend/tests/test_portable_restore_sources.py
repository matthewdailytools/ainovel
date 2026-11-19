from dataclasses import replace
from hashlib import sha256
import json

from fastapi.testclient import TestClient
import pytest

from ainovel.api import create_app
from ainovel.db import DomainError, uid
from ainovel import work_snapshot
from ainovel.manuscript_exchange import export_manuscript
from ainovel.portable_checkpoint import decode_checkpoint
from ainovel.portable_restore_sources import PortableRestoreSources, RestoreSourceCatalog
from ainovel.story_sync import story_version_id
from ainovel.work_snapshot import _read_snapshot, checkpoint_tx
from test_outline_workspace import chapter_plan, setting_confirmation
from test_project_brief import BRIEF, IDEA
from test_story_sync import record, visible


@pytest.fixture
def archived_sources(client, domain, story):
    branch, first = story["branch_id"], story["chapter_id"]
    second = domain.create_chapter(branch, "第二卷章")
    chapter = domain.save(first, 0, "门内📯响了。")
    domain.save(second["id"], 0, "师姐回到渡口。")
    proof = {"chapter_id": first, "revision": chapter["revision"], "start": 2, "end": 4, "quote": "📯"}
    with domain.db.connect(True) as tx:
        person = record(tx, branch, {"kind": "character", "title": "师姐"})
        event = record(
            tx,
            branch,
            {
                "kind": "event",
                "title": "听到号角",
                "status": "actual",
                "participants": [person],
                "sources": [proof],
            },
        )
        promise = record(
            tx,
            branch,
            {
                "kind": "promise",
                "title": "找到声源",
                "stages": [
                    {"status": "planned"},
                    {"status": "resolved", "event_id": event, "sources": [proof]},
                ],
            },
        )
        domain.add_fact_tx(
            tx,
            branch,
            first,
            chapter["revision"],
            {"subject": "号角", "kind": "event", "statement": "号角在门内响起。", "evidence": "📯"},
            chapter["content"],
        )
    old_person = visible(domain, branch, person)
    with domain.db.connect(True) as tx:
        record(tx, branch, {**old_person["payload"], "aliases": ["渡口守门人"]}, existing=old_person)

    route = f"/api/branches/{branch}"
    response = client.put(
        route + "/conception/project-brief",
        json={"base_revision": 0, "idempotency_key": uid(), "idea": IDEA, "brief": BRIEF},
    )
    assert response.is_success, response.text
    confirmation = setting_confirmation(
        client,
        branch,
        [
            {
                "id": "source-notes",
                "title": "参考正文",
                "items": [{"id": "source-note", "title": "音色记录", "detail": "记录号角音色。"}],
            }
        ],
    )
    plan = {
        "summary": "追寻号角声。",
        "volumes": [
            {
                "id": f"volume-{number}",
                "title": f"第 {number} 卷",
                "summary": "查清过去。",
                "units": [
                    {
                        "id": f"unit-{number}",
                        "title": "寻踪",
                        "summary": "查找线索。",
                        "chapters": [chapter_plan(identifier, "找到师姐")],
                    }
                ],
            }
            for number, identifier in enumerate((first, second["id"]), 1)
        ],
        "promises": [
            {
                "promise_id": promise,
                "plant_chapter_id": first,
                "opens_chapter_id": second["id"],
                "closes_chapter_id": second["id"],
                "fulfillment": "author_required",
            }
        ],
    }
    saved = client.put(
        route + "/outline-workspace",
        json={"base_revision": 0, "confirmation_id": confirmation, "plan": plan},
    )
    assert saved.is_success, saved.text
    confirmed = client.post(
        route + "/outline-workspace/confirm",
        json={"base_revision": saved.json()["draft"]["revision"], "acknowledge_required_promises": True},
    )
    assert confirmed.is_success, confirmed.text
    response = client.put(
        route + "/workflow-inspections/length-targets",
        json={"scope": "volume", "scope_id": "volume-1", "targets": {"min_chars": 3}},
    )
    assert response.is_success, response.text
    with domain.db.connect(True) as tx:
        identifier = checkpoint_tx(tx, branch, "卷末恢复点", scope="volume", scope_id="volume-1")
        checkpoint_tx(tx, branch, "整书同一恢复点", scope="book")
        native = _read_snapshot(tx, branch, identifier)["manifest"]

    edited = visible(domain, branch, event)
    with domain.db.connect(True) as tx:
        record(tx, branch, {**edited["payload"], "title": "较晚的改写"}, existing=edited)
    domain.save(first, chapter["revision"], "新修订改成沉默不语。")
    package = export_manuscript(domain, branch)
    entry = next(row for row in package.snapshot_history.entries if row.id == identifier)
    checkpoint = decode_checkpoint(entry, branch)
    catalog = RestoreSourceCatalog(package)
    return catalog, checkpoint, native, person, event, promise


def test_exact_restore_sources_use_old_formal_adoption_graph_and_memos(archived_sources):
    catalog, checkpoint, native, person, event, promise = archived_sources
    sources = catalog.capture(native)
    selected = catalog.resolve(checkpoint, sources)

    assert (selected.project.id, selected.settings.id, selected.outline.id) == (
        checkpoint.shared.project_id,
        checkpoint.shared.settings_id,
        checkpoint.shared.outline_id,
    )
    assert selected.outline.plan.promises[0].promise_id == promise
    assert selected.outline.plan.promises[0].fulfillment == "author_required"
    assert [row.native.revision for row in selected.stories[person]] == [0, 1]
    assert selected.stories[event][-1].native.dependencies == {person: 0}
    assert selected.stories[event][-1].payload["title"] == "听到号角"
    assert selected.stories[promise][-1].payload["stages"][-1]["sources"][0]["quote"] == "📯"
    assert selected.facts[0]["evidence"] == "📯"
    assert selected.facts[0]["evidence_start"] == 2
    assert selected.facts[0]["evidence_end"] == 4
    assert sources.facts[0].evidence.quote_sha256 == sha256("📯".encode()).hexdigest()
    assert "📯" not in sources.model_dump_json()
    assert sources.length_targets[0].scope_id == "volume-1"
    assert sources.length_targets[0].targets.min_chars == 3


def test_restore_sources_reject_missing_parent_wrong_quote_and_wrong_volume(archived_sources):
    catalog, checkpoint, native, person, _, _ = archived_sources
    sources = catalog.capture(native)
    missing = sources.model_copy(
        update={"story_heads": {key: head for key, head in sources.story_heads.items() if key != person}}
    )
    with pytest.raises(DomainError, match="旧依赖"):
        catalog.resolve(checkpoint, missing)

    source = sources.facts[0].evidence.model_copy(update={"start": 0, "end": 2})
    wrong_quote = sources.model_copy(
        update={"facts": [sources.facts[0].model_copy(update={"evidence": source})]}
    )
    with pytest.raises(DomainError, match="原文摘要"):
        catalog.resolve(checkpoint, wrong_quote)

    misplaced = checkpoint.model_copy(update={"scope_id": "volume-2"})
    with pytest.raises(DomainError, match="范围和旧目录"):
        catalog.resolve(misplaced, sources)


def test_restore_source_capture_cannot_drop_a_checkpoint_dependency(archived_sources):
    catalog, _, native, _, _, _ = archived_sources
    with pytest.raises(DomainError, match="故事依赖与历史修订不一致"):
        catalog.capture({**native, "dependencies": []})


def test_restore_sources_reject_effective_cycle_between_exact_historical_edges(archived_sources):
    catalog, checkpoint, native, person, event, _ = archived_sources
    sources = catalog.capture(native)
    head = sources.story_heads[person]
    event_id = sources.story_heads[event].version_id
    original = catalog.stories[head.version_id]
    changed = original.model_copy(
        update={
            "dependencies": {event: event_id},
            "record": original.record.model_copy(update={"depends_on": [event]}),
        }
    )
    identifier = story_version_id(checkpoint.branch_id, changed)
    catalog.stories[identifier] = changed.model_copy(update={"id": identifier})
    row = catalog.story_rows[person, head.revision].model_copy(
        update={"version_id": identifier, "dependencies": {event: 0}}
    )
    catalog.story_rows[person, head.revision] = row
    catalog.story_history[person][-1] = row
    heads = {**sources.story_heads, person: head.model_copy(update={"version_id": identifier})}
    with pytest.raises(DomainError) as raised:
        catalog.resolve(checkpoint, sources.model_copy(update={"story_heads": heads}))
    assert "构成循环" in raised.value.__cause__.message


def test_unsupported_projection_preserves_readable_archives(monkeypatch, domain, archived_sources):
    _, checkpoint, _, _, _, _ = archived_sources

    def invalid_sources(*_):
        return PortableRestoreSources.model_validate({})

    with monkeypatch.context() as patch:
        patch.setattr(RestoreSourceCatalog, "capture", invalid_sources)
        package = export_manuscript(domain, checkpoint.branch_id)
    entry = next(row for row in package.snapshot_history.entries if row.id == checkpoint.id)
    projected = decode_checkpoint(entry, checkpoint.branch_id)
    assert projected.restore_sources is None
    assert "restore_sources" not in json.loads(entry.body)
    assert projected.chapters == checkpoint.chapters
    with monkeypatch.context() as patch:
        patch.setattr(work_snapshot, "PORTABLE_CHECKPOINT_MAX_BODY", len(entry.body))
        bounded = export_manuscript(domain, checkpoint.branch_id)
        assert next(row for row in bounded.snapshot_history.entries if row.id == checkpoint.id) == entry


def test_setting_proof_rebind_requires_native_revision_in_checkpoint_prefix(archived_sources):
    catalog, checkpoint, native, _, _, _ = archived_sources
    sources = catalog.capture(native)
    evidence = sources.facts[0].evidence
    chosen = catalog.settings[checkpoint.shared.settings_id]
    item_id = "source-note"
    origin = chosen.origins[item_id]
    catalog.settings[chosen.id] = chosen.model_copy(
        update={"origins": {**chosen.origins, item_id: origin.model_copy(update={"evidence": evidence})}}
    )
    assert catalog.resolve(checkpoint, sources).settings.origins[item_id].evidence == evidence
    newer = catalog.native_revision_numbers[evidence.chapter_id, 2]
    latest_proof = evidence.model_copy(
        update={
            "revision_id": newer.id,
            "start": 0,
            "end": 1,
            "quote_sha256": sha256(newer.content[0].encode()).hexdigest(),
        }
    )
    catalog.settings[chosen.id] = chosen.model_copy(
        update={"origins": {**chosen.origins, item_id: origin.model_copy(update={"evidence": latest_proof})}}
    )
    with pytest.raises(DomainError, match="设定引文缺少确切的本机原稿修订"):
        catalog.resolve(checkpoint, sources)


def test_portable_fork_uses_import_origin_and_preserves_exact_story_prefix(
    client, archived_sources, settings, tmp_path, monkeypatch
):
    _, checkpoint, _, person, event, promise = archived_sources
    branch = checkpoint.branch_id
    route = f"/api/branches/{branch}/portable-checkpoints"
    assert client.get(f"{route}/{checkpoint.id}/fork-preview").status_code == 404
    supplied = client.get(f"/api/branches/{branch}/manuscript-package").json()
    book = next(
        row
        for row in supplied["snapshot_history"]["entries"]
        if json.loads(row["body"])["label"] == "整书同一恢复点"
    )
    legacy = next(row for row in supplied["snapshot_history"]["entries"] if row["id"] == checkpoint.id)
    recorded = json.loads(legacy["body"])
    assert "restore_sources" in recorded
    recorded["id"] = "legacy-checkpoint"
    del recorded["restore_sources"]
    body = json.dumps(recorded, ensure_ascii=False)
    supplied["snapshot_history"]["entries"].append(
        {"id": recorded["id"], "body": body, "sha256": sha256(body.encode()).hexdigest()}
    )
    config = replace(settings, data_dir=tmp_path / "independent-restore")
    with TestClient(create_app(config)) as other:
        other.headers["X-Ainovel-CSRF"] = other.get("/api/session").json()["csrf"]
        imported = other.post("/api/manuscript-packages/import", json=supplied)
        assert imported.status_code == 201, imported.text
        legacy_route = route + "/legacy-checkpoint"
        assert other.get(legacy_route).is_success
        assert other.get(legacy_route + "/fork-preview").status_code == 409
        base = route + "/" + checkpoint.id
        preview = other.get(base + "/fork-preview")
        assert preview.is_success, preview.text
        assert preview.json()["chapter_count"] == 2
        assert preview.json()["story_record_count"] == 3
        assert preview.json()["outline"] == "confirmed"
        request = {
            "basis_sha256": preview.json()["basis_sha256"],
            "branch_name": "从原卷末另存",
            "idempotency_key": "portable-fixture-request",
        }
        stale = other.post(base + "/fork", json={**request, "basis_sha256": "0" * 64})
        assert stale.status_code == 409

        def reject_after_graph(*args, **kwargs):
            raise DomainError("fixture_failure", "模拟事务末段写入失败。", 409)

        with monkeypatch.context() as patch:
            patch.setattr(work_snapshot, "_portable_fork_outline", reject_after_graph)
            refused = other.post(base + "/fork", json=request)
            assert refused.status_code == 409, refused.text

        first = other.post(base + "/fork", json=request)
        assert first.status_code == 201, first.text
        assert other.post(base + "/fork", json=request).json() == first.json()
        assert other.post(base + "/fork", json={**request, "branch_name": "改名"}).status_code == 409

        fork = first.json()["branch_id"]
        assert fork != branch
        fresh = other.get(f"/api/branches/{fork}/manuscript-package")
        assert fresh.is_success, fresh.text
        result = fresh.json()
        assert result["project_history"]["adopted_revision_id"] == checkpoint.shared.project_id
        assert result["settings_history"]["adopted_revision_id"]
        heads = {
            record["title"]: record
            for record in (
                entry["record"] | {"id": entry["record_id"], "version_id": entry["id"]}
                for entry in result["story_history"]["versions"]
                if entry["id"] in result["story_history"]["adopted_heads"].values()
            )
        }
        assert set(heads) == {"师姐", "听到号角", "找到声源"}
        assert not {person, event, promise} & set(result["story_history"]["adopted_heads"])
        adopted_event = result["story_history"]["native_history"][heads["听到号角"]["id"]][-1]
        person_id = heads["师姐"]["id"]
        assert adopted_event["dependencies"] == {person_id: 0}
        assert len(result["story_history"]["native_history"][person_id]) == 2
        old_chapter = next(row for row in result["chapters"] if row["position"] == 1)
        assert old_chapter["revision"] == 1
        assert {row["content"] for row in result["revisions"] if row["chapter_id"] == old_chapter["id"]} == {
            "",
            "门内📯响了。",
        }
        held = other.get(f"/api/branches/{branch}/chapters").json()
        assert other.get(f"/api/chapters/{held[0]['id']}").json()["content"] == "新修订改成沉默不语。"

        book_route = f"{route}/{book['id']}"
        preview = other.get(book_route + "/fork-preview")
        assert preview.is_success, preview.text
        assert preview.json()["chapter_count"] == 2
        assert preview.json()["story_record_count"] == 3
        saved_book = other.post(
            book_route + "/fork",
            json={
                "basis_sha256": preview.json()["basis_sha256"],
                "branch_name": "从原整书另存",
                "idempotency_key": "portable-book-fixture-request",
            },
        )
        assert saved_book.status_code == 201, saved_book.text
        restored_book = other.get(f"/api/branches/{saved_book.json()['branch_id']}/chapters").json()
        assert [other.get(f"/api/chapters/{entry['id']}").json()["content"] for entry in restored_book] == [
            "门内📯响了。",
            "师姐回到渡口。",
        ]
