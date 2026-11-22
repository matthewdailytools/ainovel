from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from test_chapter_item_check import attempt, run


def path(story):
    return f"/api/branches/{story['branch_id']}/delivery"


def setup_book(domain, story):
    first = domain.save(story["chapter_id"], 0, "𠮷灯熄了。沅归还了故人的旧钟。")
    second = domain.create_chapter(story["branch_id"], "潮退以后")
    second = domain.save(second["id"], 0, "他在桥头等到退潮。最后，他亲手打开了水门。")
    return first, second


def reviewed(state, first, second, nonce):
    return {
        "base_sha256": state["basis_sha256"],
        "idempotency_key": nonce,
        "synopsis": "修钟人的寻声旅途。",
        "spoiler_scope": "简介与试读只展示第一章发生的事。",
        "sample_start_chapter_id": first["id"],
        "sample_end_chapter_id": first["id"],
        "notes": {
            "manuscript": "作者已依次通读两章，稿件齐全。",
            "ending": "最后打开水门，结束水患。",
            "characters": "沅把旧钟归给故人，归还的动作明确两人的关系。",
            "promises": "看过现有伏笔清单，本短篇没有未处理承诺。",
            "consistency": "作者核对归钟到开水门的先后与物件归属。",
            "materials": "已核对书名、简介和试读，正文结局未出现在试读。",
        },
        "sources": {
            "ending": [{"chapter_id": second["id"], "quote": "最后，他亲手打开了水门。"}],
            "characters": [{"chapter_id": first["id"], "quote": "沅归还了故人的旧钟。"}],
        },
    }


def preview(client, endpoint, body):
    response = client.post(endpoint + "/packages", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def finalize(client, endpoint, package, nonce="finish-test-package"):
    return client.post(
        endpoint + f"/packages/{package['id']}/finalize",
        json={"base_sha256": package["basis_sha256"], "idempotency_key": nonce},
    )


@pytest.mark.parametrize(
    "gate_id", ["manuscript", "ending", "characters", "promises", "consistency", "materials"]
)
def test_each_gate_independently_holds_formal_delivery(client, domain, story, gate_id):
    first, second = setup_book(domain, story)
    endpoint = path(story)
    body = reviewed(client.get(endpoint).json(), first, second, "preview-gate-" + gate_id)
    body["notes"].pop(gate_id)
    package = preview(client, endpoint, body)
    assert package["status"] == "draft"
    assert {gate["id"] for gate in package["gates"] if gate["status"] != "pass"} == {gate_id}
    assert finalize(client, endpoint, package).status_code == 409
    assert client.get(endpoint + f"/packages/{package['id']}/download").status_code == 409
    feature = client.get(endpoint + f"/packages/{package['id']}/files/sample")
    assert feature.status_code == 200 and first["content"] in feature.text
    assert client.get(f"/api/branches/{story['branch_id']}/export?format=txt").status_code == 200


def test_zip_is_same_frozen_snapshot_after_new_prose_and_outline(client, domain, story):
    first, second = setup_book(domain, story)
    endpoint = path(story)
    branch_id = story["branch_id"]
    outline_path = f"/api/branches/{branch_id}/chapters/{first['id']}/writing-context/outline"
    outline = client.put(outline_path, json={"base_version": None, "text": "让归钟发生在涨潮之前。"})
    assert outline.status_code == 200, outline.text
    state = client.get(endpoint).json()
    body = reviewed(state, first, second, "preview-full-test")
    package = preview(client, endpoint, body)
    assert package["status"] == "ready"
    assert package["chapters"] == state["chapters"]
    source = next(gate for gate in package["gates"] if gate["id"] == "characters")["sources"][0]
    assert (source["chapter_id"], source["title"], source["revision"], source["start"]) == (
        first["id"],
        first["title"],
        first["revision"],
        6,
    )
    assert preview(client, endpoint, body)["id"] == package["id"]
    confirmation = finalize(client, endpoint, package)
    assert confirmation.status_code == 200, confirmation.text
    assert confirmation.json()["status"] == "finished"
    assert finalize(client, endpoint, package).json() == confirmation.json()
    assert not client.get(f"/api/branches/{branch_id}/finalization").json()["author_finalized"]
    saved_url = endpoint + f"/packages/{package['id']}"
    archive = client.get(saved_url + "/download")
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert set(bundle.namelist()) == {
            "manuscript.txt",
            "settings.md",
            "synopsis.md",
            "sample.txt",
            "manifest.json",
        }
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["basis_sha256"] == package["basis_sha256"]
        assert manifest["chapters"] == state["chapters"]
        assert manifest["outline_versions"][0]["version"] == outline.json()["version"]
        for item in manifest["files"]:
            original = bundle.read(item["name"])
            assert (sha256(original).hexdigest(), len(original)) == (item["sha256"], item["bytes"])
            assert client.get(saved_url + "/files/" + item["key"]).content == original
        assert first["content"] in bundle.read("sample.txt").decode()
        assert second["content"] not in bundle.read("sample.txt").decode()
        assert "让归钟发生在涨潮之前。" in bundle.read("settings.md").decode()
    updated = client.put(
        outline_path, json={"base_version": outline.json()["version"], "text": "让涨潮发生在归钟之前。"}
    )
    assert updated.status_code == 200, updated.text
    assert client.get(saved_url).json()["status"] == "outdated"
    assert finalize(client, endpoint, package, "new-confirmation-attempt").status_code == 409
    domain.save(second["id"], second["revision"], "暴雨之后，水门已经被他打开。")
    reloaded = client.get(saved_url).json()
    assert reloaded["status"] == "outdated" and reloaded["finished"]
    assert reloaded["gates"] == package["gates"]
    assert client.get(saved_url + "/download").content == archive.content
    other = domain.create_novel("另一作品")
    foreign = path(other) + f"/packages/{package['id']}"
    assert client.get(foreign).status_code == 404
    assert client.get(foreign + "/download").status_code == 404


def test_validated_ledger_and_check_quotes_appear_and_stale_checks_hold(client, settings, domain, story):
    first, second = setup_book(domain, story)
    branch_id = story["branch_id"]
    title = "水门的回收"
    quote = "最后，他亲手打开了水门。"
    start = len(second["content"].split(quote)[0].encode("utf-16-le")) // 2
    graph = f"/api/branches/{branch_id}/story-records"
    retained = client.post(
        graph,
        json={
            "base_story_revision": domain.branch(branch_id)["story_revision"],
            "record": {
                "kind": "promise",
                "title": "候鸟",
                "stages": [
                    {"status": "retained", "note": "只保留作远行的暗示。"},
                ],
            },
        },
    )
    assert retained.status_code == 201, retained.text
    resolved = client.post(
        graph,
        json={
            "base_story_revision": domain.branch(branch_id)["story_revision"],
            "record": {
                "kind": "promise",
                "title": title,
                "stages": [
                    {
                        "status": "resolved",
                        "note": "水门最终开启。",
                        "sources": [
                            {
                                "chapter_id": second["id"],
                                "revision": second["revision"],
                                "start": start,
                                "end": start + len(quote),
                                "quote": quote,
                            }
                        ],
                    }
                ],
            },
        },
    )
    assert resolved.status_code == 201, resolved.text
    check_response = client.post(
        f"/api/chapters/{second['id']}/checks",
        json=attempt(second["revision"], "delivery-check-1", condition="核对水门是否已打开。"),
    )
    assert check_response.status_code == 202, check_response.text
    check = run(settings, domain, check_response.json()["id"])
    assert check["status"] == "succeeded", check["error"]
    endpoint = path(story)
    package = preview(
        client, endpoint, reviewed(client.get(endpoint).json(), first, second, "preview-proofs-1")
    )
    assert package["status"] == "ready"
    gates = {gate["id"]: gate for gate in package["gates"]}
    assert gates["promises"]["sources"][0]["origin"] == "ledger"
    assert gates["promises"]["sources"][0]["quote"] == quote
    assert any("只保留作远行的暗示。" in row for row in gates["promises"]["evidence"])
    assert gates["consistency"]["sources"][0]["origin"] == "chapter_check"
    assert gates["consistency"]["sources"][0]["quote"] == second["content"]
    changed = domain.save(second["id"], second["revision"], "大潮已经退去。" + second["content"])
    newest = preview(
        client, endpoint, reviewed(client.get(endpoint).json(), first, changed, "preview-proofs-2")
    )
    gates = {gate["id"]: gate for gate in newest["gates"]}
    assert not gates["promises"]["sources"]
    assert not gates["consistency"]["sources"]
    assert gates["promises"]["status"] == "blocked"
    assert gates["consistency"]["status"] == "blocked"


def test_old_local_pass_cannot_clear_downstream_chapter_rereview(client, settings, domain, story):
    first, second = setup_book(domain, story)
    check_path = f"/api/chapters/{second['id']}/checks"
    check = client.post(
        check_path,
        json=attempt(second["revision"], "unrelated-old-local-check", condition="末句包含一个完整动作。"),
    )
    assert check.status_code == 202, check.text
    assert run(settings, domain, check.json()["id"])["status"] == "succeeded"
    first = domain.save(first["id"], first["revision"], "沅在启程前已答应照看水门。" + first["content"])
    assert domain.chapter(second["id"])["stale"]
    scoped_item = client.get(check_path).json()["items"][0]
    assert scoped_item["status"] == "current" and scoped_item["latest"]["result"] == "pass"

    endpoint = path(story)
    body = reviewed(client.get(endpoint).json(), first, second, "unreviewed-after-upstream-edit")
    package = preview(client, endpoint, body)
    assert package["status"] == "draft"
    assert {gate["id"] for gate in package["gates"] if gate["status"] != "pass"} == {"consistency"}
    gate = next(gate for gate in package["gates"] if gate["id"] == "consistency")
    assert second["title"] in gate["reason"]
    assert any(source["origin"] == "chapter_check" for source in gate["sources"])
    assert finalize(client, endpoint, package).status_code == 409

    body["idempotency_key"] = "explicit-rereview-after-upstream-edit"
    body["notes"]["consistency"] = "重新通读第二章，照看水门的约定与最后亲手开门相符。"
    body["sources"]["consistency"] = [{"chapter_id": second["id"], "quote": "最后，他亲手打开了水门。"}]
    confirmed = preview(client, endpoint, body)
    assert confirmed["status"] == "ready"
    assert finalize(client, endpoint, confirmed).json()["status"] == "finished"
