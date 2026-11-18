from contextlib import contextmanager

import pytest

from ainovel.db import Database, DomainError
from ainovel.desktop_sync import ForkChapter, Resolve
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript, source_revision
from test_story_sync import linked_cloud


@contextmanager
def chapter_conflict(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    domain.save(chapter, 0, "雨夜，城门仍开着。")
    second = domain.create_chapter(branch, "第二章")
    domain.save(second["id"], 0, "无人知道渡船停在哪里。")
    other = Domain(Database(tmp_path / "author-workspace.sqlite"))
    import_manuscript(other, export_manuscript(domain, branch))
    with linked_cloud(domain, other, branch, tmp_path) as linked:
        left, right, origin, left_token, token, cloud, endpoint, _ = linked
        domain.save(chapter, 1, "云端稿，城门落了锁。")
        other.save(chapter, 1, "本机稿，守卫交出钥匙。")
        left.run(branch, origin, left_token)
        state = right.run(branch, origin, token)
        [item] = state["conflicts"]
        assert item["object_id"] == f"chapter:{chapter}"
        conflict_id = item["id"]
        compared = right.compare(branch, origin, token, conflict_id)
        stamp = {
            "origin": origin,
            "token": token,
            "parent_revision": compared["remote"]["head_revision"],
            "local_sha256": compared["local_sha256"],
        }
        yield other, linked, compared, conflict_id, stamp


@pytest.mark.parametrize("drop_ack", [False, True])
def test_manual_merge_keeps_exact_sources_and_returns_one_receipt(domain, story, tmp_path, drop_ack):
    branch, chapter = story["branch_id"], story["chapter_id"]
    with chapter_conflict(domain, story, tmp_path) as (other, linked, compared, conflict_id, stamp):
        left, right, origin, left_token, token, cloud, endpoint, _ = linked
        local = compared["local"]["head"]
        current = compared["remote"]["current"]["payload"]["head"]
        incoming = compared["remote"]["incoming"]["payload"]["head"]
        merge = Resolve(
            **stamp, choice="merge", content="守卫交出钥匙，城门随即落锁。", idempotency_key="merge-chapter"
        )
        transport = right.transport

        def lose_response(*args, **kwargs):
            result = transport(*args, **kwargs)
            if args[3].endswith("/resolve"):
                right.transport = transport
                raise DomainError("sync_transport", "模拟合并回执中断。", 502)
            return result

        if drop_ack:
            right.transport = lose_response
            with pytest.raises(DomainError, match="回执中断"):
                right.resolve(branch, origin, token, conflict_id, merge)
            assert other.chapter(chapter)["content"] == local["content"]
            assert right._state(branch)["pending"] == 1

        merged = right.resolve(branch, origin, token, conflict_id, merge)
        receipt = merged["merge_receipt"]
        assert not merged["conflicts"] and not merged["deferred"]
        assert receipt["change_id"] == "merge-chapter"
        assert receipt["source_parent_id"] == current["id"]
        assert receipt["local_revision_id"]
        assert other.chapter(chapter)["content"] == merge.content
        assert right.resolve(branch, origin, token, conflict_id, merge)["merge_receipt"] == receipt
        with pytest.raises(DomainError, match="提交编号"):
            right.resolve(branch, origin, token, conflict_id, merge.model_copy(update={"content": "另一稿"}))

        with other.db.connect() as tx:
            assert not tx.execute(
                "SELECT 1 FROM desktop_sync_outbox WHERE branch_id=? AND change_id=?",
                (branch, "merge-chapter"),
            ).fetchone()
            original = source_revision(tx, branch, local["id"])
            cloud_original = source_revision(tx, branch, current["id"])
            chosen = source_revision(tx, branch, receipt["source_revision_id"])
            assert original.content == local["content"]
            assert cloud_original.content == current["content"]
            assert chosen.parent_id == current["id"] and chosen.content == merge.content
        left.run(branch, origin, left_token)
        assert domain.chapter(chapter)["content"] == merge.content
        answer = cloud.get(
            endpoint + "/pull", params={"after": 0}, headers={"Authorization": f"Bearer {token}"}
        )
        assert answer.is_success, answer.text
        changes = answer.json()["changes"]
        assert len([row for row in changes if row["change_id"] == receipt["change_id"]]) == 1
        assert len([row for row in changes if row["object_id"] == "revision:" + chosen.id]) == 1
        assert any(row["object_id"] == "revision:" + incoming["id"] for row in changes)


def test_author_can_fork_exact_cloud_chapter_without_resolving_conflict(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    with chapter_conflict(domain, story, tmp_path) as (other, linked, compared, conflict_id, stamp):
        _, right, origin, _, token, _, _, _ = linked
        fork = ForkChapter(
            **stamp, choice="current", branch_name="采用云稿的试写版本", idempotency_key="fork-chapter"
        )
        before = export_manuscript(other, branch)
        saved = right.fork(branch, origin, token, conflict_id, fork)
        assert saved["branch_id"] != branch and saved["chapter_id"] != chapter
        chosen = compared["remote"]["current"]["payload"]["head"]
        assert saved["source_revision_id"] == chosen["id"] and saved["source_sha256"] == chosen["sha256"]
        assert other.chapter(saved["chapter_id"])["content"] == chosen["content"]
        assert other.chapter(chapter)["content"] == compared["local"]["head"]["content"]
        original = export_manuscript(other, branch)
        assert original.chapters == before.chapters and original.revisions == before.revisions
        target = export_manuscript(other, saved["branch_id"])
        assert len(target.chapters) == 2
        assert any(row.content == "无人知道渡船停在哪里。" for row in target.revisions)
        assert not right._state(saved["branch_id"])["enabled"]
        assert right.compare(branch, origin, token, conflict_id)["remote"]["resolved_by"] is None
        assert right.fork(branch, origin, token, conflict_id, fork) == saved
        with pytest.raises(DomainError, match="提交编号"):
            right.fork(
                branch, origin, token, conflict_id, fork.model_copy(update={"branch_name": "其他版本"})
            )


@pytest.mark.parametrize("where", ["local", "cloud"])
def test_conflict_actions_reject_stale_compared_baselines(domain, story, tmp_path, where):
    branch, chapter = story["branch_id"], story["chapter_id"]
    with chapter_conflict(domain, story, tmp_path) as (other, linked, compared, conflict_id, stamp):
        _, right, origin, left_token, token, cloud, endpoint, _ = linked
        changed = "冲突比较后保存的新内容。"
        if where == "local":
            other.save(chapter, other.chapter(chapter)["revision"], changed)
        else:
            updated = compared["remote"]["current"]["payload"]
            updated["chapter"]["title"] = changed
            response = cloud.post(
                endpoint + "/push",
                headers={"Authorization": f"Bearer {left_token}"},
                json={
                    "change_id": "other-cloud-writer",
                    "object_id": f"chapter:{chapter}",
                    "parent_revision": stamp["parent_revision"],
                    "payload": updated,
                    "tombstone": False,
                },
            )
            assert response.is_success and response.json()["status"] == "applied", response.text
        merge = Resolve(**stamp, choice="merge", content="旧比较合并稿", idempotency_key="old-merge")
        fork = ForkChapter(**stamp, choice="current", branch_name="旧比较分支", idempotency_key="old-fork")
        with pytest.raises(DomainError, match="重新比较"):
            right.resolve(branch, origin, token, conflict_id, merge)
        with pytest.raises(DomainError, match="重新比较"):
            right.fork(branch, origin, token, conflict_id, fork)
        latest = right.compare(branch, origin, token, conflict_id)
        assert latest["remote"]["resolved_by"] is None
        if where == "local":
            assert other.chapter(chapter)["content"] == changed
        else:
            assert latest["remote"]["current"]["payload"]["chapter"]["title"] == changed
        with other.db.connect() as tx:
            assert not tx.execute("SELECT 1 FROM desktop_sync_outbox WHERE branch_id=?", (branch,)).fetchone()


def test_cloud_merge_receipt_preserves_a_local_edit_made_during_transmission(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    with chapter_conflict(domain, story, tmp_path) as (other, linked, _, conflict_id, stamp):
        _, right, origin, _, token, _, _, _ = linked
        transport = right.transport
        later = "第一个合并请求发出时，本机另写的末句。"

        def edit_during_request(*args, **kwargs):
            if args[3].endswith("/resolve"):
                other.save(chapter, other.chapter(chapter)["revision"], later)
                right.transport = transport
            return transport(*args, **kwargs)

        right.transport = edit_during_request
        merge = Resolve(**stamp, choice="merge", content="合并终稿", idempotency_key="merge-with-local-race")
        state = right.resolve(branch, origin, token, conflict_id, merge)
        assert state["merge_receipt"]["local_revision_id"] is None
        assert any(row["object_id"] == f"chapter:{chapter}" for row in state["deferred"])
        assert other.chapter(chapter)["content"] == later
        assert (
            right.resolve(branch, origin, token, conflict_id, merge)["merge_receipt"]
            == state["merge_receipt"]
        )
        with other.db.connect() as tx:
            cloud_original = source_revision(tx, branch, state["merge_receipt"]["source_revision_id"])
            assert cloud_original.content == merge.content


def test_archive_is_not_a_prose_merge_candidate_but_the_active_version_can_be_forked(domain, story, tmp_path):
    branch, chapter = story["branch_id"], story["chapter_id"]
    with chapter_conflict(domain, story, tmp_path) as (other, linked, compared, conflict_id, stamp):
        _, right, origin, left_token, token, cloud, endpoint, _ = linked
        response = cloud.post(
            endpoint + "/push",
            headers={"Authorization": f"Bearer {left_token}"},
            json={
                "change_id": "archive-cloud-winner",
                "object_id": f"chapter:{chapter}",
                "parent_revision": stamp["parent_revision"],
                "payload": None,
                "tombstone": True,
            },
        )
        assert response.is_success and response.json()["status"] == "applied", response.text
        stamp = {**stamp, "parent_revision": response.json()["change_id"]}
        merged = cloud.post(
            f"{endpoint}/conflicts/{conflict_id}/resolve",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "choice": "merge_prose",
                "change_id": "invalid-archive-merge",
                "parent_revision": stamp["parent_revision"],
                "content": "有正文的一侧",
            },
        )
        assert merged.status_code == 409 and merged.json()["code"] == "sync_chapter"
        merge = Resolve(
            **stamp, choice="merge", content="本机提交同样被拒绝", idempotency_key="archive-merge"
        )
        with pytest.raises(DomainError) as rejected:
            right.resolve(branch, origin, token, conflict_id, merge)
        assert rejected.value.code == "sync_chapter"
        fork = ForkChapter(
            **stamp, choice="incoming", branch_name="保留另一提交稿", idempotency_key="archive-fork"
        )
        saved = right.fork(branch, origin, token, conflict_id, fork)
        assert (
            other.chapter(saved["chapter_id"])["content"]
            == compared["remote"]["incoming"]["payload"]["head"]["content"]
        )
        assert right.compare(branch, origin, token, conflict_id)["remote"]["resolved_by"] is None
