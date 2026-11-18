import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import httpx

from ainovel.api import create_app
from ainovel.db import Database
from ainovel.domain import Domain
from ainovel.manuscript_exchange import (
    MAX_PACKAGE_BYTES,
    ManuscriptPackage,
    export_manuscript,
    import_manuscript,
)


IMPORT_PACKAGE = "/api/manuscript-packages/import"


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


def test_desktop_json_exchange_bounds_real_bytes_on_chunked_upload(client, settings, domain, story, tmp_path):
    domain.save(story["chapter_id"], 0, "只在门内响。😀")
    pack = export_manuscript(domain, story["branch_id"]).model_dump(mode="json")
    content = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    app = create_app(replace(settings, data_dir=tmp_path / "isolated-receiver"))
    request = app.openapi()["paths"][IMPORT_PACKAGE]["post"]["requestBody"]
    assert request == {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ManuscriptPackage"}}},
        "required": True,
    }

    async def padded(total):
        yield content
        remaining = total - len(content)
        block = b" " * (256 * 1024)
        while remaining:
            yield block[:remaining]
            remaining -= min(remaining, len(block))

    async def submit():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as receiver:
            receiver.headers["X-Ainovel-CSRF"] = (await receiver.get("/api/session")).json()["csrf"]
            retained = (await receiver.post("/api/novels", json={"title": "已有作品"})).json()
            branch = retained["branch_id"]
            chapter = (await receiver.post(f"/api/branches/{branch}/chapters", json={"title": "原稿"})).json()
            saved = await receiver.put(
                f"/api/chapters/{chapter['id']}",
                json={"base_revision": 0, "content": "之前保存的作品仍可读。"},
            )
            assert saved.status_code == 200
            history = f"/api/chapters/{chapter['id']}/history"
            previous = (await receiver.get(history)).json()
            json_header = {"Content-Type": "application/json"}
            with_length = await receiver.post(
                IMPORT_PACKAGE,
                headers={**json_header, "Content-Length": str(MAX_PACKAGE_BYTES + 1)},
                content=content,
            )
            chunked = await receiver.post(
                IMPORT_PACKAGE, headers=json_header, content=padded(MAX_PACKAGE_BYTES + 1)
            )
            assert "Content-Length" not in chunked.request.headers
            assert chunked.request.headers["Transfer-Encoding"] == "chunked"
            assert with_length.status_code == chunked.status_code == 413
            assert (
                with_length.json()
                == chunked.json()
                == {"code": "file_limit", "message": "请求超过 24 MiB，未读取或保存。"}
            )
            assert [row["id"] for row in (await receiver.get("/api/novels")).json()] == [retained["id"]]
            assert (await receiver.get(history)).json() == previous

            at_limit = await receiver.post(
                IMPORT_PACKAGE, headers=json_header, content=padded(MAX_PACKAGE_BYTES)
            )
            assert at_limit.status_code == 201, at_limit.text
            assert at_limit.json() == {"id": story["id"], "branch_id": story["branch_id"]}
            assert (await receiver.get(history)).json() == previous
            restored_history = f"/api/chapters/{story['chapter_id']}/history"
            assert (await receiver.get(restored_history)).json() == client.get(restored_history).json()

    asyncio.run(submit())


def test_invalid_json_unicode_is_controlled_and_cannot_write_work(client, domain, story):
    pack = export_manuscript(domain, story["branch_id"]).model_dump(mode="json")
    pack["novel"]["title"] = "\ud800"
    invalid_surrogate = json.dumps(pack).encode()
    invalid_utf8 = invalid_surrogate.replace(b"\\ud800", b"\xff")
    for body in (invalid_utf8, invalid_surrogate):
        rejected = client.post(IMPORT_PACKAGE, headers={"Content-Type": "application/json"}, content=body)
        assert rejected.status_code == 400, rejected.text
        assert "Traceback" not in rejected.text
        assert [row["id"] for row in client.get("/api/novels").json()] == [story["id"]]
