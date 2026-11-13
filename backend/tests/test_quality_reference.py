import json
from pathlib import Path

import httpx
import pytest

from ainovel.quality_reference import CASES, Evaluation, blind_pack, write_json


@pytest.mark.parametrize("reference_first", [False, True])
def test_selected_case_pair_order_is_recorded_and_resume_cannot_change_it(tmp_path, reference_first):
    class Runner(Evaluation):
        def __init__(self):
            super().__init__(None, tmp_path, "deepseek", 1)
            self.order = []

        def request(self, method, url, **kwargs):
            if url == "/config":
                return {"accounts": [{"id": "deepseek", "configured": True, "priced": True}], "budget": []}
            if url.endswith("/units"):
                return [{"id": "unit"}]
            if url == "/chapters/chapter":
                return {"revision": 0, "content": ""}
            raise AssertionError(url)

        def once(self, name, method, url, **kwargs):
            if name.endswith("-novel"):
                return {"id": "novel", "branch_id": "branch"}
            if name.endswith("-chapter"):
                return {"id": "chapter"}
            if name.endswith("-book"):
                return {"id": "book"}
            if name.endswith("-session"):
                return {
                    "id": "session",
                    "revision": 0,
                    "payload": {
                        key: ""
                        for key in (
                            "goal",
                            "cards",
                            "selected_ids",
                            "plot",
                            "fixed",
                            "application_plan",
                            "branch_id",
                            "chapter_id",
                        )
                    },
                }
            return {"revision": 1}

        def task(self, name, url, payload):
            self.order.append(name)
            return {"id": name, "payload": {"text": "独立候选", "cards": [{"id": "rule"}], "conflicts": []}}

    runner = Runner()
    runner.configure_cases(["调查"], reference_first)
    runner.run()
    assert len(runner.record["results"]) == 1
    assert runner.record["results"][0]["case"] == "调查"
    assert runner.order == (
        ["case-3-analyze", "case-3-plan", "case-3-reference", "case-3-baseline"]
        if reference_first
        else ["case-3-baseline", "case-3-analyze", "case-3-plan", "case-3-reference"]
    )
    with pytest.raises(ValueError, match="changed"):
        runner.configure_cases(["交涉"], reference_first)
    with pytest.raises(ValueError, match="changed"):
        runner.configure_cases(["调查"], not reference_first)


def test_transient_windows_journal_lock_retries_only_local_replace(tmp_path, monkeypatch):
    target = tmp_path / "run.json"
    target.write_text('{"old": true}')
    original = Path.replace
    attempts = []

    def replace(path, destination):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("reader temporarily denies delete sharing")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("ainovel.quality_reference.sleep", lambda seconds: None)
    write_json(target, {"task_id": "already-submitted"})
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert json.loads(target.read_text()) == {"task_id": "already-submitted"}


def test_persistent_journal_lock_is_bounded_and_preserves_previous_record(tmp_path, monkeypatch):
    target = tmp_path / "run.json"
    target.write_text('{"old": true}')
    attempts = []

    def replace(path, destination):
        attempts.append(path)
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("ainovel.quality_reference.sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        write_json(target, {"new": True})
    assert len(attempts) == 20
    assert json.loads(target.read_text()) == {"old": True}
    assert json.loads(attempts[-1].read_text()) == {"new": True}


def test_original_case_matrix_and_anonymous_pack_keep_decoding_key_separate(tmp_path):
    assert len(CASES) == len({row[0] for row in CASES}) == 7
    rows = []
    for kind, source, plot, fixed in CASES:
        assert source and plot and fixed
        rows.append(
            {
                "case": kind,
                "plot": plot,
                "fixed": fixed,
                "baseline": {"id": "original-" + kind, "payload": {"text": "基线正文😀\n第二行"}},
                "reference": {"id": "reference-" + kind, "payload": {"text": "参考正文𠮷"}},
            }
        )
    blind_pack(tmp_path, rows)
    key = json.loads((tmp_path / "answer-key.json").read_text("utf-8"))
    assert len(key) == 14
    for number in range(1, 8):
        text = (tmp_path / "blind-review" / f"样例{number:02}.md").read_text("utf-8")
        assert "基线正文😀\n第二行" in text and "参考正文𠮷" in text
        assert "artifact_id" not in text and "original-" not in text
        assert {row["origin"] for row in key if row["sample"] == number} == {"baseline", "reference"}
    assert not (tmp_path / "blind-review" / "answer-key.json").exists()


def test_unknown_resource_creation_is_not_automatically_repeated(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("unknown outcome")

    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
        runner = Evaluation(client, tmp_path, "deepseek", 7)
        with pytest.raises(httpx.ReadTimeout):
            runner.once("create", "POST", "/novels", json={"title": "test"})
        resumed = Evaluation(client, tmp_path, "deepseek", 7)
        with pytest.raises(RuntimeError, match="outcome unknown"):
            resumed.once("create", "POST", "/novels", json={"title": "test"})
    assert len(requests) == 1


def test_task_dispatch_resume_uses_same_key_and_never_retries_failed_model(tmp_path):
    submissions = []

    def handler(request):
        if request.method == "POST":
            submissions.append(json.loads(request.content))
            if len(submissions) == 1:
                raise httpx.ReadTimeout("dispatch response lost")
            return httpx.Response(202, json={"id": "stable-task"})
        return httpx.Response(200, json={"status": "blocked_runtime", "error": "unknown provider outcome"})

    with httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
        runner = Evaluation(client, tmp_path, "deepseek", 7)
        with pytest.raises(httpx.ReadTimeout):
            runner.task("baseline", "/modules", {"account": "deepseek"})
        resumed = Evaluation(client, tmp_path, "deepseek", 7)
        with pytest.raises(RuntimeError, match="no automatic paid retry"):
            resumed.task("baseline", "/modules", {"account": "deepseek"})
        with pytest.raises(RuntimeError, match="no automatic paid retry"):
            resumed.task("baseline", "/modules", {"account": "deepseek"})
    assert len(submissions) == 2
    assert submissions[0]["client_request_id"] == submissions[1]["client_request_id"]
