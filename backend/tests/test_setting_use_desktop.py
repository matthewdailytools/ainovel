from dataclasses import replace
import json

from fastapi.testclient import TestClient
import pytest

from ainovel import setting_use_desktop
from ainovel.api import create_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve, _network
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.setting_use_portability import (
    HEAD_PREFIX,
    VERSION_PREFIX,
    SettingUseVersion,
    head_object,
    slot_id,
    version_object,
)
from test_setting_use import TEXT, author_plan as author_plan, digest, select, verified
from test_story_sync import choose_current, linked_cloud
from test_workflow_cloud import _grant, loopback as loopback


def receiving_desktop(domain, branch, settings, target_dir):
    target_settings = replace(settings, data_dir=target_dir)
    target = Domain(Database(target_dir / "manuscripts.sqlite"))
    import_manuscript(target, export_manuscript(domain, branch))
    return target, TestClient(create_app(target_settings))


def confirm_use(client, route, key, *, parent=None, content=TEXT, quote=None):
    request = select(verified(client.get(route)), key, parent)
    if quote:
        before = content[: content.index(quote)]
        start = len(before.encode("utf-16-le")) // 2
        end = start + len(quote.encode("utf-16-le")) // 2
        request["evidence"].update(start=start, end=end, quote_sha256=digest(quote))
    result = verified(client.post(route, json=request), 201)
    assert result["status"] == "current"
    return result["version"]


def record(client, route, identifier):
    return verified(client.get(f"{route}/{identifier}"))


def test_setting_use_desktop_preserves_competing_heads_and_formal_withdrawal(
    client, domain, settings, author_plan, tmp_path, loopback
):
    route, branch = author_plan["route"], author_plan["branch_id"]
    first_chapter = author_plan["first"]["id"]
    target, receiver = receiving_desktop(domain, branch, settings, tmp_path / "setting-use-desktop")
    origin, _ = loopback
    endpoint = f"/api/sync/works/{branch}"
    left_token = _grant(loopback, branch, "setting-use-left")
    right_token = _grant(loopback, branch, "setting-use-right")
    left, right = DesktopSync(domain), DesktopSync(target)
    left.enable(branch, origin, left_token)
    right.enable(branch, origin, right_token)
    with receiver:
        receiver.headers["X-Ainovel-CSRF"] = verified(receiver.get("/api/session"))["csrf"]
        initial = confirm_use(client, route, "base-author-proof")
        with pytest.raises(DomainError) as missing:
            _network(
                origin,
                left_token,
                "POST",
                endpoint + "/push",
                {
                    "change_id": "proof-before-dependencies",
                    "object_id": VERSION_PREFIX + initial["id"],
                    "parent_revision": None,
                    "payload": version_object(branch, SettingUseVersion.model_validate(initial)).model_dump(),
                    "tombstone": False,
                },
            )
        assert missing.value.code == "setting_use_dependency" and missing.value.status == 409
        assert not left.run(branch, origin, left_token)["pending"]
        assert not right.run(branch, origin, right_token)["deferred"]
        assert record(receiver, route, initial["id"])["status"] == "current"

        left_version = confirm_use(
            client, route, "left-competing-proof", parent=initial["id"], quote="领航人阿禾"
        )
        right_version = confirm_use(
            receiver, route, "right-competing-proof", parent=initial["id"], quote="核实铜牌"
        )
        assert left._state(branch)["pending"] == right._state(branch)["pending"] == 1
        left.run(branch, origin, left_token)
        state = right.run(branch, origin, right_token)
        slot = slot_id(branch, first_chapter, initial["settings_version_id"], initial["item_id"])
        assert len(state["conflicts"]) == 1
        conflict = state["conflicts"][0]
        assert conflict["object_id"] == HEAD_PREFIX + slot
        assert record(receiver, route, right_version["id"])["status"] == "current"
        assert record(receiver, route, left_version["id"])["status"] == "superseded"
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["base"]["payload"]["revision_id"] == initial["id"]
        assert compared["remote"]["current"]["payload"]["revision_id"] == left_version["id"]
        assert compared["remote"]["incoming"]["payload"]["revision_id"] == right_version["id"]
        assert compared["setting_use_versions"] == {
            proof["id"]: proof for proof in (initial, left_version, right_version)
        }
        result = right.resolve(
            branch,
            origin,
            right_token,
            conflict["id"],
            Resolve(
                origin=origin,
                token=right_token,
                choice="local",
                parent_revision=compared["remote"]["head_revision"],
                local_sha256=compared["local_sha256"],
                idempotency_key="keep-right-setting-use",
            ),
        )
        assert not result["conflicts"] and not result["deferred"], result
        assert not left.run(branch, origin, left_token)["deferred"]
        assert record(client, route, right_version["id"])["status"] == "current"
        assert record(client, route, left_version["id"])["quote"] == "领航人阿禾"

        withdrawn = verified(
            client.post(
                f"{route}/{right_version['id']}/withdraw", json={"idempotency_key": "withdraw-right-proof"}
            ),
            201,
        )
        assert withdrawn["status"] == "withdrawn"
        assert not left.run(branch, origin, left_token)["pending"]
        assert not right.run(branch, origin, right_token)["deferred"]
        latest = record(receiver, route, withdrawn["version"]["id"])
        assert latest["status"] == "withdrawn" and latest["quote"] == "核实铜牌"
        assert latest["version"]["parent_id"] == right_version["id"]
        assert record(receiver, route, right_version["id"])["status"] == "superseded"
        assert domain.chapter(first_chapter)["content"] == target.chapter(first_chapter)["content"] == TEXT

        package = export_manuscript(target, branch)
        history = package.setting_use_history
        assert history.adopted_heads == {slot: latest["version"]["id"]}
        assert {entry.version.id: entry.source_device for entry in history.versions} == {
            initial["id"]: "setting-use-left",
            left_version["id"]: "setting-use-left",
            right_version["id"]: "setting-use-right",
            latest["version"]["id"]: "setting-use-left",
        }
        backed_up = Domain(Database(tmp_path / "backup-setting-use.sqlite"))
        import_manuscript(backed_up, package)
        assert export_manuscript(backed_up, branch).setting_use_history == history

        changes = _network(origin, left_token, "GET", endpoint + "/pull?after=0&limit=50")["changes"]
        proofs = [event for event in changes if event["object_id"].startswith(VERSION_PREFIX)]
        assert len(proofs) == len(history.versions)
        assert all(not event["tombstone"] for event in proofs)
        assert all("quote" not in event["payload"]["version"]["evidence"] for event in proofs)
        assert all("领航人阿禾" not in json.dumps(event["payload"], ensure_ascii=False) for event in proofs)

        # Explicitly choose a known ancestor, then sign the exact same selection again.
        previous_head = next(event for event in reversed(changes) if event["object_id"] == HEAD_PREFIX + slot)
        adopted = _network(
            origin,
            right_token,
            "POST",
            endpoint + "/push",
            {
                "change_id": "reset-setting-use-to-known-proof",
                "object_id": HEAD_PREFIX + slot,
                "parent_revision": previous_head["head_revision"],
                "payload": head_object(branch, slot, initial["id"]).model_dump(),
                "tombstone": False,
            },
        )
        assert adopted["status"] == "applied"
        assert not left.run(branch, origin, left_token)["pending"]
        repeated = confirm_use(client, route, "repeat-known-proof", parent=initial["id"], quote="领航人阿禾")
        assert repeated == left_version and left._state(branch)["pending"] == 1
        assert not left.run(branch, origin, left_token)["pending"]
        assert not right.run(branch, origin, right_token)["deferred"]
        assert record(receiver, route, repeated["id"])["status"] == "current"


def test_setting_use_desktop_retries_late_source_and_needs_exact_adopted_chapter(
    client, domain, settings, author_plan, tmp_path, monkeypatch
):
    route, branch = author_plan["route"], author_plan["branch_id"]
    saved = author_plan["first"]
    chapter_id = saved["id"]
    target, receiver = receiving_desktop(domain, branch, settings, tmp_path / "setting-use-desktop-late")
    with receiver, linked_cloud(domain, target, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        cloud_text = "门外响起铃声，" + TEXT
        private_text = "本机记录铜牌并关门。"
        domain.save(chapter_id, saved["revision"], cloud_text)
        target.save(chapter_id, saved["revision"], private_text)
        proof = confirm_use(client, route, "late-source-proof", content=cloud_text, quote="领航人阿禾")
        assert not left.run(branch, origin, left_token)["pending"]
        selected_source = proof["evidence"]["revision_id"]
        slot = slot_id(branch, chapter_id, proof["settings_version_id"], proof["item_id"])
        read_source = setting_use_desktop.source_revision
        with monkeypatch.context() as delayed:
            delayed.setattr(
                setting_use_desktop,
                "source_revision",
                lambda tx, branch, identifier: (
                    None if identifier == selected_source else read_source(tx, branch, identifier)
                ),
            )
            waiting = right.run(branch, origin, right_token)
            deferred = {item["object_id"]: item["reason"] for item in waiting["deferred"]}
            assert VERSION_PREFIX + proof["id"] in deferred and HEAD_PREFIX + slot in deferred
            assert receiver.get(f"{route}/{proof['id']}").status_code == 404

        state = right.run(branch, origin, right_token)
        assert not any(
            item["object_id"].startswith((VERSION_PREFIX, HEAD_PREFIX)) for item in state["deferred"]
        )
        historical = record(receiver, route, proof["id"])
        assert historical["status"] == "outdated" and historical["quote"] == "领航人阿禾"
        assert target.chapter(chapter_id)["content"] == private_text
        conflict = next(item for item in state["conflicts"] if item["object_id"] == f"chapter:{chapter_id}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        chosen = choose_current(
            right, branch, origin, right_token, conflict, compared, "adopt-source-of-proof"
        )
        assert not chosen["deferred"] and not chosen["conflicts"], chosen
        assert target.chapter(chapter_id)["content"] == cloud_text
        assert record(receiver, route, proof["id"])["status"] == "current"
