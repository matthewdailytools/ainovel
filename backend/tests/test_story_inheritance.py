import asyncio
import json

import pytest

from ainovel.story_graph import SaveRecord, graph, query_context, save_record
from ainovel.worker import Worker
from test_api_worker import FakeModel


@pytest.fixture
def setup(domain, story):
    chapter = domain.save(story["chapter_id"], 0, "😀旧版经历：宁禾已经认出暗号。")
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 2,
        "end": len(chapter["content"].encode("utf-16-le")) // 2,
        "quote": chapter["content"][1:],
    }

    def add(record):
        before = {r["id"] for r in graph(domain, story["branch_id"])["records"]}
        result = save_record(
            domain,
            story["branch_id"],
            SaveRecord(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=record
            ),
        )
        return next(r for r in result["records"] if r["id"] not in before)

    character = add(
        {
            "kind": "character",
            "title": "宁禾",
            "aliases": ["小宁"],
            "profile": "作者选择的本版人物档案",
            "sources": [source],
        }
    )
    plan = add(
        {
            "kind": "event",
            "title": "未来调查",
            "participants": [character["id"]],
            "status": "planned",
            "story_order": 20,
        }
    )
    state = add(
        {
            "kind": "state",
            "title": "计划获知线索",
            "character_id": character["id"],
            "event_id": plan["id"],
            "category": "knowledge",
            "key": "线索",
            "value": "调查后知道",
            "audience": "character",
            "truth": "planned",
        }
    )
    promise = add(
        {
            "kind": "promise",
            "title": "计划中的追问",
            "stages": [{"status": "planned", "event_id": plan["id"]}],
        }
    )
    actual = add(
        {
            "kind": "event",
            "title": "旧版已经发生",
            "status": "actual",
            "participants": [character["id"]],
            "story_order": 10,
            "sources": [source],
        }
    )
    dependent = add(
        {
            "kind": "state",
            "title": "依赖旧经历的计划",
            "character_id": character["id"],
            "event_id": actual["id"],
            "category": "capability",
            "key": "能力",
            "value": "待练习",
            "audience": "author",
            "truth": "planned",
        }
    )
    return {
        "selected": [character["id"], plan["id"], state["id"], promise["id"]],
        "character": character,
        "actual": actual,
        "dependent": dependent,
        "chapter": chapter,
        "add": add,
    }


def request(story, setup, client, **changes):
    preview = client.get(f"/api/branches/{story['branch_id']}/story-inheritance").json()
    return {
        "novel_id": story["id"],
        "source_branch_id": story["branch_id"],
        "mode": "automatic",
        "brief": "量潮并在公开核验后结束。",
        "chapters": 1,
        "min_chars": 200,
        "max_chars": 600,
        "account": "openai_1",
        "adoption": "automatic",
        "inherit_story_record_ids": setup["selected"],
        "inherit_source_revision": preview["source_story_revision"],
        **changes,
    }


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
def test_selected_setup_plans_have_independent_ids_and_never_copy_actual_history(
    client, domain, settings, story, setup, mode
):
    body = request(story, setup, client, mode=mode)
    if mode == "assisted":
        body["plots"] = [{"number": 1, "title": "核验", "plot": "量潮并核验", "fixed": []}]
    response = client.post("/api/workflows", json=body)
    assert response.status_code == 202, response.text
    task = response.json()
    target = task["branch_id"]
    snapshot = graph(domain, target)
    assert len(snapshot["records"]) == 4
    assert not set(setup["selected"]) & {r["id"] for r in snapshot["records"]}
    assert all(not r["payload"]["sources"] and not r["invalid_reasons"] for r in snapshot["records"])
    assert client.get(f"/api/branches/{target}/chapters").json() == []
    context = query_context(domain, target, story_order=30, audience="character")
    assert context["states"] == []
    assert context["uncertain"][0]["payload"]["truth"] == "planned"
    assert context["characters"][0]["payload"]["profile"] == "作者选择的本版人物档案"
    manifest = task["result"]["structure_inheritance"]
    assert manifest["source_branch_id"] == story["branch_id"]
    assert manifest["copied_manuscripts"] is False
    assert {r["source_id"] for r in manifest["records"]} == set(setup["selected"])
    assert "旧版经历" not in json.dumps(manifest, ensure_ascii=False)
    source_before = graph(domain, story["branch_id"])
    model = FakeModel()
    asyncio.run(Worker(settings, model).run_once())
    # Planned unfulfilled promises remain blocking, even if the fixture claims closure.
    assert domain.task(task["id"])["status"] == "blocked_creative"
    if mode == "automatic":
        assert model.plan_context["longform"]["states"] == []
        assert model.plan_context["recent"] == []
        assert "旧版经历" not in json.dumps(model.plan_context, ensure_ascii=False)
    assert graph(domain, story["branch_id"]) == source_before
    inherited_char = next(r for r in snapshot["records"] if r["kind"] == "character")
    save_record(
        domain,
        target,
        SaveRecord(
            base_story_revision=domain.branch(target)["story_revision"],
            base_revision=inherited_char["revision"],
            record={**inherited_char["payload"], "profile": "本版另行修改"},
        ),
        inherited_char["id"],
    )
    assert graph(domain, story["branch_id"]) == source_before


@pytest.mark.parametrize(
    "invalid", ["actual", "dependent", "missing_dependency", "duplicate", "stale", "foreign"]
)
def test_invalid_inheritance_rolls_back_new_branch_and_all_records(client, domain, story, setup, invalid):
    body = request(story, setup, client)
    if invalid in ("actual", "dependent"):
        body["inherit_story_record_ids"] = [setup[invalid]["id"]]
    elif invalid == "missing_dependency":
        body["inherit_story_record_ids"] = setup["selected"][1:]
    elif invalid == "duplicate":
        body["inherit_story_record_ids"] += [setup["selected"][0]]
    elif invalid == "stale":
        setup["add"]({"kind": "character", "title": "后来新增"})
    else:
        other = domain.create_novel("别的作品", "")
        body["source_branch_id"] = other["branch_id"]
        body["inherit_source_revision"] = 0
    counts = {
        t: domain.db.one("SELECT count(*) AS n FROM " + t)["n"]
        for t in ["branches", "story_records", "story_record_history", "artifacts", "tasks"]
    }
    response = client.post("/api/workflows", json=body)
    assert response.status_code == (403 if invalid == "foreign" else 409), response.text
    assert counts == {t: domain.db.one("SELECT count(*) AS n FROM " + t)["n"] for t in counts}


def test_preview_explains_excluded_history_and_old_callers_do_not_inherit(client, domain, story, setup):
    preview = client.get(f"/api/branches/{story['branch_id']}/story-inheritance").json()
    indexed = {r["id"]: r for r in preview["items"]}
    assert indexed[setup["character"]["id"]]["eligible"]
    assert not indexed[setup["actual"]["id"]]["eligible"]
    assert not indexed[setup["dependent"]["id"]]["eligible"]
    body = request(story, setup, client)
    body.pop("inherit_story_record_ids")
    body.pop("inherit_source_revision")
    body["client_request_id"] = "old-caller-without-inheritance"
    first = client.post("/api/workflows", json=body).json()
    assert graph(domain, first["branch_id"])["records"] == []
    assert client.post("/api/workflows", json=body).json()["id"] == first["id"]


def test_mid_copy_failure_rolls_back_definitions_records_history_and_branch(
    client, domain, story, setup, monkeypatch
):
    from ainovel import story_inheritance
    from ainovel.db import DomainError

    original = story_inheritance.validate_record
    calls = []

    def injected(tx, branch_id, identifier, payload):
        calls.append(identifier)
        if len(calls) == 2:
            raise DomainError("injected_failure", "模拟复制中断", 409)
        return original(tx, branch_id, identifier, payload)

    monkeypatch.setattr(story_inheritance, "validate_record", injected)
    counts = {
        t: domain.db.one("SELECT count(*) AS n FROM " + t)["n"]
        for t in [
            "branches",
            "artifacts",
            "story_records",
            "story_record_history",
            "story_record_dependencies",
            "tasks",
        ]
    }
    response = client.post("/api/workflows", json=request(story, setup, client))
    assert response.status_code == 409
    assert response.json()["code"] == "injected_failure"
    assert len(calls) == 2
    assert counts == {t: domain.db.one("SELECT count(*) AS n FROM " + t)["n"] for t in counts}


def test_same_novel_other_branch_records_and_missing_seen_basis_are_rejected(client, domain, story, setup):
    other = client.post(
        "/api/workflows",
        json=request(story, setup, client, inherit_story_record_ids=[], inherit_source_revision=None),
    ).json()
    body = request(story, setup, client, source_branch_id=other["branch_id"], inherit_source_revision=0)
    response = client.post("/api/workflows", json=body)
    assert response.status_code == 409
    assert response.json()["code"] == "story_inheritance"
    body = request(story, setup, client, inherit_source_revision=None)
    assert client.post("/api/workflows", json=body).json()["code"] == "story_inheritance"
