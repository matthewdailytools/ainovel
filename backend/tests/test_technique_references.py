"""Version-bound chapter reuse of a reference technique."""

import asyncio
import json

from ainovel.library import Library
from ainovel.worker import Worker

from test_reference_flow import update


def test_chapter_technique_keeps_original_occurrence_and_writes_only_on_opt_in(
    client, domain, settings, story
):
    chapter = domain.chapter(story["chapter_id"])
    branch_id = story["branch_id"]
    second = domain.create_chapter(branch_id, "下一章")
    evidence = "🛶桥上留了一盏灯。"
    original = f"她先交代约定。{evidence}夜色已深。{evidence}后来人记得那条河。"
    book = Library(domain.db, settings.data_dir).import_file("试读旧稿.txt", original.encode())
    unit = domain.db.one("SELECT * FROM source_units WHERE book_id=?", (book["id"],))
    response = client.post(
        "/api/reference-sessions",
        json={
            "unit_id": unit["id"],
            "start": 0,
            "end": len(original.encode("utf-16-le")) // 2,
            "quote": original,
            "goal": "核对用重复意象推进情节的写法。",
        },
    )
    assert response.status_code == 201, response.text
    session = response.json()
    card = {
        "id": "the-card",
        "observation": "两次遇到灯，主人公反应有变化。",
        "interpretation": "重复可能让读者感到约定的效力。",
        "technique": "第二次出现时让意象改变选择。",
        "conditions": "先建立一个可被辨认的约定。",
        "exceptions": "前次出现已解决主线时可以省略。",
        "evidence": evidence,
    }
    session = update(client, domain, session, cards=[card])
    base = f"/api/branches/{branch_id}/chapters/{chapter['id']}/technique-references"
    select = {"session_id": session["id"], "session_revision": session["revision"], "card_id": card["id"]}
    assert client.post(base, json=select).status_code == 409

    card["replacement"] = "另设地点、意象和关系，由本书冲突决定第二次选择。"
    session = update(client, domain, session, cards=[card])
    select["session_revision"] = session["revision"]
    source_url = f"/api/reference-sessions/{session['id']}/cards/{card['id']}/source"
    located = client.get(source_url, params={"revision": session["revision"]})
    assert located.status_code == 200, located.text
    first, last = located.json()["passages"]
    assert first["quote"] == last["quote"] == evidence
    assert last["start"] == len(original[: original.rfind(evidence)].encode("utf-16-le")) // 2
    assert last["after"] == "后来人记得那条河。"
    assert client.post(base, json=select).status_code == 409
    selected = {**select, "evidence_start": last["start"], "application_note": "放在人物作决定之前。"}
    cited = client.post(base, json=selected)
    assert cited.status_code == 201, cited.text
    pin = cited.json()
    assert client.post(base, json=selected).json() == pin
    second_base = f"/api/branches/{branch_id}/chapters/{second['id']}/technique-references"
    reused = client.post(second_base, json=selected)
    assert reused.status_code == 201, reused.text
    second_pin = reused.json()
    assert second_pin == {**pin, "id": second_pin["id"], "chapter_id": second["id"]}
    assert second_pin["id"] != pin["id"]

    # A second extraction replaces the card in the editor but cannot change the citation.
    next_card = {**card, "technique": "用独立副线验证承诺。", "evidence": "后来人记得那条河。"}
    new_session = update(client, domain, session, cards=[next_card])
    [stored] = client.get(base).json()
    assert stored == {**pin, "current_session_revision": new_session["revision"]}
    assert client.get(second_base).json() == [
        {**second_pin, "current_session_revision": new_session["revision"]}
    ]
    frozen = client.get(f"{base}/{pin['id']}/source")
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["passages"] == [last]
    historical = client.get(source_url, params={"revision": session["revision"]}).json()
    assert historical["passages"] == [first, last]
    assert client.post(base, json=selected).status_code == 409

    class Model:
        def __init__(self):
            self.requests = []

        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            self.requests.append(json.loads(prompt.split("\n只返回")[0]))
            return {"text": "安排第二次出现的线索触发选择。", "usage": {"input": 1, "output": 1}}

    request = {
        "action": "writing.advise",
        "novel_id": story["id"],
        "branch_id": branch_id,
        "chapter_id": chapter["id"],
        "base_revision": chapter["revision"],
        "account": "deepseek",
        "instruction": "看看下一步怎么展开。",
    }
    with_pin = {**request, "technique_reference_ids": [pin["id"]]}
    wrong = client.post(
        "/api/modules", json={**with_pin, "chapter_id": second["id"], "base_revision": second["revision"]}
    )
    assert wrong.status_code == 404, wrong.text
    wrong_mode = client.post("/api/modules", json={**with_pin, "action": "memory.extract"})
    assert wrong_mode.status_code == 409, wrong_mode.text
    implicit = client.post("/api/modules", json=request)
    chosen = client.post("/api/modules", json=with_pin)
    assert implicit.status_code == chosen.status_code == 202, chosen.text
    assert "technique_reference_snapshot" not in implicit.json()["result"]
    snapshot = chosen.json()["result"]["technique_reference_snapshot"]
    assert snapshot[0]["session_revision"] == session["revision"]
    model = Model()
    for _ in range(2):
        assert asyncio.run(Worker(settings, model).run_once())
    assert "作者本次选用的手法" not in model.requests[0]["任务"]
    assert model.requests[1]["任务"]["作者本次选用的手法"] == [
        {
            **{k: card[k] for k in ("technique", "conditions", "exceptions", "replacement")},
            "application_note": selected["application_note"],
        }
    ]
    assert evidence not in json.dumps(model.requests[1], ensure_ascii=False)
    completed = domain.task(chosen.json()["id"])
    assert completed["status"] == "succeeded", completed["error"]
    assert domain.artifact(completed["result"]["artifact_id"])["basis"]["technique_references"] == snapshot
    assert domain.chapter(chapter["id"]) == chapter
