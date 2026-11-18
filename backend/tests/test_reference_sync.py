from contextlib import closing
import sqlite3

import pytest

from ainovel import reference_sync as sync
from ainovel.db import Database, DomainError
from ainovel.domain import Domain
from ainovel.library import Library
from ainovel.manuscript_exchange import ManuscriptPackage, export_manuscript, import_manuscript
from ainovel.reference import EditReference, edit_reference, session
from ainovel.reference_portability import ReferenceCardHead
from test_reference_flow import SOURCE, reference as reference_fixture, update
from test_settings_sync import receipt

reference = reference_fixture


def install(domain):
    with domain.db.connect() as tx:
        tx.executescript(sync.SCHEMA)


def card(excerpt, **extra):
    return {
        "id": "limited-progress",
        "observation": "先得到一点线索",
        "interpretation": "部分信息保留推进动力",
        "technique": " 以半步收获构成下一次选择 ",
        "conditions": "角色有条件继续查证",
        "exceptions": "替换原地理、角色和具体物件",
        "evidence": excerpt,
        **extra,
    }


def archive(domain, branch, **args):
    with domain.db.connect() as tx:
        return sync.export_reference_archive(tx, branch, **args)


def test_saved_card_archive_preserves_lineage_but_includes_raw_excerpt_only_when_selected(
    client, domain, story, reference, tmp_path
):
    branch, identifier = story["branch_id"], reference["id"]
    install(domain)
    assert archive(domain, branch) is None
    raw = "😀渡口"
    tentative = update(client, domain, reference, cards=[card(raw, exceptions="")])
    first = archive(domain, branch)
    assert first.versions[0].assessment == "incomplete"
    assert first.versions[0].missing_fields == ["exceptions"]
    assert first.versions[0].card is None and first.versions[0].citation is None
    assert raw not in first.model_dump_json()
    saved = update(client, domain, tentative, cards=[card(raw)])
    modified = update(client, domain, saved, cards=[card("半幅图", technique="把未得的证据留给下次尝试")])
    update(client, domain, modified, cards=[])
    exported = archive(domain, branch)
    assert [item.assessment for item in exported.versions] == ["incomplete", "ready", "ready", "archived"]
    for parent, child in zip(exported.versions, exported.versions[1:]):
        assert child.parent_id == parent.id
    first_ready = exported.versions[1]
    assert (first_ready.citation.evidence_start, first_ready.citation.evidence_end) == (0, 4)
    assert first_ready.card.technique == card(raw)["technique"]
    assert exported.adopted_heads[0].revision_id == exported.versions[-1].id
    assert "excerpts" not in exported.model_dump() and raw not in exported.model_dump_json()
    assert SOURCE not in exported.model_dump_json()

    explicit = archive(domain, branch, include_evidence_for=[first_ready.id])
    assert explicit.excerpts == {first_ready.id: raw}
    assert "半幅图" not in explicit.model_dump_json()
    repaired = Domain(Database(tmp_path / "card-archive.sqlite"))
    blank_archive = export_manuscript(domain, branch).model_copy(update={"reference_history": None})
    import_manuscript(repaired, blank_archive)
    install(repaired)
    with repaired.db.connect(True) as tx:
        corrupted = explicit.model_copy(update={"excerpts": {first_ready.id: "改过的原文"}})
        with pytest.raises(DomainError, match="来源摘要不一致"):
            sync.import_reference_archive(tx, branch, corrupted)
        assert not tx.execute("SELECT 1 FROM portable_reference_cards").fetchone()
        sync.import_reference_archive(tx, branch, explicit)
        assert not tx.execute("SELECT 1 FROM reference_sessions").fetchone()
        assert not tx.execute("SELECT 1 FROM books").fetchone()
        assert sync.reference_excerpt(tx, branch, first_ready) == (raw, "included_excerpt")
        assert sync.reference_excerpt(tx, branch, exported.versions[2]) == (None, "metadata_only")
        assert (
            sync.read_reference_head(tx, branch, identifier, first_ready.card_id) == exported.versions[-1].id
        )
    assert archive(repaired, branch) == exported


def test_two_devices_same_numeric_revision_requires_exact_version_address(
    client, domain, story, reference, tmp_path
):
    branch, identifier = story["branch_id"], reference["id"]
    install(domain)
    first = update(client, domain, reference, cards=[card("半幅图")])
    other_path = tmp_path / "other-card.sqlite"
    with domain.db.connect() as source, closing(sqlite3.connect(other_path)) as destination:
        source.backup(destination)
    other = Domain(Database(other_path))
    left = update(client, domain, first, cards=[card("半幅图", technique="左端保留下一步疑问")])
    before = session(other, identifier)["payload"]
    right = edit_reference(
        other,
        identifier,
        EditReference(
            base_revision=first["revision"],
            goal=before["goal"],
            cards=[card("半幅图", technique="右端引入可继续的动作")],
            branch_id=branch,
            chapter_id=story["chapter_id"],
        ),
    )
    assert left["revision"] == right["revision"] == 2
    left_version = archive(domain, branch).versions[-1]
    right_version = archive(other, branch).versions[-1]
    assert left_version.id != right_version.id and left_version.parent_id == right_version.parent_id
    wrapped = sync.version_object(branch, left_version)
    source_id = f"reference-card-version:{left_version.id}"
    assert sync.parse_reference_object(branch, source_id, wrapped.model_dump(), False) == wrapped
    tampered = wrapped.model_dump()
    tampered["version"]["card"]["technique"] = "篡改内容"
    with pytest.raises(DomainError, match="编号与内容不一致"):
        sync.parse_reference_object(branch, source_id, tampered, False)
    with other.db.connect(True) as tx:
        sync.observe_reference_version(tx, branch, left_version, receipt("left-card-source"))
        with pytest.raises(DomainError, match="准确的来源版本"):
            sync.reference_card_at(tx, branch, identifier, left_version.card_id, 2)
        for exact in (left_version, right_version):
            assert (
                sync.reference_card_at(tx, branch, identifier, exact.card_id, 2, version_id=exact.id) == exact
            )
        sync.apply_reference_head(
            tx,
            branch,
            ReferenceCardHead(
                session_id=identifier, card_id=left_version.card_id, revision_id=left_version.id
            ),
        )
        key = sync.reference_head_object_id(branch, identifier, left_version.card_id)
        assert sync.read_reference_head_object(tx, branch, key).revision_id == left_version.id
        assert sync.has_reference_source(tx, branch, left_version.id)
    assert archive(other, branch).source_devices[left_version.id] == "writing-laptop"
    assert archive(other, branch).versions[-1].revision == 2

    # A new saved local revision can advance without deleting the reviewed cloud alternative.
    edit_reference(
        other,
        identifier,
        EditReference(
            base_revision=2,
            goal=before["goal"],
            cards=[card("半幅图", technique="本机后续修改")],
            branch_id=branch,
            chapter_id=story["chapter_id"],
        ),
    )
    final = archive(other, branch)
    assert final.adopted_heads[0].revision_id != left_version.id
    assert {left_version.id, right_version.id} <= {version.id for version in final.versions}


def test_ambiguous_quote_keeps_selection_and_branch_transfer_archives_old_head(
    client, domain, settings, story
):
    install(domain)
    branch = story["branch_id"]
    full = "😀南门已关。再回南门，听见铃响。"
    book = Library(domain.db, settings.data_dir).import_file("合成材料.txt", full.encode("utf-8"))
    unit = domain.db.one("SELECT id FROM source_units WHERE book_id=?", (book["id"],))
    created = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": unit["id"],
            "start": 0,
            "end": len(full.encode("utf-16-le")) // 2,
            "quote": full,
            "goal": "检查关键词重复时的证据定位",
            "branch_id": branch,
            "chapter_id": story["chapter_id"],
        },
    )
    assert created.status_code == 201, created.text
    original = update(client, domain, created.json(), cards=[card("南门")])
    version = archive(domain, branch).versions[0]
    assert version.citation.evidence_start is None and version.citation.evidence_end is None
    assert version.citation.selection_end == len(full.encode("utf-16-le")) // 2
    assert archive(domain, branch, include_evidence_for=[version.id]).excerpts == {version.id: "南门"}

    another = domain.create_novel("另一作品")["branch_id"]
    update(client, domain, original, branch_id=another, chapter_id=None, cards=[card("南门")])
    old = archive(domain, branch)
    new = archive(domain, another)
    assert [(item.assessment, item.revision) for item in old.versions] == [("ready", 1), ("archived", 2)]
    assert [(item.assessment, item.revision) for item in new.versions] == [("ready", 2)]
    assert new.versions[0].parent_id is None
    with pytest.raises(DomainError, match="不属于当前作品"):
        archive(domain, another, include_evidence_for=[version.id])


def test_desktop_manuscript_package_exports_reference_metadata_and_requires_explicit_proof_selection(
    client, domain, story, reference, tmp_path
):
    branch = story["branch_id"]
    cards = [card("😀渡口"), card("半幅图", id="second-card", technique="保留另一张卡的选择")]
    update(client, domain, reference, cards=cards)
    location = f"/api/branches/{branch}/manuscript-package"
    response = client.get(location)
    assert response.status_code == 200, response.text
    default = response.json()
    history = default["reference_history"]
    assert "excerpts" not in history
    assert "😀渡口" not in response.text and "半幅图" not in response.text and SOURCE not in response.text
    assert len(history["adopted_heads"]) == 2
    head = next(item for item in history["adopted_heads"] if item["card_id"] == cards[0]["id"])
    version_id = head["revision_id"]
    catalogue = client.get(
        f"/api/branches/{branch}/reference-cards", params={"session_id": head["session_id"]}
    )
    assert catalogue.status_code == 200, catalogue.text
    assert "😀渡口" not in catalogue.text and "半幅图" not in catalogue.text
    listed = next(row for row in catalogue.json() if row["version"]["id"] == version_id)
    assert listed["adopted"] and listed["source_state"] == "local"
    assert listed["version"]["assessment"] == "ready"
    exact = client.get(f"/api/branches/{branch}/reference-cards/{version_id}/evidence")
    assert exact.status_code == 200, exact.text
    assert exact.json() == {"version_id": version_id, "source_state": "local", "excerpt": "😀渡口"}
    elected = client.get(location, params={"include_reference_evidence_for": version_id})
    assert elected.status_code == 200, elected.text
    payload = elected.json()
    assert payload["reference_history"]["excerpts"] == {version_id: "😀渡口"}
    assert "半幅图" not in elected.text and SOURCE not in elected.text
    remote = domain.create_novel("另一本作品")
    outside = client.get(
        f"/api/branches/{remote['branch_id']}/manuscript-package",
        params={"include_reference_evidence_for": version_id},
    )
    assert outside.status_code == 409 and outside.json()["code"] == "reference_citation"
    guessed = client.get(f"/api/branches/{remote['branch_id']}/reference-cards/{version_id}/evidence")
    assert guessed.status_code == 404 and "😀渡口" not in guessed.text

    target = Domain(Database(tmp_path / "reference-pkg.sqlite"))
    altered = payload.copy()
    altered["reference_history"] = {
        **payload["reference_history"],
        "excerpts": {version_id: "换过的证据"},
    }
    with pytest.raises(DomainError, match="来源摘要不一致"):
        import_manuscript(target, ManuscriptPackage.model_validate(altered))
    assert target.db.all("SELECT id FROM novels") == []
    import_manuscript(target, ManuscriptPackage.model_validate(payload))
    with target.db.connect() as tx:
        recovered = sync.reference_card_at(
            tx, branch, head["session_id"], head["card_id"], 1, version_id=version_id
        )
        assert sync.reference_excerpt(tx, branch, recovered) == ("😀渡口", "included_excerpt")
        assert tx.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
        assert tx.execute("SELECT COUNT(*) FROM reference_sessions").fetchone()[0] == 0
        recovered_rows = sync.list_reference_history(tx, branch)
        assert {row.source_state for row in recovered_rows} == {"included_excerpt", "metadata_only"}
    assert export_manuscript(target, branch).model_dump() == default
    assert (
        export_manuscript(target, branch, include_reference_evidence_for=[version_id]).model_dump() == payload
    )
