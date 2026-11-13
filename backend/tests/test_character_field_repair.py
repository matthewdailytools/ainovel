import asyncio
from copy import deepcopy
import json

import pytest

from ainovel import character_repair, character_review
from ainovel.character_repair import ACTION, VERSION, origin
from ainovel.character_review import ACTION as REVIEW_ACTION
from ainovel.db import DomainError, dumps
from ainovel.story_extraction import AdoptExtraction, adopt
from ainovel.story_graph import graph
from ainovel.workflow_structure import maintain
from test_character_review import pending, restart


class Model:
    """Actual Worker steps and validators, with no provider/network calls."""

    def __init__(self, *, always_unknown=False, cannot=False, defect=None):
        self.calls = []
        self.always_unknown, self.cannot, self.defect = always_unknown, cannot, defect
        self.callback = None
        self.fenced = False

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        request = frame["任务"]
        assert request["动作"] in {ACTION, REVIEW_ACTION}
        self.calls.append(deepcopy(frame))
        if request["动作"] == REVIEW_ACTION:
            fields = [
                {
                    "record_id": t["record_id"],
                    "field": t["field"],
                    "result": "unknown" if self.always_unknown or "没有依据" in t["text"] else "supported",
                    "reason": "离线替身结论，不作为小说质量证明。",
                    "source_ids": []
                    if self.always_unknown or "没有依据" in t["text"]
                    else t["source_ids"][:1],
                }
                for t in request["人物字段"]
            ]
            version = request["字段复核契约版本"]
        else:
            fields = []
            for t in request["人物字段"]:
                text = (
                    f"宁禾的母亲失踪，尚未归来。记录{request['轮次']}"
                    if self.always_unknown
                    else "宁禾的母亲失踪，尚未归来。"
                )
                if self.defect == "empty":
                    text = ""
                elif self.defect == "noop":
                    text = t["text"]
                elif self.defect == "generic":
                    text = "原文未提及她的来历。"
                field = {
                    "record_id": t["record_id"],
                    "field": t["field"],
                    "old_sha256": t["old_sha256"],
                    "decision": "cannot_repair" if self.cannot else "replace",
                    "replacement": None if self.cannot else text,
                    "source_ids": ["CHR9999"] if self.defect == "wrong_source" else t["source_ids"][:1],
                    "reason": "仅使用本人物来源保留母亲失踪这一已知信息。",
                }
                fields.append(field)
            version = request["人物修订契约版本"]
            if self.callback:
                self.callback()
        text = dumps({"version": version, "input_sha256": request["input_sha256"], "fields": fields})
        return {
            "text": "```json\n" + text + "\n```" if self.fenced else text,
            "usage": {"input": 3, "output": 4},
            "call_id": f"offline-{len(self.calls)}",
            "model": f"observed-{account}",
        }


def setup(settings, domain, story, *, model=None, enabled=True, stamped=True, adoption="automatic"):
    worker, result, artifact, chapter = pending(
        settings,
        domain,
        story,
        profile="没有依据的超能力",
        notes="没有依据的身世",
        model=model or Model(),
        adoption=adoption,
    )
    payload = {**worker.task["payload"], "character_field_repair": enabled}
    basis = deepcopy(artifact["basis"])
    if stamped:
        basis["character_field_origin"] = origin(artifact["payload"], worker.task["id"], worker.attempt)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(payload), worker.task["id"]))
        tx.execute("UPDATE artifacts SET basis=? WHERE id=?", (dumps(basis), artifact["id"]))
    worker.task = domain.task(worker.task["id"])
    return worker, result, domain.artifact(artifact["id"]), chapter


def chain(domain, task, root):
    return domain.task(task)["result"]["character_field_repairs"][root]


def test_supported_child_is_independently_reviewed_and_old_candidate_and_sources_survive(
    settings, domain, story
):
    model = Model()
    worker, result, root, chapter = setup(settings, domain, story, model=model)
    original = deepcopy(root["payload"])
    assert asyncio.run(maintain(worker, result))
    assert [f["任务"]["动作"] for f in model.calls] == [REVIEW_ACTION, ACTION, REVIEW_ACTION]
    history = chain(domain, worker.task["id"], root["id"])
    assert len(history["rounds"]) == 1
    step = history["rounds"][0]
    child = domain.artifact(step["child_artifact_id"])
    assert child["id"] != root["id"] and child["status"] == "accepted"
    assert domain.artifact(root["id"])["status"] == "candidate"
    assert domain.artifact(root["id"])["payload"]["character_semantic_review"]["status"] == "needs_review"
    assert child["payload"]["character_semantic_review"]["status"] == "supported"
    assert child["payload"]["character_semantic_review"]["binding"]["artifact_id"] == child["id"]
    expected = deepcopy(original)
    expected["records"][0]["record"].update(
        notes="宁禾的母亲失踪，尚未归来。", profile="宁禾的母亲失踪，尚未归来。"
    )
    assert {k: v for k, v in child["payload"].items() if k != "character_semantic_review"} == expected
    assert domain.chapter(chapter["id"]) == chapter
    assert (
        graph(domain, story["branch_id"])["records"][0]["payload"]["sources"]
        == original["records"][0]["record"]["sources"]
    )
    assert step["completed_steps"][0]["usage"] == {"input": 3, "output": 4}
    assert step["completed_steps"][0]["model"] == "observed-openai_1"
    assert len(step["diff"]) == 2 and step["diff"] == child["basis"]["character_field_repair"]["diff"]
    repair_context = model.calls[1]["上下文"]
    assert set(repair_context) == {"branch", "coverage"}
    with pytest.raises(DomainError) as error:
        adopt(
            domain,
            story["branch_id"],
            root["id"],
            AdoptExtraction(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in original["records"]],
                idempotency_key="old-root-no-adoption",
            ),
        )
    assert error.value.code == "workflow_progress"


def test_two_rounds_remain_bounded_after_restart_and_author_can_choose_current_candidate(
    settings, domain, story
):
    model = Model(always_unknown=True)
    worker, result, root, chapter = setup(settings, domain, story, model=model)
    assert not asyncio.run(maintain(worker, result))
    history = chain(domain, worker.task["id"], root["id"])
    assert len(history["rounds"]) == 2 and len(model.calls) == 5
    assert len(domain.db.all("SELECT * FROM artifacts")) == 3
    assert domain.db.all("SELECT * FROM story_records") == []
    current = domain.artifact(history["current_artifact_id"])
    assert current["payload"]["character_semantic_review"]["status"] == "needs_review"
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 5
    # Explicit author adoption remains a different decision, without falsifying review.
    receipt = adopt(
        domain,
        story["branch_id"],
        current["id"],
        AdoptExtraction(
            base_story_revision=result["expected_story_revision"],
            candidate_revision=0,
            selected_ids=[r["id"] for r in current["payload"]["records"]],
            idempotency_key="author-current-repair",
        ),
    )
    assert receipt["record_ids"]
    assert domain.artifact(current["id"])["payload"]["character_semantic_review"]["status"] == "needs_review"
    assert domain.chapter(chapter["id"]) == chapter


def test_cannot_repair_preserves_original_without_a_second_attempt(settings, domain, story):
    model = Model(cannot=True)
    worker, result, root, _ = setup(settings, domain, story, model=model)
    assert not asyncio.run(maintain(worker, result))
    saved = chain(domain, worker.task["id"], root["id"])
    assert len(saved["rounds"]) == 1 and saved["rounds"][0]["status"] == "stopped"
    assert "明确无法" in saved["rounds"][0]["stop_reason"]
    assert len(domain.db.all("SELECT * FROM artifacts")) == 1
    assert domain.artifact(root["id"])["payload"]["records"] == root["payload"]["records"]
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 2


@pytest.mark.parametrize("defect", ["empty", "noop", "generic", "wrong_source"])
def test_invalid_replacement_never_stages_or_discards_fields(settings, domain, story, defect):
    model = Model(defect=defect)
    worker, result, root, _ = setup(settings, domain, story, model=model)
    with pytest.raises(DomainError) as error:
        asyncio.run(maintain(worker, result))
    assert error.value.code == "evidence"
    # The identical final invalid correction is cached, while all three
    # validator passes still fail (initial + at most two citation corrections).
    assert len([c for c in model.calls if c["任务"]["动作"] == ACTION]) == 2
    events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair'", (worker.task["id"],)
    )
    assert [json.loads(e["payload"])["round"] for e in events] == [0, 1, 2]
    assert len(domain.db.all("SELECT * FROM artifacts")) == 1
    assert domain.artifact(root["id"])["payload"]["records"] == root["payload"]["records"]
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3  # Failure of bounded grounding cannot reset after restart.
    saved = chain(domain, resumed.task["id"], root["id"])["rounds"][0]
    assert len(saved["completed_steps"]) == 2  # Failed paid attempts remain in the trace too.


@pytest.mark.parametrize("boundary", ["disabled", "unstamped", "edited", "mutated", "manual", "unsafe"])
def test_author_and_legacy_boundaries_do_not_enter_repair(settings, domain, story, boundary):
    model = Model()
    worker, result, root, _ = setup(
        settings,
        domain,
        story,
        model=model,
        enabled=boundary != "disabled",
        stamped=boundary != "unstamped",
        adoption="manual" if boundary == "manual" else "automatic",
    )
    payload = deepcopy(root["payload"])
    if boundary == "edited":
        payload["_candidate_revision"] = 1
    elif boundary == "mutated":
        payload["records"][0]["record"]["notes"] = "作者没有依据的补充"
    elif boundary == "unsafe":
        payload["records"].append(
            {
                "id": "same-ends",
                "record": {"kind": "relationship", "from_id": "character-0", "to_id": "character-0"},
            }
        )
    if payload != root["payload"]:
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), root["id"]))
    assert not asyncio.run(maintain(worker, result))
    assert not [c for c in model.calls if c["任务"]["动作"] == ACTION]
    assert domain.db.all("SELECT * FROM story_records") == []
    if boundary in {"manual", "unsafe"}:
        assert model.calls == []


@pytest.mark.parametrize("change", ["source", "constraints", "candidate", "attempt", "cancel"])
def test_late_repair_cannot_apply_after_frozen_inputs_change(settings, domain, story, change):
    model = Model()
    worker, result, root, chapter = setup(settings, domain, story, model=model)

    def mutate():
        with domain.db.connect(True) as tx:
            if change == "source":
                tx.execute("UPDATE chapters SET content=content||'新稿' WHERE id=?", (chapter["id"],))
            elif change == "constraints":
                payload = {**worker.task["payload"], "brief": "改变后的作者条件"}
                tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(payload), worker.task["id"]))
            elif change == "candidate":
                payload = deepcopy(root["payload"])
                payload["records"][0]["record"]["notes"] = "作者修改"
                tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), root["id"]))
            elif change == "attempt":
                tx.execute("UPDATE tasks SET attempt='new-attempt' WHERE id=?", (worker.task["id"],))
            else:
                tx.execute("UPDATE tasks SET cancel=1 WHERE id=?", (worker.task["id"],))

    model.callback = mutate
    with pytest.raises(DomainError) as error:
        asyncio.run(maintain(worker, result))
    assert error.value.code in {"candidate_changed", "cancelled", "evidence"}
    assert len(domain.db.all("SELECT * FROM artifacts")) == 1
    assert domain.db.all("SELECT * FROM story_records") == []


def test_pause_saves_child_without_dispatching_review_then_resumes(settings, domain, story):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)

    def pause():
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE tasks SET pause=1 WHERE id=?", (worker.task["id"],))

    model.callback = pause
    assert not asyncio.run(maintain(worker, result))
    assert len(model.calls) == 2
    saved = chain(domain, worker.task["id"], root["id"])
    child = domain.artifact(saved["current_artifact_id"])
    assert child["id"] != root["id"] and "character_semantic_review" not in child["payload"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET pause=0 WHERE id=?", (worker.task["id"],))
    model.callback = None
    resumed = restart(settings, worker, model)
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3 and len(chain(domain, worker.task["id"], root["id"])["rounds"]) == 1


@pytest.mark.parametrize("continuation", [False, True])
def test_paid_step_before_candidate_commit_recovers_without_repair_charge(
    settings, domain, story, monkeypatch, continuation
):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    original_finish = character_repair.finish

    def crash(*args, **kwargs):
        raise RuntimeError("isolated crash after paid completion")

    monkeypatch.setattr(character_repair, "finish", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(maintain(worker, result))
    assert len(model.calls) == 2 and len(domain.db.all("SELECT * FROM artifacts")) == 1
    monkeypatch.setattr(character_repair, "finish", original_finish)
    if continuation:
        worker.checkpoint(result, "paused")
        current = domain.task(worker.task["id"])
        domain.continue_workflow(
            current,
            {**current["payload"], "account": "deepseek", "client_request_id": "continue-paid-character"},
            {"acknowledge_unknown": True, "reason": "复用已完成结果后换账户"},
        )
        resumed = type(worker)(settings, model)
        resumed.task = resumed.claim()
        resumed.attempt = resumed.task["attempt"]
    else:
        resumed = restart(settings, worker, model)
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3
    saved = chain(domain, resumed.task["id"], root["id"])
    assert len(saved["rounds"]) == 1
    assert saved["rounds"][0]["completed_steps"][0]["task_id"] == worker.task["id"]


def test_atomic_checkpoint_failure_leaves_no_orphan_candidate(settings, domain, story, monkeypatch):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    original_event = worker.db.event

    def fail(tx, task_id, kind, payload):
        if kind == "character_field_repair_completed":
            raise RuntimeError("failure before transaction commit")
        original_event(tx, task_id, kind, payload)

    monkeypatch.setattr(worker.db, "event", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(maintain(worker, result))
    assert len(domain.db.all("SELECT * FROM artifacts")) == 1
    assert domain.task(worker.task["id"])["result"]["pending_structure"]["artifact_id"] == root["id"]
    monkeypatch.setattr(worker.db, "event", original_event)
    resumed = restart(settings, worker, model)
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3


def test_unknown_repair_request_is_never_resent_after_restart(settings, domain, story):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)

    def unknown():
        raise DomainError("network_unknown", "结果未知，需要保留费用预留", 502)

    model.callback = unknown
    with pytest.raises(DomainError) as error:
        asyncio.run(maintain(worker, result))
    assert error.value.code == "network_unknown"
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 2
    assert chain(domain, worker.task["id"], root["id"])["rounds"][0]["status"] == "stopped"


def test_author_adoption_during_repair_is_not_overwritten(settings, domain, story):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)

    def author_adopts():
        adopt(
            domain,
            story["branch_id"],
            root["id"],
            AdoptExtraction(
                base_story_revision=result["expected_story_revision"],
                candidate_revision=0,
                selected_ids=[r["id"] for r in root["payload"]["records"]],
                idempotency_key="author-concurrent-root",
            ),
        )

    model.callback = author_adopts
    assert asyncio.run(maintain(worker, result))
    assert len(model.calls) == 2 and len(domain.db.all("SELECT * FROM artifacts")) == 1
    assert domain.artifact(root["id"])["status"] == "accepted"
    assert graph(domain, story["branch_id"])["records"][0]["payload"]["notes"] == "没有依据的身世"
    assert domain.artifact(root["id"])["payload"]["character_semantic_review"]["status"] == "needs_review"


@pytest.mark.parametrize("fenced", [False, True])
def test_paid_independent_review_before_report_recovers_across_account_continuation(
    settings, domain, story, monkeypatch, fenced
):
    model = Model()
    model.fenced = fenced
    worker, result, root, _ = setup(settings, domain, story, model=model)
    original_time = character_review.time

    def crash_before_report():
        if len(model.calls) >= 3:
            raise RuntimeError("isolated crash after independent review completion")
        return original_time()

    monkeypatch.setattr(character_review, "time", crash_before_report)
    with pytest.raises(RuntimeError):
        asyncio.run(maintain(worker, result))
    assert len(model.calls) == 3
    child = domain.artifact(chain(domain, worker.task["id"], root["id"])["current_artifact_id"])
    assert child["id"] != root["id"] and "character_semantic_review" not in child["payload"]
    monkeypatch.setattr(character_review, "time", original_time)
    worker.checkpoint(result, "paused")
    current = domain.task(worker.task["id"])
    domain.continue_workflow(
        current,
        {**current["payload"], "account": "deepseek", "client_request_id": "continue-paid-character-review"},
        {"acknowledge_unknown": True, "reason": "只复用已付费复检"},
    )
    resumed = type(worker)(settings, model)
    resumed.task = resumed.claim()
    resumed.attempt = resumed.task["attempt"]
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3
    report = domain.artifact(child["id"])["payload"]["character_semantic_review"]
    assert report["reviewer"]["task_id"] == worker.task["id"]
    assert report["reviewer"]["account"] == "openai_1"
    assert report["completed_steps"][0]["model"] == "observed-openai_1"
    assert report["completed_steps"][0]["task_id"] == worker.task["id"]


def test_fenced_paid_repair_recovers_before_child_commit(settings, domain, story, monkeypatch):
    model = Model()
    model.fenced = True
    worker, result, root, _ = setup(settings, domain, story, model=model)
    original_finish = character_repair.finish

    def crash(*args, **kwargs):
        raise RuntimeError("isolated crash after paid completion")

    monkeypatch.setattr(character_repair, "finish", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(maintain(worker, result))
    assert len(model.calls) == 2
    monkeypatch.setattr(character_repair, "finish", original_finish)
    resumed = restart(settings, worker, model)
    assert asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3
    assert chain(domain, resumed.task["id"], root["id"])["rounds"][0]["completed_steps"]


def test_extraction_staging_stamps_immutable_ai_provenance(settings, domain, story, monkeypatch):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model, stamped=False)
    # Only remove the isolated fixture to exercise the actual extraction→stage path.
    with domain.db.connect(True) as tx:
        tx.execute("DELETE FROM artifacts WHERE id=?", (root["id"],))
    result["pending_structure"].pop("artifact_id")
    worker.checkpoint(result)

    async def extraction(*args, **kwargs):
        return "story_extraction", deepcopy(root["payload"])

    from ainovel import workflow_structure

    monkeypatch.setattr(workflow_structure, "run_extraction", extraction)
    assert asyncio.run(maintain(worker, result))
    rows = domain.db.all("SELECT * FROM artifacts ORDER BY created")
    assert len(rows) == 2
    staged = domain.artifact(rows[0]["id"])
    assert staged["basis"]["character_field_origin"]["version"] == VERSION
    assert staged["basis"]["character_field_origin"]["content_sha256"] == character_repair.content_sha(
        root["payload"]
    )
    assert staged["payload"]["character_semantic_review"]["status"] == "needs_review"


def test_real_budget_adapter_blocks_repair_and_retains_paid_review(settings, domain, story):
    from dataclasses import replace
    from decimal import Decimal
    import httpx
    from ainovel.budget import Budget
    from ainovel.providers import ModelClient

    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    asyncio.run(character_review.ensure_character_review(worker, root))
    budget = Budget(settings)
    expensive = replace(
        settings.providers["openai_2"], input_rate=Decimal("19000000"), output_rate=Decimal("0")
    )
    call = budget.reserve(expensive, "prior-explicit-usage", 1, 1)
    budget.settle(call)
    attempted = []

    def handler(request):
        attempted.append(request)
        raise AssertionError("shared budget must stop before network")

    settings.providers["openai_1"] = replace(settings.providers["openai_1"], input_rate=Decimal("1000000"))
    worker.client = ModelClient(settings, budget, httpx.MockTransport(handler))
    before = deepcopy(budget.report())
    with pytest.raises(DomainError) as error:
        asyncio.run(maintain(worker, result))
    assert error.value.code == "budget"
    assert attempted == [] and budget.report() == before
    assert len(domain.db.all("SELECT * FROM artifacts")) == 1
    assert domain.artifact(root["id"])["payload"]["character_semantic_review"]["status"] == "needs_review"


def test_existing_character_prose_is_unsafe_even_with_a_valid_origin_marker(settings, domain, story):
    from ainovel.story_graph import SaveRecord, save_record

    old = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={"kind": "character", "title": "宁禾", "notes": "作者正式档案"},
        ),
    )["records"][0]
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    payload = deepcopy(root["payload"])
    payload["records"][0].update(
        id=old["id"],
        target_id=old["id"],
        target_revision=old["revision"],
        previous_record={"record": old["payload"], "revision": old["revision"]},
        update_fields=["notes", "sources"],
    )
    basis = {**root["basis"], "character_field_origin": origin(payload, worker.task["id"], worker.attempt)}
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE artifacts SET payload=?,basis=? WHERE id=?", (dumps(payload), dumps(basis), root["id"])
        )
    assert not asyncio.run(maintain(worker, result))
    assert model.calls == []
    assert graph(domain, story["branch_id"])["records"][0]["payload"] == old["payload"]


def test_repair_only_sends_failed_characters_own_sources(settings, domain, story):
    model = Model()
    worker, result, root, _ = pending(
        settings, domain, story, count=2, profile="没有依据的能力", notes="没有依据的身世", model=model
    )
    payload = deepcopy(root["payload"])
    payload["records"][1]["record"].update(profile="江舟是渡口掌柜。", notes="江舟正在看账。")
    basis = {**root["basis"], "character_field_origin": origin(payload, worker.task["id"], worker.attempt)}
    task_payload = {**worker.task["payload"], "character_field_repair": True}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(task_payload), worker.task["id"]))
        tx.execute(
            "UPDATE artifacts SET payload=?,basis=? WHERE id=?", (dumps(payload), dumps(basis), root["id"])
        )
    worker.task = domain.task(worker.task["id"])
    assert asyncio.run(maintain(worker, result))
    request = next(c["任务"] for c in model.calls if c["任务"]["动作"] == ACTION)
    assert {t["record_id"] for t in request["人物字段"]} == {"character-0"}
    assert len(request["本项原文来源"]) == 1
    assert (
        request["本项原文来源"][0]["quote"] == root["payload"]["records"][0]["record"]["sources"][0]["quote"]
    )
    assert "江舟" not in dumps(request)


def test_false_claim_review_is_preserved_instead_of_forcing_it_supported(
    settings, domain, story, monkeypatch
):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    original_generate = model.generate

    async def with_contradiction(*args, **kwargs):
        response = await original_generate(*args, **kwargs)
        value = json.loads(response["text"])
        if len(model.calls) == 1:
            for field in value["fields"]:
                field.update(result="contradicted", source_ids=["CHR0001"])
        response["text"] = dumps(value)
        return response

    monkeypatch.setattr(model, "generate", with_contradiction)
    assert asyncio.run(maintain(worker, result))
    old = domain.artifact(root["id"])["payload"]["character_semantic_review"]
    assert {f["result"] for f in old["response"]["fields"]} == {"contradicted"}
    assert old["status"] == "needs_review" and len(model.calls) == 3


def test_unknown_independent_review_is_not_resent_after_restart(settings, domain, story, monkeypatch):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    generate = model.generate

    async def unknown_child_review(*args, **kwargs):
        response = await generate(*args, **kwargs)
        if len(model.calls) == 3:
            raise DomainError("network_unknown", "独立复检结果未知，保留费用预留", 502)
        return response

    monkeypatch.setattr(model, "generate", unknown_child_review)
    with pytest.raises(DomainError) as error:
        asyncio.run(maintain(worker, result))
    assert error.value.code == "network_unknown"
    child = domain.artifact(chain(domain, worker.task["id"], root["id"])["current_artifact_id"])
    assert "character_semantic_review" not in child["payload"]
    resumed = restart(settings, worker, model)
    assert not asyncio.run(maintain(resumed, resumed.task["result"]))
    assert len(model.calls) == 3
    assert domain.artifact(child["id"])["status"] == "candidate"
    assert domain.db.all("SELECT * FROM story_records") == []


def test_new_failure_outside_initial_whitelist_stops_without_another_repair(
    settings, domain, story, monkeypatch
):
    model = Model()
    worker, result, root, _ = setup(settings, domain, story, model=model)
    generate = model.generate

    async def initial_partial_review(*args, **kwargs):
        response = await generate(*args, **kwargs)
        if len(model.calls) == 1:
            value = json.loads(response["text"])
            for field in value["fields"]:
                if field["field"] == "profile":
                    field.update(result="supported", source_ids=["CHR0001"])
            response["text"] = dumps(value)
        return response

    monkeypatch.setattr(model, "generate", initial_partial_review)
    assert not asyncio.run(maintain(worker, result))
    saved = chain(domain, worker.task["id"], root["id"])
    assert saved["whitelist"] == [["character-0", "notes"]]
    assert len(saved["rounds"]) == 1 and len(model.calls) == 3
    child = domain.artifact(saved["current_artifact_id"])
    assert (
        child["payload"]["records"][0]["record"]["profile"]
        == root["payload"]["records"][0]["record"]["profile"]
    )
    assert "白名单以外" in domain.task(worker.task["id"])["error"]
