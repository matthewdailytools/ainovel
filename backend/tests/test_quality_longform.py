from dataclasses import replace
import json
import sqlite3

import httpx
import pytest

from ainovel.quality_longform import LongformEvaluation, RunSpec, read_usage, workflow_payload
from ainovel.settings import ROOT


@pytest.fixture
def story():
    return json.loads((ROOT / "fixtures/original-story.json").read_text("utf-8"))


def test_ten_is_original_opening_not_compressed_book_and_books_remain_36(story):
    ten = RunSpec()
    assisted = workflow_payload(ten, story, "novel", "assisted")
    automatic = workflow_payload(ten, story, "novel", "automatic")
    assert assisted["chapters"] == automatic["chapters"] == 10
    assert assisted["completion_scope"] == automatic["completion_scope"] == "segment"
    assert assisted["writing_profile"] == automatic["writing_profile"]
    assert "共同固定基准" in assisted["writing_profile"]
    assert assisted["plots"] == story["chapters"][:10]
    assert automatic["plots"] == []
    assert assisted["brief"] == automatic["brief"]
    assert assisted["brief"].startswith(story["brief"])
    assert "不把全书结局压缩" in assisted["brief"]
    assert assisted["structured_maintenance"] and assisted["scene_maintenance"]
    assert "source_branch_id" not in assisted and not assisted["inherit_story_record_ids"]
    books = workflow_payload(replace(ten, stage="books"), story, "novel", "assisted")
    assert books["chapters"] == 36 and books["plots"] == story["chapters"]
    assert books["brief"] == story["brief"]
    assert books["completion_scope"] == "whole_book"
    assert books["writing_profile"] == assisted["writing_profile"]


def test_legacy_ten_payload_is_not_silently_reclassified_or_redispatched(tmp_path, story):
    with httpx.Client(base_url="http://127.0.0.1") as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(), story)
        payload = workflow_payload(RunSpec(), story, "n", "assisted")
        payload.pop("completion_scope")
        from ainovel.quality_longform import signature

        runner.record["workflows"]["assisted"] = {
            "payload": payload,
            "payload_sha256": signature(payload),
            "id": "old-frozen-task",
        }
        with pytest.raises(ValueError, match="Legacy ten-chapter"):
            runner.dispatch("assisted", {"id": "n"})
        assert "completion_scope" not in runner.record["workflows"]["assisted"]["payload"]


@pytest.mark.parametrize("status", ["segment_complete", "production_complete"])
def test_ten_run_requires_segment_terminal_without_confusing_book_completion(
    tmp_path, story, monkeypatch, status
):
    with httpx.Client(base_url="http://127.0.0.1") as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(), story)
        monkeypatch.setattr(
            runner,
            "get",
            lambda path: {"accounts": [{"id": "deepseek", "configured": True, "priced": True}], "budget": []},
        )
        monkeypatch.setattr(runner, "create_novel", lambda: {"id": "n"})
        monkeypatch.setattr(runner, "dispatch", lambda mode, novel: "t")
        monkeypatch.setattr(runner, "observe", lambda identifier: task(status))
        runner.run()
    assert ("finished" in runner.record) == (status == "segment_complete")
    assert ("stopped" in runner.record) == (status != "segment_complete")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stage": "short-book"},
        {"modes": ("automatic", "automatic")},
        {"modes": ()},
        {"min_chars": 3500, "max_chars": 2500},
        {"run_limit": 0},
        {"run_limit": float("nan")},
    ],
)
def test_no_implicit_scope_reduction_or_unbounded_spend(kwargs):
    with pytest.raises(ValueError):
        RunSpec(**kwargs)


def test_unknown_novel_creation_cannot_be_repeated_on_resume(tmp_path, story):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown resource creation outcome")

    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(), story)
        with pytest.raises(httpx.ReadTimeout):
            runner.create_novel()
        resumed = LongformEvaluation(client, tmp_path, RunSpec(), story)
        with pytest.raises(RuntimeError, match="outcome unknown"):
            resumed.create_novel()
    assert len(calls) == 1


def test_workflow_response_loss_uses_frozen_key_payload_and_distinct_branches(tmp_path, story):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            raise httpx.ReadTimeout("lost workflow response")
        return httpx.Response(
            202, json={"id": "task-" + payload["mode"], "branch_id": "branch-" + payload["mode"]}
        )

    spec = RunSpec(stage="books", modes=("assisted", "automatic"))
    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
        runner = LongformEvaluation(client, tmp_path, spec, story)
        with pytest.raises(httpx.ReadTimeout):
            runner.dispatch("assisted", {"id": "novel"})
        resumed = LongformEvaluation(client, tmp_path, spec, story)
        assert resumed.dispatch("assisted", {"id": "novel"}) == "task-assisted"
        assert resumed.dispatch("automatic", {"id": "novel"}) == "task-automatic"
    assert calls[0] == calls[1]
    assert calls[2]["client_request_id"] != calls[1]["client_request_id"]
    assert calls[2]["plots"] == [] and calls[1]["plots"] == story["chapters"]
    assert (
        resumed.record["workflows"]["assisted"]["branch_id"]
        != resumed.record["workflows"]["automatic"]["branch_id"]
    )


def test_fixture_or_configuration_change_cannot_silently_resume(tmp_path, story):
    with httpx.Client(base_url="http://127.0.0.1") as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(), story)
        runner.save()
        with pytest.raises(ValueError, match="Frozen"):
            LongformEvaluation(client, tmp_path, RunSpec(stage="books"), story)
        with pytest.raises(ValueError, match="Frozen"):
            LongformEvaluation(client, tmp_path, RunSpec(), {**story, "brief": "changed"})


def task(status="blocked_runtime"):
    return {
        "id": "t",
        "kind": "workflow",
        "novel_id": "n",
        "branch_id": "b",
        "status": status,
        "error": "unknown outcome" if status == "blocked_runtime" else "",
        "payload": {"chapters": 10},
        "result": {"next_chapter": 2},
    }


def test_read_only_observation_of_failure_exports_without_restart_or_accept(tmp_path, story):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET"
        path = request.url.path
        if path == "/api/tasks/t":
            return httpx.Response(200, json=task())
        if path.endswith("/context"):
            return httpx.Response(200, json={"branch": {"story_revision": 1}})
        if path.endswith("/chapters"):
            return httpx.Response(200, json=[{"id": "c"}])
        if path == "/api/chapters/c":
            return httpx.Response(200, json={"id": "c", "revision": 1, "content": "原稿😀𠮷"})
        if path.endswith("/export"):
            return httpx.Response(200, text="# 第一章\n\n原稿😀𠮷")
        if path == "/api/config":
            return httpx.Response(200, json={"budget": [{"provider": "deepseek", "settled": 2}]})
        if path.endswith("/completion"):
            return httpx.Response(200, json={"production_complete": False, "accepted": False})
        return httpx.Response(200, json={"scenes": []} if path.endswith("/scene-times") else [])

    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(), story)
        observed = runner.observe("t")
    assert observed["status"] == "blocked_runtime"
    snapshot = next((tmp_path / "t").glob("snapshot-*"))
    report = json.loads((snapshot / "evidence.json").read_text("utf-8"))
    assert report["capture_consistent"] and not report["model_completion"]
    assert report["literary_quality"] == report["benefit"] == "unverified"
    assert "原稿😀𠮷" in (snapshot / "manuscript.md").read_text("utf-8")
    assert ("GET", "/api/chapters/c/history") in calls


def test_identity_change_during_poll_is_not_treated_as_same_workflow(tmp_path, story, monkeypatch):
    count = []

    def handler(request):
        count.append(request)
        row = task("running")
        if len(count) > 1:
            row["branch_id"] = "other-branch"
        return httpx.Response(200, json=row)

    monkeypatch.setattr("ainovel.quality_longform.sleep", lambda seconds: None)
    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(), story)
        with pytest.raises(ValueError, match="identity"):
            runner.observe("t")
    assert len(count) == 2


def test_failed_first_mode_never_dispatches_second_paid_workflow(tmp_path, story, monkeypatch):
    calls = []
    with httpx.Client(base_url="http://127.0.0.1") as client:
        runner = LongformEvaluation(client, tmp_path, RunSpec(modes=("assisted", "automatic")), story)
        monkeypatch.setattr(
            runner,
            "get",
            lambda path: {"accounts": [{"id": "deepseek", "configured": True, "priced": True}], "budget": []},
        )
        monkeypatch.setattr(runner, "create_novel", lambda: {"id": "n"})
        monkeypatch.setattr(runner, "dispatch", lambda mode, novel: calls.append(mode) or "t")
        monkeypatch.setattr(runner, "observe", lambda identifier: task())
        runner.run()
    assert calls == ["assisted"]
    assert runner.record["stopped"]["status"] == "blocked_runtime"
    assert runner.record["engineering"] == runner.record["literary_quality"] == "unverified"


def test_full_read_only_usage_keeps_unknown_holds_and_currency_separate(tmp_path):
    path = tmp_path / "usage.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE calls(id TEXT,task_id TEXT,currency TEXT,charged INTEGER,reserved INTEGER,created INTEGER)"
        )
        db.execute("CREATE TABLE call_metadata(call_id TEXT,actual_model TEXT,request_id TEXT)")
        db.executemany(
            "INSERT INTO calls VALUES(?,?,?,?,?,?)",
            [
                ("a", "t", "USD", 100, 200, 1),
                ("b", "t", "CNY", None, 500000, 2),
                ("c", "other", "USD", 100000, 200000, 3),
            ],
        )
    before = path.read_bytes()
    usage = read_usage(tmp_path, ["t"])
    assert len(usage["calls"]) == 2
    assert usage["cost_by_currency"] == {
        "USD": {"settled": 0.0001, "reserved_unknown": 0},
        "CNY": {"settled": 0, "reserved_unknown": 0.5},
    }
    assert path.read_bytes() == before
