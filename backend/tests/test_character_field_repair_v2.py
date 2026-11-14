"""Static pre-review scope permits bounded newly discovered failures, not new authority."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel import character_repair
from ainovel.character_repair import ACTION, VERSION, V2_VERSION, content_sha, initial_scope, origin
from ainovel.character_review import ACTION as REVIEW_ACTION, ensure_character_review, sha
from ainovel.db import DomainError, dumps
from ainovel.models import WorkflowInput
from ainovel.workflow_structure import maintain
from test_character_review import pending, restart


class FlipModel:
    """Deterministic engineering judgments; no provider or literary-quality claim."""

    def __init__(self, *, final_unknown=False, all_supported=False):
        self.calls = []
        self.review_count = 0
        self.final_unknown, self.all_supported = final_unknown, all_supported
        self.callback = None

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        instruction = frame["任务"]
        self.calls.append(deepcopy(frame))
        if instruction["动作"] == REVIEW_ACTION:
            self.review_count += 1
            fields = []
            for target in instruction["人物字段"]:
                bad = not self.all_supported and (
                    target["text"].startswith("缺证profile")
                    or (self.review_count >= 2 and target["text"] == "初检通过的过程备注")
                    or (
                        self.final_unknown and self.review_count >= 3 and target["record_id"] == "character-1"
                    )
                )
                fields.append(
                    {
                        "record_id": target["record_id"],
                        "field": target["field"],
                        "result": "unknown" if bad else "supported",
                        "reason": "工程替身，模拟完整复检。",
                        "source_ids": [] if bad else target["source_ids"][:1],
                    }
                )
            version = instruction["字段复核契约版本"]
        else:
            assert instruction["动作"] == ACTION
            sources = {s["source_id"]: s for s in instruction["本项原文来源"]}
            fields = [
                {
                    "record_id": t["record_id"],
                    "field": t["field"],
                    "old_sha256": t["old_sha256"],
                    "decision": "replace",
                    "replacement": sources[t["source_ids"][0]]["quote"],
                    "source_ids": t["source_ids"][:1],
                    "reason": "仅保留该人物自身附引文支持的内容。",
                }
                for t in instruction["人物字段"]
            ]
            version = instruction["人物修订契约版本"]
            if self.callback:
                self.callback(instruction)
        return {
            "text": dumps(
                {"version": version, "input_sha256": instruction["input_sha256"], "fields": fields}
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": f"offline-v2-{len(self.calls)}",
            "model": "offline-v2",
        }


def setup_v2(settings, domain, story, *, model=None, version=V2_VERSION):
    model = model or FlipModel()
    texts = ["宁禾的母亲失踪，尚未归来。", "江舟是渡口掌柜，正在看账。", "梁成是修船师傅，正在收好木尺。"]
    worker, result, root, chapter = pending(settings, domain, story, count=2, texts=texts, model=model)
    payload = deepcopy(root["payload"])
    third = deepcopy(payload["records"][1])
    third.update(id="character-2", local_id="local-2")
    start = len(("\n".join(texts[:2]) + "\n").encode("utf-16-le")) // 2
    third["record"].update(
        title="梁成",
        sources=[
            {
                "chapter_id": chapter["id"],
                "revision": 1,
                "start": start,
                "end": start + len(texts[2]),
                "quote": texts[2],
            }
        ],
    )
    payload["records"].append(third)
    for i, item in enumerate(payload["records"]):
        item["record"].update(profile=f"缺证profile{i}", notes="初检通过的过程备注" if i == 0 else texts[i])
    if version is not None:
        result["character_field_repair_version"] = version
    else:
        result.pop("character_field_repair_version", None)
    task_payload = {**worker.task["payload"], "character_field_repair": True}
    basis = {
        **root["basis"],
        "character_field_origin": origin(
            payload,
            worker.task["id"],
            worker.attempt,
            version=version if version in (VERSION, V2_VERSION) else VERSION,
        ),
    }
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?",
            (dumps(task_payload), dumps(result), worker.task["id"]),
        )
        tx.execute(
            "UPDATE artifacts SET payload=?,basis=? WHERE id=?", (dumps(payload), dumps(basis), root["id"])
        )
    worker.task = domain.task(worker.task["id"])
    return worker, worker.task["result"], domain.artifact(root["id"]), chapter


def test_second_round_repairs_newly_unknown_notes_inside_original_scope_and_preserves_profiles(
    settings, domain, story
):
    model = FlipModel()
    worker, result, root, chapter = setup_v2(settings, domain, story, model=model)
    frozen = deepcopy(root["basis"]["character_field_origin"]["initial_scope"])
    assert len(frozen["fields"]) == 6
    assert "character_semantic_review" not in root["payload"]
    assert asyncio.run(maintain(worker, result))
    chain = domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]
    assert chain["version"] == V2_VERSION and chain["initial_scope"] == frozen
    assert len(chain["whitelist"]) == 6 and len(chain["initial_failures"]) == 3
    assert len(chain["rounds"]) == 2
    repairs = [f["任务"] for f in model.calls if f["任务"]["动作"] == ACTION]
    assert [[(t["record_id"], t["field"]) for t in r["人物字段"]] for r in repairs] == [
        [(f"character-{i}", "profile") for i in range(3)],
        [("character-0", "notes")],
    ]
    first = domain.artifact(chain["rounds"][0]["child_artifact_id"])
    final = domain.artifact(chain["current_artifact_id"])
    assert (
        final["status"] == "accepted"
        and final["payload"]["character_semantic_review"]["status"] == "supported"
    )
    for i in range(3):
        assert (
            first["payload"]["records"][i]["record"]["profile"]
            == final["payload"]["records"][i]["record"]["profile"]
        )
    assert all(
        child["basis"]["character_field_origin"]["initial_scope"] == frozen for child in (first, final)
    )
    assert domain.artifact(root["id"])["payload"]["records"] == root["payload"]["records"]
    assert domain.chapter(chapter["id"]) == chapter
    for repair in repairs:
        assert repair["人物修订契约版本"] == V2_VERSION
        for target in repair["人物字段"]:
            initial = next(
                f
                for f in frozen["fields"]
                if (f["record_id"], f["field"]) == (target["record_id"], target["field"])
            )
            assert target["initial_sha256"] == initial["initial_sha256"]
            assert target["old_sha256"] == sha(target["text"])
    assert model.review_count == 3 and all(
        len(f["任务"]["人物字段"]) == 6 for f in model.calls if f["任务"]["动作"] == REVIEW_ACTION
    )


def test_third_repair_round_never_starts_and_restart_does_not_reset_scope_or_rounds(settings, domain, story):
    model = FlipModel(final_unknown=True)
    worker, result, root, _ = setup_v2(settings, domain, story, model=model)
    assert not asyncio.run(maintain(worker, result))
    saved = domain.task(worker.task["id"])
    chain = saved["result"]["character_field_repairs"][root["id"]]
    assert len(chain["rounds"]) == 2 and model.review_count == 3
    before = deepcopy(chain)
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    assert (
        len(model.calls) == 5
        and domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]] == before
    )
    assert not domain.db.all("SELECT id FROM story_records")


def test_all_supported_initial_fields_never_rewritten_and_scope_exists_before_review(settings, domain, story):
    model = FlipModel(all_supported=True)
    worker, result, root, _ = setup_v2(settings, domain, story, model=model)
    assert root["basis"]["character_field_origin"]["initial_scope"]["root_content_sha256"] == content_sha(
        root["payload"]
    )
    assert asyncio.run(maintain(worker, result))
    assert len(model.calls) == 1 and model.review_count == 1
    assert not domain.task(worker.task["id"])["result"].get("character_field_repairs")


@pytest.mark.parametrize("version", [None, VERSION])
def test_legacy_missing_marker_and_explicit_v1_still_stop_at_first_failure_whitelist(
    settings, domain, story, version
):
    model = FlipModel()
    worker, result, root, _ = setup_v2(settings, domain, story, model=model, version=version)
    assert not asyncio.run(maintain(worker, result))
    chain = domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]
    assert chain["version"] == VERSION and len(chain["rounds"]) == 1
    assert len(chain["whitelist"]) == 3 and "initial_scope" not in chain
    assert len(model.calls) == 3


@pytest.mark.parametrize("version", ["future/3", {}, True])
def test_unknown_task_policy_stops_before_model_calls(settings, domain, story, version):
    model = FlipModel()
    worker, result, _, _ = setup_v2(settings, domain, story, model=model, version=version)
    with pytest.raises(DomainError, match="政策无法确认"):
        asyncio.run(maintain(worker, result))
    assert model.calls == []


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "sources",
        "event",
        "notes_outside_scope",
        "scope_removed",
        "scope_regenerated",
        "original_sha",
    ],
)
def test_static_scope_and_noneditable_content_cannot_change_between_rounds(settings, domain, story, change):
    worker, result, root, _ = setup_v2(settings, domain, story)
    artifact, token = asyncio.run(ensure_character_review(worker, root))
    prepared, _ = character_repair.prepare(worker, artifact["id"], result)
    _, inputs, slot, _ = prepared
    response = {
        "version": V2_VERSION,
        "input_sha256": inputs["input_sha256"],
        "fields": [
            {
                "record_id": t["record_id"],
                "field": t["field"],
                "old_sha256": t["old_sha256"],
                "decision": "replace",
                "replacement": "仅保留本人物已引原文。",
                "source_ids": t["source_ids"][:1],
                "reason": "工程边界",
            }
            for t in inputs["targets"]
        ],
    }
    child, _ = character_repair.finish(worker, result, artifact, inputs, response, [])
    payload, basis = deepcopy(child["payload"]), deepcopy(child["basis"])
    scope = basis["character_field_origin"]["initial_scope"]
    if change == "identity":
        payload["records"][0]["record"]["aliases"].append("新身份")
    elif change == "sources":
        payload["records"][0]["record"]["sources"] = payload["records"][1]["record"]["sources"]
    elif change == "event":
        payload["unknowns"].append("额外改动")
    elif change == "notes_outside_scope":
        payload["records"].append(deepcopy(payload["records"][0]))
        payload["records"][-1]["id"] = "new-character"
    elif change == "scope_removed":
        scope["fields"].pop()
    elif change == "scope_regenerated":
        basis["character_field_origin"]["initial_scope"] = initial_scope(payload)
    else:
        scope["fields"][0]["initial_sha256"] = "0" * 64
    if change in {"identity", "sources", "event", "notes_outside_scope"}:
        basis["character_field_origin"]["content_sha256"] = content_sha(payload)
    if change in {"scope_removed", "original_sha"}:
        scope["scope_sha256"] = sha({k: v for k, v in scope.items() if k != "scope_sha256"})
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE artifacts SET payload=?,basis=? WHERE id=?", (dumps(payload), dumps(basis), child["id"])
        )
    with domain.db.connect() as tx:
        assert not character_repair.eligible(tx, domain.artifact(child["id"]), domain.task(worker.task["id"]))


@pytest.mark.parametrize("count", [120, 121])
def test_static_scope_limit_is_exact_and_never_truncates(count):
    payload = {
        "records": [
            {
                "id": f"c-{i}",
                "record": {"kind": "character", "notes": "叙事事实", "profile": "", "sources": []},
            }
            for i in range(count)
        ]
    }
    if count == 120:
        assert len(initial_scope(payload)["fields"]) == 120
    else:
        with pytest.raises(DomainError, match="超过120"):
            initial_scope(payload)


def test_scope_excludes_formal_updates_empty_fields_and_noncharacters():
    payload = {
        "records": [
            {
                "id": "new",
                "record": {"kind": "character", "notes": "叙事事实", "profile": " ", "sources": []},
            },
            {
                "id": "old",
                "target_id": "formal",
                "record": {"kind": "character", "notes": "已有档案", "profile": "已有档案", "sources": []},
            },
            {"id": "event", "record": {"kind": "event", "notes": "事件", "sources": []}},
        ]
    }
    assert [(f["record_id"], f["field"]) for f in initial_scope(payload)["fields"]] == [("new", "notes")]


def test_new_queue_freezes_v2_and_old_nonce_does_not_retrofit(domain, story):
    payload = WorkflowInput(
        novel_id=story["id"],
        mode="automatic",
        brief="原创brief",
        account="openai_1",
        structured_maintenance=True,
        adoption="automatic",
        character_field_repair=True,
        client_request_id="v2-nonce",
    ).model_dump()
    task = domain.queue("workflow", payload, story["branch_id"])
    assert task["result"]["character_field_repair_version"] == V2_VERSION
    old = {k: v for k, v in task["result"].items() if k != "character_field_repair_version"}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(old), task["id"]))
    assert domain.queue("workflow", payload, story["branch_id"])["result"] == old


@pytest.mark.parametrize("version", [None, VERSION, V2_VERSION])
def test_account_continuation_preserves_original_policy_including_missing_marker(
    settings, domain, story, version
):
    worker, result, _, _ = setup_v2(settings, domain, story, version=version)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET status='paused' WHERE id=?", (worker.task["id"],))
    current = domain.task(worker.task["id"])
    continued = domain.continue_workflow(
        current,
        {**current["payload"], "account": "openai_2", "client_request_id": "keep-character-policy"},
        {"reason": "明确保留原政策换账户", "acknowledge_unknown": True},
    )
    assert continued["result"].get("character_field_repair_version") == version
    assert ("character_field_repair_version" in continued["result"]) is (version is not None)


def test_corrupt_initial_scope_stops_before_even_supported_review(settings, domain, story):
    model = FlipModel(all_supported=True)
    worker, result, root, _ = setup_v2(settings, domain, story, model=model)
    basis = deepcopy(root["basis"])
    basis["character_field_origin"]["initial_scope"]["fields"].pop()
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET basis=? WHERE id=?", (dumps(basis), root["id"]))
    assert not asyncio.run(maintain(worker, result))
    assert model.calls == [] and not domain.db.all("SELECT id FROM story_records")


@pytest.mark.parametrize("boundary", ["source", "candidate", "attempt", "cancel", "scope"])
def test_v2_late_response_cannot_create_child_after_author_or_attempt_changes(
    settings, domain, story, boundary
):
    model = FlipModel()
    worker, result, root, chapter = setup_v2(settings, domain, story, model=model)

    def mutate(instruction):
        with domain.db.connect(True) as tx:
            if boundary == "source":
                tx.execute("UPDATE chapters SET content=content||'改稿' WHERE id=?", (chapter["id"],))
            elif boundary == "candidate":
                payload = deepcopy(root["payload"])
                payload["_candidate_revision"] = 1
                tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), root["id"]))
            elif boundary == "attempt":
                tx.execute("UPDATE tasks SET attempt='new-owner' WHERE id=?", (worker.task["id"],))
            elif boundary == "cancel":
                tx.execute("UPDATE tasks SET cancel=1 WHERE id=?", (worker.task["id"],))
            else:
                basis = deepcopy(root["basis"])
                basis["character_field_origin"]["initial_scope"]["fields"].pop()
                tx.execute("UPDATE artifacts SET basis=? WHERE id=?", (dumps(basis), root["id"]))

    model.callback = mutate
    with pytest.raises(DomainError):
        asyncio.run(maintain(worker, result))
    assert len(domain.db.all("SELECT id FROM artifacts")) == 1
    assert domain.task(worker.task["id"])["result"]["pending_structure"]["artifact_id"] == root["id"]
    assert not domain.db.all("SELECT id FROM story_records")


def test_v2_paid_completion_reuses_cache_and_scope_after_restart(settings, domain, story, monkeypatch):
    model = FlipModel()
    worker, result, root, _ = setup_v2(settings, domain, story, model=model)
    finish = character_repair.finish

    def crash(*args, **kwargs):
        raise RuntimeError("paid-result-before-child")

    monkeypatch.setattr(character_repair, "finish", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(maintain(worker, result))
    assert len(model.calls) == 2
    initial = deepcopy(
        domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]["initial_scope"]
    )
    monkeypatch.setattr(character_repair, "finish", finish)
    resumed = restart(settings, worker, model)
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    chain = domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]
    assert len(model.calls) == 5 and len(chain["rounds"]) == 2 and chain["initial_scope"] == initial


def test_v2_unknown_paid_result_never_reissues_and_keeps_original_scope(settings, domain, story):
    model = FlipModel()
    worker, result, root, _ = setup_v2(settings, domain, story, model=model)
    original = model.generate

    async def unknown(*args, **kwargs):
        instruction = json.loads(args[3].split("\n只返回")[0])["任务"]
        if instruction["动作"] == ACTION:
            raise DomainError("timeout_unknown", "工程替身未知费用结果")
        return await original(*args, **kwargs)

    model.generate = unknown
    with pytest.raises(DomainError):
        asyncio.run(maintain(worker, result))
    first = deepcopy(domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]])
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    after = domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]
    assert (
        len(model.calls) == 1
        and len(after["rounds"]) == 1
        and after["initial_scope"] == first["initial_scope"]
    )
    assert "未自动重发" in after["rounds"][0]["stop_reason"]


@pytest.mark.parametrize("version", [None, VERSION, V2_VERSION])
def test_extraction_field_scope_guidance_is_v2_only_and_legacy_cache_reuses(settings, domain, story, version):
    from ainovel.story_extraction import freeze_sources, run_extraction
    from test_story_extraction import ExtractionModel

    worker, _, _, chapter = setup_v2(settings, domain, story, version=version)
    model = ExtractionModel()
    worker.client = model
    request = {"novel_id": story["id"], "branch_id": story["branch_id"], "story_chapter_ids": [chapter["id"]]}
    snapshot = freeze_sources(domain, request)
    context = worker.context(story["branch_id"])
    asyncio.run(run_extraction(worker, request, context, snapshot))
    asyncio.run(run_extraction(worker, request, context, snapshot))
    assert len(model.calls) == 1
    instruction = model.calls[0]["任务"]
    assert ("人物自由字段契约版本" in instruction) is (version == V2_VERSION)
    assert ("人物自由字段范围" in instruction) is (version == V2_VERSION)
    if version == V2_VERSION:
        assert "reasoning或unknowns" in instruction["人物自由字段范围"]
        assert "本人物的原文支持" in instruction["人物自由字段范围"]


def test_v2_real_adapter_unknown_timeout_keeps_money_and_never_redispatches(settings, domain, story):
    import httpx
    from ainovel.providers import ModelClient

    model = FlipModel()
    worker, result, root, _ = setup_v2(settings, domain, story, model=model)
    asyncio.run(ensure_character_review(worker, root))
    requests = []

    def handler(request):
        requests.append(request.content)
        raise httpx.ReadTimeout("unknown after sending", request=request)

    real = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    worker.client = real
    with pytest.raises(DomainError):
        asyncio.run(maintain(worker, result))
    before = worker.budget.report()[0]
    assert len(requests) == 1 and before["reserved_unknown"] > 0
    scope = deepcopy(
        domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]["initial_scope"]
    )
    resumed = restart(settings, worker, real)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    after = worker.budget.report()[0]
    assert len(requests) == 1 and after == before
    chain = domain.task(worker.task["id"])["result"]["character_field_repairs"][root["id"]]
    assert chain["initial_scope"] == scope and len(chain["rounds"]) == 1
    assert domain.artifact(root["id"])["status"] == "candidate"


def test_v2_real_budget_rejection_does_not_dispatch_or_modify_candidate(settings, domain, story):
    from dataclasses import replace
    from decimal import Decimal
    import httpx
    from ainovel.providers import ModelClient

    worker, result, root, _ = setup_v2(settings, domain, story)
    asyncio.run(ensure_character_review(worker, root))
    settings.providers["openai_1"] = replace(settings.providers["openai_1"], input_rate=Decimal("1000000"))
    attempted = []

    def handler(request):
        attempted.append(request.content)
        raise AssertionError("budget must prevent HTTP")

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    before = deepcopy(worker.budget.report())
    with pytest.raises(DomainError) as caught:
        asyncio.run(maintain(worker, result))
    assert caught.value.code == "budget" and not attempted and worker.budget.report() == before
    assert len(domain.db.all("SELECT id FROM artifacts")) == 1
    assert domain.artifact(root["id"])["payload"]["records"] == root["payload"]["records"]
