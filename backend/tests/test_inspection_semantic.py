import asyncio
from dataclasses import replace
import json
from uuid import uuid4

from ainovel.db import dumps
from ainovel.worker import Worker
from test_outline_generation import prepare
from test_outline_workspace import chapter_plan, put_and_confirm
from test_workflow_inspection import author, endpoint, read


def setup_scope(client, domain, story):
    outline, options, _, second = prepare(client, domain, story)
    chapter = domain.save(story["chapter_id"], 0, "学徒入门。😀铜铃仍旧沉默。")
    domain.save(second["id"], second["revision"], "不应出现在本次送审的第二章。")
    put_and_confirm(
        client,
        outline,
        {
            "base_revision": 0,
            "confirmation_id": options["confirmation_id"],
            "plan": {
                "summary": "学徒追查铁铃。",
                "volumes": [
                    {
                        "id": "vol",
                        "title": "遗铃",
                        "summary": "沿着门痕寻人。",
                        "units": [
                            {
                                "id": "unit",
                                "title": "验证",
                                "summary": "第一次进门时听见响铃。",
                                "chapters": [chapter_plan(chapter["id"], "进门后听见铜铃")],
                            }
                        ],
                    }
                ],
            },
        },
    )
    route = endpoint(story)
    scope = {"scope": "chapter", "scope_id": chapter["id"]}
    preview = read(client, route, **scope)["preview"]
    request = {
        **scope,
        "basis_sha256": preview["basis_sha256"],
        "account": "deepseek",
        "client_request_id": uuid4().hex,
    }
    return route, scope, request, chapter


class SemanticModel:
    def __init__(self, changed=None):
        self.calls = []
        self.changed = changed

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        assert instruction["动作"] == "对账立项章纲与节奏"
        self.calls.append(instruction)
        paragraph = next(iter(instruction["保存原文"]))
        assert "不应出现在本次送审" not in dumps(instruction)
        assert len(instruction["送审目录"]) == 1
        if self.changed:
            self.changed()
        result = [
            {
                "check_id": check_id,
                "result": status,
                "reason": "入门后正文明确写出铃声仍未出现。" if status == "fail" else "本次仅给出证据位置。",
                "paragraph_refs": [paragraph] if status != "unknown" else [],
                "basis_refs": ([instruction["对照依据"][check_id][0]["id"]] if status != "unknown" else []),
            }
            for check_id, status in (("01", "pass"), ("02", "fail"), ("10", "unknown"))
        ]
        return {
            "text": dumps({"items": result}),
            "usage": {"input": 1, "output": 1},
            "call_id": "offline-inspection",
        }


def test_frozen_candidate_keeps_proof_separate_from_author_signature(client, domain, settings, story):
    route, scope, request, chapter = setup_scope(client, domain, story)
    task = client.post(route + "/semantic", json=request)
    assert task.status_code == 202, task.text
    assert client.post(route + "/semantic", json=request).json()["id"] == task.json()["id"]
    model = SemanticModel()
    assert asyncio.run(Worker(settings, model).run_once())
    logged = read(client, route + "/semantic/tasks", **scope)[0]
    assert logged["status"] == "succeeded", logged
    candidate = read(client, route + "/semantic/" + logged["artifact_id"])
    assert candidate["current"] is True
    assert candidate["chapters"][0]["revision"] == chapter["revision"]
    assert candidate["items"][1]["evidence"] == [
        {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": 0,
            "end": len(chapter["content"].encode("utf-16-le")) // 2,
            "quote": chapter["content"],
        }
    ]
    assert candidate["items"][1]["basis_excerpts"][0]["text"] == "进门后听见铜铃"
    assert read(client, route, **scope)["latest"] is None
    body = {**scope, "basis_sha256": request["basis_sha256"]}
    verdict = {**author("02", chapter), "source_candidate_id": candidate["id"]}
    refused = client.post(route, json={**body, "author_reviews": [verdict]})
    assert refused.status_code == 400
    assert refused.json()["code"] == "inspection_candidate_override"
    copied = client.post(
        route,
        json={
            **body,
            "author_reviews": [
                {**verdict, "reason": candidate["items"][1]["reason"], "author_overrides_candidate": True}
            ],
        },
    )
    assert copied.status_code == 400
    assert copied.json()["code"] == "inspection_candidate_override"

    red = client.post(route, json={**body, "author_reviews": [{**verdict, "status": "red"}]})
    assert red.status_code == 201, red.text
    signed02 = next(row for row in red.json()["items"] if row["check_id"] == "02")
    assert signed02["current_status"] == "red"
    assert signed02["author_review"]["source_candidate_id"] == candidate["id"]

    green = client.post(
        route,
        json={
            **body,
            "author_reviews": [
                {
                    **verdict,
                    "reason": "作者复核后认定末句可保留，作为下一章补验的故意误导。",
                    "author_overrides_candidate": True,
                }
            ],
        },
    )
    assert green.status_code == 201, green.text
    assert next(row for row in green.json()["items"] if row["check_id"] == "02")["current_status"] == "green"

    domain.save(chapter["id"], chapter["revision"], "学徒后来听见了铃声。")
    assert not read(client, route + "/semantic/" + candidate["id"])["current"]
    preview = read(client, route, **scope)["preview"]
    rejected = client.post(
        route,
        json={
            **scope,
            "basis_sha256": preview["basis_sha256"],
            "author_reviews": [
                {**author("02", domain.chapter(chapter["id"]), "red"), "source_candidate_id": candidate["id"]}
            ],
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "inspection_candidate_stale"


def test_candidate_preserves_in_flight_changed_scope_without_auto_signing(client, domain, settings, story):
    route, scope, request, chapter = setup_scope(client, domain, story)
    task = client.post(route + "/semantic", json=request)
    assert task.status_code == 202, task.text
    provider = SemanticModel(lambda: domain.save(chapter["id"], chapter["revision"], "最新章稿。"))
    assert asyncio.run(Worker(settings, provider).run_once())
    logged = read(client, route + "/semantic/tasks", **scope)[0]
    assert logged["status"] == "succeeded", logged
    candidate = read(client, route + "/semantic/" + logged["artifact_id"])
    assert not candidate["current"]
    assert candidate["items"][1]["evidence"][0]["quote"] == chapter["content"]
    assert read(client, route, **scope)["latest"] is None


def test_semantic_candidate_does_not_truncate_scope_or_dispatch_over_limit(client, domain, settings, story):
    route, scope, request, _ = setup_scope(client, domain, story)
    task = client.post(route + "/semantic", json=request)
    assert task.status_code == 202, task.text
    settings.providers["deepseek"] = replace(settings.providers["deepseek"], max_input_bytes=1)
    model = SemanticModel()
    assert asyncio.run(Worker(settings, model).run_once())
    logged = read(client, route + "/semantic/tasks", **scope)[0]
    assert logged["status"] == "blocked_runtime", logged
    assert "改按章或卷送审" in logged["error"]
    assert not model.calls and logged["artifact_id"] is None
