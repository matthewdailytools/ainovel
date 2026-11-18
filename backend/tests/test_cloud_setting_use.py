import httpx
import pytest

from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, _network
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.setting_use_portability import (
    SettingUseVersion,
    head_object,
    version_id,
    version_object,
    version_slot,
)
from test_setting_use import author_plan as author_plan
from test_setting_use import select, verified
from test_workflow_cloud import _grant, loopback as loopback


def test_cloud_checks_setting_use_dependencies_and_preserves_distinct_head_history(
    client, domain, author_plan, loopback, tmp_path
):
    work = author_plan["branch_id"]
    origin, _ = loopback
    token = _grant(loopback, work, "author-proof")
    peer = _grant(loopback, work, "second-proof")
    baseline = export_manuscript(domain, work)
    route = author_plan["route"]
    basis = verified(client.get(route))
    proof = SettingUseVersion.model_validate(
        verified(client.post(route, json=select(basis, "prove-usage")), 201)["version"]
    )
    slot = version_slot(work, proof)

    def push(identifier, model, change, parent=None, credential=token):
        with httpx.Client(trust_env=False) as sender:
            response = sender.post(
                f"{origin}/api/sync/works/{work}/push",
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "change_id": change,
                    "object_id": identifier,
                    "parent_revision": parent,
                    "payload": model.model_dump(),
                    "tombstone": False,
                },
            )
        result = response.json()
        if not response.is_success:
            raise DomainError(result["code"], result["message"], response.status_code)
        return result

    name = f"setting-use-version:{proof.id}"
    with pytest.raises(DomainError) as absent:
        push(name, version_object(work, proof), "before-chapters")
    assert absent.value.code == "setting_use_dependency"
    with pytest.raises(DomainError) as unreceived:
        push(f"setting-use:{slot}", head_object(work, slot, proof.id), "before-proof")
    assert unreceived.value.code == "setting_use_dependency"

    # Use a separate sender with only the formal source objects to exercise late dependencies.
    sources = Domain(Database(tmp_path / "setting-use-dependencies.sqlite"))
    import_manuscript(sources, baseline)
    sync = DesktopSync(sources)
    sync.enable(work, origin, token)
    sync.run(work, origin, token)
    invalid = proof.model_dump(exclude={"id"})
    invalid["evidence"]["quote_sha256"] = "0" * 64
    invalid = SettingUseVersion(id=version_id(work, invalid), **invalid)
    with pytest.raises(DomainError) as tampered:
        push(f"setting-use-version:{invalid.id}", version_object(work, invalid), "bad-quote")
    assert tampered.value.code == "setting_use_integrity"
    assert push(name, version_object(work, proof), "original-proof")["status"] == "applied"
    with pytest.raises(DomainError) as wrong_slot:
        push(f"setting-use:{'f' * 64}", head_object(work, "f" * 64, proof.id), "wrong-slot")
    assert wrong_slot.value.code == "setting_use_integrity"

    head = push(f"setting-use:{slot}", head_object(work, slot, proof.id), "original-head")
    assert head["status"] == "applied"
    withdrawn = SettingUseVersion.model_validate(
        verified(client.post(route + f"/{proof.id}/withdraw", json={"idempotency_key": "stop-proof"}), 201)[
            "version"
        ]
    )
    assert (
        push(f"setting-use-version:{withdrawn.id}", version_object(work, withdrawn), "old-proof-withdrawn")[
            "status"
        ]
        == "applied"
    )
    updated = push(
        f"setting-use:{slot}",
        head_object(work, slot, withdrawn.id),
        "after-withdrawal",
        head["head_revision"],
    )
    stale = push(
        f"setting-use:{slot}", head_object(work, slot, proof.id), "old-peer", head["head_revision"], peer
    )
    assert stale["status"] == "conflict" and stale["head_revision"] == updated["head_revision"]
    original = _network(origin, peer, "GET", f"/api/sync/works/{work}/changes/original-proof")
    assert original["payload"]["version"] == proof.model_dump()
