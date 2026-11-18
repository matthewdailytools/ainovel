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


def access(chapter, reader, available_from):
    return {"chapter_id": chapter, "reader_key": reader, "available_from": available_from}


def test_diagnosis_uses_comparable_transitions_and_explicit_release_changes():
    data = {
        **source("events"),
        "window_hours": 24,
        "events": [
            *(event("one", reader, 1) for reader in ("a", "b", "c")),
            *(event("two", reader, 5) for reader in ("a", "b", "c")),
            event("three", "a", 13),
        ],
        "reader_access": [
            access(chapter, reader, f"2026-11-{day}T00:00:00+08:00")
            for chapter, day in (("two", "05"), ("three", "13"))
            for reader in ("a", "b", "c")
        ],
        "publication_changes": [
            {
                "chapter_id": "three",
                "kind": "access",
                "at": "2026-11-13T00:00:00+08:00",
                "note": "第三章改为订阅读者可读。",
            },
            {
                "chapter_id": "three",
                "kind": "revision",
                "at": "2026-11-14T01:00:00+08:00",
                "note": "观察结束后才更新。",
            },
        ],
    }
    report = calculate_report(parse(data))
    [diagnosis] = report.diagnostics
    assert (diagnosis.chapter_id, diagnosis.target_chapter_id) == ("two", "three")
    assert (diagnosis.numerator, diagnosis.denominator, diagnosis.baseline_pairs) == (1, 3, 2)
    assert diagnosis.rate == pytest.approx(1 / 3)
    assert diagnosis.baseline_rate == pytest.approx(2 / 3)
    assert (diagnosis.publication_gap_hours, diagnosis.median_publication_gap_hours) == (192, 144)
    assert [change.kind for change in diagnosis.changes] == ["access"]
    assert not calculate_report(parse({**data, "window_hours": 48})).diagnostics
    partial = calculate_report(parse({**data, "reader_access": data["reader_access"][:-1]}))
    uncertain = select(partial, "adjacent_follow", "two", "three")
    assert (uncertain.numerator, uncertain.denominator, uncertain.excluded_access_unknown) == (1, 2, 1)
    assert not partial.diagnostics


def test_entitlements_exclude_denied_readers_and_use_last_of_read_open_and_grant():
    data = {
        **source("events", ("one", "two")),
        "window_hours": 24,
        "events": [
            event("one", "allowed", 1, channel="subscriber"),
            event("one", "denied-a", 1, channel="discovery"),
            event("one", "denied-b", 1, channel="discovery"),
            event("two", "allowed", 9),
        ],
        "reader_access": [
            access("two", "allowed", "2026-11-09T00:00:00+08:00"),
            access("two", "denied-a", None),
            access("two", "denied-b", None),
        ],
    }
    data["chapters"][1]["opens_at"] = "2026-11-06T00:00:00+08:00"
    report = calculate_report(parse(data))
    assert report.access_mode == "reader_eligibility"
    pair = select(report, "adjacent_follow", "one", "two")
    assert (pair.numerator, pair.denominator, pair.rate) == (1, 1, 1.0)
    assert (pair.excluded_ineligible, pair.excluded_access_unknown, pair.excluded_incomplete) == (2, 0, 0)
    assert [
        (row.channel, row.numerator, row.denominator, row.excluded_ineligible)
        for row in pair.channel_breakdown
    ] == [("discovery", 0, 0, 2), ("subscriber", 1, 1, 0)]
    retention = select(report, "first_chapter_retention", "one", "two")
    assert (retention.numerator, retention.denominator) == (1, 1)
    assert "allowed" not in report.model_dump_json() and "denied-a" not in report.model_dump_json()

    data["reader_access"][0]["available_from"] = "2026-11-02T00:00:00+08:00"
    data["events"][-1] = event("two", "allowed", 7)
    pair = select(calculate_report(parse(data)), "adjacent_follow", "one", "two")
    assert (pair.numerator, pair.denominator) == (1, 1)
    data["events"][0] = event("one", "allowed", 10, channel="subscriber")
    data["events"][-1] = event("two", "allowed", 11)
    pair = select(calculate_report(parse(data)), "adjacent_follow", "one", "two")
    assert (pair.numerator, pair.denominator) == (1, 1)


def test_qualification_coverage_is_separate_from_late_window_and_already_read():
    data = {
        **source("events", ("one", "two")),
        "access_mode": "reader_eligibility",
        "window_hours": 48,
        "events": [
            event("one", "known", 1, channel="direct"),
            event("one", "late", 1, channel="direct"),
            event("one", "unknown", 1, channel="newsletter"),
            event("one", "denied", 1, channel="newsletter"),
            event("one", "prior", 10, channel="archive"),
            event("two", "known", 6),
            event("two", "unknown", 6),
            event("two", "prior", 7),
        ],
        "reader_access": [
            access("two", "known", "2026-11-05T00:00:00+08:00"),
            access("two", "late", "2026-11-13T02:00:00+08:00"),
            access("two", "denied", None),
        ],
    }
    report = calculate_report(parse(data))
    pair = select(report, "adjacent_follow", "one", "two")
    assert (pair.numerator, pair.denominator) == (1, 1)
    assert (
        pair.excluded_ineligible,
        pair.excluded_access_unknown,
        pair.excluded_incomplete,
        pair.excluded_prior_or_tied,
    ) == (1, 1, 1, 1)
    for field in (
        "numerator",
        "denominator",
        "excluded_ineligible",
        "excluded_access_unknown",
        "excluded_incomplete",
        "excluded_prior_or_tied",
    ):
        assert sum(getattr(row, field) for row in pair.channel_breakdown) == getattr(pair, field)
    assert (
        calculate_report(
            parse(
                {
                    **data,
                    "events": list(reversed(data["events"])),
                    "reader_access": list(reversed(data["reader_access"])),
                }
            )
        )
        == report
    )

    for invalid in [
        [*data["reader_access"], access("two", "known", None)],
        [access("two", "known", None)],
        [access("two", "known", "2026-11-07T00:00:00+08:00")],
        [access("two", "late", "2026-11-15T00:00:00+08:00")],
        [access("wrong-chapter", "unknown", None)],
    ]:
        with pytest.raises(DomainError, match="追读文件格式无效"):
            parse({**data, "reader_access": invalid})


def test_unobserved_qualification_reports_assumption_or_explicit_unknown():
    data = {
        **source("events", ("one", "two")),
        "window_hours": 24,
        "events": [event("one", "a", 1), event("one", "b", 1), event("two", "a", 6)],
    }
    assumed = calculate_report(parse(data))
    assert assumed.access_mode == "shared_opening_assumed"
    assert select(assumed, "adjacent_follow", "one", "two").rate == 0.5
    restricted = {
        **data,
        "publication_changes": [
            {"chapter_id": "two", "kind": "access", "at": "2026-11-05T00:00:00+08:00", "note": "权限有变化。"}
        ],
    }
    for value in [restricted, {**data, "access_mode": "unknown"}]:
        report = calculate_report(parse(value))
        pair = select(report, "adjacent_follow", "one", "two")
        assert report.access_mode == "unknown"
        assert (pair.rate, pair.denominator, pair.excluded_access_unknown) == (None, 0, 2)
    declared = calculate_report(parse({**restricted, "access_mode": "shared_opening"}))
    assert declared.access_mode == "shared_opening"
    assert select(declared, "adjacent_follow", "one", "two").rate == 0.5


def test_diagnosis_attributes_each_reader_to_one_source_channel_and_keeps_exclusions():
    data = {
        **source("events"),
        "window_hours": 24,
        "events": [
            *(event("one", reader, 1) for reader in ("followed", "opted-out", "ambiguous")),
            event("two", "followed", 5, channel="first"),
            event("two", "followed", 6, channel="later"),
            event("three", "followed", 13, channel="destination"),
            event("two", "opted-out", 5, channel="newsletter"),
            event("two", "ambiguous", 5, channel="first"),
            event("two", "ambiguous", 5, channel="newsletter"),
            event("two", "ambiguous", 5, channel="newsletter"),
            event("three", "ambiguous", 13),
            {**event("two", "window-unfinished", 13, channel="first"), "at": "2026-11-13T02:00:00+08:00"},
            event("three", "already-read", 13),
            {**event("two", "already-read", 13, channel="archive"), "at": "2026-11-13T03:00:00+08:00"},
        ],
    }
    report = calculate_report(parse(data))
    [diagnosis] = report.diagnostics
    assert (diagnosis.chapter_id, diagnosis.target_chapter_id) == ("two", "three")
    rows = diagnosis.channel_breakdown
    assert [row.channel for row in rows] == ["archive", "first", "newsletter", None]
    assert [
        (row.numerator, row.denominator, row.rate, row.excluded_incomplete, row.excluded_prior_or_tied)
        for row in rows
    ] == [(0, 0, None, 0, 1), (1, 1, 1.0, 1, 0), (0, 1, 0.0, 0, 0), (1, 1, 1.0, 0, 0)]
    overall = select(report, "adjacent_follow", "two", "three")
    for key in ("numerator", "denominator", "excluded_incomplete", "excluded_prior_or_tied"):
        assert sum(getattr(row, key) for row in rows) == getattr(overall, key)
    assert report == calculate_report(parse({**data, "events": list(reversed(data["events"]))}))


@pytest.mark.parametrize("access_mode", [None, "shared_opening"])
def test_single_shared_opening_pair_shows_channels_without_a_drop_off_baseline(access_mode):
    data = {
        **source("events", ("one", "two")),
        "window_hours": 24,
        "access_mode": access_mode,
        "events": [
            event("one", "a", 1, channel="direct"),
            event("one", "b", 1, channel="search"),
            event("two", "a", 5, channel="search"),
        ],
    }
    report = calculate_report(parse(data))
    assert report.access_mode == (access_mode or "shared_opening_assumed")
    assert report.diagnostics == []
    pair = select(report, "adjacent_follow", "one", "two")
    assert (pair.numerator, pair.denominator) == (1, 2)
    assert [
        (row.channel, row.numerator, row.denominator, row.excluded_access_unknown)
        for row in pair.channel_breakdown
    ] == [("direct", 1, 1, None), ("search", 0, 1, None)]


def test_saved_diagnosis_can_distinguish_preexisting_reports_without_channel_breakdown(client, domain, story):
    branch = story["branch_id"]
    first = story["chapter_id"]
    second = domain.create_chapter(branch, "二")["id"]
    third = domain.create_chapter(branch, "三")["id"]
    data = {
        **source("events", (first, second, third)),
        "window_hours": 24,
        "events": [
            *(event(first, reader, 1) for reader in ("anonymous-1", "anonymous-2")),
            *(event(second, reader, 5) for reader in ("anonymous-1", "anonymous-2")),
            event(third, "anonymous-1", 13),
        ],
    }
    base = f"/api/branches/{branch}/reading-metrics"
    response = client.post(base + "/import", files={"file": ("reading.json", json.dumps(data))})
    assert response.status_code == 201, response.text
    document = response.json()
    item = document["report"]["diagnostics"][0]
    assert item["channel_breakdown"][0]["denominator"] == 2 and "anonymous-" not in response.text
    del item["channel_breakdown"]
    del document["report"]["access_mode"]
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE artifacts SET payload=? WHERE id=?", (json.dumps(document["report"]), document["id"])
        )
    historical = client.get(f"{base}/history/{document['id']}")
    assert historical.status_code == 200
    assert historical.json()["report"]["diagnostics"] == [{**item, "channel_breakdown": None}]
    assert "access_mode" not in historical.json()["report"]


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
    second = select(report, "chapter_completion", "two")
    assert (second.numerator, second.denominator, second.rate) == (None, 1, None)
    declared = calculate_report(
        parse({**data, "completion_coverage": [{"chapter_id": "two", "coverage": "observed"}]})
    )
    assert select(declared, "chapter_completion", "two").rate == 0
    undocumented = calculate_report(parse({**data, "completion_definition": None}))
    assert select(undocumented, "chapter_completion", "two").rate is None


def test_confirmed_completion_coverage_distinguishes_zero_from_missing_and_has_per_chapter_definition():
    data = {
        **source("events"),
        "window_hours": 24,
        "completion_definition": "默认读至最后一屏",
        "completion_coverage": [
            {"chapter_id": "one", "coverage": "observed", "definition": "第一章读满九成"},
            {"chapter_id": "three", "coverage": "not_observed"},
        ],
        "events": [
            event("one", "reader", 1, "start"),
            event("two", "reader", 5, "start"),
            event("two", "out-of-cohort", 5, "finish"),
            event("three", "reader", 13, "start"),
        ],
    }
    report = calculate_report(parse(data))
    assert [(row.chapter_id, row.coverage, row.definition) for row in report.completion_coverage] == [
        ("one", "observed", "第一章读满九成"),
        ("two", "event_evidence", "默认读至最后一屏"),
        ("three", "not_observed", "默认读至最后一屏"),
    ]
    assert [
        (row.numerator, row.denominator, row.rate)
        for row in report.metrics
        if row.kind == "chapter_completion"
    ] == [(0, 1, 0), (None, 1, None), (None, 1, None)]

    bad_inputs = [
        {**data, "events": [*data["events"], event("three", "reader", 13, "finish")]},
        {
            **data,
            "completion_coverage": [
                *data["completion_coverage"],
                {"chapter_id": "one", "coverage": "not_observed"},
            ],
        },
        {**data, "completion_coverage": [{"chapter_id": "missing", "coverage": "not_observed"}]},
        {
            **data,
            "completion_definition": None,
            "completion_coverage": [{"chapter_id": "one", "coverage": "observed"}],
        },
    ]
    for invalid in bad_inputs:
        with pytest.raises(DomainError, match="完读"):
            parse(invalid)


def test_historical_completion_zero_is_read_without_recalculating(client, domain, story):
    chapter_id = story["chapter_id"]
    base = f"/api/branches/{story['branch_id']}/reading-metrics"
    data = {
        **source("events", (chapter_id,)),
        "window_hours": 24,
        "completion_definition": "读至最后一屏",
        "completion_coverage": [{"chapter_id": chapter_id, "coverage": "observed"}],
        "events": [event(chapter_id, "reader", 1, "start")],
    }
    response = client.post(base + "/import", files={"file": ("reading.json", json.dumps(data))})
    assert response.status_code == 201, response.text
    archived = response.json()
    del archived["report"]["completion_coverage"]
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE artifacts SET payload=? WHERE id=?", (json.dumps(archived["report"]), archived["id"])
        )
    historical = client.get(f"{base}/history/{archived['id']}")
    assert historical.status_code == 200
    assert historical.json() == archived
    assert historical.json()["report"]["metrics"][0]["rate"] == 0


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
        "reader_access": [access(story["chapter_id"], "eligibility-key-only", None)],
    }
    with TestClient(create_app(settings)) as client:
        session = client.get("/api/session").json()
        client.headers["X-Ainovel-CSRF"] = session["csrf"]
        assert client.get(route).json() == {"latest": None}
        response = client.post(f"{route}/import", files={"file": ("reading.json", json.dumps(data))})
        assert response.status_code == 201, response.text
        imported = response.json()
        assert "pseudonym-test-only" not in response.text and "events" not in imported["report"]
        assert "eligibility-key-only" not in response.text and "reader_access" not in imported["report"]
        assert client.get(route).json() == {"latest": imported}
        assert client.get(route + "/history").json()[0]["id"] == imported["id"]
        assert client.get(route + "/history/" + imported["id"]).json() == imported
        artifact = domain.artifact(imported["id"])
        assert artifact["status"] == "accepted"
        assert "pseudonym-test-only" not in json.dumps(artifact)
        assert "eligibility-key-only" not in json.dumps(artifact)
        assert len(artifact["basis"]["source_sha256"]) == 64
        other = domain.create_novel("另一部测试作品")
        foreign_route = f"/api/branches/{other['branch_id']}/reading-metrics"
        assert client.get(foreign_route + "/history/" + imported["id"]).status_code == 404
        response = client.post(f"{foreign_route}/import", files={"file": ("reading.json", json.dumps(data))})
        assert response.status_code == 409
        response = client.post(f"{route}/import", files={"file": ("reading.json", "{invalid")})
        assert response.status_code == 400
        monkeypatch.setattr("ainovel.reading_metrics.MAX_BYTES", 5)
        response = client.post(f"{route}/import", files={"file": ("reading.json", json.dumps(data))})
        assert response.status_code == 413
        assert client.get(route).json() == {"latest": imported}
