from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest

from ainovel.setting_use import current_outline_uses, version_id
from test_conception_workbench import confirm, path, save
from test_outline_workspace import chapter_plan, put_and_confirm
from test_workflow_inspection import item as inspection_item

TEXT = "在😀渡口，领航人阿禾先核实铜牌。"
QUOTE = "😀渡口，领航人阿禾"


def digest(text):
    return sha256(text.encode()).hexdigest()


def verified(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


@pytest.fixture
def author_plan(client, domain, story):
    branch_id = story["branch_id"]
    request = f"/api/branches/{branch_id}"
    first = domain.save(story["chapter_id"], 0, TEXT)
    second = domain.create_chapter(branch_id, "另卷也见阿禾")
    second = domain.save(second["id"], 0, TEXT)
    setting = path(branch_id)
    workspace = verified(client.get(setting))
    categories = {row["id"]: row for row in workspace["categories"]}
    categories["world"]["items"] = [{"id": "bell", "title": "鸣铃口令", "detail": "鸣铃只能在木门内。"}]
    categories["characters"]["items"] = [
        {"id": "pilot", "title": "领航人阿禾", "detail": "开船前须先核实铜牌。"},
        {"id": "guard", "title": "守门人", "detail": "留在木门外。"},
    ]
    settings = confirm(client, setting, save(client, setting, workspace))
    confirmation = settings["confirmation"]["id"]
    plan = {
        "summary": "先核对过门凭据，再离岸。",
        "volumes": [
            {
                "id": f"v{index}",
                "title": f"卷{index}",
                "summary": "找出渡口暗号。",
                "units": [
                    {
                        "id": f"u{index}",
                        "title": "校验",
                        "summary": "核对铜牌。",
                        "chapters": [
                            chapter_plan(chapter["id"], "核实凭据")
                            | {"setting_ids": ["pilot", "bell"] if index == 1 else ["guard"]},
                        ],
                    }
                ],
            }
            for index, chapter in enumerate((first, second), 1)
        ],
    }
    outline = put_and_confirm(
        client,
        request + "/outline-workspace",
        {"base_revision": 0, "plan": plan, "confirmation_id": confirmation},
    )["confirmed"]
    return {
        "first": first,
        "second": second,
        "branch_id": branch_id,
        "setting": setting,
        "outline": outline,
        "request": request,
        "plan": plan,
        "route": f"/api/chapters/{first['id']}/setting-uses",
        "other": f"/api/chapters/{second['id']}/setting-uses",
    }


def select(view, key, parent=None):
    basis = view["basis"]
    return {
        "outline_version_id": basis["outline_version_id"],
        "settings_version_id": basis["settings_version_id"],
        "item_id": "pilot",
        "parent_id": parent,
        "chapter_sha256": basis["chapter_sha256"],
        "evidence": {
            "chapter_id": view["chapter_id"],
            "revision_id": basis["chapter_revision_id"],
            "start": 1,
            "end": 1 + len(QUOTE.encode("utf-16-le")) // 2,
            "quote_sha256": digest(QUOTE),
        },
        "idempotency_key": key,
    }


def test_author_use_is_distinct_from_context_story_records_and_world_review(client, domain, author_plan):
    setup = author_plan
    route = setup["route"]
    branch = setup["branch_id"]
    first = setup["first"]
    graph = verified(client.get(setup["request"] + "/story-graph"))
    verified(
        client.post(
            setup["request"] + "/story-records",
            json={
                "base_story_revision": graph["story_revision"],
                "record": {
                    "kind": "character",
                    "title": "领航人阿禾",
                    "sources": [
                        {
                            "chapter_id": first["id"],
                            "revision": first["revision"],
                            "start": 1,
                            "end": 1 + len(QUOTE.encode("utf-16-le")) // 2,
                            "quote": QUOTE,
                        },
                    ],
                },
            },
        ),
        201,
    )
    inspection = setup["request"] + "/workflow-inspections"
    preview = verified(client.get(inspection, params={"scope": "chapter", "scope_id": first["id"]}))[
        "preview"
    ]
    snapshot = verified(client.get(route))
    assert snapshot["records"] == []
    assert [(row["item_id"], row["category_id"]) for row in snapshot["options"]] == [("pilot", "characters")]
    assert snapshot["options"][0]["head_id"] is None
    assert verified(client.get(setup["other"]))["records"] == []
    request = select(snapshot, "author-proof-first")
    saved = verified(client.post(route, json=request), 201)
    proof = saved["version"]
    assert saved["status"] == "current" and saved["quote"] == QUOTE
    assert proof["actor"] == "author" and proof["action"] == "confirmed"
    assert proof["id"] == version_id(branch, {name: value for name, value in proof.items() if name != "id"})
    assert verified(client.post(route, json=request), 201) == saved
    repeated_key = client.post(route, json={**request, "item_id": "guard"})
    assert repeated_key.status_code == 409 and repeated_key.json()["code"] == "idempotency"
    assert verified(client.get(route))["options"][0]["head_id"] == proof["id"]
    assert client.get(setup["other"] + "/" + proof["id"]).status_code == 404
    assert verified(client.get(setup["other"]))["records"] == []
    with domain.db.connect() as tx:
        projected = current_outline_uses(
            tx, branch, proof["outline_version_id"], proof["settings_version_id"]
        )
        assert projected == {(first["id"], "pilot"): saved}
    refreshed = verified(client.get(inspection, params={"scope": "chapter", "scope_id": first["id"]}))[
        "preview"
    ]
    assert inspection_item(refreshed, "05") == inspection_item(preview, "05")

    # Same visible characters in another saved revision require a fresh author confirmation.
    domain.save(first["id"], first["revision"], TEXT + "她随后转身。")
    assert verified(client.get(route + "/" + proof["id"])) == {**saved, "status": "outdated"}
    stale = client.post(route, json=request | {"idempotency_key": "old-prose-wrong"})
    assert stale.status_code == 409 and stale.json()["code"] == "setting_use_changed"
    current = verified(client.get(route))
    successor = verified(client.post(route, json=select(current, "author-proof-second", proof["id"])), 201)
    assert successor["version"]["parent_id"] == proof["id"] and successor["status"] == "current"
    assert successor["version"]["id"] != proof["id"]
    old = verified(client.get(route + "/" + proof["id"]))
    assert old["status"] == "superseded" and old["quote"] == QUOTE
    withdrawn = verified(
        client.post(
            route + f"/{successor['version']['id']}/withdraw", json={"idempotency_key": "author-withdraw"}
        ),
        201,
    )
    assert withdrawn["status"] == withdrawn["version"]["action"] == "withdrawn"
    assert withdrawn["quote"] == QUOTE
    assert withdrawn["version"]["parent_id"] == successor["version"]["id"]
    assert (
        verified(
            client.post(
                route + f"/{successor['version']['id']}/withdraw", json={"idempotency_key": "author-withdraw"}
            ),
            201,
        )
        == withdrawn
    )
    page = verified(client.get(route, params={"limit": 1}))
    assert page["next_before_id"] == withdrawn["version"]["id"]
    assert len(page["records"]) == 1 and page["records"][0] == withdrawn
    history = verified(client.get(route, params={"before_id": page["next_before_id"]}))
    assert [row["version"]["id"] for row in history["records"]] == [successor["version"]["id"], proof["id"]]
    assert all(row["quote"] == QUOTE and row["status"] == "superseded" for row in history["records"])
    with domain.db.connect() as tx:
        assert (
            current_outline_uses(tx, branch, proof["outline_version_id"], proof["settings_version_id"]) == {}
        )


def test_exact_scope_and_same_slot_concurrency(client, author_plan):
    setup = author_plan
    route = setup["route"]
    first = verified(client.get(route))
    payload = select(first, "author-proof-start")
    wrong = [
        (payload | {"item_id": "bell"}, "setting_use_unplanned"),
        (payload | {"item_id": "guard"}, "setting_use_unplanned"),
        (payload | {"chapter_sha256": "0" * 64}, "setting_use_changed"),
        (
            payload | {"evidence": payload["evidence"] | {"chapter_id": setup["second"]["id"]}},
            "setting_use_changed",
        ),
        (payload | {"evidence": payload["evidence"] | {"start": 2}}, "setting_use_evidence"),
        (payload | {"evidence": payload["evidence"] | {"quote_sha256": "0" * 64}}, "setting_use_evidence"),
    ]
    for body, code in wrong:
        response = client.post(route, json=body)
        assert response.status_code == 409 and response.json()["code"] == code
    assert verified(client.get(route))["records"] == []

    def compete(key):
        return client.post(route, json=payload | {"idempotency_key": key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(compete, ("parallel-proof-left", "parallel-proof-right")))
    assert sorted(response.status_code for response in responses) == [201, 409]
    failed = next(response for response in responses if response.status_code == 409)
    assert failed.json()["code"] == "setting_use_conflict"
    view = verified(client.get(route))
    [adopted] = view["records"]
    assert adopted["status"] == "current"
    assert view["options"][0]["head_id"] == adopted["version"]["id"]


def test_outline_and_setting_changes_require_new_proof_and_keep_original(client, author_plan):
    setup = author_plan
    route = setup["route"]
    starting = verified(client.get(route))
    old = verified(client.post(route, json=select(starting, "first-outline-use")), 201)["version"]
    next_plan = setup["plan"] | {"summary": "先查验铜牌，再等潮汐。"}
    outline = put_and_confirm(
        client,
        setup["request"] + "/outline-workspace",
        {
            "base_revision": setup["outline"]["revision"],
            "plan": next_plan,
            "confirmation_id": starting["basis"]["settings_version_id"],
        },
    )["confirmed"]
    assert outline["id"] != old["outline_version_id"]
    denied = client.post(route, json=select(starting, "previous-outline-use", old["id"]))
    assert denied.status_code == 409 and denied.json()["code"] == "setting_use_changed"
    revised = verified(client.get(route))
    assert revised["basis"]["outline_version_id"] == outline["id"]
    assert revised["records"][0]["status"] == "outdated"

    endpoint = setup["setting"]
    workspace = verified(client.get(endpoint))
    characters = next(category for category in workspace["categories"] if category["id"] == "characters")
    characters["items"][0]["detail"] = "开船前须请另一个人同时看过铜牌。"
    confirmation = confirm(client, endpoint, save(client, endpoint, workspace), "new-setting-use-base")
    assert confirmation["confirmation"]["id"] != old["settings_version_id"]
    denied = client.post(route, json=select(revised, "previous-setting-use", old["id"]))
    assert denied.status_code == 409 and denied.json()["code"] == "setting_use_changed"
    pending = verified(client.get(route))
    assert pending["basis"] is None and pending["options"] == []
    assert pending["records"][0]["status"] == "outdated"
    assert pending["records"][0]["quote"] == QUOTE
