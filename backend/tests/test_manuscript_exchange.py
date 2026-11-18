from copy import deepcopy

from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.manuscript_exchange import ManuscriptPackage, export_manuscript, import_manuscript


def test_desktop_portable_manuscript_preserves_history_and_rejects_corruption(
    client, domain, story, tmp_path
):
    chapter_id = story["chapter_id"]
    domain.save(chapter_id, 0, "含😀与全角标点。")
    domain.save(chapter_id, 1, "只在门内响。")
    response = client.get(f"/api/branches/{story['branch_id']}/manuscript-package")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["revisions"]) == 3
    assert payload["revisions"][-1]["parent_id"] == payload["revisions"][-2]["id"]
    assert (
        client.post("/api/manuscript-packages/import", json=payload).json()["code"] == "manuscript_conflict"
    )

    damaged = deepcopy(payload)
    damaged["revisions"][1]["content"] = "任意内容"
    assert (
        client.post("/api/manuscript-packages/import", json=damaged).json()["code"] == "manuscript_integrity"
    )
    assert domain.chapter(chapter_id)["content"] == "只在门内响。"

    isolated = Domain(Database(tmp_path / "isolated.sqlite"))
    import_manuscript(isolated, ManuscriptPackage.model_validate(payload))
    assert export_manuscript(isolated, story["branch_id"]).model_dump() == payload


def test_exchange_preserves_frozen_inspection_as_read_only_evidence(client, domain, story, tmp_path):
    chapter = domain.save(story["chapter_id"], 0, "老师认出了铜铃。")
    branch = f"/api/branches/{story['branch_id']}"
    preview = client.get(branch + "/workflow-inspections").json()["preview"]
    recorded = client.post(branch + "/workflow-inspections", json={"basis_sha256": preview["basis_sha256"]})
    assert recorded.status_code == 201, recorded.text
    report = recorded.json()
    domain.save(story["chapter_id"], chapter["revision"], "老师尚未见到铜铃。")
    payload = client.get(branch + "/manuscript-package").json()
    document = payload["evidence"][0]
    assert document["id"] == report["id"]
    assert document["kind"] == "inspection"
    assert document["chapters"][0]["revision"] == chapter["revision"]
    assert client.get(branch + "/manuscript-evidence").json() == payload["evidence"]

    damaged = deepcopy(payload)
    damaged["evidence"][0]["chapters"][0]["sha256"] = "0" * 64
    assert (
        client.post("/api/manuscript-packages/import", json=damaged).json()["code"] == "manuscript_evidence"
    )
    isolated = Domain(Database(tmp_path / "with-evidence.sqlite"))
    import_manuscript(isolated, ManuscriptPackage.model_validate(payload))
    assert export_manuscript(isolated, story["branch_id"]).model_dump() == payload
    assert not isolated.db.all("SELECT * FROM artifacts WHERE kind='workflow_inspection'")
