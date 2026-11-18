from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.chapter_adoption import remember_chapter_adoption
from ainovel.db import DomainError
from ainovel.desktop_sync import DesktopSync
from ainovel.manuscript_exchange import export_manuscript, native_revision
from ainovel.setting_use_portability import SettingUseVersion, version_object
from ainovel.workflow_archive import ArchivedInspection, validate_workflow_object, workflow_object
from ainovel.workflow_inspection import _item, _sha
from test_outline_workspace import put_and_confirm
from test_setting_use import QUOTE, TEXT, select, verified
from test_setting_use import author_plan as author_plan
from test_workflow_inspection import author, item, read, report
from test_workflow_cloud import _grant, loopback as loopback


def checks(view):
    return {entry["check_id"]: entry["basis_sha256"] for entry in view["items"]}


def test_author_setting_use_changes_only_check_08_in_affected_scopes(client, author_plan):
    setup = author_plan
    first, second = setup["first"], setup["second"]
    route = setup["request"] + "/workflow-inspections"
    scopes = [
        ("chapter", first["id"]),
        ("chapter", second["id"]),
        ("volume", "v1"),
        ("volume", "v2"),
        ("book", None),
    ]

    def previews():
        return {
            (scope, identifier): read(
                client, route, scope=scope, **({"scope_id": identifier} if identifier else {})
            )["preview"]
            for scope, identifier in scopes
        }

    before = previews()
    saved = verified(
        client.post(setup["route"], json=select(verified(client.get(setup["route"])), "pilot-use")), 201
    )
    proof = saved["version"]
    after = previews()
    affected = {("chapter", first["id"]), ("volume", "v1"), ("book", None)}
    for scope in scopes:
        changed = {key for key, value in checks(before[scope]).items() if value != checks(after[scope])[key]}
        assert changed == ({"08"} if scope in affected else set())
        setting_uses = item(after[scope], "08")["basis"].get("author_setting_uses")
        if scope not in affected:
            assert setting_uses is None
            continue
        assert setting_uses == [
            {
                "version_id": proof["id"],
                "outline_version_id": proof["outline_version_id"],
                "settings_version_id": proof["settings_version_id"],
                "item_id": proof["item_id"],
                "title": "领航人阿禾",
                "revision": first["revision"],
                "chapter_sha256": proof["chapter_sha256"],
                "evidence": proof["evidence"],
            }
        ]
        assert (
            item(after[scope], "08")["basis"]["author_setting_use_outline_id"] == proof["outline_version_id"]
        )

    frozen = report(
        client, route, after[("chapter", first["id"])], author_reviews=[author("08", first, "green")]
    )
    assert item(frozen, "08")["current_status"] == "green"
    source = f"{route}/{frozen['id']}/source-context/08"

    def original():
        addresses = read(client, source)["entries"]
        evidence = next(
            address for address in addresses if address["label"] == "作者确认设定使用 · 领航人阿禾"
        )
        return read(client, source, source_index=evidence["index"])["context"]

    initial = original()
    assert (initial["revision_id"], initial["quote"]) == (proof["evidence"]["revision_id"], QUOTE)
    verified(
        client.post(
            setup["route"] + f"/{proof['id']}/withdraw", json={"idempotency_key": "withdraw-pilot-use"}
        ),
        201,
    )
    withdrawn = previews()
    assert {key: checks(value) for key, value in withdrawn.items()} == {
        key: checks(value) for key, value in before.items()
    }
    old = read(client, f"{route}/{frozen['id']}")
    assert not old["current"]
    assert [entry["check_id"] for entry in old["items"] if entry["current_status"] != entry["status"]] == [
        "08"
    ]
    assert original() == initial
    expired = client.post(
        route, json={"scope": "chapter", "scope_id": first["id"], "basis_sha256": frozen["basis_sha256"]}
    )
    assert expired.status_code == 409 and expired.json()["code"] == "inspection_changed"


def test_inspection_with_adopted_revision_alias_needs_original_source_to_archive(
    client, domain, settings, tmp_path, author_plan
):
    setup = author_plan
    branch, first = setup["branch_id"], setup["first"]
    route = setup["request"] + "/workflow-inspections"
    saved = verified(
        client.post(setup["route"], json=select(verified(client.get(setup["route"])), "original-pilot-use")),
        201,
    )
    origin = saved["version"]["evidence"]["revision_id"]
    edited = domain.save(first["id"], first["revision"], TEXT + "另有一封信。")
    newest = domain.save(first["id"], edited["revision"], TEXT)
    with domain.db.connect(True) as tx:
        native_id = tx.execute(
            "SELECT id FROM revisions WHERE chapter_id=? AND revision=?", (first["id"], newest["revision"])
        ).fetchone()[0]
        assert native_id != origin
        remember_chapter_adoption(tx, branch, native_revision(tx, branch, origin))

    frozen = report(client, route, read(client, route)["preview"])
    address = f"{route}/{frozen['id']}/source-context/08"
    old_source = read(client, address, source_index=0)["context"]
    assert old_source["address"]["revision"] == first["revision"]
    assert (old_source["revision_id"], old_source["quote"]) == (origin, QUOTE)
    package = export_manuscript(domain, branch)
    archived = next(row for row in package.workflow_history.inspections if row.id == frozen["id"])
    original = {row.id: row for row in package.revisions}
    authorities = {
        "setting_use": {
            entry.version.id: entry.version for entry in package.setting_use_history.versions
        }.get,
        "settings": {version.id: version for version in package.settings_history.versions}.get,
    }
    assert (
        next(binding for binding in archived.chapter_bindings if binding.id == first["id"]).revision_id
        == native_id
    )
    with pytest.raises(DomainError, match=origin) as missing:
        validate_workflow_object(
            archived,
            branch,
            {key: value for key, value in original.items() if key != origin}.get,
            lambda _: None,
            **authorities,
        )
    assert missing.value.code == "sync_workflow_dependency"
    assert validate_workflow_object(archived, branch, original.get, lambda _: None, **authorities) == archived

    with TestClient(
        create_app(replace(settings, data_dir=tmp_path / "restore-setting-use-inspection"))
    ) as restored:
        restored.headers["X-Ainovel-CSRF"] = restored.get("/api/session").json()["csrf"]
        verified(restored.post("/api/manuscript-packages/import", json=package.model_dump()), 201)
        assert read(restored, address, source_index=0)["context"] == old_source


def test_two_distinct_setting_use_proofs_at_the_same_span_both_open(client, author_plan):
    setup = author_plan
    route, first = setup["route"], setup["first"]
    plan = deepcopy(setup["plan"])
    plan["volumes"][0]["units"][0]["chapters"][0]["setting_ids"].append("guard")
    put_and_confirm(
        client,
        setup["request"] + "/outline-workspace",
        {
            "base_revision": setup["outline"]["revision"],
            "plan": plan,
            "confirmation_id": setup["outline"]["setting_basis"]["confirmation_id"],
        },
    )
    view = verified(client.get(route))
    for identifier in ("pilot", "guard"):
        verified(client.post(route, json=select(view, f"proof-{identifier}") | {"item_id": identifier}), 201)
    inspection = setup["request"] + "/workflow-inspections"
    frozen = report(
        client, inspection, read(client, inspection, scope="chapter", scope_id=first["id"])["preview"]
    )
    source = f"{inspection}/{frozen['id']}/source-context/08"
    addresses = read(client, source)["entries"]
    assert {row["label"] for row in addresses} == {
        "作者确认设定使用 · 领航人阿禾",
        "作者确认设定使用 · 守门人",
    }
    assert [read(client, source, source_index=row["index"])["context"]["quote"] for row in addresses] == [
        QUOTE,
        QUOTE,
    ]


def forged_inspection(entry, **fields):
    data = entry.model_dump()
    data["id"] = "forged-" + "-".join(fields)
    payload = data["report"]
    check = item(payload, "08")
    check["basis"]["author_setting_uses"][0].update(fields)
    check["basis_sha256"] = _item(
        "08", check["summary"], check["basis"], manifest=payload["chapters"], scope=payload["scope"]
    )["basis_sha256"]
    payload["basis_sha256"] = _sha(
        [payload["scope"], payload["scope_id"], [row["basis_sha256"] for row in payload["items"]]]
    )
    return ArchivedInspection.model_validate(data)


def test_portable_inspection_requires_confirmed_proof_and_title_before_cloud_or_import(
    client, domain, settings, tmp_path, author_plan, loopback
):
    setup = author_plan
    branch = setup["branch_id"]
    sync_route = f"/api/sync/works/{branch}"
    server, _ = loopback
    token = _grant(loopback, branch, "inspection-setting-use")
    desktop = DesktopSync(domain)
    desktop.enable(branch, server, token)
    assert desktop.run(branch, server, token)["pending"] == 0

    saved = verified(
        client.post(setup["route"], json=select(verified(client.get(setup["route"])), "proof-for-report")),
        201,
    )
    proof = SettingUseVersion.model_validate(saved["version"])
    inspection = setup["request"] + "/workflow-inspections"
    frozen = report(client, inspection, read(client, inspection)["preview"])
    package = export_manuscript(domain, branch)
    archived = next(row for row in package.workflow_history.inspections if row.id == frozen["id"])

    def push(object_id, payload, change_id):
        with httpx.Client(trust_env=False) as request:
            response = request.post(
                server + sync_route + "/push",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "change_id": change_id,
                    "object_id": object_id,
                    "parent_revision": None,
                    "payload": payload,
                    "tombstone": False,
                },
            )
        value = response.json()
        if not response.is_success:
            raise DomainError(value["code"], value["message"], response.status_code)
        return value

    def publish(entry, change_id):
        return push(f"workflow-inspection:{entry.id}", workflow_object(branch, entry), change_id)

    with pytest.raises(DomainError, match=proof.id) as missing:
        publish(archived, "report-waits-proof")
    assert missing.value.code == "sync_workflow_dependency"
    push(
        f"setting-use-version:{proof.id}",
        version_object(branch, proof).model_dump(),
        "confirmed-proof-arrived",
    )
    assert publish(archived, "report-waits-proof")["status"] == "applied"
    with pytest.raises(DomainError, match="不一致") as wrong_title:
        publish(forged_inspection(archived, title="伪造标签"), "false-title")
    assert wrong_title.value.code == "sync_workflow"
    with pytest.raises(DomainError, match="缺设定使用原修订"):
        publish(forged_inspection(archived, version_id="0" * 64), "invented-proof")

    with TestClient(create_app(replace(settings, data_dir=tmp_path / "proof-required-import"))) as restored:
        restored.headers["X-Ainovel-CSRF"] = restored.get("/api/session").json()["csrf"]
        data = package.model_dump()
        original_history = data.pop("setting_use_history")
        response = restored.post("/api/manuscript-packages/import", json=data)
        assert response.status_code == 409 and response.json()["code"] == "manuscript_workflow"
        assert restored.get("/api/novels").json() == []
        good = data | {"setting_use_history": original_history}
        forged = deepcopy(good)
        forged["workflow_history"]["inspections"] = [
            forged_inspection(archived, item_id="guard").model_dump()
        ]
        response = restored.post("/api/manuscript-packages/import", json=forged)
        assert response.status_code == 409 and response.json()["code"] == "manuscript_workflow"
        verified(restored.post("/api/manuscript-packages/import", json=good), 201)
        returned = read(restored, f"{inspection}/{frozen['id']}/source-context/08", source_index=0)["context"]
        assert returned["revision_id"] == proof.evidence.revision_id and returned["quote"] == QUOTE
