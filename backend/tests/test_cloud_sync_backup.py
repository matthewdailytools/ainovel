from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
import shutil
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ainovel import cloud_sync_backup as backup
from ainovel.cloud_attachments import CHUNK_BYTES
from ainovel.cloud_sync import Push, SyncStore, create_sync_app
from ainovel.cloud_sync_storage import StorageProofError
from test_cloud_sync import ADMIN, WORK, grant, parent, push


def test_online_backup_includes_committed_wal_and_recovers_author_history_without_any_old_device_token(
    tmp_path,
):
    live = tmp_path / "live.sqlite"
    archive = tmp_path / "archive.sqlite"
    restored = tmp_path / "restored.sqlite"
    provisioning = ADMIN["Authorization"].split(" ", 1)[1]
    with TestClient(create_sync_app(live, provisioning)) as source, closing(sqlite3.connect(live)) as reader:
        store = SyncStore(live)
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM sync_changes").fetchone() == (0,)
        desktop, peer = grant(source, "desk"), grant(source, "peer")
        foreign_work = "/api/sync/works/another"
        foreign = grant(source, "desk", foreign_work)
        original = parent("first-draft", payload={"text": "第一份作者稿。"})
        first = push(source, desktop, original)
        head = push(source, peer, parent("peer-adopted", first["change_id"], {"text": "同一章的采用稿。"}))
        conflict = push(
            source, desktop, parent("desk-competing", first["change_id"], {"text": "保留分歧稿。"})
        )
        receipt_path = f"{WORK}/receipts/{first['change_id']}"
        compare_path = f"{WORK}/conflicts/{conflict['change_id']}"
        compared = source.get(compare_path, headers=peer).json()
        assert compared["current"] == head
        material = b"a" * CHUNK_BYTES + "已选择的附件。".encode()
        digest = sha256(material).hexdigest()
        attachments = WORK + "/attachments"
        file_path = f"{attachments}/{digest}"
        assert (
            source.post(
                attachments, headers=peer, json={"name": "资料.txt", "size": len(material), "sha256": digest}
            ).status_code
            == 200
        )
        for slot, content in enumerate((material[:CHUNK_BYTES], material[CHUNK_BYTES:])):
            assert (
                source.post(
                    f"{file_path}/parts/{slot}",
                    headers={**peer, "Content-Type": "application/octet-stream"},
                    content=content,
                ).status_code
                == 204
            )
        assert source.post(file_path + "/seal", headers=peer).is_success
        assert live.with_name("live.sqlite-wal").stat().st_size > 0
        assert reader.execute("SELECT count(*) FROM sync_changes").fetchone() == (0,)

        # Hold a separate writing transaction on the real WAL database while taking the copy.
        change = Push.model_validate(
            parent("future-commit", head["change_id"], {"text": "备份开始时未提交。"})
        )
        with ThreadPoolExecutor(max_workers=1) as worker:
            with store.transaction(write=True) as writer:
                store._append(
                    writer,
                    "bell",
                    "peer",
                    change,
                    "fixture-request-digest",
                    json.dumps(change.payload),
                    change.change_id,
                    "applied",
                )
                created = worker.submit(backup.create_backup, live, archive).result(timeout=12)
        assert source.get(WORK + "/pull", headers=peer).json()["cursor"] == 4
        assert source.get(receipt_path, headers=desktop).json() == first
        assert source.delete(WORK + "/devices/desk", headers=ADMIN).status_code == 204
        assert backup.verify_backup(archive, created["sha256"]) == created
        assert created["works"] == 2
        assert all(
            not archive.with_name(archive.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
        )
        with closing(sqlite3.connect(archive)) as copy, closing(sqlite3.connect(live)) as current:
            desensitized = dict(copy.execute("SELECT id,token_sha256 FROM sync_devices WHERE work_id='bell'"))
            old = dict(current.execute("SELECT id,token_sha256 FROM sync_devices WHERE work_id='bell'"))
            assert desensitized.keys() == old.keys() and all(desensitized[key] != old[key] for key in old)
            assert copy.execute("SELECT sum(active) FROM sync_devices").fetchone() == (0,)

        result = backup.restore_backup(archive, restored, expected_sha256=created["sha256"])
        assert result["works"] == 2 and result["sha256"] != created["sha256"]
        with TestClient(create_sync_app(restored, provisioning)) as recovered:
            for old_token in (desktop, peer, foreign):
                assert recovered.get(WORK + "/pull", headers=old_token).status_code == 401
                assert recovered.delete(WORK + "/device-session", headers=old_token).status_code == 401
            assert recovered.get(foreign_work + "/pull", headers=foreign).status_code == 401
            new_desktop = grant(recovered, "desk")
            pull = recovered.get(WORK + "/pull", headers=new_desktop).json()
            assert pull["cursor"] == 3
            assert pull["changes"] == [first, head, conflict]
            assert recovered.get(WORK + "/pull?after=4", headers=new_desktop).status_code == 409
            assert recovered.get(compare_path, headers=new_desktop).json() == compared
            assert recovered.get(receipt_path, headers=new_desktop).json() == first
            assert push(recovered, new_desktop, original) == first
            assert recovered.get(file_path + "/content", headers=new_desktop).content == material
            assert recovered.get(foreign_work + "/pull", headers=new_desktop).status_code == 401
        assert source.get(WORK + "/pull", headers=peer).json()["cursor"] == 4
        assert source.get(WORK + "/pull", headers=desktop).status_code == 401
        assert source.get(WORK + "/pull", headers=new_desktop).status_code == 401
        assert backup.verify_backup(archive) == created


def test_wrong_hash_foreign_file_sidecars_and_raced_targets_do_not_publish(tmp_path, monkeypatch):
    live = tmp_path / "live.sqlite"
    archive = tmp_path / "archive.sqlite"
    target = tmp_path / "fresh.sqlite"
    SyncStore(live)
    backup.create_backup(live, archive)
    with pytest.raises(backup.SyncBackupError, match="已存在"):
        backup.restore_backup(archive, live)
    with pytest.raises(backup.SyncBackupError, match="SHA-256"):
        backup.restore_backup(archive, target, expected_sha256="0" * 64)
    assert not target.exists()
    with pytest.raises(backup.SyncBackupError, match="完整独立"):
        backup.restore_backup(live, target)
    sidecar = target.with_name(target.name + "-wal")
    sidecar.write_bytes(b"foreign data")
    with pytest.raises(backup.SyncBackupError, match="已存在"):
        backup.restore_backup(archive, target)
    assert sidecar.read_bytes() == b"foreign data" and not target.exists()
    sidecar.unlink()

    # Simulate a new independent database appearing immediately before publication.
    publish = backup._publish

    def collide(path, snapshot):
        path.write_bytes(b"new independent owner")
        return publish(path, snapshot)

    monkeypatch.setattr(backup, "_publish", collide)
    with pytest.raises(backup.SyncBackupError, match="已存在"):
        backup.restore_backup(archive, target)
    assert target.read_bytes() == b"new independent owner"
    assert not list(tmp_path.glob(".ainovel-sync-*"))
    assert "works" in backup.verify_backup(archive)


def test_integrity_guard_rejects_modified_cover_or_attachment_and_storage_preflight_creates_no_files(
    tmp_path, monkeypatch
):
    live, archive = tmp_path / "live.sqlite", tmp_path / "archive.sqlite"
    with TestClient(create_sync_app(live, ADMIN["Authorization"].split(" ", 1)[1])) as source:
        credential = grant(source, "desktop")
        data = b"valid"
        material = sha256(data).hexdigest()
        route = f"{WORK}/attachments"
        assert source.post(
            route, headers=credential, json={"name": "sample", "size": len(data), "sha256": material}
        ).is_success
        assert source.post(
            route + f"/{material}/parts/0",
            headers={**credential, "Content-Type": "application/octet-stream"},
            content=data,
        ).is_success
        assert source.post(route + f"/{material}/seal", headers=credential).is_success
        push(source, credential, parent("seed"))
    backup.create_backup(live, archive)
    tampered = tmp_path / "tampered.sqlite"
    shutil.copyfile(archive, tampered)
    with closing(sqlite3.connect(tampered)) as tx, tx:
        tx.execute("UPDATE sync_attachment_parts SET data=?", (b"wrong",))
    with pytest.raises(backup.SyncBackupError, match="附件"):
        backup.verify_backup(tampered)
    shutil.copyfile(archive, tampered)
    with closing(sqlite3.connect(tampered)) as tx, tx:
        tx.execute("UPDATE sync_works SET sequence=10")
    with pytest.raises(backup.SyncBackupError, match="游标"):
        backup.verify_backup(tampered)
    shutil.copyfile(archive, tampered)
    with closing(sqlite3.connect(tampered)) as tx, tx:
        tx.execute("CREATE TRIGGER sqlitefixture AFTER UPDATE ON sync_devices BEGIN SELECT 1; END")
    with pytest.raises(backup.SyncBackupError, match="格式"):
        backup.restore_backup(tampered, tmp_path / "rejected-trigger.sqlite")
    assert not (tmp_path / "rejected-trigger.sqlite").exists()

    target = tmp_path / "protected-target.sqlite"
    calls = []

    def require_proof(source, backup_dir):
        calls.append((source, backup_dir))
        raise StorageProofError("无法核验加密状态。")

    monkeypatch.setattr(backup, "verify_storage", require_proof)
    with pytest.raises(StorageProofError):
        backup.create_backup(live, target, require_encrypted_storage=True)
    with pytest.raises(StorageProofError):
        backup.restore_backup(archive, target, require_encrypted_storage=True)
    assert calls == [(live, tmp_path), (archive, tmp_path)]
    assert not target.exists()
