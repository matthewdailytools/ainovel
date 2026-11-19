from hashlib import sha256

import pytest

from ainovel import restore_state_desktop as desktop, restore_state_sync as wire
from ainovel.db import uid
from ainovel.desktop_sync import _headers
from ainovel.manuscript_exchange import export_manuscript
from ainovel.workflow_inspection import _save_length_target, current_length_target
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_story_sync import choose_current, linked_cloud, paired
from test_work_snapshot import verify


def fact(chapter, statement, quote):
    return {
        "chapter_id": chapter["id"],
        "source_revision": chapter["revision"],
        "subject": "守岗人",
        "kind": "knowledge",
        "statement": statement,
        "truth": "fact",
        "story_time": "日落",
        "evidence": quote,
    }


def native_fact(domain, branch, chapter, statement, quote):
    data = fact(chapter, statement, quote)
    with domain.db.connect(True) as tx:
        domain.add_fact_tx(
            tx,
            branch,
            chapter["id"],
            chapter["revision"],
            {key: value for key, value in data.items() if key not in ("chapter_id", "source_revision")},
            chapter["content"],
        )


def facts(domain, branch):
    state = export_manuscript(domain, branch).restore_state
    return state.facts if state else []


def read_length(domain, branch, scope, identifier=None):
    with domain.db.connect() as tx:
        return current_length_target(tx, branch, scope, identifier, include_cleared=True)


def synced_length(client, branch, scope, minimum, identifier=None, base=None):
    path = f"/api/branches/{branch}/workflow-inspections/length-targets"
    return verify(
        client.put(
            path,
            json={
                "scope": scope,
                "scope_id": identifier,
                "base_target_id": base,
                "targets": {"min_chars": minimum},
            },
        )
    )


def kept_state(sync, branch, origin, token):
    result = sync.run(branch, origin, token)
    assert not result["conflicts"] and not result["deferred"] and not result["pending"], result
    return result["cursor"]


def test_desktop_projects_public_facts_and_length_in_both_directions_without_echo(
    client, domain, story, tmp_path
):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    source_chapter = domain.save(chapter_id, 0, "🌧守岗人听见铜铃。")
    receiver = paired(domain, branch, tmp_path / "receiver.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        old_fact = "铜铃从岗亭外响起。"
        verify(client.post("/api/facts", json=fact(source_chapter, old_fact, "铜铃")), 201)
        target = synced_length(client, branch, "book", 8)["preview"]["length_target"]
        chapter_target = synced_length(client, branch, "chapter", 4, chapter_id)["preview"]["length_target"]
        head = kept_state(left, branch, origin, left_token)
        assert kept_state(right, branch, origin, right_token) == head
        adopted = facts(receiver, branch)
        assert [row.statement for row in adopted] == [old_fact]
        original = facts(domain, branch)
        assert adopted == original and (adopted[0].evidence.start, adopted[0].evidence.end) == (7, 9)
        assert read_length(receiver, branch, "book")["targets"] == target["targets"]
        assert read_length(receiver, branch, "chapter", chapter_id)["targets"] == chapter_target["targets"]

        local_chapter = receiver.chapter(chapter_id)
        next_chapter = receiver.save(chapter_id, local_chapter["revision"], "🌧今夜岗亭传来木鼓。")
        next_fact = "守岗人改听木鼓。"
        native_fact(receiver, branch, next_chapter, next_fact, "木鼓")
        with receiver.db.connect(True) as tx:
            previous = current_length_target(tx, branch, "book", None, include_cleared=True)
            _save_length_target(tx, branch, "book", None, previous, None)
        inbound_head = kept_state(right, branch, origin, right_token)
        assert inbound_head > head
        assert kept_state(left, branch, origin, left_token) == inbound_head
        assert [row.statement for row in facts(domain, branch)] == [next_fact]
        assert facts(domain, branch) == facts(receiver, branch)
        assert read_length(domain, branch, "book")["targets"] is None
        assert read_length(domain, branch, "chapter", chapter_id)["targets"] == chapter_target["targets"]
        assert kept_state(right, branch, origin, right_token) == inbound_head


def test_desktop_waits_for_late_same_text_chapter_source_after_directory_batch(domain, story, tmp_path):
    branch, first = story["branch_id"], story["chapter_id"]
    content, quote = "🌧\r\n守岗人听见铜铃。", "铜铃"
    domain.save(first, 0, content)
    second = domain.create_chapter(branch, "第二章")["id"]
    domain.save(second, 0, "第二章正文。")
    receiver = paired(domain, branch, tmp_path / "late-chapter-source.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        _, right, origin, left_token, right_token, cloud, endpoint, _ = linked
        checkpoint = verify(cloud.get(endpoint + "/checkpoint", headers=_headers(left_token)))
        headers = _headers(left_token, checkpoint["ledger_id"])
        initial = verify(cloud.get(endpoint + "/pull", headers=headers))
        chapters = {
            row["payload"]["chapter"]["id"]: row
            for row in initial["changes"]
            if row["object_id"].startswith("chapter:")
        }
        original = chapters[first]["payload"]["head"]
        manifest = verify(
            cloud.post(
                endpoint + "/chapter-order-batches",
                headers=headers,
                json={
                    "change_id": "swap-before-late-source",
                    "moves": [
                        {
                            "chapter_id": chapter_id,
                            "position": position,
                            "next_position": next_position,
                            "head_revision": chapters[chapter_id]["head_revision"],
                        }
                        for chapter_id, position, next_position in ((first, 1, 2), (second, 2, 1))
                    ],
                },
            )
        )
        move = next(row for row in manifest["payload"]["moves"] if row["chapter_id"] == first)
        part = verify(cloud.get(f"{endpoint}/changes/{move['receipt_id']}", headers=headers))
        revision = {
            **original,
            "id": uid(),
            "parent_id": original["id"],
            "revision": original["revision"] + 1,
            "cause": "从跨端归档恢复",
        }

        def push(identifier, payload, parent=None):
            return verify(
                cloud.post(
                    endpoint + "/push",
                    headers=headers,
                    json={
                        "change_id": uid(),
                        "object_id": identifier,
                        "parent_revision": parent,
                        "payload": payload,
                        "tombstone": False,
                    },
                )
            )

        head = push(
            f"chapter:{first}",
            {
                **part["payload"],
                "chapter": {**part["payload"]["chapter"], "revision": revision["revision"]},
                "head": revision,
            },
            part["head_revision"],
        )
        assert head["status"] == "applied"
        waiting = right.run(branch, origin, right_token)
        assert [row["object_id"] for row in waiting["deferred"]] == [f"chapter:{first}"]
        assert not waiting["conflicts"] and not waiting["pending"]
        assert receiver.chapter(first)["position"] == 2 and receiver.chapter(second)["position"] == 1
        assert receiver.chapter(first)["revision"] == original["revision"]
        assert not facts(receiver, branch)

        source = push(
            f"revision:{revision['id']}",
            {"format": wire.FORMAT, "kind": "revision", "branch_id": branch, "revision": revision},
        )
        assert source["sequence"] > head["sequence"]
        data = {
            "parent_id": None,
            "chapter_id": first,
            "chapter_revision_id": revision["id"],
            "facts": [
                {
                    "subject": "守岗人",
                    "kind": "knowledge",
                    "statement": "守岗人听到铜铃。",
                    "truth": "fact",
                    "story_time": "日落",
                    "evidence": {
                        "chapter_id": first,
                        "revision_id": revision["id"],
                        "start": 9,
                        "end": 11,
                        "quote_sha256": sha256(quote.encode()).hexdigest(),
                    },
                }
            ],
        }
        version = wire.FactSetVersion(id=wire.version_id(branch, data), **data)
        for wrapped in (wire.version_object(branch, version), wire.head_object(branch, version)):
            fact_receipt = push(wire.object_id(wrapped), wrapped.model_dump())
            assert fact_receipt["status"] == "applied"
        assert kept_state(right, branch, origin, right_token) == fact_receipt["sequence"]
        assert kept_state(right, branch, origin, right_token) == fact_receipt["sequence"]
        exported = export_manuscript(receiver, branch)
        restored_ids = {original["id"], revision["id"]}
        assert [row.model_dump() for row in exported.revisions if row.id in restored_ids] == [
            original,
            revision,
        ]
        assert exported.restore_state.facts == version.facts
        rows = receiver.db.all(
            "SELECT source_revision,evidence FROM facts WHERE chapter_id=? AND stale=0",
            (first,),
        )
        assert [(row["source_revision"], row["evidence"]) for row in rows] == [(revision["revision"], quote)]


def test_fact_conflict_retains_both_exact_versions_until_author_selects_cloud(domain, story, tmp_path):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    chapter = domain.save(chapter_id, 0, "守岗人听到铜铃，在纸上记下。")
    native_fact(domain, branch, chapter, "铜铃响了一声。", "铜铃")
    receiver = paired(domain, branch, tmp_path / "fact-conflict.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        native_fact(domain, branch, chapter, "左端确认是晚岗。", "守岗人")
        native_fact(receiver, branch, receiver.chapter(chapter_id), "右端确认写在纸上。", "纸上")
        kept_state(left, branch, origin, left_token)
        status = right.run(branch, origin, right_token)
        [conflict] = status["conflicts"]
        assert conflict["object_id"].startswith(desktop.FACT_HEAD)
        assert {row.statement for row in facts(receiver, branch)} == {"铜铃响了一声。", "右端确认写在纸上。"}
        compared = right.compare(branch, origin, right_token, conflict["id"])
        versions = compared["fact_set_versions"]
        assert len(versions) == 3
        assert {row["statement"] for row in versions[compared["local"]["revision_id"]]["facts"]} == {
            "铜铃响了一声。",
            "右端确认写在纸上。",
        }
        adopted = choose_current(right, branch, origin, right_token, conflict, compared, uid())
        assert not adopted["conflicts"] and not adopted["deferred"], adopted
        assert {row.statement for row in facts(receiver, branch)} == {"铜铃响了一声。", "左端确认是晚岗。"}
        with receiver.db.connect() as tx:
            assert tx.execute(
                "SELECT stale FROM facts WHERE statement=?", ("右端确认写在纸上。",)
            ).fetchone()[0]


@pytest.mark.parametrize("remote_facts", [False, True])
def test_new_remote_chapter_keeps_a_pending_fact_edit_bound_to_its_original_source(
    domain, story, tmp_path, remote_facts
):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    chapter = domain.save(chapter_id, 0, "守岗人先听见铜铃。")
    native_fact(domain, branch, chapter, "原文写着铜铃。", "铜铃")
    receiver = paired(domain, branch, tmp_path / "fact-reattach.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        older = receiver.chapter(chapter_id)
        native_fact(receiver, branch, older, "右端在旧正文核对守岗人。", "守岗人")
        original_source_id = facts(receiver, branch)[0].evidence.revision_id
        new = domain.save(chapter_id, chapter["revision"], "更深时，岗亭只听见木鼓。")
        if remote_facts:
            native_fact(domain, branch, new, "远端新正文的依据是木鼓。", "木鼓")
        kept_state(left, branch, origin, left_token)
        result = right.run(branch, origin, right_token)
        [conflict] = result["conflicts"]
        assert receiver.chapter(chapter_id)["content"] == new["content"]
        assert facts(receiver, branch) == []
        compared = right.compare(branch, origin, right_token, conflict["id"])
        versions = compared["fact_set_versions"]
        local = versions[compared["local"]["revision_id"]]
        assert local["chapter_revision_id"] == original_source_id
        assert {row["statement"] for row in local["facts"]} == {"原文写着铜铃。", "右端在旧正文核对守岗人。"}
        adopted = choose_current(right, branch, origin, right_token, conflict, compared, uid())
        assert not adopted["conflicts"] and not adopted["deferred"], adopted
        assert facts(receiver, branch) == facts(domain, branch)
        assert kept_state(right, branch, origin, right_token) == adopted["cursor"]


def test_volume_target_rebinds_to_new_outline_and_divergence_keeps_both_targets(
    client, domain, story, tmp_path
):
    branch, chapter_id = story["branch_id"], story["chapter_id"]
    plan_path = f"/api/branches/{branch}/outline-workspace"
    setting = setting_confirmation(client, branch)
    volume = "river-watch-volume"

    def confirm(base, label):
        return put_and_confirm(
            client,
            plan_path,
            {
                "base_revision": base,
                "confirmation_id": setting,
                "plan": {
                    "summary": label,
                    "volumes": [
                        {
                            "id": volume,
                            "title": "河口",
                            "summary": label,
                            "units": [
                                {
                                    "id": "river-watch-unit",
                                    "title": "查岗",
                                    "summary": label,
                                    "chapters": [chapter_plan(chapter_id, label)],
                                }
                            ],
                        }
                    ],
                },
            },
        )["confirmed"]

    first = confirm(0, "确认渡口的夜岗")
    receiver = paired(domain, branch, tmp_path / "volume-receiver.sqlite")
    with linked_cloud(domain, receiver, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, cloud, endpoint, _ = linked
        synced_length(client, branch, "volume", 22, volume)
        before = kept_state(left, branch, origin, left_token)
        assert kept_state(right, branch, origin, right_token) == before
        second = confirm(first["revision"], "保留夜岗，先找上岸石阶")
        after = kept_state(left, branch, origin, left_token)
        assert after > before
        received = kept_state(right, branch, origin, right_token)
        ledger = verify(cloud.get(endpoint + "/checkpoint", headers=_headers(right_token)))["ledger_id"]
        remaining = verify(
            cloud.get(endpoint + f"/pull?after={after}&limit=50", headers=_headers(right_token, ledger))
        )
        assert not [
            row["object_id"] for row in remaining["changes"] if row["object_id"].startswith(desktop.PREFIXES)
        ]
        assert kept_state(right, branch, origin, right_token) == received
        with receiver.db.connect() as tx:
            entries = list(desktop.draft_objects(tx, branch, lambda address, reason: None))
        versions = {
            entry.version.id: entry.version
            for entry in entries
            if isinstance(entry, wire.LengthTargetVersionObject)
        }
        head = next(entry for entry in entries if isinstance(entry, wire.LengthTargetHeadObject))
        current = versions[head.revision_id]
        assert current.outline_version_id == second["id"]
        assert versions[current.parent_id].outline_version_id == first["id"]
        assert (
            current.targets.min_chars
            == read_length(receiver, branch, "volume", volume)["targets"]["min_chars"]
            == 22
        )
        original = read_length(domain, branch, "volume", volume)
        synced_length(client, branch, "volume", 31, volume, original["id"])
        with receiver.db.connect(True) as tx:
            target = current_length_target(tx, branch, "volume", volume, include_cleared=True)
            _save_length_target(tx, branch, "volume", volume, target, {**target["targets"], "min_chars": 40})
        kept_state(left, branch, origin, left_token)
        status = right.run(branch, origin, right_token)
        [conflict] = status["conflicts"]
        assert conflict["object_id"].startswith(desktop.LENGTH_HEAD)
        assert read_length(receiver, branch, "volume", volume)["targets"]["min_chars"] == 40
        compared = right.compare(branch, origin, right_token, conflict["id"])
        cloud_version = compared["remote"]["current"]["payload"]["revision_id"]
        versions = compared["length_target_versions"]
        assert versions[cloud_version]["targets"]["min_chars"] == 31
        assert versions[compared["local"]["revision_id"]]["targets"]["min_chars"] == 40
        adopted = choose_current(right, branch, origin, right_token, conflict, compared, uid())
        assert not adopted["conflicts"] and not adopted["deferred"], adopted
        assert read_length(receiver, branch, "volume", volume)["targets"]["min_chars"] == 31
        assert kept_state(right, branch, origin, right_token) == adopted["cursor"]
        assert kept_state(left, branch, origin, left_token) == adopted["cursor"]
