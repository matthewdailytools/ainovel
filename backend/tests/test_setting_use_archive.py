from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

from fastapi.testclient import TestClient
import pytest

from ainovel.api import create_app
from ainovel.manuscript_backup import BACKUP_IMPORT_PATH, _backup_chunks
from ainovel.manuscript_exchange import ManuscriptPackage
from ainovel.setting_use_portability import slot_id, version_id
from test_setting_use import QUOTE, TEXT, author_plan as author_plan_fixture, select, verified
from test_story_sync import choose_current, linked_cloud, paired

author_plan = author_plan_fixture


def receiver(settings, name):
    return TestClient(create_app(replace(settings, data_dir=settings.data_dir / name)))


def setup_receiver(target):
    target.headers["X-Ainovel-CSRF"] = verified(target.get("/api/session"))["csrf"]


def record(client, endpoint, key, *, item_id="pilot"):
    return verified(
        client.post(endpoint, json=select(verified(client.get(endpoint)), key) | {"item_id": item_id}), 201
    )


def download(client, setup):
    return verified(client.get(setup["request"] + "/manuscript-package"))


def resign(package, payload, *, branch_id=None):
    root = package["setting_use_history"]
    payload.pop("id", None)
    identifier = version_id(branch_id or package["branch"]["id"], payload)
    payload["id"] = identifier
    root["adopted_heads"] = {
        slot_id(
            package["branch"]["id"],
            payload["evidence"]["chapter_id"],
            payload["settings_version_id"],
            payload["item_id"],
        ): identifier
    }
    return identifier


@pytest.mark.parametrize("container", ["json", "backup"])
def test_transfer_keeps_frozen_sources_withdrawal_head_and_live_proof(
    client, settings, author_plan, container
):
    setup = author_plan
    first = record(client, setup["route"], "original-use")
    withdrawn = verified(
        client.post(
            setup["route"] + f"/{first['version']['id']}/withdraw", json={"idempotency_key": "withdraw-use"}
        ),
        201,
    )
    current = record(client, setup["other"], "another-volume-use", item_id="guard")
    package = download(client, setup)
    history = package["setting_use_history"]
    assert [row["version"]["id"] for row in history["versions"]] == [
        first["version"]["id"],
        withdrawn["version"]["id"],
        current["version"]["id"],
    ]
    assert set(history["adopted_heads"].values()) == {withdrawn["version"]["id"], current["version"]["id"]}
    assert all("quote" not in row["version"] for row in history["versions"])
    with receiver(settings, "new-" + container) as target:
        setup_receiver(target)
        if container == "backup":
            backup = client.get(setup["request"] + "/manuscript-backup")
            assert backup.status_code == 200, backup.text
            assert b'"setting_use_history.versions","list",null' in backup.content
            response = target.post(
                BACKUP_IMPORT_PATH,
                content=backup.content,
                headers={"Content-Type": "application/octet-stream"},
            )
        else:
            response = target.post("/api/manuscript-packages/import", json=package)
        verified(response, 201)
        assert download(target, setup)["setting_use_history"] == history
        view = verified(target.get(setup["route"]))
        assert view["options"][0]["head_id"] == withdrawn["version"]["id"]
        assert [row["status"] for row in view["records"]] == ["withdrawn", "superseded"]
        assert all(row["quote"] == QUOTE for row in view["records"])
        assert verified(target.get(setup["other"] + f"/{current['version']['id']}")) == current


def test_forked_history_retains_side_variant_but_never_adopts_source_only_same_text(
    client, settings, author_plan
):
    setup = author_plan
    saved = record(client, setup["route"], "known-original")
    package = download(client, setup)
    root = package["setting_use_history"]
    first = root["versions"][0]
    proof_source = first["version"]["evidence"]["revision_id"]
    source = next(row for row in package["revisions"] if row["id"] == proof_source)
    # A different original ID with exactly the same number and body is verifiable as history only.
    package["source_revisions"] = [source | {"id": "another-saved-proof-address"}]
    competing = deepcopy(first)
    old_version = competing["version"]
    old_version["parent_id"] = saved["version"]["id"]
    old_version["evidence"]["revision_id"] = "another-saved-proof-address"
    old_version.pop("id")
    other_id = version_id(setup["branch_id"], old_version)
    old_version["id"] = other_id
    competing["source_device"] = "mirror-writer"
    root["versions"].append(competing)
    with receiver(settings, "side-only") as target:
        setup_receiver(target)
        verified(target.post("/api/manuscript-packages/import", json=package), 201)
        view = verified(target.get(setup["route"]))
        assert view["options"][0]["head_id"] == saved["version"]["id"]
        assert (
            view["records"][0]["version"]["id"] == other_id and view["records"][0]["status"] == "superseded"
        )
        assert download(target, setup)["setting_use_history"] == root

    root["adopted_heads"] = {next(iter(root["adopted_heads"])): other_id}
    with receiver(settings, "explicit-source-only") as target:
        setup_receiver(target)
        verified(target.post("/api/manuscript-packages/import", json=package), 201)
        view = verified(target.get(setup["route"]))
        assert view["options"][0]["head_id"] == other_id
        assert view["records"][0]["status"] == "outdated"
        assert view["records"][0]["quote"] == QUOTE


def test_both_containers_reject_tampered_dependencies_before_import(client, settings, author_plan):
    setup = author_plan
    record(client, setup["route"], "original-proof")
    original = download(client, setup)
    with receiver(settings, "rejected") as target:
        setup_receiver(target)
        existing = verified(target.post("/api/novels", json={"title": "收到前的独立原稿"}), 201)
        for fault in ("setting", "outline", "revision", "offset", "foreign", "slot", "item"):
            package = deepcopy(original)
            history = package["setting_use_history"]
            version = history["versions"][0]["version"]
            if fault == "setting":
                version["settings_version_id"] = "missing-setting-proof"
            elif fault == "outline":
                version["outline_version_id"] = "missing-outline-proof"
            elif fault == "revision":
                version["evidence"]["revision_id"] = "unknown-source-proof"
            elif fault == "offset":
                version["evidence"]["start"] = 2
            elif fault == "item":
                version["item_id"] = "guard"
            sign = resign(package, version, branch_id="another-work" if fault == "foreign" else None)
            if fault == "slot":
                history["adopted_heads"] = {"0" * 64: sign}
            denied = target.post("/api/manuscript-packages/import", json=package)
            assert denied.status_code == 409 and denied.json()["code"] == "manuscript_setting_use", (
                denied.text
            )
            if fault in {"foreign", "slot", "offset"}:
                archive = b"".join(_backup_chunks(ManuscriptPackage.model_validate(package)))
                refused = target.post(
                    BACKUP_IMPORT_PATH, content=archive, headers={"Content-Type": "application/octet-stream"}
                )
                assert refused.status_code == 409 and refused.json()["code"] == "manuscript_setting_use"
            assert [row["id"] for row in verified(target.get("/api/novels"))] == [existing["id"]]
        assert verified(target.post("/api/manuscript-packages/import", json=original), 201)


def test_withdraw_history_cannot_rewrite_the_parent_selection(client, settings, author_plan):
    setup = author_plan
    original = record(client, setup["route"], "source-of-withdrawal")
    verified(
        client.post(
            setup["route"] + f"/{original['version']['id']}/withdraw",
            json={"idempotency_key": "withdraw-original-proof"},
        ),
        201,
    )
    package = download(client, setup)
    withdrawal = package["setting_use_history"]["versions"][-1]["version"]
    # The alternate range is valid in the same chapter, but a withdrawal may not change the old proof.
    withdrawal["evidence"].update(start=0, end=1, quote_sha256=sha256("在".encode()).hexdigest())
    resign(package, withdrawal)
    with receiver(settings, "broken-withdrawal") as target:
        setup_receiver(target)
        denied = target.post("/api/manuscript-packages/import", json=package)
        assert denied.status_code == 409 and denied.json()["code"] == "manuscript_setting_use"
        assert "原始依据" in denied.json()["message"]
        assert verified(target.get("/api/novels")) == []


def test_explicit_chapter_adoption_survives_both_backups_and_story_aliases_cannot_promote_proof(
    client, domain, settings, tmp_path, author_plan
):
    setup = author_plan
    branch, chapter = setup["branch_id"], setup["first"]["id"]
    route = setup["route"]
    target = paired(domain, branch, tmp_path / "proven-adoption" / "manuscripts.sqlite")
    with linked_cloud(domain, target, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        body = TEXT + "一同离岸。"
        source_chapter = domain.save(chapter, setup["first"]["revision"], body)
        target.save(chapter, setup["first"]["revision"], "本机把铜牌交给守门人。")
        confirmed = record(client, route, "with-exact-chapter-source")
        proof = confirmed["version"]
        identifier = proof["id"]
        source_id = proof["evidence"]["revision_id"]
        left.run(branch, origin, left_token)
        pending = right.run(branch, origin, right_token)
        conflict = next(row for row in pending["conflicts"] if row["object_id"] == f"chapter:{chapter}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        result = choose_current(
            right, branch, origin, right_token, conflict, compared, "pick-exact-proof-source"
        )
        assert not result["conflicts"] and not result["deferred"], result

        with receiver(settings, "proven-adoption") as desktop:
            setup_receiver(desktop)
            local_id = verified(desktop.get(route))["basis"]["chapter_revision_id"]
            assert local_id != source_id
            active = verified(desktop.get(route + "/" + identifier))
            assert active["status"] == "current" and active["quote"] == QUOTE
            package = download(desktop, setup)
            assert "story_history" not in package
            assert package["setting_use_history"]["native_bindings"] == {source_id: local_id}
            backup = desktop.get(setup["request"] + "/manuscript-backup")
            assert backup.status_code == 200, backup.text
            assert b'"setting_use_history.native_bindings","value"' in backup.content
            for form in ("json", "framed"):
                with receiver(settings, "alias-" + form) as restored:
                    setup_receiver(restored)
                    response = (
                        restored.post("/api/manuscript-packages/import", json=package)
                        if form == "json"
                        else restored.post(
                            BACKUP_IMPORT_PATH,
                            content=backup.content,
                            headers={"Content-Type": "application/octet-stream"},
                        )
                    )
                    verified(response, 201)
                    assert verified(restored.get(route + "/" + identifier)) == active
                    assert verified(restored.get(route))["options"][0]["head_id"] == identifier
                    assert download(restored, setup)["setting_use_history"] == package["setting_use_history"]

            with receiver(settings, "damaged-adoption") as restored:
                setup_receiver(restored)
                unrelated = verified(restored.post("/api/novels", json={"title": "原稿保持"}), 201)
                foreign_chapter = next(
                    row["id"] for row in package["revisions"] if row["chapter_id"] == setup["second"]["id"]
                )
                incompatible = next(
                    row["id"]
                    for row in package["revisions"]
                    if row["chapter_id"] == chapter and row["content"] != body
                )
                broken = []
                for invalid_target in ("missing-native", foreign_chapter, incompatible):
                    invalid = deepcopy(package)
                    invalid["setting_use_history"]["native_bindings"][source_id] = invalid_target
                    broken.append(invalid)
                missing_source = deepcopy(package)
                missing_source["source_revisions"] = [
                    row for row in missing_source["source_revisions"] if row["id"] != source_id
                ]
                broken.append(missing_source)
                for invalid in broken:
                    refused = restored.post("/api/manuscript-packages/import", json=invalid)
                    assert refused.status_code == 409 and refused.json()["code"] == "manuscript_setting_use"
                    assert [row["id"] for row in verified(restored.get("/api/novels"))] == [unrelated["id"]]

            graph = verified(client.get(setup["request"] + "/story-graph"))
            story = verified(
                client.post(
                    setup["request"] + "/story-records",
                    json={
                        "base_story_revision": graph["story_revision"],
                        "record": {
                            "kind": "character",
                            "title": "另有证据的领航人",
                            "sources": [
                                {
                                    "chapter_id": chapter,
                                    "revision": source_chapter["revision"],
                                    "start": 1,
                                    "end": 1 + len(QUOTE.encode("utf-16-le")) // 2,
                                    "quote": QUOTE,
                                }
                            ],
                        },
                    },
                ),
                201,
            )["records"][0]["id"]
            left.run(branch, origin, left_token)
            state = right.run(branch, origin, right_token)
            assert not state["conflicts"] and not state["deferred"], state
            base_revision = target.chapter(chapter)["revision"]
            target.save(chapter, base_revision, "分别记录试写和恢复。")
            target.save(chapter, base_revision + 1, body)
            live = verified(desktop.get(route))["basis"]["chapter_revision_id"]
            assert live != local_id
            assert verified(desktop.get(route + "/" + identifier))["status"] == "outdated"
            story_pack = download(desktop, setup)
            addresses = story_pack["story_history"]["native_history"][story][0]["evidence"]["record"]
            assert addresses == [local_id]
            addresses[0] = live
            for include_binding in (True, False):
                isolated = deepcopy(story_pack)
                if not include_binding:
                    isolated["setting_use_history"].pop("native_bindings")
                with receiver(settings, f"story-metadata-{include_binding}") as restored:
                    setup_receiver(restored)
                    verified(restored.post("/api/manuscript-packages/import", json=isolated), 201)
                    old = verified(restored.get(route + "/" + identifier))
                    assert old["status"] == "outdated" and old["quote"] == QUOTE


def test_readopting_a_cloud_source_already_in_native_history_preserves_proof(
    client, domain, settings, tmp_path, author_plan
):
    setup = author_plan
    branch, chapter = setup["branch_id"], setup["first"]["id"]
    route = setup["route"]
    confirmed = record(client, route, "proof-of-shared-original")
    source_id = confirmed["version"]["evidence"]["revision_id"]
    proof_url = route + "/" + confirmed["version"]["id"]
    target = paired(domain, branch, tmp_path / "known-source-workspace" / "manuscripts.sqlite")
    with linked_cloud(domain, target, branch, tmp_path) as linked:
        left, right, origin, left_token, right_token, *_ = linked
        with receiver(settings, "known-source-workspace") as desktop:
            setup_receiver(desktop)
            assert verified(desktop.get(proof_url))["status"] == "current"
            target.save(chapter, setup["first"]["revision"], "本机把铜牌交给看门人。")
            assert verified(desktop.get(proof_url))["status"] == "outdated"
            # A cloud title edit advances the competing chapter's head while retaining its saved source.
            verified(client.patch(f"/api/chapters/{chapter}", json={"title": "云端为原章换标题"}))
            left.run(branch, origin, left_token)
            pending = right.run(branch, origin, right_token)
            conflict = next(row for row in pending["conflicts"] if row["object_id"] == f"chapter:{chapter}")
            compared = right.compare(branch, origin, right_token, conflict["id"])
            assert compared["remote"]["current"]["payload"]["head"]["id"] == source_id
            chosen = choose_current(
                right, branch, origin, right_token, conflict, compared, "choose-old-source"
            )
            assert not chosen["conflicts"] and not chosen["deferred"], chosen
            current = verified(desktop.get(route))["basis"]
            native_id = current["chapter_revision_id"]
            assert current["chapter_revision"] == setup["first"]["revision"] + 2
            assert native_id != source_id and target.chapter(chapter)["content"] == TEXT
            assert verified(desktop.get(proof_url))["status"] == "current"

            package = download(desktop, setup)
            assert package["setting_use_history"]["native_bindings"] == {source_id: native_id}
            assert {source_id, native_id} <= {row["id"] for row in package["revisions"]}
            backup = desktop.get(setup["request"] + "/manuscript-backup")
            assert backup.status_code == 200, backup.text
            for form in ("native-source-json", "native-source-backup", "no-declared-adoption"):
                with receiver(settings, form) as restored:
                    setup_receiver(restored)
                    isolated = deepcopy(package)
                    if form == "no-declared-adoption":
                        isolated["setting_use_history"].pop("native_bindings")
                    response = (
                        restored.post(
                            BACKUP_IMPORT_PATH,
                            content=backup.content,
                            headers={"Content-Type": "application/octet-stream"},
                        )
                        if form == "native-source-backup"
                        else restored.post("/api/manuscript-packages/import", json=isolated)
                    )
                    verified(response, 201)
                    result = verified(restored.get(proof_url))
                    assert result["status"] == ("outdated" if form == "no-declared-adoption" else "current")
                    assert result["quote"] == QUOTE

            target.save(chapter, current["chapter_revision"], "作者另起同一段的稿。")
            target.save(chapter, current["chapter_revision"] + 1, TEXT)
            assert verified(desktop.get(proof_url))["status"] == "outdated"
