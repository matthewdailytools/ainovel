"""Explicit scene references retain evidence, frozen versions and atomic adoption."""

import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from ainovel.db import DomainError, dumps
from ainovel.scene_order import (
    LEGACY_VERSION,
    VERSION,
    LinkedSceneProposal,
    effective_records,
    execution_version,
    linked_scene_issues,
    audit_valid,
    adopted_audit,
    make_audit,
    payload_sha,
)
from ainovel.scene_time import SceneTime, SaveScene, save_scene, scenes
from ainovel.story_extraction import ExtractionWithScenes, ExtractionWithLinkedScenes, adopt
from ainovel.story_graph import RECORD, SaveRecord, graph, save_record
from ainovel.worker import Worker
from ainovel.workflow_structure import maintain
from test_extraction_scene_diagnostics import SceneDiagnosticModel, pending, proposals, units_for
from test_extraction_scenes import SceneOnlyModel, data_for, prepared as prepared


def scene(units, refs=None, reason=None):
    return LinkedSceneProposal(
        title="回到渡口",
        source_ids=[u["source_id"] for u in units],
        audience="author",
        order_basis={"kind": "unknown", "reason": reason, "refs": refs or []}
        if reason is not None
        else {"kind": "event_refs", "refs": refs or ["event-0"]},
    ).model_dump()


def row(record, identifier="existing", revision=0, invalid=None):
    return {
        "id": identifier,
        "kind": record["kind"],
        "revision": revision,
        "payload": record,
        "archived": 0,
        "invalid_reasons": invalid or [],
    }


@pytest.mark.parametrize(
    "orders,refs,reason,valid,expected",
    [
        ((3,), ["event-0"], None, True, 3),
        ((3, 3), ["event-0", "event-1"], None, True, 3),
        ((3, 4), ["event-0"], None, False, None),
        ((3, 4), ["event-0", "event-1"], None, False, None),
        ((None,), ["event-0"], None, False, None),
        ((3, None), ["event-0"], None, False, None),
        ((3, None), ["event-0", "event-1"], None, False, None),
        ((3, None), ["event-0", "event-1"], "覆盖事件中有时点未确认，不能取已知值代表全场景", True, None),
        ((3, 4), ["event-0", "event-1"], "同一回忆范围出现两个实际时点，尚不能确定当前时点", True, None),
        ((3, 4), [], "尚未核实时点，覆盖不是时点认定", True, None),
        ((3,), ["event-0", "event-0"], None, False, None),
        ((3,), ["absent"], "虽未知也不得引用不存在身份", False, None),
        ((3,), [], "  ", False, None),
    ],
)
def test_exact_refs_and_all_overlap_never_pick_or_clear(orders, refs, reason, valid, expected):
    units = units_for("\n".join(f"😀𠮷事件{i}" for i in range(len(orders))))
    items = proposals(units, orders)
    value = scene(units, refs, reason)
    before = deepcopy(value)
    issues = linked_scene_issues([value], units, {}, items)
    assert bool(issues) is not valid
    assert value == before
    if valid:
        order, audit = make_audit(value, units, effective_records({}, items), {})
        assert order == expected
        assert audit["kind"] == ("unknown" if reason else "event_refs")
        assert len(audit["covered_events"]) == len(items)
        if reason:
            assert audit["reason"] == reason


@pytest.mark.parametrize(
    "damage", ["planned", "wrong_chapter", "old_revision", "outside", "invalid", "wrong_kind"]
)
def test_refs_require_current_actual_source_even_for_unknown(damage):
    units = units_for("渡船\n岸边")
    event = proposals(units[:1], (3,))[0]["record"]
    if damage == "planned":
        event["status"] = "planned"
    elif damage == "wrong_chapter":
        event["sources"][0]["chapter_id"] = "other"
    elif damage == "old_revision":
        event["sources"][0]["revision"] = 0
    elif damage == "outside":
        event["sources"][0].update(start=100, end=102)
    elif damage == "wrong_kind":
        event = RECORD.validate_python(
            {"kind": "character", "title": "船夫", "sources": event["sources"]}
        ).model_dump()
    known = {"existing": row(event, invalid=["正文失效"] if damage == "invalid" else [])}
    for reason in (None, "引用本身仍须有效"):
        assert linked_scene_issues([scene(units, ["existing"], reason)], units, known, [])


def test_order_mismatch_diagnostic_explains_coverage_without_changing_decision():
    units = units_for("第一段观察。\n第二段事件时点未确认。")
    items = proposals(units, (3, None))
    value = scene(units, ["event-0"])
    before = deepcopy(value)
    issues = linked_scene_issues([value], units, {}, items)
    assert len(issues) == 1
    assert "有效引用order=[3]" in issues[0] and "全部覆盖order=[3, null]" in issues[0]
    assert "不能忽略" in issues[0]
    assert all(unit["text"] not in issues[0] for unit in units)
    assert value == before


def test_old_known_scene_audit_with_unknown_covered_event_is_invalid_without_rewriting():
    units = units_for("已知事件。\n实际事件的时点未知。")
    items = proposals(units, (3, None))
    indexed = {item["local_id"]: row(item["record"], item["local_id"]) for item in items}
    event = items[0]["record"]
    witness = {
        "ref": "event-0",
        "event_id": "event-0",
        "event_revision": 0,
        "story_order": 3,
        "record_sha256": payload_sha(event),
    }
    payload = {
        "source_revision": units[0]["revision"],
        "start": units[0]["start"],
        "end": units[-1]["end"],
        "story_order": 3,
        "order_basis": {"version": VERSION, "kind": "event_refs", "refs": [witness], "covered_events": []},
    }
    before = deepcopy(payload)
    assert not audit_valid(None, payload, units[0]["chapter_id"], indexed)
    assert not audit_valid(None, payload, units[0]["chapter_id"], indexed, adopting=True)
    with pytest.raises(DomainError, match="未采用"):
        adopted_audit(None, payload, units[0]["chapter_id"], indexed)
    assert payload == before


def test_new_schema_rejects_redundant_scalar_while_legacy_schema_stays_scalar():
    units = units_for("井")
    value = scene(units)
    assert (
        "story_order"
        not in ExtractionWithLinkedScenes.model_json_schema()["$defs"]["LinkedSceneProposal"]["properties"]
    )
    legacy = ExtractionWithScenes.model_json_schema()["$defs"]["SceneProposal"]["properties"]
    assert "story_order" in legacy and "order_basis" not in legacy
    with pytest.raises(ValidationError):
        LinkedSceneProposal.model_validate({**value, "story_order": 1})
    old = SceneTime(title="手工", source_revision=1, start=0, end=1).model_dump()
    assert "order_basis" not in old
    # Derived from pre-change HEAD c61cf5d's complete schema, including defaults
    # and all nested Record variants. Paid OpenAI legacy cache identity stays fixed.
    assert (
        payload_sha(ExtractionWithScenes.model_json_schema())
        == "a2dc2a86a374c9cec2c3fec3eed1397bfee12e60832ba59b6cb1d51a87be9d08"
    )


@pytest.mark.parametrize(
    "field_order,proposed_order,expected,explicit_sources",
    [(False, 99, 3, False), (True, 7, 7, False), (True, 7, 7, True)],
)
def test_effective_update_uses_listed_fields_and_exact_source_merge(
    field_order, proposed_order, expected, explicit_sources
):
    units = units_for("旧岸\n新岸")
    event = proposals(units[:1], (3,))[0]["record"]
    proposed = proposals(units[1:], (proposed_order,))[0]
    proposed.update(
        target_id="existing", target_revision=0, update_fields=["story_order"] if field_order else ["notes"]
    )
    if explicit_sources:
        proposed["update_fields"].append("sources")
    nodes = effective_records({"existing": row(event)}, [proposed])
    value = nodes["existing"]["record"]
    assert value["story_order"] == expected
    assert len(value["sources"]) == (1 if explicit_sources else 2)
    assert not linked_scene_issues([scene(units, ["existing"])], units, {"existing": row(event)}, [proposed])
    # The local alias cannot be used to duplicate the same formal target.
    assert linked_scene_issues(
        [scene(units, ["existing", proposed["local_id"]])], units, {"existing": row(event)}, [proposed]
    )


@pytest.mark.parametrize("damage", ["absent_field", "different_kind", "kind_field"])
def test_bad_update_payload_returns_repair_issues_instead_of_key_error(damage):
    units = units_for("渡船")
    event = proposals(units, (3,))[0]["record"]
    item = proposals(units, (3,))[0]
    item.update(target_id="existing", target_revision=0, update_fields=["not_a_field"])
    if damage == "different_kind":
        item.update(
            record=RECORD.validate_python(
                {"kind": "character", "title": "船夫", "sources": event["sources"]}
            ).model_dump(),
            update_fields=["profile"],
        )
    elif damage == "kind_field":
        item["update_fields"] = ["kind"]
    assert linked_scene_issues([scene(units, ["existing"])], units, {"existing": row(event)}, [item])


def test_fresh_task_resolves_new_character_and_event_refs_before_atomic_adoption(prepared, domain, story):
    chapter, generate = prepared
    artifact = generate()
    assert artifact["payload"]["scene_contract_version"] == VERSION
    binding = artifact["payload"]["scenes"][0]["scene"]
    event = next(r for r in artifact["payload"]["records"] if r["record"]["kind"] == "event")
    assert binding["event_id"] is None and binding["event_revision"] is None
    assert binding["order_basis"]["refs"][0]["event_id"] == event["id"]
    before = deepcopy(chapter)
    receipt = adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    saved = scenes(domain, story["branch_id"], chapter["id"])
    assert all(not r["stale"] for r in saved["scenes"])
    assert saved["scenes"][0]["payload"]["order_basis"]["refs"][0]["event_revision"] == 0
    assert receipt["unknown_story_times"] == 1 and receipt["story_times_verified"] is False
    assert saved["scenes"][1]["payload"]["order_basis"]["reason"]
    assert domain.chapter(chapter["id"]) == before


def test_partial_selection_cannot_substitute_another_same_order_event(prepared, domain, story):
    _, generate = prepared
    artifact = generate()
    event = next(r for r in artifact["payload"]["records"] if r["record"]["kind"] == "event")
    second = deepcopy(event)
    second.update(id="alternative-same-order-event", local_id="e2")
    payload = deepcopy(artifact["payload"])
    payload["records"].append(second)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    artifact = domain.artifact(artifact["id"])
    # Keep scene, both actors and alternative event, but omit the actual explicit ref.
    chosen = [r["id"] for r in artifact["payload"]["records"] if r["record"]["kind"] == "character"]
    chosen.append(second["id"])
    request = data_for(domain, story, artifact).model_copy(update={"selected_ids": chosen})
    with pytest.raises(DomainError, match="显式事件依据"):
        adopt(domain, story["branch_id"], artifact["id"], request)
    assert domain.db.all("SELECT * FROM scene_times") == []
    assert domain.db.all("SELECT * FROM story_records") == []
    assert event["id"] not in chosen


def test_saved_basis_is_preserved_for_old_client_metadata_edit_and_not_detachable(prepared, domain, story):
    chapter, generate = prepared
    artifact = generate()
    adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    old = scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]
    original = deepcopy(old["payload"])
    omitted = {k: v for k, v in original.items() if k != "order_basis"}
    omitted.update(title="作者改名", note="亲自核对", audience="reader", character_id=None)
    saved = save_scene(
        domain,
        story["branch_id"],
        chapter["id"],
        SaveScene(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            base_revision=old["revision"],
            scene=omitted,
        ),
        old["id"],
    )["scenes"][0]
    assert saved["payload"]["order_basis"] == original["order_basis"]
    for change in ({"order_basis": None}, {"story_order": 7}, {"start": 1}, {"story_time": "擅改时点"}):
        with pytest.raises(DomainError, match="提取依据与范围须保留"):
            save_scene(
                domain,
                story["branch_id"],
                chapter["id"],
                SaveScene(
                    base_story_revision=domain.branch(story["branch_id"])["story_revision"],
                    base_revision=saved["revision"],
                    scene={**saved["payload"], **change},
                ),
                old["id"],
            )
    assert len(domain.db.all("SELECT * FROM scene_time_history WHERE scene_id=?", (old["id"],))) == 2


def test_new_scene_cannot_forge_server_audit(prepared, domain, story):
    chapter, generate = prepared
    artifact = generate()
    value = artifact["payload"]["scenes"][0]["scene"]
    with pytest.raises(DomainError, match="手工范围不伪造依据"):
        save_scene(
            domain,
            story["branch_id"],
            chapter["id"],
            SaveScene(base_story_revision=domain.branch(story["branch_id"])["story_revision"], scene=value),
        )


def test_new_workflow_freezes_marker_and_unknown_adopts_without_claiming_verified(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "😀𠮷只见潮水。")
    task = domain.queue(
        "workflow",
        {
            "novel_id": story["id"],
            "account": "deepseek",
            "adoption": "automatic",
            "structured_maintenance": True,
            "scene_maintenance": True,
        },
        story["branch_id"],
    )
    assert task["result"]["scene_contract_version"] == VERSION
    worker = Worker(settings, SceneOnlyModel())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    progress = worker.task["result"]
    progress.update(
        next_chapter=2,
        expected_story_revision=domain.branch(story["branch_id"])["story_revision"],
        pending_structure={"chapter_id": chapter["id"], "revision": chapter["revision"]},
    )
    worker.checkpoint(progress)
    assert asyncio.run(maintain(worker, progress))
    binding = scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]
    assert binding["payload"]["story_order"] is None
    assert binding["payload"]["order_basis"]["kind"] == "unknown"
    receipt = json.loads(domain.db.one("SELECT result FROM receipts")["result"])
    assert receipt["unknown_story_times"] == 1 and receipt["story_times_verified"] is False


def test_old_nonce_and_missing_marker_stay_legacy_without_mutating_new_tasks(domain, story):
    domain.save(story["chapter_id"], 0, "冻结原稿")
    payload = {
        "novel_id": story["id"],
        "branch_id": story["branch_id"],
        "action": "story.extract",
        "include_scene_ranges": True,
        "story_chapter_ids": [story["chapter_id"]],
        "client_request_id": "old-scene-nonce",
    }
    fresh = domain.queue("module", payload)
    assert execution_version(fresh) == VERSION
    old = deepcopy(fresh["result"])
    old.pop("scene_contract_version")
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(old), fresh["id"]))
    replay = domain.queue("module", payload)
    assert replay["result"] == old and execution_version(replay) == LEGACY_VERSION
    assert execution_version({"result": {}}) == LEGACY_VERSION
    with pytest.raises(DomainError, match="未升级或派发旧任务"):
        execution_version({"result": {"scene_contract_version": "future"}})


def test_unrelated_tasks_do_not_gain_scene_marker(domain, story):
    task = domain.queue("workflow", {"novel_id": story["id"], "scene_maintenance": True}, story["branch_id"])
    assert "scene_contract_version" not in task["result"]


def test_each_explicit_reference_remains_live_not_only_first_event(prepared, domain, story):
    chapter, generate = prepared
    artifact = generate()
    # Add a second *explicit* same-order actual event via a legitimate new extraction.
    event = next(r for r in artifact["payload"]["records"] if r["record"]["kind"] == "event")
    # Pure candidate fixture includes second event with final formal references.
    second = deepcopy(event)
    second.update(id="second-same-order-event", local_id="e2")
    value = deepcopy(artifact["payload"])
    value["records"].append(second)
    audit = value["scenes"][0]["scene"]["order_basis"]
    ref = deepcopy(audit["refs"][0])
    ref.update(ref="e2", event_id=second["id"])
    audit["refs"].append(ref)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(value), artifact["id"]))
    artifact = domain.artifact(artifact["id"])
    adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    saved = scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]
    assert len(saved["payload"]["order_basis"]["refs"]) == 2 and not saved["stale"]
    changed = next(r for r in graph(domain, story["branch_id"])["records"] if r["id"] == second["id"])
    save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            base_revision=changed["revision"],
            record={**changed["payload"], "notes": "作者纠正事件含义"},
        ),
        changed["id"],
    )
    assert scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]["stale"]


@pytest.mark.parametrize("replace_sources", [False, True])
def test_source_replacement_repairs_effective_event_but_append_retains_stale_evidence(
    domain, story, replace_sources
):
    chapter = domain.save(story["chapter_id"], 0, "😀𠮷旧岸")
    old_units = [
        {**u, "chapter_id": chapter["id"], "revision": chapter["revision"]}
        for u in units_for(chapter["content"])
    ]
    event = proposals(old_units, (3,))[0]["record"]
    saved = save_record(
        domain,
        story["branch_id"],
        SaveRecord(base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=event),
    )["records"][0]
    chapter = domain.save(chapter["id"], chapter["revision"], "😀𠮷新岸")
    units = [
        {**u, "chapter_id": chapter["id"], "revision": chapter["revision"]}
        for u in units_for(chapter["content"])
    ]
    item = proposals(units, (3,))[0]
    item.update(
        target_id=saved["id"], target_revision=0, update_fields=["sources"] if replace_sources else ["notes"]
    )
    known = {r["id"]: r for r in graph(domain, story["branch_id"])["records"]}
    assert known[saved["id"]]["invalid_reasons"]
    issues = linked_scene_issues(
        [scene(units, [saved["id"]])], units, known, [item], domain, story["branch_id"]
    )
    assert bool(issues) is not replace_sources


@pytest.mark.parametrize("clear_marker", [False, True])
def test_continuation_keeps_frozen_contract_and_unknown_charge_ack_guard(domain, story, clear_marker):
    payload = {
        "novel_id": story["id"],
        "branch_id": story["branch_id"],
        "account": "deepseek",
        "structured_maintenance": True,
        "scene_maintenance": True,
        "client_request_id": "scene-old-workflow",
    }
    current = domain.queue("workflow", payload)
    progress = deepcopy(current["result"])
    if clear_marker:
        progress.pop("scene_contract_version")
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='blocked_runtime',result=? WHERE id=?", (dumps(progress), current["id"])
        )
    current = domain.task(current["id"])
    next_payload = {**payload, "account": "openai_1", "client_request_id": "scene-continue-workflow"}
    request = {"acknowledge_unknown": False, "reason": "已核对原账户，再用新账户继续"}
    with pytest.raises(DomainError, match="费用预留"):
        domain.continue_workflow(current, next_payload, request)
    assert len(domain.db.all("SELECT id FROM tasks")) == 1
    continued = domain.continue_workflow(current, next_payload, {**request, "acknowledge_unknown": True})
    assert execution_version(continued) == (LEGACY_VERSION if clear_marker else VERSION)
    assert ("scene_contract_version" in continued["result"]) is not clear_marker


def test_old_failed_extraction_reuses_three_completed_steps_and_never_gains_v2(settings, domain, story):
    model = SceneDiagnosticModel()
    worker, progress, _ = pending(settings, domain, story, model)
    assert execution_version(worker.task) == LEGACY_VERSION
    for attempt in range(2):
        with pytest.raises(DomainError, match="两轮引用修正"):
            asyncio.run(maintain(worker, progress))
        assert len(model.calls) == 3
        assert domain.db.one("SELECT count(*) AS n FROM task_steps")["n"] == 3
        assert "scene_contract_version" not in domain.task(worker.task["id"])["result"]
        assert all(c["任务"]["场景证据契约版本"] == LEGACY_VERSION for c in model.calls)


@pytest.mark.parametrize("mode", ["listed_order", "unlisted_order", "realized_plan", "replace_stale_sources"])
def test_formal_adoption_uses_exact_effective_event_fields(settings, domain, story, client, mode):
    chapter = domain.save(story["chapter_id"], 0, "😀𠮷在新岸等渡船。")
    units = [
        {**u, "chapter_id": chapter["id"], "revision": chapter["revision"]}
        for u in units_for(chapter["content"])
    ]
    event = proposals(units, (3,))[0]["record"]
    if mode == "realized_plan":
        event.update(status="planned", sources=[])
    saved = save_record(
        domain,
        story["branch_id"],
        SaveRecord(base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=event),
    )["records"][0]
    if mode == "replace_stale_sources":
        chapter = domain.save(chapter["id"], chapter["revision"], "😀𠮷在改过的新岸等渡船。")

    class UpdateEventModel:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            assert instruction["场景证据契约版本"] == VERSION
            current_units = instruction["可引用原文"]
            source = {k: current_units[0][k] for k in ("chapter_id", "revision", "start", "end")}
            source["quote"] = current_units[0]["source_id"]
            fields = ["story_order"] if mode == "listed_order" else ["notes"]
            if mode == "realized_plan":
                fields = ["status", "sources"]
            elif mode == "replace_stale_sources":
                fields = ["sources"]
            record = {
                **saved["payload"],
                "status": "actual",
                "story_order": 7 if mode == "listed_order" else 99,
                "notes": "只修改列明字段",
                "sources": [source],
            }
            return {
                "text": dumps(
                    {
                        "records": [
                            {
                                "local_id": "updated-event",
                                "target_id": saved["id"],
                                "target_revision": 0,
                                "update_fields": fields,
                                "reasoning": "按明确本批证据更新",
                                "record": record,
                            }
                        ],
                        "unknowns": [],
                        "coverage": "仅当前章",
                        "scenes": [scene(current_units, ["updated-event"])],
                    }
                ),
                "usage": {"input": 1, "output": 1},
                "call_id": "offline-effective-event",
            }

    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions",
        json={
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
            "chapter_ids": [chapter["id"]],
            "account": "deepseek",
            "client_request_id": "effective-event-" + mode,
            "include_scene_ranges": True,
        },
    )
    assert response.status_code == 202, response.text
    asyncio.run(Worker(settings, UpdateEventModel()).run_once())
    task = domain.task(response.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    artifact = domain.artifact(task["result"]["artifact_id"])
    adopt(domain, story["branch_id"], artifact["id"], data_for(domain, story, artifact))
    binding = scenes(domain, story["branch_id"], chapter["id"])["scenes"][0]
    assert not binding["stale"]
    assert binding["payload"]["story_order"] == (7 if mode == "listed_order" else 3)
    assert binding["payload"]["order_basis"]["refs"][0]["event_revision"] == 1
