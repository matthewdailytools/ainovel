import json

import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.db import DomainError
from ainovel.reading_metrics import calculate_report, parse_import


def source(mode, chapters=("one", "two", "three")):
    return {
        "format": "ainovel-reading/1",
        "mode": mode,
        "source": "合成发布观察",
        "release_version": "serial-1",
        "timezone": "Asia/Shanghai",
        "observed_from": "2026-11-01T00:00:00+08:00",
        "observed_until": "2026-11-14T00:00:00+08:00",
        "extracted_at": "2026-11-14T10:00:00+08:00",
        "read_definition": "完整加载本章后计入有效阅读",
        "chapters": [
            {"chapter_id": chapter, "published_at": f"2026-11-{day:02}T00:00:00+08:00"}
            for chapter, day in zip(chapters, (1, 5, 13))
        ],
    }


def event(chapter, reader, day, kind="read", channel="direct"):
    return {
        "chapter_id": chapter,
        "reader_key": reader,
        "channel": channel,
        "at": f"2026-11-{day:02}T00:00:00+08:00",
        "kind": kind,
    }


def parse(value):
    return parse_import(json.dumps(value, ensure_ascii=False).encode("utf-8-sig"))


def select(report, kind, chapter, target=None):
    return next(
        metric
        for metric in report.metrics
        if (metric.kind, metric.chapter_id, metric.target_chapter_id) == (kind, chapter, target)
    )


def test_dedup_prior_reader_late_release_and_incomplete_window():
    data = {
        **source("events"),
        "events": [
            event("one", "followed", 1),
            event("one", "followed", 1),
            event("one", "followed", 2, channel="search"),
            event("two", "followed", 11),
            event("two", "followed", 12),
            event("one", "did-not-follow", 4),
            event("two", "prior", 5),
            event("one", "prior", 6),
            event("two", "prior", 7),
            event("one", "tie", 7),
            event("two", "tie", 7),
            event("one", "incomplete", 10),
            event("two", "incomplete", 11),
            event("one", "out-of-window", 1),
            event("two", "out-of-window", 13),
            event("three", "followed", 13),
        ],
    }
    report = calculate_report(parse(data))
    assert report.window_hours == 168
    assert report.distinct_readers == 6
    assert report.event_channels == ["direct", "search"]
    pair = select(report, "adjacent_follow", "one", "two")
    assert (pair.numerator, pair.denominator, pair.rate) == (1, 3, 1 / 3)
    assert pair.excluded_incomplete == 1
    assert pair.excluded_prior_or_tied == 2
    retention = select(report, "first_chapter_retention", "one", "three")
    assert retention.denominator == 0 and retention.rate is None
    assert retention.excluded_incomplete == 6
    assert report == calculate_report(parse({**data, "events": list(reversed(data["events"]))}))


def test_completion_uses_beginning_cohort_and_never_matches_prior_finish():
    data = {
        **source("events"),
        "window_hours": 24,
        "completion_definition": "读到本章最后一屏",
        "cohort": {
            "started_from": "2026-11-02T00:00:00+08:00",
            "started_before": "2026-11-12T00:00:00+08:00",
            "channel": "direct",
        },
        "events": [
            event("one", "same", 2, "start"),
            event("one", "same", 3, "start"),
            event("one", "same", 3, "finish", "search"),
            event("one", "same", 3, "finish", "search"),
            event("one", "previous-finish", 3, "start"),
            event("one", "previous-finish", 2, "finish"),
            event("one", "mixed-channel", 2, "start"),
            event("one", "mixed-channel", 2, "start", "search"),
            event("one", "mixed-channel", 2, "finish"),
            event("one", "later-repeat", 1, "start"),
            event("one", "later-repeat", 3, "start"),
            event("one", "later-repeat", 3, "finish"),
            event("two", "only-started", 8, "start"),
        ],
    }
    report = calculate_report(parse(data))
    completed = select(report, "chapter_completion", "one")
    assert (completed.numerator, completed.denominator, completed.rate) == (1, 2, 0.5)
    assert select(report, "chapter_completion", "two").rate == 0
    undocumented = calculate_report(parse({**data, "completion_definition": None}))
    assert select(undocumented, "chapter_completion", "two").rate is None


def test_aggregate_downgrades_to_independent_readers_and_preserves_native_definition():
    data = {
        **source("aggregate"),
        "counts": [
            {"chapter_id": "one", "unique_readers": 0},
            {"chapter_id": "two", "unique_readers": 12},
            {"chapter_id": "three", "unique_readers": None},
        ],
        "native_metrics": [
            {"name": "平台追读", "definition": "平台的独立原生口径", "value": 25, "unit": "%"}
        ],
    }
    report = calculate_report(parse(data))
    assert {metric.kind for metric in report.metrics} == {"aggregate_ratio"}
    assert [(metric.numerator, metric.denominator, metric.rate) for metric in report.metrics] == [
        (12, 0, None),
        (None, 12, None),
    ]
    assert report.native_metrics[0].definition == "平台的独立原生口径"
    data["counts"][0]["unique_readers"] = 10
    ratio = calculate_report(parse(data)).metrics[0]
    assert ratio.rate == 1.2


def test_aggregate_empty_counts_yield_no_data_and_retain_version():
    data = {**source("aggregate"), "counts": [], "release_version": "  发布版本 A  "}
    report = calculate_report(parse(data))
    assert report.release_version == "发布版本 A"
    assert len(report.metrics) == 2
    assert all(
        metric.numerator is None and metric.denominator is None and metric.rate is None
        for metric in report.metrics
    )
    with pytest.raises(DomainError, match="release_version"):
        parse({**data, "release_version": "  "})


@pytest.mark.parametrize("chapter, day", [("unknown", 10), ("two", 4), ("one", 15)])
def test_invalid_scope_or_event_time_is_rejected(chapter, day):
    with pytest.raises(DomainError, match="追读文件格式无效"):
        parse({**source("events"), "events": [event(chapter, "anonymous", day)]})


def test_file_route_saves_only_summary_and_checks_publication_scope(domain, settings, story, monkeypatch):
    branch_id = story["branch_id"]
    route = f"/api/branches/{branch_id}/reading-metrics"
    data = {
        **source("events", [story["chapter_id"]]),
        "events": [event(story["chapter_id"], "pseudonym-test-only", 1)],
    }
    with TestClient(create_app(settings)) as client:
        session = client.get("/api/session").json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        assert client.get(route).json() == {"latest": None}
        response = client.post(f"{route}/import", files={"file": ("reading.json", json.dumps(data))})
        assert response.status_code == 201, response.text
        imported = response.json()
        assert "pseudonym-test-only" not in response.text and "events" not in imported["report"]
        assert client.get(route).json() == {"latest": imported}
        artifact = domain.artifact(imported["id"])
        assert artifact["status"] == "accepted"
        assert "pseudonym-test-only" not in json.dumps(artifact)
        assert len(artifact["basis"]["source_sha256"]) == 64
        other = domain.create_novel("另一部测试作品")
        foreign_route = f"/api/branches/{other['branch_id']}/reading-metrics"
        response = client.post(f"{foreign_route}/import", files={"file": ("reading.json", json.dumps(data))})
        assert response.status_code == 409
        response = client.post(f"{route}/import", files={"file": ("reading.json", "{invalid")})
        assert response.status_code == 400
        monkeypatch.setattr("ainovel.reading_metrics.MAX_BYTES", 5)
        response = client.post(f"{route}/import", files={"file": ("reading.json", json.dumps(data))})
        assert response.status_code == 413
        assert client.get(route).json() == {"latest": imported}
