import json

import httpx
import pytest

from ainovel.quality_reference import CASES, Evaluation, blind_pack


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
