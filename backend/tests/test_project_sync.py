import json

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from ainovel.cloud_sync import create_sync_app
from ainovel.db import Database, DomainError
from ainovel.desktop_sync import DesktopSync, Resolve
from ainovel.domain import Domain
from ainovel.manuscript_exchange import export_manuscript, import_manuscript
from ainovel.project_brief import ConfirmProject, confirm
from ainovel.project_portability import ProjectHistory


BRIEF = {
    "position": "追查被调包的铜铃",
    "track": "线索式悬疑",
    "genre_ids": ["xianxia"],
    "audience": "喜欢逐步揭开谜底的读者",
    "distinction": "只使用首章出现的证物",
    "length": "三卷十万字",
    "ending": "找到幕后调包人，并交出证物",
}


def test_formal_project_sync_uses_native_confirmation_and_preserves_cas_competitors(
    client, domain, story, tmp_path
):
    branch = story["branch_id"]
    another = Domain(Database(tmp_path / "another.sqlite"))
    import_manuscript(another, export_manuscript(domain, branch))
    origin = "http://127.0.0.1:8888"
    prefix = f"/api/sync/works/{branch}"
    local = f"/api/branches/{branch}/conception/project-brief"
    provisioning = "isolated-project-sync-provision-abcdefgh"
    with TestClient(create_sync_app(tmp_path / "cloud.sqlite", provisioning)) as cloud:

        def grant(device):
            response = cloud.post(
                prefix + "/devices",
                headers={"Authorization": f"Bearer {provisioning}"},
                json={"device_id": device},
            )
            assert response.status_code == 201
            return response.json()["token"]

        def transport(origin, token, method, route, payload):
            response = cloud.request(
                method, route, headers={"Authorization": f"Bearer {token}"}, json=payload
            )
            if not response.is_success:
                detail = response.json()
                raise DomainError(detail["code"], detail["message"], response.status_code)
            return response.json()

        left, right = DesktopSync(domain, transport), DesktopSync(another, transport)
        left_token, right_token = grant("desktop-left"), grant("desktop-right")
        left.enable(branch, origin, left_token)
        right.enable(branch, origin, right_token)
        first = client.put(
            local,
            json={
                "base_revision": 0,
                "idempotency_key": "left-first-project",
                "idea": "追查铜铃来历",
                "brief": BRIEF,
            },
        )
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]
        left.run(branch, origin, left_token)
        right.run(branch, origin, right_token)
        assert right.run(branch, origin, right_token)["deferred"] == []
        with another.db.connect() as tx:
            row = tx.execute("SELECT payload,basis FROM artifacts WHERE kind='project_brief'").fetchone()
            assert json.loads(row["payload"])["revision"] == 1
            assert json.loads(row["payload"])["candidate_id"] is None
            synced = json.loads(row["basis"])["sync_project_source"]
            assert synced["revision_id"] == first_id
            assert synced["device_id"] == "desktop-left"
            assert synced["source_change_id"] != synced["head_change_id"]

        left_version = client.put(
            local,
            json={
                "base_revision": 1,
                "idempotency_key": "left-next-project",
                "idea": "从旧铃匠查起",
                "brief": BRIEF,
            },
        ).json()
        right_version = confirm(
            another,
            branch,
            ConfirmProject(
                base_revision=1, idempotency_key="right-next-project", idea="从渡口船夫查起", brief=BRIEF
            ),
        )
        left.run(branch, origin, left_token)
        state = right.run(branch, origin, right_token)
        conflict = next(row for row in state["conflicts"] if row["object_id"] == f"project:{branch}")
        compared = right.compare(branch, origin, right_token, conflict["id"])
        assert compared["remote"]["base"]["payload"]["revision_id"] == first_id
        assert {row["idea"] for row in compared["project_versions"].values()} == {
            "追查铜铃来历",
            "从旧铃匠查起",
            "从渡口船夫查起",
        }
        resolved = right.resolve(
            branch,
            origin,
            right_token,
            conflict["id"],
            Resolve(
                origin=origin,
                token=right_token,
                choice="incoming",
                parent_revision=compared["remote"]["head_revision"],
                local_sha256=compared["local_sha256"],
                idempotency_key="resolve-differing-projects",
            ),
        )
        assert not resolved["conflicts"]
        left.run(branch, origin, left_token)
        applied = client.get(local).json()
        assert applied["idea"] == right_version["idea"] and applied["revision"] == 3
        with domain.db.connect() as tx:
            row = tx.execute("SELECT basis FROM artifacts WHERE id=?", (applied["id"],)).fetchone()
            basis = json.loads(row["basis"])
            assert basis["previous_project_id"] == left_version["id"]
            assert basis["sync_project_source"]["revision_id"] == right_version["id"]
            assert basis["sync_project_source"]["device_id"] == "desktop-right"
            assert tx.execute("SELECT COUNT(*) FROM artifacts WHERE kind='project_brief'").fetchone()[0] == 3
        left.run(branch, origin, left_token)
        assert client.get(local).json() == applied

        bad = {
            "format": "ainovel-manuscript-sync/1",
            "kind": "project-head",
            "branch_id": branch,
            "revision_id": "missing-source",
        }
        response = cloud.post(
            prefix + "/push",
            headers={"Authorization": f"Bearer {left_token}"},
            json={
                "change_id": "missing-project-source",
                "object_id": f"project:{branch}",
                "payload": bad,
                "parent_revision": None,
                "tombstone": False,
            },
        )
        assert response.status_code == 409 and response.json()["code"] == "sync_project"


def test_formal_project_history_survives_a_file_only_roundtrip(client, domain, story, tmp_path):
    branch = story["branch_id"]
    first = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=0,
            idempotency_key="portable-project-first",
            idea="从铜铃查起",
            brief=BRIEF,
        ),
    )
    head = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=1,
            idempotency_key="portable-project-next",
            idea="从渡口查起",
            brief=BRIEF,
        ),
    )
    pack = export_manuscript(domain, branch)
    assert pack.project_history.adopted_revision_id == head["id"]
    versions = pack.project_history.versions
    assert [(row.id, row.parent_id) for row in versions] == [(first["id"], None), (head["id"], first["id"])]
    alternate = versions[-1].model_copy(update={"id": "competing-origin", "idea": "从铃匠查起"})
    payload = pack.model_dump()
    payload["project_history"]["versions"].append(alternate.model_dump())
    history = ProjectHistory.model_validate(payload["project_history"])
    with pytest.raises(ValidationError, match="父修订"):
        ProjectHistory(
            adopted_revision_id=head["id"],
            versions=[versions[0], versions[1].model_copy(update={"parent_id": "missing-root"})],
        )

    restored = Domain(Database(tmp_path / "project-restored.sqlite"))
    import_manuscript(restored, type(pack).model_validate(payload))
    recovered = export_manuscript(restored, branch).project_history
    assert recovered.adopted_revision_id == history.adopted_revision_id
    assert {row.id: row for row in recovered.versions} == {row.id: row for row in history.versions}
    with restored.db.connect() as tx:
        row = tx.execute("SELECT payload,basis FROM artifacts WHERE kind='project_brief'").fetchone()
        assert json.loads(row["payload"])["idea"] == head["idea"]
        basis = json.loads(row["basis"])
        assert basis["manuscript_project_source"] == {"revision_id": head["id"]}
        assert "sync_project_source" not in basis
    local = confirm(
        restored,
        branch,
        ConfirmProject(
            base_revision=1,
            idempotency_key="new-after-package",
            idea="核对渡口账本",
            brief=BRIEF,
        ),
    )
    assert (
        next(
            row
            for row in export_manuscript(restored, branch).project_history.versions
            if row.id == local["id"]
        ).parent_id
        == head["id"]
    )
    assert (
        client.get(f"/api/branches/{branch}/manuscript-package").json()["project_history"]
        == pack.project_history.model_dump()
    )
