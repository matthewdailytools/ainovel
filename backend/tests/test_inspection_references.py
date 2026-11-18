import asyncio
from hashlib import sha256
import json

import pytest

from ainovel.db import uid
from ainovel.manuscript_exchange import export_manuscript
from ainovel.worker import Worker
from ainovel.workflow_archive import workflow_object
from test_outline_workspace import chapter_plan, put_and_confirm
from test_reference_flow import SOURCE, reference as reference_fixture, update
from test_workflow_inspection import author, endpoint, item, read, report

reference = reference_fixture


@pytest.mark.parametrize("pin_address", [False, True])
def test_inspection_keeps_planned_and_adopted_card_sources_separate(
    client, domain, settings, story, reference, pin_address
):
    branch = story["branch_id"]
    chapter_id = story["chapter_id"]
    chapter_url = f"/api/chapters/{chapter_id}"
    manual = client.put(chapter_url, json={"base_revision": 0, "content": "学徒在门外看到半枚铃痕。"})
    assert manual.status_code == 200, manual.text
    manuscript = manual.json()
    unrelated = domain.create_chapter(branch, "第二章")
    card = {
        "id": "partial-clue",
        "observation": "只得到一部分信息。",
        "interpretation": "局部收获明确下一步。",
        "technique": "留下另一条可追查的线索",
        "conditions": "信息不足以立刻定案。",
        "exceptions": "替换道具、人物与情节。",
        "evidence": "😀渡口",
    }
    saved = update(client, domain, reference, cards=[card])
    collection = f"/api/branches/{branch}/reference-cards"
    original = read(client, collection)[0]["version"]
    address = {"session_id": saved["id"], "card_id": card["id"], "revision": saved["revision"]}
    if pin_address:
        address["version_id"] = original["id"]
    plan = {
        "summary": "凭部分铃痕追到旧铺。",
        "volumes": [
            {
                "id": "volume",
                "title": "遗铃",
                "summary": "找到旧铺。",
                "units": [
                    {
                        "id": "unit",
                        "title": "拾铃",
                        "summary": "辨认刻痕。",
                        "chapters": [
                            {
                                **chapter_plan(chapter_id, "认出一半铃痕"),
                                "setting_ids": [],
                                "reference_cards": [address],
                            },
                            {**chapter_plan(unrelated["id"], "问到旧铺地址"), "setting_ids": []},
                        ],
                    }
                ],
            }
        ],
    }
    outline = put_and_confirm(
        client, f"/api/branches/{branch}/outline-workspace", {"base_revision": 0, "plan": plan}
    )["confirmed"]
    update(client, domain, saved, cards=[{**card, "technique": "先封存全部证据"}])
    route = endpoint(story)
    scope = {"scope": "chapter", "scope_id": chapter_id}
    initial = read(client, route, **scope)
    assert initial["latest"] is None
    frozen_source = {
        **address,
        "version_id": original["id"],
        "chapter_id": chapter_id,
        "source": original["citation"],
    }
    assert original["citation"]["evidence_sha256"] == sha256(card["evidence"].encode()).hexdigest()
    draft = item(initial["preview"], "08")
    assert draft["basis"]["planned_card_references"] == [frozen_source]
    assert draft["basis"]["card_references"] == []
    assert draft["status"] == "pending"
    sign_off = report(client, route, initial["preview"], author_reviews=[author("08", manuscript)])
    assert item(sign_off, "08")["status"] == "green"
    unrelated_check = item(read(client, route, scope="chapter", scope_id=unrelated["id"])["preview"], "08")
    assert unrelated_check["basis"].get("planned_card_references", []) == []
    assert item(read(client, route, scope="volume", scope_id="volume")["preview"], "08")["basis"][
        "planned_card_references"
    ] == [frozen_source]

    replacement = "学徒从半枚刻痕确定了该往哪家铺子查。"

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            assert SOURCE not in prompt
            frame = json.loads(prompt.split("\n只返回")[0])
            frozen = frame["上下文"]["采用章纲"]["reference_cards"][0]
            assert frozen["revision"] == original["revision"] and frozen["technique"] == card["technique"]
            return {"text": replacement, "usage": {"input": 1, "output": 1}, "call_id": uid()}

    queued = client.post(
        "/api/modules",
        json={
            "novel_id": story["id"],
            "branch_id": branch,
            "chapter_id": chapter_id,
            "base_revision": manuscript["revision"],
            "action": "writing.rewrite_selection",
            "instruction": "聚焦刻痕带来的可行动线索。",
            "selection_start": 0,
            "selection_end": len(manuscript["content"]),
            "selection_text": manuscript["content"],
            "outline_revision_id": outline["id"],
            "account": "deepseek",
        },
    )
    assert queued.status_code == 202, queued.text
    assert asyncio.run(Worker(settings, Model()).run_once())
    task = domain.task(queued.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    artifact_id = task["result"]["artifact_id"]
    assert item(read(client, route, **scope)["preview"], "08")["basis"]["card_references"] == []
    applied = client.post(
        f"/api/artifacts/{artifact_id}/accept",
        json={"idempotency_key": uid(), "candidate_revision": 0},
    )
    assert applied.status_code == 200, applied.text
    accepted = read(client, chapter_url)
    assert accepted["revision"] == applied.json()["revision"]
    assert accepted["content"] == replacement
    after = read(client, route, **scope)["preview"]
    check = item(after, "08")
    assert check["basis"]["planned_card_references"] == [frozen_source]
    assert check["basis"]["card_references"] == [
        {**frozen_source, "artifact_id": artifact_id, "chapter_revision": accepted["revision"]}
    ]
    assert check["status"] == "pending"
    latest = report(client, route, after, author_reviews=[author("08", accepted)])

    class InspectModel:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            assert card["evidence"] not in prompt and SOURCE not in prompt
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            assert instruction["动作"] == "对账立项章纲与节奏"
            assert tuple(instruction["对照依据"]) == ("01", "02", "10")
            assert [entry["quote"] for entry in instruction["保存原文"].values()] == [replacement]
            items = [
                {
                    "check_id": identifier,
                    "result": "unknown",
                    "reason": "需要作者继续核对本章与全书安排。",
                    "paragraph_refs": [],
                    "basis_refs": [],
                }
                for identifier in ("01", "02", "10")
            ]
            return {
                "text": json.dumps({"items": items}),
                "usage": {"input": 1, "output": 1},
                "call_id": uid(),
            }

    pending = client.post(
        route + "/semantic",
        json={
            **scope,
            "basis_sha256": after["basis_sha256"],
            "account": "deepseek",
            "client_request_id": uid(),
        },
    )
    assert pending.status_code == 202, pending.text
    assert asyncio.run(Worker(settings, InspectModel()).run_once())
    candidate = domain.task(pending.json()["id"])
    assert candidate["status"] == "succeeded", candidate["error"]
    assert read(client, route + f"/semantic/{candidate['result']['artifact_id']}")["current"]
    assert read(client, route, **scope)["latest"]["id"] == latest["id"]
    archived = export_manuscript(domain, branch).workflow_history
    assert card["evidence"] not in archived.model_dump_json()
    exact = next(row for row in archived.inspections if row.id == latest["id"])
    outbound = workflow_object(branch, exact)["entry_json"]
    assert card["evidence"] not in outbound and SOURCE not in outbound

    edited = client.put(
        chapter_url, json={"base_revision": accepted["revision"], "content": "作者改写了线索。"}
    )
    assert edited.status_code == 200, edited.text
    current = read(client, route, **scope)
    assert item(current["preview"], "08")["basis"]["planned_card_references"] == [frozen_source]
    assert item(current["preview"], "08")["basis"]["card_references"] == []
    assert item(current["latest"], "08")["current_status"] == "outdated"
    historic = read(client, route + f"/{latest['id']}")
    assert item(historic, "08")["basis"]["card_references"] == check["basis"]["card_references"]
