"""Adopted creation context is frozen for choice/check, never source evidence."""

import asyncio
from copy import deepcopy
import json

import pytest
import httpx

from ainovel.db import DomainError, dumps, uid
from ainovel.story_graph import SaveRecord, save_record
from ainovel.workflow_plan_context import KEY as CONTEXT_KEY
from ainovel.workflow_plan_open_details import KEY as OPEN_KEY
from ainovel.workflow_plan_preparation import CHECK_ACTION, CHOICE_ACTION, prepare
from ainovel.workflow_plan_recipient_scope import ACTION as SCOPE_ACTION
from ainovel.workflow_plan_scope_classification import ACTION as CLASSIFICATION_ACTION
from ainovel.authored_source_classification import ACTION as AUTHOR_CLASSIFICATION_ACTION
from ainovel.prepared_source_object_aliases import ASSOCIATE, VERIFY
from ainovel.models import ModuleInput
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from test_workflow_plan_open_details import OpenModel, SOURCE
from test_workflow_plan_preparation import PreparationModel, claimed
from test_author_contract import adopt_intent, FIELDS
from test_genre_integration import adopt_reader
from test_openai_schema import completed


@pytest.mark.parametrize("action", [ASSOCIATE, VERIFY, AUTHOR_CLASSIFICATION_ACTION, CLASSIFICATION_ACTION])
def test_new_source_actions_native_wire_keeps_explicit_frozen_inputs_without_implicit_enrichment(
    settings, domain, story, action
):
    genre_marker = "UNRELATED_GENRE_FUTURE_MARKER"
    author_marker = "UNRELATED_AUTHOR_FUTURE_MARKER"
    adopt_reader(domain, story, text=genre_marker, author_exceptions=[genre_marker])
    adopt_intent(domain, story, text=author_marker, fields={**FIELDS, "ending": author_marker})
    payload = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.advise",
        instruction="原生上下文发送范围探针。",
        account="openai_1",
    ).model_dump(exclude_none=True)
    task = domain.queue("module", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == task["id"]
    explicit = {
        "冻结来源原文": "EXPLICIT_SOURCE_ONLY",
        "原文观察和P": {"observation_id": "O01", "item_id": "P01", "source": "EXPLICIT_SOURCE_ONLY"},
    }
    if action != ASSOCIATE:
        explicit.update(
            全部固定项=[{"source_id": "FIX001", "text": "EXPLICIT_FIXED_ONLY"}],
            采用约定={"text": "EXPLICIT_CONTRACT_ONLY"},
            原始全批规划={"chapters": [{"number": 1, "plot": "EXPLICIT_PLAN_ONLY"}]},
        )
    frames = []

    def handler(request):
        frame = json.loads(json.loads(request.content)["input"][1]["content"])
        frames.append(frame)
        return completed("source-wire-ok")

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    assert (
        asyncio.run(worker.call({"动作": action, **explicit}, context={"scope": "仅本次显式冻结来源。"}))
        == "source-wire-ok"
    )
    assert len(frames) == 1
    assert frames[0]["上下文"] == {"scope": "仅本次显式冻结来源。"}
    assert frames[0]["任务"] == {"动作": action, **explicit}
    text = dumps(frames[0])
    assert genre_marker not in text and author_marker not in text
    assert domain.chapter(story["chapter_id"])["content"] == ""
    with worker.budget.db() as tx:
        calls = list(tx.execute("SELECT status,charged FROM calls WHERE task_id=?", (task["id"],)))
    assert len(calls) == 1 and calls[0]["status"] == "settled" and calls[0]["charged"] is not None


@pytest.fixture
def book_context(settings, domain, story, client):
    prior = domain.save(
        story["chapter_id"], 0, "PAST_MARKER：门吏与学徒检查旧灯。门吏左手受伤，备用灯没有试亮。"
    )
    target = domain.create_chapter(story["branch_id"], "当前空章", 2)
    future = domain.create_chapter(story["branch_id"], "后章", 3)
    future = domain.save(future["id"], 0, "FUTURE_SECRET：后章才发现灯库密钥。")
    other = domain.create_novel("OTHER_NOVEL_MARKER", "不得发送")
    other_chapter = domain.create_chapter(other["branch_id"], "他书")
    domain.save(other_chapter["id"], 0, "OTHER_PROSE_MARKER")

    def definition(branch, kind, text, **fields):
        response = client.post(
            f"/api/branches/{branch}/definitions",
            json={
                "kind": kind,
                "text": text,
                "base_story_revision": domain.branch(branch)["story_revision"],
                **fields,
            },
        )
        assert response.status_code == 201, response.text

    definition(story["branch_id"], "world", "WORLD_MARKER：本书用机关灯，不存在手机传信。")
    definition(story["branch_id"], "characters", "IDENTITY_MARKER：门吏是守闸人；学徒随老师学习，不是商人。")
    definition(story["branch_id"], "outline", "PLANNING_MARKER：后卷才解决灯库谜题，计划不是已经发生。")
    definition(
        story["branch_id"],
        "reader",
        "尊重本书疑点",
        audience="女频",
        genres=["悬疑"],
        mechanisms=["无CP"],
        author_exceptions=["不新增恋爱线"],
    )
    definition(other["branch_id"], "characters", "OTHER_CHARACTER_MARKER")
    domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "characters",
        {"text": "UNACCEPTED_CANDIDATE_SECRET"},
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    evidence = {
        "chapter_id": prior["id"],
        "revision": prior["revision"],
        "start": 0,
        "end": len(prior["content"].encode("utf-16-le")) // 2,
        "quote": prior["content"],
    }
    later = {
        "chapter_id": future["id"],
        "revision": future["revision"],
        "start": 0,
        "end": len(future["content"].encode("utf-16-le")) // 2,
        "quote": future["content"],
    }

    def record(payload):
        result = save_record(
            domain,
            story["branch_id"],
            SaveRecord(
                base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=payload
            ),
        )
        return result["records"][-1]

    character = record({"kind": "character", "title": "门吏", "sources": [evidence]})
    event = record(
        {
            "kind": "event",
            "title": "左手受伤",
            "status": "actual",
            "story_order": 10,
            "participants": [character["id"]],
            "sources": [evidence],
        }
    )
    state = record(
        {
            "kind": "state",
            "title": "手伤状态",
            "character_id": character["id"],
            "event_id": event["id"],
            "category": "capability",
            "key": "左手",
            "value": "受伤",
            "truth": "actual",
            "audience": "author",
            "sources": [evidence],
        }
    )
    mixed = record(
        {
            "kind": "event",
            "title": "FUTURE_SECRET在自由字段，前章只含部分来源",
            "status": "actual",
            "story_order": 90,
            "sources": [evidence, later],
        }
    )
    planned = record({"kind": "event", "title": "打算以后修灯", "status": "planned", "sources": [evidence]})
    with domain.db.connect(True) as tx:
        for chapter, statement in [
            (prior, "LEGACY_PAST_FACT"),
            (future, "FUTURE_FACT_SECRET"),
            (other_chapter, "OTHER_FACT_SECRET"),
        ]:
            branch = story["branch_id"] if chapter["id"] != other_chapter["id"] else other["branch_id"]
            tx.execute(
                "INSERT INTO facts(id,branch_id,chapter_id,source_revision,subject,kind,statement,truth,story_time,evidence,evidence_start,evidence_end) VALUES(?,?,?,?,?,'knowledge',?,'fact','某日',?,0,?)",
                (
                    uid(),
                    branch,
                    chapter["id"],
                    1,
                    "门吏",
                    statement,
                    chapter["content"],
                    len(chapter["content"].encode("utf-16-le")) // 2,
                ),
            )
    return {
        **story,
        "chapter_id": target["id"],
        "prior": prior,
        "target": target,
        "future": future,
        "state": state,
        "mixed": mixed,
        "planned": planned,
    }


def worker_for(settings, domain, book, model=None, *, legacy=False):
    worker, plot, chapter, task = claimed(
        settings, domain, book, model=model or OpenModel(), legacy=legacy, chapters=2
    )
    plot.update(number=2, plot=SOURCE)
    worker.task["result"]["plan"]["chapters"] = [
        {"number": 1, "title": "前章", "plot": "检查旧灯", "fixed": []},
        plot,
    ]
    worker.task["result"]["next_chapter"] = 2
    if legacy:
        worker.task["result"].pop(OPEN_KEY)
    worker.checkpoint(worker.task["result"])
    return worker, plot, chapter, task


def test_choice_and_compatibility_check_receive_frozen_adopted_context_but_interpretation_does_not(
    settings, domain, book_context
):
    worker, plot, chapter, task = worker_for(settings, domain, book_context)
    asyncio.run(prepare(worker, plot, chapter))
    row = domain.task(task["id"])["result"]["plot_preparations"]["2"]
    frozen = row[CONTEXT_KEY]
    assert frozen["basis"]["chapter_position"] == 2 and frozen["basis"]["chapter_revision"] == 0
    assert "content" not in frozen["data"]["chapter"]
    for frame in worker.client.preparation_frames:
        action, context = frame["任务"]["动作"], frame["上下文"]
        if action in ("写前解释AI工作章纲", SCOPE_ACTION):
            assert set(context) == {"scope"}
            assert "creative_context" not in dumps(frame) and "WORLD_MARKER" not in dumps(frame)
            if action == SCOPE_ACTION:
                assert frame["任务"]["本章工作章纲原文"].startswith(SOURCE)
                assert "recipient_scope_inventory" not in frame["任务"]
                assert "原始事项" not in frame["任务"]
        elif action == CLASSIFICATION_ACTION:
            assert set(context) == {"scope"}
            assert frame["任务"]["接收范围核对执行版本"] == "workflow-plan-recipient-scope/3"
            assert frame["任务"]["原始AI本章"] == row["original_plot"]
            assert frame["任务"]["有效AI本章"] == row["effective_plot"]
            assert frame["任务"]["完整原规划与采用约定"]["原始全批规划"] == worker.task["result"]["plan"]
            text = dumps(frame)
            assert "FUTURE_SECRET" not in text and "FUTURE_FACT_SECRET" not in text and "OTHER_" not in text
            assert "UNACCEPTED_CANDIDATE_SECRET" not in text
            assert "本次候选" not in frame["任务"] and "正文" not in frame["任务"]
        else:
            assert action in (CHECK_ACTION, CHOICE_ACTION)
            assert context["creative_context_sha256"] == frozen["sha256"]
            text = dumps(context)
            assert (
                "WORLD_MARKER" in text
                and "IDENTITY_MARKER" in text
                and "PAST_MARKER" in text
                and "LEGACY_PAST_FACT" in text
            )
            assert "FUTURE_SECRET" not in text and "FUTURE_FACT_SECRET" not in text and "OTHER_" not in text
            assert "UNACCEPTED_CANDIDATE_SECRET" not in text
            assert context["genre_profile"]["audience"] == "女频" and context["genre_profile"][
                "genre_ids"
            ] == ["mystery"]
            assert "不新增恋爱线" in context["genre_profile"]["author_exceptions"]
            assert any(topic["group"] == "mystery" for topic in context["genre_profile"]["required_topics"])
            assert book_context["state"]["id"] in [r["id"] for r in context["longform"]["uncertain"]]
            assert not context["longform"]["states"]  # no invented current story order
            assert book_context["planned"]["id"] in [r["id"] for r in context["longform"]["plans"]]
            assert book_context["mixed"]["id"] not in [r["id"] for r in context["longform"]["events"]]
    actions = [frame["任务"]["动作"] for frame in worker.client.preparation_frames]
    assert actions.index(SCOPE_ACTION) < actions.index("写前解释AI工作章纲")
    requests = [
        json.loads(r["payload"])["request"]
        for r in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
        )
    ]
    assert sum(r.get("workflow_plan_creative_context_sha256") == frozen["sha256"] for r in requests) == 3


class ChoiceTimeout(OpenModel):
    async def generate(self, account, task_id, system, prompt, *args, **kwargs):
        if json.loads(prompt.split("\n只返回")[0])["任务"]["动作"] == CHOICE_ACTION:
            raise DomainError("timeout", "未知结果，不能重发")
        return await super().generate(account, task_id, system, prompt, *args, **kwargs)


def started(settings, domain, book):
    worker, plot, chapter, task = worker_for(settings, domain, book, ChoiceTimeout())
    with pytest.raises(DomainError, match="未知结果"):
        asyncio.run(prepare(worker, plot, chapter))
    worker.task = domain.task(task["id"])
    return worker, plot, chapter, task


def test_unknown_phase_recovery_uses_same_context_without_requery_or_new_dispatch(
    settings, domain, book_context, monkeypatch
):
    worker, plot, chapter, task = started(settings, domain, book_context)
    frozen = deepcopy(worker.task["result"]["plot_preparations"]["2"][CONTEXT_KEY])
    model = OpenModel()
    worker.client = model
    monkeypatch.setattr(worker, "context", lambda *a, **k: pytest.fail("不得补取新的已采用上下文"))
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    assert worker.task["result"]["plot_preparations"]["2"][CONTEXT_KEY] == frozen
    assert model.calls == 0
    assert domain.task(task["id"])["status"] == "needs_review"


@pytest.mark.parametrize("damage", ["missing", "tampered", "acknowledged_source_change"])
def test_started_context_cannot_be_silently_added_changed_or_refreshed(
    settings, domain, book_context, monkeypatch, damage
):
    worker, plot, chapter, _ = started(settings, domain, book_context)
    row = worker.task["result"]["plot_preparations"]["2"]
    if damage == "missing":
        row.pop(CONTEXT_KEY)
    elif damage == "tampered":
        row[CONTEXT_KEY]["data"]["definitions"][0]["payload"]["text"] = "已被替换"
    else:
        prior = book_context["prior"]
        domain.save(prior["id"], prior["revision"], prior["content"] + "前文已改变")
        worker.task["result"]["expected_story_revision"] = domain.branch(book_context["branch_id"])[
            "story_revision"
        ]
    model = OpenModel()
    worker.client = model
    monkeypatch.setattr(worker, "context", lambda *a, **k: pytest.fail("不得在已有阶段后重新冻结"))
    with pytest.raises(DomainError) as caught:
        asyncio.run(prepare(worker, plot, chapter))
    assert caught.value.code in {"plan_preparation_context", "plan_preparation_context_changed"}
    assert model.calls == 0


def test_v1_keeps_original_isolated_scope_and_never_reads_creation_context(
    settings, domain, book_context, monkeypatch
):
    worker, plot, chapter, task = worker_for(
        settings, domain, book_context, PreparationModel(ambiguous_until=0), legacy=True
    )
    monkeypatch.setattr(worker, "context", lambda *a, **k: pytest.fail("v1不能引入新上下文或新提示"))
    asyncio.run(prepare(worker, plot, chapter))
    assert CONTEXT_KEY not in domain.task(task["id"])["result"]["plot_preparations"]["2"]
    assert all(set(f["上下文"]) == {"scope"} for f in worker.client.preparation_frames)
    assert all("写前创作上下文" not in dumps(f) for f in worker.client.preparation_frames)


@pytest.mark.parametrize("verdict", ["fail", "unknown"])
def test_adopted_world_incompatibility_or_uncertainty_stops_before_prose(
    settings, domain, book_context, verdict
):
    class WorldCheck(OpenModel):
        async def generate(self, account, task_id, system, prompt, *args, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            response = await super().generate(account, task_id, system, prompt, *args, **kwargs)
            value = json.loads(response["text"])
            if frame["任务"]["动作"] == CHOICE_ACTION:
                value["addendum"] = "门吏通过手机把备用灯未试亮的消息说给学徒，学徒记下缺口。"
            elif frame["任务"]["动作"] == CHECK_ACTION and "手机" in frame["任务"]["有效章纲"]:
                assert "不存在手机传信" in dumps(frame["上下文"]["definitions"])
                value["constraints_check"].update(
                    result=verdict,
                    reason="预置独立核对：附注与已采用世界规则不兼容或尚待判断，不能只按原章纲逐项表面一致通过。",
                )
            response["text"] = dumps(value)
            return response

    worker, plot, chapter, task = worker_for(settings, domain, book_context, WorldCheck())
    assert asyncio.run(prepare(worker, plot, chapter)) is None
    saved = domain.task(task["id"])["result"]["plot_preparations"]["2"]
    assert saved["status"] == "needs_review" and saved["round"] == 1
    assert saved["preservation_checks"][-1]["result"]["constraints_check"]["result"] == verdict
    assert domain.chapter(chapter["id"])["revision"] == 0
    assert not domain.db.all(
        "SELECT id FROM artifacts WHERE chapter_id=? AND kind='chapter_draft'", (chapter["id"],)
    )
