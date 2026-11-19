import asyncio
from dataclasses import replace
import json
from uuid import uuid4

import httpx
import pytest

from ainovel.budget import Budget
from ainovel.db import dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from test_outline_generation import prepare
from test_outline_workspace import chapter_plan, put_and_confirm
from test_workflow_inspection import author, endpoint, read


def setup_scope(client, domain, story):
    outline, options, _, second = prepare(client, domain, story)
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, "学徒入门。😀铜铃仍旧沉默。")
    domain.save(second["id"], second["revision"], "不应出现在本次送审的第二章。")
    third = domain.create_chapter(branch, "第三章")
    domain.save(third["id"], 0, "不应出现在本次送审的第三章。")
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
                            },
                            {
                                "id": "sibling",
                                "title": "再查",
                                "summary": "不应出现在本次送审的同卷另一单元摘要。",
                                "chapters": [chapter_plan(second["id"], "跟随门痕到石井")],
                            },
                        ],
                    },
                    {
                        "id": "other-volume",
                        "title": "回响",
                        "summary": "不应出现在本次送审的另一卷摘要。",
                        "units": [
                            {
                                "id": "other-unit",
                                "title": "回家",
                                "summary": "不应出现在本次送审的另一卷单元摘要。",
                                "chapters": [chapter_plan(third["id"], "将铜铃放回门前")],
                            }
                        ],
                    },
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


def confirm_unit_summary(client, story, summary):
    outline = f"/api/branches/{story['branch_id']}/outline-workspace"
    confirmed = read(client, outline)["confirmed"]
    revised = confirmed["plan"]
    revised["volumes"][0]["units"][0]["summary"] = summary
    put_and_confirm(
        client,
        outline,
        {
            "base_revision": confirmed["revision"],
            "confirmation_id": confirmed["setting_basis"]["confirmation_id"],
            "plan": revised,
        },
    )


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
        basis = instruction["对照依据"]
        ending = next(row for row in basis["01"] if row["label"].endswith(" · 终局"))
        pacing_refs = [
            row["id"] for row in basis["10"] if row["text"] in {"沿着门痕寻人。", "第一次进门时听见响铃。"}
        ]
        results = (
            ("01", "pass", "本次仅给出证据位置。", [basis["01"][0]["id"], ending["id"]]),
            ("02", "fail", "入门后正文明确写出铃声仍未出现。", [basis["02"][0]["id"]]),
            ("10", "fail", "单元安排要求入门时听见铃声，正文却说明铃声仍未响起。", pacing_refs),
        )
        result = [
            {
                "check_id": check_id,
                "result": status,
                "reason": reason,
                "paragraph_refs": [paragraph],
                "basis_refs": references,
            }
            for check_id, status, reason, references in results
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
    pacing = candidate["items"][2]
    assert pacing["result"] == "fail" and pacing["evidence"] == candidate["items"][1]["evidence"]
    excerpts = pacing["basis_excerpts"]
    assert [(row["label"].split(" · ")[0], row["text"]) for row in excerpts] == [
        ("卷纲", "沿着门痕寻人。"),
        ("单元纲", "第一次进门时听见响铃。"),
    ]
    sent = model.calls[0]["对照依据"]
    assert all(
        not any(row["text"] in {"沿着门痕寻人。", "第一次进门时听见响铃。"} for row in sent[key])
        for key in ("01", "02")
    )
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
    assert all(
        row["current_status"] == "pending" and not row.get("author_review")
        for row in red.json()["items"]
        if row["check_id"] in {"01", "10"}
    )

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


@pytest.mark.parametrize("changed", ["manuscript", "pacing", "project"])
def test_candidate_preserves_in_flight_changed_scope_without_auto_signing(
    client, domain, settings, story, changed
):
    route, scope, request, chapter = setup_scope(client, domain, story)
    task = client.post(route + "/semantic", json=request)
    assert task.status_code == 202, task.text
    project_route = f"/api/branches/{story['branch_id']}/conception/project-brief"
    old_project = read(client, project_route)
    new_ending = "学徒将铜铃交给渡口守灯人，独自离开。"

    def edit():
        if changed == "pacing":
            confirm_unit_summary(client, story, "保留悬念，到第二次进门才响铃。")
        elif changed == "project":
            revision = old_project["revision"]
            impact = read(client, project_route + "/impact", base_revision=revision)
            updated = client.put(
                project_route,
                json={
                    "base_revision": revision,
                    "idempotency_key": uuid4().hex,
                    "idea": old_project["idea"],
                    "brief": {**old_project["brief"], "ending": new_ending},
                    "impact_sha256": impact["basis_sha256"],
                },
            )
            assert updated.status_code == 200, updated.text
        else:
            domain.save(chapter["id"], chapter["revision"], "最新章稿。")

    provider = SemanticModel(edit)
    assert asyncio.run(Worker(settings, provider).run_once())
    logged = read(client, route + "/semantic/tasks", **scope)[0]
    assert logged["status"] == "succeeded", logged
    candidate = read(client, route + "/semantic/" + logged["artifact_id"])
    assert not candidate["current"]
    assert candidate["items"][1]["evidence"][0]["quote"] == chapter["content"]
    assert candidate["items"][2]["basis_excerpts"][1]["text"] == "第一次进门时听见响铃。"
    assert candidate["items"][0]["basis_excerpts"][1]["text"] == old_project["brief"]["ending"]
    if changed == "project":
        assert read(client, project_route)["brief"]["ending"] == new_ending
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


def test_frozen_pacing_context_counts_toward_the_complete_request_limit(
    client, domain, settings, story, monkeypatch
):
    route, scope, request, chapter = setup_scope(client, domain, story)
    budget = Budget(settings)
    recorded = []

    def measure(message):
        recorded.append(len(message.content))
        return httpx.Response(400)

    assert client.post(route + "/semantic", json=request).status_code == 202
    baseline = ModelClient(settings, budget, httpx.MockTransport(measure))
    assert asyncio.run(Worker(settings, baseline).run_once())
    (sent_bytes,) = recorded
    limit = sent_bytes + 32
    assert len(chapter["content"].encode()) < limit
    settings.providers["deepseek"] = replace(settings.providers["deepseek"], max_input_bytes=limit)

    confirm_unit_summary(client, story, "在入门前用三次阻碍积累悬念，等初次展示铜铃后回报疑问。" * 120)
    request.update(
        basis_sha256=read(client, route, **scope)["preview"]["basis_sha256"],
        client_request_id=uuid4().hex,
    )
    assert client.post(route + "/semantic", json=request).status_code == 202
    before = budget.report()
    monkeypatch.setattr(budget, "reserve", lambda *_args: pytest.fail("request reserved budget"))
    model = ModelClient(
        settings, budget, httpx.MockTransport(lambda _: pytest.fail("request reached the network transport"))
    )
    assert asyncio.run(Worker(settings, model).run_once())
    logged = read(client, route + "/semantic/tasks", **scope)[0]
    assert logged["status"] == "blocked_runtime", logged
    assert "改按章或卷送审" in logged["error"]
    assert logged["artifact_id"] is None and budget.report() == before
