from ainovel.models import ModuleInput
from test_author_contract import FIELDS, WORDS, adopt_intent


def path(story):
    return f"/api/branches/{story['branch_id']}/project-brief"


def save(client, endpoint, fields, base=None):
    response = client.put(endpoint, json={"fields": fields, "base_draft_id": base})
    assert response.status_code == 200, response.text
    return response.json()


def confirmation(state, *, key="confirm-brief-test", update=()):
    return {
        "draft_id": state["draft"]["id"],
        "base_adoption_id": state["adopted"]["id"] if state["adopted"] else None,
        "base_author_contract_id": state["author_contract"]["id"],
        "idempotency_key": key,
        "update_fields": list(update),
    }


def test_draft_resume_conflict_and_isolation(client, domain, story):
    endpoint = path(story)
    assert client.get(endpoint).json()["draft"] is None
    draft = save(client, endpoint, {"concept": "   补旧物能听见记忆。\n", "scope": "三卷"})
    assert draft["draft"]["fields"]["concept"] == "补旧物能听见记忆。"
    assert client.get(endpoint).json() == draft
    assert client.get(path(domain.create_novel("另一部书"))).json()["draft"] is None
    assert (
        client.put(endpoint, json={"fields": {"concept": "旧窗口"}, "base_draft_id": None}).status_code == 409
    )
    edited = save(client, endpoint, {"concept": "补旧物能找回梦。"}, draft["draft"]["id"])
    assert edited["draft"]["id"] != draft["draft"]["id"]
    assert domain.artifact(draft["draft"]["id"])["payload"]["fields"]["scope"] == "三卷"
    assert not domain.author_contract(story["branch_id"])["configured"]
    assert domain.chapter(story["chapter_id"])["revision"] == 0


def test_confirm_can_keep_intent_or_update_only_checked_field_and_preserve_task_snapshot(
    client, domain, story
):
    prior = adopt_intent(domain, story)
    branch_id = story["branch_id"]
    task_input = ModuleInput(
        novel_id=story["id"],
        branch_id=branch_id,
        action="ideation.explore",
        instruction="试想",
        account="openai_1",
    )
    old_task = domain.queue("module", task_input.model_dump())
    endpoint = path(story)
    draft = save(
        client, endpoint, {"direction": "改去渡口。", "ending": "亲手归还铃铛。", "last_image": "放灯。"}
    )
    assert draft["referencing_task_count"] == 1
    assert draft["referencing_tasks"][0]["id"] == old_task["id"]
    kept = client.post(endpoint + "/adopt", json=confirmation(draft)).json()
    assert kept["adopted"]["applied_fields"] == []
    assert kept["adopted"]["intent_id"] == prior["id"]
    assert domain.author_contract(branch_id)["intent_contract"] == FIELDS

    request = confirmation(kept, key="change-brief-ending", update=("ending",))
    synced = client.post(endpoint + "/adopt", json=request)
    assert synced.status_code == 200, synced.text
    saved = synced.json()
    assert saved["adopted"]["applied_fields"] == ["ending"]
    assert saved["adopted"]["previous_intent_id"] == prior["id"]
    assert client.post(endpoint + "/adopt", json=request).json() == saved
    assert domain.author_contract(branch_id)["intent_contract"] == {**FIELDS, "ending": "亲手归还铃铛。"}
    assert domain.author_contract(branch_id)["author_text"] == WORDS
    assert domain.task(old_task["id"])["result"]["author_contract_snapshot"]["intent_contract"] == FIELDS
    new_task = domain.queue("module", task_input.model_dump())
    assert new_task["result"]["author_contract_snapshot"]["intent_contract"]["ending"] == "亲手归还铃铛。"
    assert domain.chapter(story["chapter_id"])["content"] == ""


def test_confirmation_stops_when_contract_or_draft_changes(client, domain, story):
    endpoint = path(story)
    draft = save(client, endpoint, {"ending": "仍在等待来信。"})
    request = confirmation(draft, update=("ending",))
    adopt_intent(domain, story)
    assert client.post(endpoint + "/adopt", json=request).status_code == 409
    saved = client.get(endpoint).json()
    assert saved["draft"]["id"] == draft["draft"]["id"]
    assert saved["adopted"] is None
    fresh = confirmation(saved, update=("ending",))
    save(client, endpoint, {"ending": "找到遗落的信。"}, saved["draft"]["id"])
    assert client.post(endpoint + "/adopt", json=fresh).status_code == 409
    assert client.get(endpoint).json()["adopted"] is None
