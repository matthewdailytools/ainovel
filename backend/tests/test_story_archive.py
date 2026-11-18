import pytest

from ainovel.db import Database, DomainError
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.story_archive import export_story_archive, validate_story_archive
from ainovel.story_graph import SaveRecord, graph, save_record
from ainovel.story_sync import SCHEMA, draft_story_objects, read_story_head, read_story_version
from test_story_sync import choose_current, linked_cloud, paired, record, visible


def export_pair(domain, branch):
    package = export_manuscript(domain, branch)
    with domain.db.connect() as tx:
        archive = export_story_archive(tx, branch)
    return package, archive


def revision_maps(package):
    native = {row.id: row for row in package.revisions}
    return {**native, **{row.id: row for row in package.source_revisions}}, native


def isolated_restore(package, archive, path):
    branch = package.branch.id
    validate_story_archive(archive, branch, *revision_maps(package))
    target = Domain(Database(path))
    import_manuscript(target, package.model_copy(update={"story_history": archive}))
    return target


def comparable_graph(domain, branch):
    return {
        record["id"]: (record["revision"], record["archived"], record["payload"], record["invalid_reasons"])
        for record in graph(domain, branch)["records"]
    }


def test_story_archive_restores_accepted_history_references_and_archived_record(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    chapter = domain.save(chapter_id, 0, "门外📯响了一声。")
    proof = {"chapter_id": chapter_id, "revision": chapter["revision"], "start": 2, "end": 4, "quote": "📯"}
    with domain.db.connect() as tx:
        tx.executescript(SCHEMA)
    with domain.db.connect(True) as tx:
        person = record(tx, branch, {"kind": "character", "title": "师姐"})
        retired = record(tx, branch, {"kind": "character", "title": "不再出场的行客"})
        event = record(
            tx,
            branch,
            {
                "kind": "event",
                "title": "听见号角",
                "status": "actual",
                "participants": [person],
                "sources": [proof],
            },
        )
        promise = record(
            tx, branch, {"kind": "promise", "title": "谁吹了号角", "stages": [{"status": "planned"}]}
        )
    original = visible(domain, branch, promise)
    with domain.db.connect(True) as tx:
        record(
            tx,
            branch,
            {
                **original["payload"],
                "stages": [
                    {"status": "planned"},
                    {"status": "planted", "event_id": event, "sources": [proof]},
                    {"status": "resolved", "event_id": event, "sources": [proof]},
                ],
            },
            existing=original,
        )
    save_record(
        domain,
        branch,
        SaveRecord(
            base_revision=0,
            base_story_revision=domain.branch(branch)["story_revision"],
            archived=True,
            record=visible(domain, branch, retired)["payload"],
        ),
        retired,
    )
    package, archive = export_pair(domain, branch)
    assert set(archive.adopted_heads) == {person, event, promise, retired}
    assert [row.revision for row in archive.native_history[promise]] == [0, 1]
    assert archive.native_history[promise][-1].dependencies == {event: 0}
    target = isolated_restore(package, archive, tmp_path / "restored-story.sqlite")
    assert comparable_graph(target, branch) == comparable_graph(domain, branch)
    assert target.branch(branch)["story_revision"] == domain.branch(branch)["story_revision"]
    assert export_pair(target, branch)[1] == archive
    with target.db.connect() as tx:
        assert read_story_head(tx, branch, promise) == archive.adopted_heads[promise]
        assert len(draft_story_objects(tx, branch)) == len(archive.versions) + len(archive.adopted_heads)
        assert (
            tx.execute(
                "SELECT parent_revision FROM story_record_dependencies WHERE record_id=? AND parent_id=?",
                (event, person),
            ).fetchone()[0]
            == 0
        )
    bad = archive.native_history[promise][-1].model_copy(update={"dependencies": {event: 1}})
    incompatible = archive.model_copy(
        update={
            "native_history": {**archive.native_history, promise: [archive.native_history[promise][0], bad]}
        }
    )
    with pytest.raises(DomainError, match="依赖与冻结版本不一致"):
        validate_story_archive(incompatible, branch, *revision_maps(package))


def test_story_archive_binds_only_explicit_approved_source_only_chapter(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    domain.save(chapter_id, 0, "师姐在门外。")
    other = paired(domain, branch, tmp_path / "story-source-other.sqlite")
    with linked_cloud(domain, other, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        chapter = domain.save(chapter_id, 1, "门外📯响起。")
        other.save(chapter_id, 1, "另一端改稿，铜铃还在响。")
        proof = {
            "chapter_id": chapter_id,
            "revision": chapter["revision"],
            "start": 2,
            "end": 4,
            "quote": "📯",
        }
        with domain.db.connect(True) as tx:
            promise = record(
                tx,
                branch,
                {
                    "kind": "promise",
                    "title": "找到声源",
                    "stages": [{"status": "resolved", "sources": [proof]}],
                },
            )
        left.run(branch, origin, left_token)
        pending = right.run(branch, origin, right_token)
        assert any(item["object_id"] == f"story:{promise}" for item in pending["deferred"])
        assert not graph(other, branch)["records"]
        pending_package, retained = export_pair(other, branch)
        assert not retained.adopted_heads and not retained.native_history
        assert [version.record_id for version in retained.versions] == [promise]
        still_pending = isolated_restore(pending_package, retained, tmp_path / "story-archive-pending.sqlite")
        assert not graph(still_pending, branch)["records"]
        assert export_pair(still_pending, branch)[1] == retained
        conflict = next(item for item in pending["conflicts"] if item["object_id"] == f"chapter:{chapter_id}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert not choose_current(
            right, branch, origin, right_token, conflict, compared, "archive-source-pick"
        )["deferred"]
    package, archive = export_pair(other, branch)
    version_id = archive.adopted_heads[promise]
    selected = next(row for row in archive.versions if row.id == version_id)
    source_id = selected.evidence.stages[0][0].revision_id
    native = archive.native_history[promise][0]
    native_id = native.evidence.stages[0][0]
    all_revisions, native_revisions = revision_maps(package)
    assert source_id in all_revisions and source_id not in native_revisions
    assert native_id in native_revisions and native_id != source_id
    assert archive.source_devices[version_id] == "story-left"
    rebuilt = isolated_restore(package, archive, tmp_path / "story-source-restored.sqlite")
    assert comparable_graph(rebuilt, branch) == comparable_graph(other, branch)
    with rebuilt.db.connect() as tx:
        assert read_story_version(tx, branch, version_id) == selected
        assert tx.execute(
            "SELECT 1 FROM story_sync_chapter_sources WHERE branch_id=? AND source_id=? AND native_id=?",
            (branch, source_id, native_id),
        ).fetchone()
    missing = native.model_copy(
        update={"evidence": native.evidence.model_copy(update={"stages": [[source_id]]})}
    )
    broken = archive.model_copy(update={"native_history": {promise: [missing]}})
    with pytest.raises(DomainError, match="缺少已采用"):
        validate_story_archive(broken, branch, all_revisions, native_revisions)
