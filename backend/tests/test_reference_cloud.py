from contextlib import closing
import sqlite3

import pytest

from ainovel import reference_sync as sync
from ainovel.db import Database, DomainError
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.reference import EditReference, edit_reference, session
from test_reference_flow import SOURCE, reference as reference_fixture, update
from test_reference_sync import archive, card
from test_story_sync import choose_current, linked_cloud

reference = reference_fixture


def test_cloud_reference_keeps_exact_receipts_and_excludes_opted_in_file_excerpt(
    client, domain, story, reference, tmp_path
):
    branch = story["branch_id"]
    saved = update(client, domain, reference, cards=[card("😀渡口")])
    version = archive(domain, branch).versions[-1]
    exported = export_manuscript(domain, branch, include_reference_evidence_for=[version.id])
    sender = Domain(Database(tmp_path / "sender-reference.sqlite"))
    receiver = Domain(Database(tmp_path / "receiver-reference.sqlite"))
    import_manuscript(sender, exported)
    import_manuscript(receiver, exported.model_copy(update={"reference_history": None}))
    with linked_cloud(sender, receiver, branch, tmp_path) as linked:
        _, right, origin, left_token, right_token, cloud, endpoint, _ = linked
        result = right.run(branch, origin, right_token)
        assert not result["conflicts"] and not result["deferred"], result
        auth = {"Authorization": f"Bearer {left_token}"}
        response = cloud.get(endpoint + "/pull", params={"after": 0, "limit": 50}, headers=auth)
        assert response.status_code == 200, response.text
        assert SOURCE not in response.text and "😀渡口" not in response.text
        changes = response.json()["changes"]
        assert not any("excerpts" in row["payload"] for row in changes if row["payload"])
        wire = next(row for row in changes if row["object_id"] == f"reference-card-version:{version.id}")
        assert wire["device_id"] == "story-left"
        key = sync.reference_head_object_id(branch, saved["id"], version.card_id)
        head = next(row for row in changes if row["object_id"] == key)
        assert wire["sequence"] < head["sequence"]
        assert wire["payload"]["version"]["citation"] == version.citation.model_dump()
        with receiver.db.connect() as tx:
            assert sync.read_reference_head_object(tx, branch, key).revision_id == version.id
            assert sync.reference_excerpt(tx, branch, version) == (None, "metadata_only")
            immutable = tx.execute(
                "SELECT source_device,change_id,sequence FROM portable_reference_cards WHERE id=?",
                (version.id,),
            ).fetchone()
            adopted = tx.execute(
                "SELECT head_device_id,head_change_id FROM portable_reference_heads WHERE adopted_id=?",
                (version.id,),
            ).fetchone()
            assert tuple(immutable) == (wire["device_id"], wire["change_id"], wire["sequence"])
            assert tuple(adopted) == (head["device_id"], head["change_id"])
            assert tx.execute("SELECT COUNT(*) FROM reference_sessions").fetchone()[0] == 0
        injected = {
            **wire["payload"],
            "version": {
                **wire["payload"]["version"],
                "card": {**wire["payload"]["version"]["card"], "evidence": "😀渡口"},
            },
        }
        rejected = cloud.post(
            endpoint + "/push",
            headers=auth,
            json={
                "object_id": wire["object_id"],
                "change_id": "reject-raw-card-evidence",
                "parent_revision": None,
                "tombstone": False,
                "payload": injected,
            },
        )
        assert rejected.status_code == 409 and rejected.json()["code"] == "sync_reference"


def test_cloud_reference_conflicts_are_per_card_even_with_same_session_revision(
    client, domain, story, reference, tmp_path
):
    branch, session_id = story["branch_id"], reference["id"]
    first, second = card("😀渡口"), card("半幅图", id="second-technique")
    update(client, domain, reference, cards=[first, second])
    store = tmp_path / "two-editors-reference.sqlite"
    with domain.db.connect() as source, closing(sqlite3.connect(store)) as target:
        source.backup(target)
    other = Domain(Database(store))

    def edit(writer, cards):
        row = session(writer, session_id)
        return edit_reference(
            writer,
            session_id,
            EditReference(
                base_revision=row["revision"],
                goal=row["payload"]["goal"],
                cards=cards,
                branch_id=branch,
                chapter_id=story["chapter_id"],
            ),
        )

    def active(writer, card_id):
        with writer.db.connect() as tx:
            identifier = sync.read_reference_head(tx, branch, session_id, card_id)
            return sync.read_reference_version(tx, branch, identifier)

    with linked_cloud(domain, other, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, cloud, endpoint, lost = linked
        first_id, second_id = first["id"], second["id"]
        first_head = sync.reference_head_object_id(branch, session_id, first_id)
        common = active(domain, first_id).id
        left_first = {**first, "technique": "左端修改第一张"}
        right_second = {**second, "technique": "右端修改第二张"}
        edit(domain, [left_first, second])
        edit(other, [first, right_second])
        lost["next"] = first_head
        with pytest.raises(DomainError, match="响应中断"):
            left.run(branch, origin, left_token)
        assert not left.run(branch, origin, left_token)["pending"]
        assert not right.run(branch, origin, right_token)["conflicts"]
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert active(other, first_id).card.technique == left_first["technique"]
        assert active(domain, second_id).card.technique == right_second["technique"]
        assert active(domain, first_id).id == active(other, first_id).id != common
        common = active(domain, first_id).id

        edit(domain, [{**left_first, "technique": "左端随后编辑同一张卡"}, second])
        edit(other, [{**first, "technique": "右端也随后编辑同一张卡"}, right_second])
        left.run(branch, origin, left_token)
        next_state = right.run(branch, origin, right_token)
        assert [row["object_id"] for row in next_state["conflicts"]] == [first_head]
        conflict = next_state["conflicts"][0]
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["base"]["payload"]["revision_id"] == common
        assert {value["card"]["technique"] for value in compared["reference_versions"].values()} == {
            left_first["technique"],
            "左端随后编辑同一张卡",
            "右端也随后编辑同一张卡",
        }
        incoming_id = compared["remote"]["incoming"]["payload"]["revision_id"]
        result = choose_current(right, branch, origin, right_token, conflict, compared, "adopt-one-card")
        assert not result["conflicts"] and not result["deferred"], result
        assert active(other, first_id).card.technique == "左端随后编辑同一张卡"
        assert active(other, second_id).card.technique == right_second["technique"]
        second_version = active(other, second_id)
        with other.db.connect() as tx:
            retained = sync.read_reference_version(tx, branch, incoming_id)
            assert retained.card.technique == "右端也随后编辑同一张卡"
            forged = sync.read_reference_head_object(tx, branch, first_head).model_dump()
        forged["revision_id"] = second_version.id
        auth = {"Authorization": f"Bearer {left_token}"}
        invalid_head = cloud.post(
            endpoint + "/push",
            headers=auth,
            json={
                "object_id": first_head,
                "change_id": "reject-head-of-different-card",
                "parent_revision": None,
                "tombstone": False,
                "payload": forged,
            },
        )
        assert invalid_head.status_code == 409 and invalid_head.json()["code"] == "sync_reference"

        assert not left.run(branch, origin, left_token)["conflicts"]
        edit(domain, [second])
        assert not left.run(branch, origin, left_token)["conflicts"]
        assert not right.run(branch, origin, right_token)["conflicts"]
        assert active(other, first_id).assessment == "archived"
        assert active(other, second_id) == second_version
        with other.db.connect() as tx:
            assert sync.read_reference_version(tx, branch, incoming_id) == retained
        response = cloud.get(
            endpoint + "/pull",
            params={"after": 0, "limit": 100},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        changes = response.json()["changes"]
        assert sum(row["change_id"] == lost["receipt"]["change_id"] for row in changes) == 1
