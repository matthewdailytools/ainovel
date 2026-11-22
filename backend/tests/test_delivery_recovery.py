"""Formal and draft deliveries survive a real local backup and restore."""

from pathlib import Path

from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.backups import create_backup, restore_backup, verify_backup

from test_delivery import finalize, path, preview, reviewed, setup_book


def test_backup_restores_historical_delivery_and_current_preview(domain, settings, story):
    first, second = setup_book(domain, story)
    endpoint = path(story)
    app = create_app(settings)
    with TestClient(app) as client:
        client.headers["X-Ainovel-CSRF"] = client.get("/api/session").json()["csrf"]
        state = client.get(endpoint).json()
        original = preview(client, endpoint, reviewed(state, first, second, "archive-original"))
        finished = finalize(client, endpoint, original, "confirm-archive-original")
        assert finished.status_code == 200, finished.text
        old_path = f"{endpoint}/packages/{original['id']}"
        original_archive = client.get(f"{old_path}/download")
        assert original_archive.status_code == 200, original_archive.text

        domain.save(second["id"], second["revision"], "她重新读完回信，将它留在桌上。")
        current_state = client.get(endpoint).json()
        draft = preview(
            client,
            endpoint,
            {
                "base_sha256": current_state["basis_sha256"],
                "idempotency_key": "archive-unchecked-preview",
                "synopsis": "一封回信改变了一次迟来的见面。",
                "sample_start_chapter_id": first["id"],
                "sample_end_chapter_id": first["id"],
            },
        )
        assert draft["status"] == "draft"
        snapshot = create_backup(settings, domain.db)
        saved = Path(snapshot["path"])
        assert verify_backup(saved)["integrity"] == "ok"

    new_chapter = domain.create_chapter(story["branch_id"], "备份外章节")
    domain.save(new_chapter["id"], new_chapter["revision"], "在备份之后留下的新内容。")
    restore_backup(settings, domain.db, saved)

    with TestClient(create_app(settings)) as client:
        restored = client.get(endpoint).json()
        assert restored["chapters"] == current_state["chapters"]
        historical = client.get(old_path).json()
        assert historical["status"] == "outdated" and historical["finished"]
        assert client.get(f"{old_path}/download").content == original_archive.content
        assert client.get(f"{endpoint}/packages/{draft['id']}").json() == draft
        raw = client.get(f"/api/branches/{story['branch_id']}/export?format=txt")
        assert "将它留在桌上" in raw.text
        assert "备份外章节" not in raw.text
