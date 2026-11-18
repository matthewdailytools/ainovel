from uuid import uuid4

from test_reference_flow import SOURCE, reference as reference_fixture, run, update

reference = reference_fixture


def card_url(session_id, card_id, revision):
    return f"/api/reference-sessions/{session_id}/cards/{card_id}/revisions/{revision}"


def test_local_analysis_and_historical_card_reuse(client, domain, settings, reference):
    session_id = reference["id"]
    original, _ = run(client, settings, domain, session_id, "analyze")
    card = original["payload"]["cards"][0]
    saved = update(
        client,
        domain,
        reference,
        cards=[card],
        selected_ids=[card["id"]],
        plot="铃声暴露位置，学徒决定绕路。",
    )
    revision = saved["revision"]
    before, _ = run(client, settings, domain, session_id, "plan")
    assert before["basis"]["reference_session"]["card_references"] == [
        {"session_id": session_id, "card_id": card["id"], "revision": revision}
    ]

    updated = update(client, domain, saved, goal="只研究最后动作如何改变下一拍")
    quote = SOURCE[SOURCE.index("却记下") :]
    scope = {
        "start": len(SOURCE[: -len(quote)].encode("utf-16-le")) // 2,
        "end": len(SOURCE.encode("utf-16-le")) // 2,
        "card_id": card["id"],
    }
    draft, worker = run(client, settings, domain, session_id, "analyze", analysis_scope=scope)
    sent = worker.client.requests[0]["任务"]
    assert sent["选段"] == quote
    assert sent["学习目标"] == "只研究最后动作如何改变下一拍"
    assert draft["payload"]["analysis_scope"] == {**scope, "quote": quote}
    assert draft["payload"]["cards"][0]["evidence"] == quote
    assert client.get(card_url(session_id, card["id"], updated["revision"])).json()["card"] == card

    replacement = {
        **draft["payload"]["cards"][0],
        "id": card["id"],
        "technique": "让看似收束的动作暴露下一重威胁",
    }
    edited = update(
        client,
        domain,
        reference,
        cards=[replacement],
        pinned_revisions={card["id"]: revision},
    )
    historical = client.get(card_url(session_id, card["id"], revision)).json()
    assert historical["card"] == card
    assert historical["source"]["quote"] == SOURCE
    assert historical["goal"] == saved["payload"]["goal"]
    assert client.get(card_url(session_id, card["id"], edited["revision"])).json()["card"] == replacement
    still_pinned, worker = run(client, settings, domain, session_id, "plan")
    assert (
        still_pinned["basis"]["reference_session"]["card_references"]
        == before["basis"]["reference_session"]["card_references"]
    )
    assert worker.client.requests[0]["任务"]["所选写法"][0]["technique"] == card["technique"]

    chosen = update(client, domain, reference, pinned_revisions={})
    refreshed, worker = run(client, settings, domain, session_id, "plan")
    assert refreshed["basis"]["reference_session"]["card_references"][0]["revision"] == chosen["revision"]
    assert worker.client.requests[0]["任务"]["所选写法"][0]["technique"] == replacement["technique"]


def test_local_analysis_rejects_invalid_range_and_unknown_saved_card(client, reference):
    endpoint = f"/api/reference-sessions/{reference['id']}/actions"
    request = {"base_revision": 0, "action": "analyze", "account": "deepseek"}
    for scope in (
        {"start": 1, "end": 4},
        {"start": 0, "end": 999},
        {"start": 2, "end": 4, "card_id": "missing"},
    ):
        response = client.post(
            endpoint, json={**request, "client_request_id": uuid4().hex, "analysis_scope": scope}
        )
        assert response.status_code in {400, 404}
    assert client.get(f"{endpoint.removesuffix('/actions')}/tasks").json() == []


def test_incomplete_card_cannot_be_selected(client, reference):
    response = client.patch(
        f"/api/reference-sessions/{reference['id']}",
        json={
            "goal": "研究行动后的信息差",
            "base_revision": 0,
            "cards": [
                {
                    "id": "incomplete",
                    "observation": "角色看到表面迹象",
                    "interpretation": "留下信息差",
                    "technique": "  ",
                    "conditions": "视角可限定",
                    "evidence": SOURCE,
                }
            ],
            "selected_ids": ["incomplete"],
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "reference_card_incomplete"
