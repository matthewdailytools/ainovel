"""Explicit correction never uses candidate prose to reinterpret the AI plan."""

import asyncio
import json
from urllib.parse import urlencode

import httpx
import pytest

from ainovel import prepared_source_scope_correction as correction
from ainovel.db import dumps
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from helpers.prepared_scope_fixture import stopped, inventory_output, preservation_output, BODY
from test_candidate_recheck import assert_parent_not_resumed
from test_openai_schema import completed


@pytest.fixture
def old_auto(settings, domain, story):
    return stopped(settings, domain, story)


def submit(client, candidate, **changes):
    preview = client.get(f"/api/artifacts/{candidate['id']}/prepared-source-scope-preview")
    assert preview.status_code == 200, preview.text
    return client.post(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-corrections",
        json={
            "candidate_revision": candidate["candidate_revision"],
            "preview_basis_sha256": preview.json()["basis_sha256"],
            "account": "openai_1",
            "client_request_id": "new-scope-request-123",
            "item_ids": ["P01"],
            "reason": "",
            "budget_limit": 1,
            **changes,
        },
    )


class SourceModel:
    def __init__(self, *, unknown=False, verdict="pass", sufficient="yes", kind="named_destination"):
        self.frames = []
        self.unknown = unknown
        self.verdict = verdict
        self.sufficient = sufficient
        self.kind = kind

    async def generate(self, account, task_id, system, prompt, max_output, **kw):
        frame = json.loads(prompt.split("\n只返回")[0])
        self.frames.append(frame)
        i = frame["任务"]
        if i["动作"] == correction.OBSERVE:
            value = inventory_output(i, unknown=self.unknown, kind=self.kind)
        else:
            assert i["动作"] == correction.VERIFY
            value = preservation_output(i, verdict=self.verdict, sufficient=self.sufficient)
        return {
            "text": dumps(value),
            "usage": {"input": 1, "output": 1},
            "call_id": f"scope-{len(self.frames)}",
        }


def execute(settings, domain, task, model=None):
    model = model or SourceModel()
    asyncio.run(Worker(settings, model).run_once())
    done = domain.task(task["id"])
    assert done["status"] == "succeeded", done["error"]
    return domain.artifact(done["result"]["artifact_id"]), model


def test_official_source_two_stages_preserve_history_and_require_separate_body(
    client, domain, settings, old_auto
):
    parent, candidate = old_auto
    response = submit(client, candidate, reason="AUDIT_REASON_SENTINEL不得成为来源条件")
    assert response.status_code == 202, response.text
    task = response.json()
    receipt, model = execute(settings, domain, task)
    assert receipt["payload"]["state"] == "ready"
    assert [f["任务"]["动作"] for f in model.frames] == [correction.OBSERVE, correction.VERIFY]
    raw = dumps(model.frames[0])
    assert "不能因章纲是AI规划就把全部事件标planned" in model.frames[0]["任务"]["要求"]
    preserve = dumps(model.frames[1])
    for secret in (
        BODY,
        "OLD_REVIEW_SENTINEL",
        "OLD_BODY_CHECK_SENTINEL",
        "FUTURE_PLAN_SENTINEL",
        "FUTURE_BRIEF_SENTINEL",
        "ARCHIVED_INTERPRETATION_SENTINEL",
        "AUDIT_REASON_SENTINEL",
    ):
        assert secret not in raw
    assert "原全部P提案" not in raw and "固定条件" not in model.frames[0]["任务"]
    assert all(
        "genre_profile" not in f["上下文"] and "author_contract" not in f["上下文"] for f in model.frames
    )
    assert BODY not in preserve and "OLD_BODY_CHECK_SENTINEL" not in preserve
    core = receipt["payload"]["corrected_contract"]
    old = parent["result"]["plot_preparations"]["1"]["prepared_contract"]
    assert core["items"][0]["recipient_scope"] == "unspecified" and core["items"][0]["scope_condition"] == ""
    assert all(core["items"][0][k] == v for k, v in old["items"][0].items() if k not in correction.FIELDS)
    assert domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)
    again = submit(client, candidate, reason="AUDIT_REASON_SENTINEL不得成为来源条件")
    assert again.json()["id"] == task["id"]
    query = client.get(
        f"/api/artifacts/{candidate['id']}/prepared-source-scope-corrections?"
        + urlencode({"client_request_id": task["payload"]["client_request_id"]})
    )
    assert query.status_code == 200 and query.json()["task"]["id"] == task["id"]
    url = f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview"
    preview = client.get(url, params={"receipt_id": receipt["id"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["version"] == correction.RECHECK_VERSION
    body = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "receipt_id": receipt["id"],
            "preview_basis_sha256": preview.json()["basis_sha256"],
            "account": "openai_1",
            "client_request_id": "explicit-prose-request-123",
            "budget_limit": 1,
        },
    )
    assert body.status_code == 202, body.text
    assert body.json()["result"]["source_corrected_recheck_version"] == correction.RECHECK_VERSION
    assert body.json()["result"]["flow_source_entailment_version"] == "source-flow-entailment/2"
    assert body.json()["result"]["source_corrected_recheck_snapshot"]["prepared_contract"] == core
    assert (
        domain.artifact(candidate["id"]) == candidate
        and domain.chapter(candidate["chapter_id"])["content"] == ""
    )


@pytest.mark.parametrize(
    "unknown,verdict,sufficient,calls",
    [
        (True, "pass", "yes", 1),
        (False, "unknown", "unknown", 2),
        (False, "fail", "no", 2),
        (False, "pass", "no", 2),
    ],
)
def test_uncertain_or_semantically_insufficient_scope_is_readonly_not_body_ready(
    client, domain, settings, old_auto, unknown, verdict, sufficient, calls
):
    parent, candidate = old_auto
    task = submit(client, candidate).json()
    receipt, model = execute(
        settings, domain, task, SourceModel(unknown=unknown, verdict=verdict, sufficient=sufficient)
    )
    assert receipt["payload"]["state"] == "unverified" and len(model.frames) == calls
    assert (
        client.get(
            f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
            params={"receipt_id": receipt["id"]},
        ).status_code
        == 409
    )
    assert domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize("operation", ["accept", "reject", "edit"])
def test_receipt_is_not_editable_or_adoptable(client, domain, settings, old_auto, operation):
    _, candidate = old_auto
    receipt, _ = execute(settings, domain, submit(client, candidate).json())
    if operation == "edit":
        response = client.patch(
            f"/api/artifacts/{receipt['id']}", json={"candidate_revision": 0, "text": "伪造来源就绪"}
        )
    else:
        response = client.post(
            f"/api/artifacts/{receipt['id']}/{operation}",
            json={
                "candidate_revision": 0,
                **({"idempotency_key": "receipt-never-accept"} if operation == "accept" else {}),
            },
        )
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
def test_real_modelclient_wire_source_scope_only(client, domain, settings, story, account):
    parent, candidate = stopped(settings, domain, story, account=account)
    task = submit(client, candidate, account=account).json()
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if account == "openai_1":
            prompt = body["input"][-1]["content"] if isinstance(body["input"], list) else body["input"]
        else:
            prompt = body["messages"][-1]["content"]
        frame = json.loads(prompt.split("\n只返回")[0])
        i = frame["任务"]
        value = inventory_output(i) if i["动作"] == correction.OBSERVE else preservation_output(i)
        if account == "openai_1":
            return completed(dumps(value))
        return httpx.Response(
            200,
            content="data: "
            + dumps(
                {
                    "choices": [{"delta": {"content": dumps(value)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                }
            )
            + "\n\ndata: [DONE]\n\n",
        )

    model = ModelClient(
        settings,
        __import__("ainovel.budget", fromlist=["Budget"]).Budget(settings),
        transport=httpx.MockTransport(handle),
    )
    receipt, _ = execute(settings, domain, task, model)
    assert receipt["payload"]["state"] == "ready" and len(requests) == 2
    assert_parent_not_resumed(domain, parent)


class BodyModel(SourceModel):
    async def generate(self, account, task_id, system, prompt, max_output, **kw):
        frame = json.loads(prompt.split("\n只返回")[0])
        i = frame["任务"]
        a = i["动作"]
        self.frames.append(frame)
        if a == "审查正文":
            value = {
                "checks": [
                    {"condition": f"FIX{n:03}", "result": "pass", "evidence": i["可引用原文"][0]["source_id"]}
                    for n, _ in enumerate(i["固定条件"], 1)
                ],
                "findings": [],
                "summary": "只作工程核对。",
            }
        elif a == "逐项对照作者情节解释与本次正文":
            assert i["独立整理的解释提案"][0]["recipient_scope"] == "unspecified"
            value = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": "unknown",
                        "observed": "工程替身保留正文待审。",
                        "evidence": "",
                        "reason": "来源就绪不等于正文通过。",
                        "repair": "",
                    }
                ],
                "summary": "正文仍待核对。",
            }
        elif a == "只从本次正文独立提取物品与信息流向":
            entities = {v: k for k, v in i["实体别名对照"].items()}
            value = {
                "flows": [
                    {
                        "object_id": "O01",
                        "supplier_id": entities["林舟"],
                        "recipient_id": entities["青石测量所"],
                        "stage": "completed",
                        "certainty": "explicit",
                        "evidence": i["可引用原文"][0]["source_id"],
                        "note": "正文寄出的抄本。",
                    }
                ],
                "note": "工程观察。",
            }
        elif a == "独立核对正文流向观察的来源蕴含":
            assert i["流向来源蕴含版本"] == "source-flow-entailment/2"
            value = {
                "input_sha256": i["input_sha256"],
                "checks": [
                    {
                        "flow_id": f["flow_id"],
                        "result": "supported",
                        "verified_fields": [
                            "object_id",
                            "supplier_id",
                            "recipient_id",
                            "stage",
                            "certainty",
                            "evidence",
                        ],
                        "evidence": i["可引用原文"][0]["source_id"],
                        "reason": "独立工程核实；不是来源规划或文学评价。",
                    }
                    for f in i["待核对观察提案"]
                ],
                "participants": [],
                "instances": [],
                "note": "工程替身。",
            }
        else:
            raise AssertionError(a)
        return {
            "text": dumps(value),
            "usage": {"input": 1, "output": 1},
            "call_id": f"body-{len(self.frames)}",
        }


def test_explicit_body_uses_flow2_but_keeps_unknown_and_never_adopts_or_resumes(
    client, domain, settings, old_auto
):
    parent, candidate = old_auto
    source, _ = execute(settings, domain, submit(client, candidate).json())
    preview = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": source["id"]},
    ).json()
    task = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "receipt_id": source["id"],
            "preview_basis_sha256": preview["basis_sha256"],
            "account": "openai_1",
            "client_request_id": "scope-body-explicit-123",
            "budget_limit": 1,
        },
    ).json()
    report, model = execute(settings, domain, task, BodyModel())
    assert len(model.frames) == 4 and report["payload"]["review_passed"] is False
    assert report["payload"]["plot_audit"]["status"] == "unverified"
    assert report["payload"]["plot_audit"]["source_scope_correction"]["receipt_id"] == source["id"]
    after = domain.artifact(candidate["id"])
    assert after["payload"]["previous_plot_audit"] == candidate["payload"]["plot_audit"]
    assert after["payload"]["text"] == candidate["payload"]["text"] and after["candidate_revision"] == 0
    assert domain.chapter(candidate["chapter_id"])["content"] == ""
    assert_parent_not_resumed(domain, parent)
    assert domain.artifact(source["id"]) == source


@pytest.mark.parametrize("sufficient", ["no", "unknown"])
def test_old_ai_note_is_independently_read_but_never_exposed_to_raw_observer_or_rewritten(
    client, domain, settings, story, sufficient
):
    historical_note = "NOTE_ONLY_SENTINEL；接收范围排除其他全部人。"
    parent, candidate = stopped(settings, domain, story, note=historical_note)

    class NoteModel(SourceModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kw):
            frame = json.loads(prompt.split("\n只返回")[0])
            i = frame["任务"]
            if i["动作"] == correction.OBSERVE:
                assert historical_note not in dumps(frame)
                return await super().generate(account, task_id, system, prompt, max_output, **kw)
            self.frames.append(frame)
            assert i["原AI解释备注（历史提案，非来源/非新锁定）"] == historical_note
            value = preservation_output(
                i, verdict="fail" if sufficient == "no" else "unknown", sufficient=sufficient
            )
            return {
                "text": dumps(value),
                "usage": {"input": 1, "output": 1},
                "call_id": "note-independent-only",
            }

    task = submit(client, candidate).json()
    receipt, model = execute(settings, domain, task, NoteModel())
    assert len(model.frames) == 2 and receipt["payload"]["state"] == "unverified"
    assert receipt["payload"]["corrected_contract"]["note"] == historical_note
    assert receipt["payload"]["corrected_contract"]["items"][0]["recipient_scope"] == "unspecified"
    assert domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)
    assert (
        client.get(
            f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
            params={"receipt_id": receipt["id"]},
        ).status_code
        == 409
    )
