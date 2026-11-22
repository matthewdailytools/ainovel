"""Named chapter grouping is separate from manuscript revisions and order."""

from ainovel.backups import create_backup, unpack_verified
from ainovel.db import Database


def volume_input(chapter_id, title="第一卷 · 渡口"):
    return {"title": title, "start_chapter_id": chapter_id}


def test_volume_boundaries_preserve_manuscript_and_follow_existing_directory(client, domain, story):
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "渡口的灯还亮着。")
    middle = domain.create_chapter(branch, "第二章")
    last = domain.create_chapter(branch, "第三章")
    path = f"/api/branches/{branch}"
    before = {route: client.get(f"{path}/{route}").json() for route in ("chapters", "volumes")}
    assert before["volumes"] == []
    assert [row["id"] for row in before["chapters"]] == [first["id"], middle["id"], last["id"]]

    for payload in (volume_input(first["id"]), volume_input(last["id"], "第二卷 · 旧桥")):
        response = client.post(f"{path}/volumes", json=payload)
        assert response.status_code == 201
        assert client.post(f"{path}/volumes", json=payload).json() == response.json()

    assert client.get(f"{path}/chapters").json() == before["chapters"]
    assert client.get(f"/api/chapters/{first['id']}").json()["content"] == "渡口的灯还亮着。"
    volumes = client.get(f"{path}/volumes").json()
    assert [row["start_chapter_id"] for row in volumes] == [first["id"], last["id"]]

    archive_url = f"/api/chapters/{first['id']}/archive"
    assert client.post(archive_url, json={"base_revision": first["revision"], "archived": True}).is_success
    assert first["id"] not in [row["id"] for row in client.get(f"{path}/chapters").json()]
    assert client.get(f"{path}/volumes").json() == volumes[::-1]
    assert client.post(archive_url, json={"base_revision": first["revision"], "archived": False}).is_success
    assert client.get(f"{path}/chapters").json()[-1]["id"] == first["id"]
    assert client.get(f"{path}/volumes").json()[-1] == volumes[0]


def test_volume_updates_reject_stale_duplicate_and_unowned_anchors(client, domain, story):
    branch = story["branch_id"]
    second = domain.create_chapter(branch, "第二章")
    third = domain.create_chapter(branch, "第三章")
    elsewhere = domain.create_novel("另一本作品", "")
    outside = domain.create_chapter(elsewhere["branch_id"], "其他作品章节")
    path = f"/api/branches/{branch}/volumes"

    response = client.post(path, json=volume_input(outside["id"]))
    assert response.status_code == 409
    assert response.json()["code"] == "volume_start"
    response = client.post(path, json=volume_input(story["chapter_id"], "分卷\n标题"))
    assert response.status_code == 400
    assert response.json()["code"] == "volume_title"
    first = client.post(path, json=volume_input(story["chapter_id"])).json()
    last = client.post(path, json=volume_input(third["id"], "次卷")).json()

    response = client.put(
        f"{path}/{first['id']}",
        json={**volume_input(last["start_chapter_id"]), "base_revision": first["revision"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "volume_start_used"
    response = client.put(
        f"{path}/{first['id']}",
        json={**volume_input(second["id"], "起卷"), "base_revision": first["revision"]},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated == {**first, "title": "起卷", "start_chapter_id": second["id"], "revision": 2}

    response = client.request("DELETE", f"{path}/{first['id']}", json={"base_revision": first["revision"]})
    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    other_path = f"/api/branches/{elsewhere['branch_id']}/volumes"
    assert (
        client.request("DELETE", f"{other_path}/{first['id']}", json={"base_revision": 2}).status_code == 404
    )
    assert client.request("DELETE", f"{path}/{first['id']}", json={"base_revision": 2}).json() == updated
    assert client.get(path).json() == [last]


def test_additive_volume_schema_upgrades_and_restores_through_sqlite_backup(
    client, domain, settings, story, tmp_path
):
    branch = story["branch_id"]
    first = domain.save(story["chapter_id"], 0, "原稿仍在。")
    with domain.db.connect(True) as tx:
        tx.execute("DROP TABLE manuscript_volumes")
        tx.execute("DELETE FROM extension_migrations WHERE name='manuscript-volumes/1'")
    Database(domain.db.path)
    assert domain.db.one("SELECT name FROM extension_migrations WHERE name='manuscript-volumes/1'")
    assert domain.chapter(first["id"]) == first
    path = f"/api/branches/{branch}/volumes"
    assert client.get(path).json() == []
    row = client.post(path, json=volume_input(first["id"])).json()

    backup = create_backup(settings, domain.db)
    restored = tmp_path / "volume-restore"
    restored.mkdir()
    unpack_verified(settings.data_dir / "backups" / f"{backup['id']}.zip", restored)
    database = Database(restored / "manuscripts.sqlite")
    assert database.one("SELECT id,title,start_chapter_id,revision FROM manuscript_volumes") == row
    assert database.one("SELECT * FROM chapters WHERE id=?", (first["id"],)) == first
