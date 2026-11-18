from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from test_workflow_inspection import author


def test_finish_requires_six_checks_and_exports_immutable_package(client, domain, story):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒找到师姐，归还了铜铃。")
    endpoint = f"/api/branches/{branch}/book-completion"
    inspection = f"/api/branches/{branch}/workflow-inspections"
    initial = client.get(endpoint).json()
    data = {
        "basis_sha256": initial["basis_sha256"],
        "read_note": "作者已通读保存稿。",
        "ending": {"note": "主冲突以归还铜铃解决。"},
        "characters": {"note": "师姐与学徒重逢。"},
        "promises": {"note": "铃和师姐的线索均在末章兑现。"},
        "intro": "学徒循着一枚旧铃寻找师姐。",
        "spoiler_scope": "opening",
        "sample_chapter_ids": [chapter["id"]],
        "materials_reviewed": True,
    }
    blocked = client.post(endpoint + "/confirm", json=data)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "completion_blocked"
    preview = client.get(inspection).json()["preview"]
    created = client.post(
        inspection,
        json={
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                author(row["check_id"], chapter) for row in preview["items"] if row["status"] == "pending"
            ],
            "length_targets": {"min_chars": 1, "expected_chapters": 1},
        },
    )
    assert created.status_code == 201, created.text
    assert all(row["current_status"] == "green" for row in created.json()["items"]), created.json()
    assert client.post(endpoint + "/confirm", json=data).json()["code"] == "completion_changed"
    data["basis_sha256"] = client.get(endpoint).json()["basis_sha256"]
    for field, value, gate in [
        ("read_note", "", "manuscript"),
        ("ending", {"note": ""}, "ending"),
        ("characters", {"note": ""}, "characters"),
        ("promises", {"note": ""}, "promises"),
        ("materials_reviewed", False, "delivery"),
    ]:
        incomplete = {**data, field: value}
        check = client.post(endpoint + "/preview", json=incomplete).json()
        assert [row["id"] for row in check["checks"] if row["status"] != "green"] == [gate]
        assert client.post(endpoint + "/confirm", json=incomplete).status_code == 409
    confirmed = client.post(endpoint + "/confirm", json=data)
    assert confirmed.status_code == 201, confirmed.text
    record = confirmed.json()
    assert client.post(endpoint + "/confirm", json=data).json()["id"] == record["id"]
    export = f"{endpoint}/{record['id']}/export"
    response = client.get(export)
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["inspection_id"] == created.json()["id"]
        assert set(manifest["files"]) == {"正文.md", "设定册.md", "简介.md", "试读.md"}
        assert all(
            sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest["files"].items()
        )
        assert chapter["content"] in archive.read("正文.md").decode()
    domain.save(chapter["id"], chapter["revision"], "学徒仍在找铃。")
    assert not client.get(endpoint).json()["latest"]["current"]
    with ZipFile(BytesIO(client.get(export).content)) as archive:
        assert chapter["content"] in archive.read("正文.md").decode()
