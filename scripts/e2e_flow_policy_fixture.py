"""Safe legacy checkpoints and sent-timeout fixtures for the disposable E2E server only."""

from copy import deepcopy
from pathlib import Path
from time import time

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ainovel.budget import Budget
from ainovel.db import DomainError, dumps, uid
from ainovel.flow_entailment import KEY
from ainovel.models import WorkflowInput
from ainovel.providers import ModelClient
from ainovel.story_extraction import AdoptExtraction, adopt
from ainovel.story_graph import RECORD

TIMEOUT_SENTINEL = "flow-policy-native-timeout"
PREFIX = "/test/flow-policy"
TEXT = "沈砚用旧尺测量潮水，留下可以回查的记录。" * 15


def guard(settings, temporary_directory):
    location = Path(temporary_directory).resolve()
    if (
        not settings.test_mode
        or settings.data_dir.resolve() != location
        or not location.is_dir()
        or not location.name.startswith("ainovel-browser-tests-")
        or set(settings.providers) != {"offline"}
        or settings.providers["offline"].base_url != "https://example.invalid"
    ):
        raise RuntimeError("FLOW浏览器夹具仅允许真正的隔离临时目录及offline配置。")


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=128)


def create_checkpoint(domain, settings, nonce):
    novel = domain.create_novel("后台验证 · 新版FLOW真实链路", "原创工程夹具，不是文学评测。")
    chapters = [domain.create_chapter(novel["branch_id"], f"工程第{n}章", n) for n in range(1, 4)]
    plots = [
        {"number": n, "title": chapter["title"], "plot": "沈砚测量并记录潮水读数，保留疑点。", "fixed": []}
        for n, chapter in enumerate(chapters, 1)
    ]
    payload = WorkflowInput(
        novel_id=novel["id"],
        mode="automatic",
        chapters=3,
        brief="FLOW原生浏览器工程夹具：测量并记录，不代表真实模型质量。",
        min_chars=200,
        max_chars=600,
        adoption="manual",
        account="offline",
        plot_contract_check=True,
        structured_maintenance=True,
        scene_maintenance=False,
        budget_limit=5,
        client_request_id=nonce,
    ).model_dump()
    parent = domain.queue("workflow", payload, novel["branch_id"])
    candidate = domain.stage(
        {
            "novel_id": novel["id"],
            "branch_id": novel["branch_id"],
            "chapter_id": chapters[0]["id"],
            "base_revision": 0,
        },
        "chapter_draft",
        {"text": TEXT, "review_passed": True, "memory": {"facts": []}},
        {},
        parent["id"],
    )
    adoption = domain.accept(candidate["id"], "fixture-text-" + uid())
    chapter = domain.chapter(chapters[0]["id"])
    progress = deepcopy(parent["result"])
    progress.pop(KEY, None)
    for name in ("genre_profile_snapshot", "author_contract_snapshot"):
        progress[name]["legacy_unconfigured"] = False
    progress.update(
        plan={"chapters": plots, "ending": "本工程仅停到下一章待审，不作整书结论。"},
        next_chapter=2,
        latest_artifact=candidate["id"],
        repair_round=1,
        plot_preparations={},
        expected_story_revision=domain.branch(novel["branch_id"])["story_revision"],
    )
    record_id = uid()
    record = RECORD.validate_python(
        {
            "kind": "event",
            "title": "首章记录潮水读数",
            "status": "actual",
            "story_order": 1,
            "sources": [
                {
                    "chapter_id": chapter["id"],
                    "revision": chapter["revision"],
                    "start": 0,
                    "end": len(TEXT.encode("utf-16-le")) // 2,
                    "quote": TEXT,
                }
            ],
        }
    ).model_dump()
    revision = domain.branch(novel["branch_id"])["story_revision"]
    structure = domain.stage(
        {
            "novel_id": novel["id"],
            "branch_id": novel["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": chapter["revision"],
        },
        "story_extraction",
        {
            "records": [{"id": record_id, "record": record, "target_id": None, "dependency_proposals": []}],
            "unknowns": [],
            "coverage": "仅隔离首章",
            "source_manifest": [{k: chapter[k] for k in ("id", "revision", "position", "title")}],
            "source_story_revision": revision,
        },
        {"story_revision": revision},
        parent["id"],
    )
    progress["pending_structure"] = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "artifact_id": structure["id"],
    }
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='paused',pause=1,result=? WHERE id=?", (dumps(progress), parent["id"])
        )
    maintenance = adopt(
        domain,
        novel["branch_id"],
        structure["id"],
        AdoptExtraction(
            base_story_revision=revision,
            candidate_revision=0,
            selected_ids=[record_id],
            idempotency_key="fixture-maintenance-" + uid(),
        ),
    )
    budget = Budget(settings)
    provider = settings.providers["offline"]
    paid = budget.reserve(provider, parent["id"], 50_000, 0)
    budget.settle(paid, {"input": 50_000, "output": 0})
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO task_steps VALUES(?,?,?,?)",
            (
                parent["id"],
                "fixture-past-settled-" + paid,
                dumps(
                    {"call_id": paid, "text": "隔离工程旧完整回执", "usage": {"input": 50_000, "output": 0}}
                ),
                time(),
            ),
        )
    return {
        **novel,
        "fixture_id": novel["id"],
        "parent_id": parent["id"],
        "first_chapter_id": chapter["id"],
        "future_chapter_id": chapters[2]["id"],
        "candidate_id": candidate["id"],
        "structure_id": structure["id"],
        "adoption_receipt": adoption,
        "maintenance_receipt": maintenance,
    }


async def known_sent_timeout(settings, sent, account, task_id, system, prompt, max_output, **kwargs):
    guard(settings, settings.data_dir)

    def timeout(wire):
        if wire.url.host != "example.invalid":
            raise RuntimeError("隔离HTTP替身拒绝任何真实外部服务。")
        sent.append({"task_id": task_id, "url_host": wire.url.host})
        raise httpx.ReadTimeout("isolated known-sent timeout, never a real request", request=wire)

    adapter = ModelClient(settings, Budget(settings), transport=httpx.MockTransport(timeout))
    return await adapter.generate(account, task_id, system, prompt, max_output, **kwargs)


def register_flow_policy_fixture(app, settings, temporary_directory):
    guard(settings, temporary_directory)
    domain, budget = app.state.domain, app.state.budget
    requests, fixtures, baseline = {}, {}, {}
    app.state.flow_native_transport_sends = []

    def capture(fixture):
        with budget.db() as tx:
            calls = [dict(r) for r in tx.execute("SELECT * FROM calls ORDER BY id")]
        parent = domain.task(fixture["parent_id"])
        return {
            "task": parent,
            "chapter": domain.chapter(fixture["first_chapter_id"]),
            "candidate": domain.artifact(fixture["candidate_id"]),
            "structure": domain.artifact(fixture["structure_id"]),
            "receipts": domain.db.all(
                "SELECT * FROM receipts WHERE artifact_id IN (?,?) ORDER BY key",
                (fixture["candidate_id"], fixture["structure_id"]),
            ),
            "steps": domain.db.all(
                "SELECT * FROM task_steps WHERE task_id=? ORDER BY fingerprint", (parent["id"],)
            ),
            "calls": calls,
        }

    @app.post(PREFIX + "/fixtures", status_code=201)
    async def seed(data: FixtureInput):
        # No yield while constructing the stopped fixture; the server worker shares this event loop.
        if data.client_request_id not in requests:
            fixture = create_checkpoint(domain, settings, data.client_request_id)
            fixtures[fixture["fixture_id"]] = fixture
            baseline[fixture["fixture_id"]] = capture(fixture)
            requests[data.client_request_id] = fixture["fixture_id"]
        return fixtures[requests[data.client_request_id]]

    @app.get(PREFIX + "/fixtures/{identifier}/proof")
    def proof(identifier: str):
        if identifier not in fixtures:
            raise DomainError("fixture", "仅可读取本隔离夹具。", 404)
        fixture, old = fixtures[identifier], baseline[identifier]
        current = capture(fixture)
        calls = {row["id"]: row for row in current["calls"]}
        return {
            "fixture": fixture,
            "before": old,
            "current": current,
            "protected": {
                name: current[name] == old[name]
                for name in ("chapter", "candidate", "structure", "receipts", "steps")
            },
            "old_calls_unchanged": all(calls.get(row["id"]) == row for row in old["calls"]),
            "transport_sends": app.state.flow_native_transport_sends,
            "parent_payload_unchanged": current["task"]["payload"] == old["task"]["payload"],
            "parent_plan_unchanged": current["task"]["result"]["plan"] == old["task"]["result"]["plan"],
            "parent_legacy_flow_preserved": KEY not in current["task"]["result"],
        }
