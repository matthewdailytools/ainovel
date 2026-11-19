import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from ainovel.api import create_app
from ainovel.db import DomainError
from ainovel.reading_metrics import CALCULATION_VERSION, calculate_report, parse_import


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
    basis = domain.artifact(document["id"])["basis"]
    del basis["calculation_version"]
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE artifacts SET payload=?,basis=? WHERE id=?",
            (json.dumps(document["report"]), json.dumps(basis), document["id"]),
        )
    historical = client.get(f"{base}/history/{document['id']}")
    assert historical.status_code == 200
    assert historical.json()["report"]["diagnostics"] == [{**item, "channel_breakdown": None}]
    assert "access_mode" not in historical.json()["report"]
    assert historical.json()["source_sha256"] == document["source_sha256"]
    assert "calculation_version" not in historical.json()


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
        original_file = b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode()
        response = client.post(f"{route}/import", files={"file": ("reading.json", original_file)})
        assert response.status_code == 201, response.text
        imported = response.json()
        assert imported["source_sha256"] == sha256(original_file).hexdigest()
        assert imported["calculation_version"] == CALCULATION_VERSION
        assert "pseudonym-test-only" not in response.text and "events" not in imported["report"]
        assert "eligibility-key-only" not in response.text and "reader_access" not in imported["report"]
        assert client.get(route).json() == {"latest": imported}
        assert client.get(route + "/history").json()[0]["id"] == imported["id"]
        assert client.get(route + "/history/" + imported["id"]).json() == imported
        artifact = domain.artifact(imported["id"])
        assert artifact["status"] == "accepted"
        assert "pseudonym-test-only" not in json.dumps(artifact)
        assert "eligibility-key-only" not in json.dumps(artifact)
        assert artifact["basis"]["source_sha256"] == imported["source_sha256"]
        assert artifact["basis"]["calculation_version"] == imported["calculation_version"]
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


def import_readings(client, branch, data):
    route = f"/api/branches/{branch}/reading-metrics"
    response = client.post(route + "/import", files={"file": ("reading.json", json.dumps(data))})
    assert response.status_code == 201, response.text
    return route, response.json()


def get_reading(client, route, **params):
    response = client.get(route, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def selection(content, excerpt):
    offset = content.index(excerpt)
    start = len(content[:offset].encode("utf-16-le")) // 2
    return {"start": start, "end": start + len(excerpt.encode("utf-16-le")) // 2, "quote": excerpt}


def test_published_diagnosis_freezes_both_revisions_and_release_catalog(client, domain, story):
    branch = story["branch_id"]
    first = story["chapter_id"]
    # Local work order deliberately differs from the measurement's publication order.
    third = domain.create_chapter(branch, "原发布目录里的第三章")["id"]
    second = domain.create_chapter(branch, "原发布目录里的第二章")["id"]
    ending = "门外一声😀铃。"
    opening = "😀铜铃忽然震响。"
    saved_second = domain.save(second, 0, f"他停在旧门前。\n{ending}")
    saved_third = domain.save(third, 0, f"{opening}\n他认出熟悉的节奏。")
    data = {
        **source("events", (first, second, third)),
        "window_hours": 24,
        "events": [
            event(first, "pseudonym-selection-a", 1),
            event(first, "pseudonym-selection-b", 1),
            event(second, "pseudonym-selection-a", 5),
            event(second, "pseudonym-selection-b", 5),
            event(third, "pseudonym-selection-a", 13),
        ],
    }
    route, original = import_readings(client, branch, data)
    history = route + "/history"
    original_route = f"{history}/{original['id']}"
    report_before = original["report"]
    (drop,) = report_before["diagnostics"]
    assert (drop["chapter_id"], drop["target_chapter_id"], drop["denominator"]) == (second, third, 2)
    assert [
        (chapter["chapter_id"], chapter["release_position"]) for chapter in report_before["chapters"]
    ] == [
        (first, 1),
        (second, 2),
        (third, 3),
    ]
    assert all("published_text" not in chapter for chapter in report_before["chapters"])

    assert client.patch(f"/api/chapters/{second}", json={"title": "当前更名的第二章"}).status_code == 200
    domain.save(second, saved_second["revision"], "当前工作稿的替换结尾。")
    domain.save(third, saved_third["revision"], "当前工作稿的替换开头。")

    def bind(report_url, chapter, saved, edge, text):
        revisions = report_url + f"/published-text/chapters/{chapter}/revisions"
        rows = get_reading(client, revisions)
        assert all(set(row) == {"id", "revision", "created", "cause"} for row in rows)
        assert [row["revision"] for row in rows] == [2, 1, 0]
        picked = next(row for row in rows if row["revision"] == saved["revision"])
        proof = get_reading(client, f"{revisions}/{picked['id']}")
        assert proof["content"] == saved["content"]
        assert proof["sha256"] == sha256(saved["content"].encode()).hexdigest()
        body = {
            "chapter_id": chapter,
            "revision_id": proof["id"],
            "sha256": proof["sha256"],
            edge: selection(proof["content"], text),
        }
        result = client.post(report_url + "/published-text", json=body)
        assert result.status_code == 201, result.text
        bound = result.json()
        assert client.post(report_url + "/published-text", json=body).json() == bound
        return bound, body

    predecessor, second_proof = bind(original_route, second, saved_second, "ending", ending)
    successor, third_proof = bind(f"{history}/{predecessor['id']}", third, saved_third, "opening", opening)
    assert len({original["id"], predecessor["id"], successor["id"]}) == 3
    for report in (predecessor, successor):
        assert report["source_sha256"] == original["source_sha256"]
        assert report["calculation_version"] == original["calculation_version"] == CALCULATION_VERSION
    final = successor["report"]
    assert {key: final[key] for key in final if key != "chapters"} == {
        key: report_before[key] for key in report_before if key != "chapters"
    }
    assert final["chapters"][0] == report_before["chapters"][0]
    for index, proof, edge in [(1, second_proof, "ending"), (2, third_proof, "opening")]:
        chapter = final["chapters"][index]
        assert {key: chapter[key] for key in chapter if key != "published_text"} == report_before["chapters"][
            index
        ]
        assert chapter["published_text"] == {
            "status": "verified",
            "revision_id": proof["revision_id"],
            "revision": 1,
            "sha256": proof["sha256"],
            edge: proof[edge],
        }
    assert final["chapters"][1]["title_at_import"] == "原发布目录里的第二章"
    assert get_reading(client, route)["latest"] == successor
    assert get_reading(client, original_route) == original
    predecessor_route = f"{history}/{predecessor['id']}"
    assert get_reading(client, predecessor_route) == predecessor
    rows = get_reading(client, history)
    assert [(row["id"], row.get("source_report_id")) for row in rows] == [
        (successor["id"], predecessor["id"]),
        (predecessor["id"], original["id"]),
        (original["id"], None),
    ]
    for row in rows:
        stored = domain.artifact(row["id"])
        assert "pseudonym-selection-" not in json.dumps(stored)
        assert "events" not in stored["payload"] and "reader_access" not in stored["payload"]

    assert (
        client.post(
            f"/api/chapters/{second}/archive", json={"base_revision": 2, "archived": True}
        ).status_code
        == 200
    )
    assert get_reading(client, f"{history}/{successor['id']}") == successor
    # Simulate an absent original after partial local restore. The newer draft is never substituted.
    with domain.db.connect(True) as tx:
        tx.execute("DELETE FROM revisions WHERE id=?", (second_proof["revision_id"],))
    historical = get_reading(client, f"{history}/{successor['id']}")["report"]
    assert "published_text" not in historical["chapters"][0]
    assert historical["chapters"][1]["published_text"] == {
        **final["chapters"][1]["published_text"],
        "status": "unavailable",
    }
    assert historical["chapters"][2]["published_text"] == final["chapters"][2]["published_text"]
    assert "当前工作稿的替换结尾" not in json.dumps(historical, ensure_ascii=False)
    assert historical["diagnostics"] == final["diagnostics"]


def test_scoped_published_sources_reject_foreign_revisions_and_bad_anchors(client, domain, story):
    branch = story["branch_id"]
    first = story["chapter_id"]
    in_report = domain.create_chapter(branch, "清单第二章")["id"]
    not_in_report = domain.create_chapter(branch, "清单外的章节")["id"]
    other = domain.create_novel("另一作品")
    foreign = domain.create_chapter(other["branch_id"], "外部章")["id"]
    content = "😀铜铃响了。"
    for identifier in (first, in_report, not_in_report, foreign):
        domain.save(identifier, 0, content)
    route, imported = import_readings(
        client, branch, {**source("aggregate", (first, in_report)), "counts": []}
    )
    history = route + "/history"
    bind = f"{history}/{imported['id']}/published-text"
    selected = bind + f"/chapters/{first}/revisions"
    picked = get_reading(client, selected)[0]["id"]
    local_other = get_reading(client, bind + f"/chapters/{in_report}/revisions")[0]["id"]
    ref = {
        "chapter_id": first,
        "revision_id": picked,
        "sha256": sha256(content.encode()).hexdigest(),
        "opening": selection(content, "😀铜铃"),
    }
    foreign_revision = domain.db.one(
        "SELECT id FROM revisions WHERE chapter_id=? AND revision=1", (foreign,)
    )["id"]
    assert client.get(bind + f"/chapters/{not_in_report}/revisions").status_code == 404
    assert client.get(bind + f"/chapters/{foreign}/revisions").status_code == 404
    for revision in (foreign_revision, local_other):
        assert client.get(f"{selected}/{revision}").status_code == 404
        rejected = client.post(bind, json={**ref, "revision_id": revision})
        assert (rejected.status_code, rejected.json()["code"]) == (404, "reading_publication_revision")
    foreign_bind = bind.replace(branch, other["branch_id"], 1)
    assert client.post(foreign_bind, json=ref).status_code == 404
    rejected = client.post(bind, json={**ref, "chapter_id": not_in_report})
    assert (rejected.status_code, rejected.json()["code"]) == (404, "reading_publication_scope")
    mismatch = client.post(bind, json={**ref, "sha256": "0" * 64})
    assert (mismatch.status_code, mismatch.json()["code"]) == (409, "reading_publication_digest")
    for anchor in (
        {"start": 1, "end": 2, "quote": "😀"},
        {"start": 0, "end": 100, "quote": content},
        {"start": 3, "end": 2, "quote": "铜"},
        {"start": 0, "end": 2, "quote": "🔔"},
    ):
        mismatch = client.post(bind, json={**ref, "opening": anchor})
        assert (mismatch.status_code, mismatch.json()["code"]) == (400, "reading_publication_selection")
    assert client.post(bind, json={**ref, "opening": None}).status_code == 422
    assert [row["id"] for row in get_reading(client, history)] == [imported["id"]]
    assert not imported["report"]["diagnostics"]
    assert all(row["rate"] is None for row in imported["report"]["metrics"])
    success = client.post(bind, json=ref)
    assert success.status_code == 201, success.text
    aggregate = success.json()["report"]
    assert aggregate["metrics"] == imported["report"]["metrics"] and not aggregate["diagnostics"]
    assert aggregate["chapters"][0]["published_text"]["opening"] == ref["opening"]
    assert "published_text" not in aggregate["chapters"][1]


def test_publication_revision_picker_pages_without_returning_contents(client, domain, story):
    branch = story["branch_id"]
    chapter = story["chapter_id"]
    for revision in range(52):
        domain.save(chapter, revision, f"已保存稿件 {revision + 1}")
    route, imported = import_readings(client, branch, {**source("aggregate", (chapter,)), "counts": []})
    revisions = f"{route}/history/{imported['id']}/published-text/chapters/{chapter}/revisions"
    first = get_reading(client, revisions)
    second = get_reading(client, revisions, before_revision=first[-1]["revision"])
    assert len(first) == 50 and len(second) == 3
    assert [row["revision"] for row in first + second] == list(range(52, -1, -1))
    assert all("content" not in row for row in first + second)
    chosen = second[0]
    assert get_reading(client, f"{revisions}/{chosen['id']}")["content"] == "已保存稿件 2"


def test_publication_history_reaches_earlier_reports_and_keeps_the_work_scope(client, domain, story):
    chapter, branch = story["chapter_id"], story["branch_id"]
    data = {**source("aggregate", (chapter,)), "counts": []}
    identifiers = []
    for index in range(51):
        route, imported = import_readings(client, branch, {**data, "release_version": f"series-{index}"})
        identifiers.append(imported["id"])
    history = route + "/history"
    first = get_reading(client, history)
    second = get_reading(client, history, before_id=first[-1]["id"])
    assert len(first) == 50 and len(second) == 1
    assert [row["id"] for row in first + second] == identifiers[::-1]
    other = domain.create_novel("另一作品")
    assert (
        client.get(
            history.replace(branch, other["branch_id"]), params={"before_id": identifiers[0]}
        ).status_code
        == 404
    )


def test_rebinding_only_keeps_omitted_anchors_on_the_same_revision(client, domain, story):
    branch = story["branch_id"]
    chapter = story["chapter_id"]
    first_text = "风声。铜铃响了。"
    second_text = "门开。铜铃无声。"
    domain.save(chapter, 0, first_text)
    domain.save(chapter, 1, second_text)
    route, original = import_readings(client, branch, {**source("aggregate", (chapter,)), "counts": []})

    def path(identifier):
        return f"{route}/history/{identifier}/published-text"

    recent, earlier, _ = get_reading(client, path(original["id"]) + f"/chapters/{chapter}/revisions")

    def submit(identifier, body):
        result = client.post(path(identifier), json=body)
        assert result.status_code == 201, result.text
        payload = result.json()
        return payload["id"], payload["report"]["chapters"][0]["published_text"]

    opening = selection(first_text, "风声。")
    original_ref = {
        "chapter_id": chapter,
        "revision_id": earlier["id"],
        "sha256": sha256(first_text.encode()).hexdigest(),
    }
    opening_report, _ = submit(original["id"], {**original_ref, "opening": opening})
    ending = selection(first_text, "铜铃响了。")
    both, anchors = submit(opening_report, {**original_ref, "ending": ending})
    assert anchors["opening"] == opening and anchors["ending"] == ending
    assert submit(opening_report, {**original_ref, "ending": ending}) == (both, anchors)
    assert submit(both, {**original_ref, "ending": ending}) == (both, anchors)
    only_ending, anchors = submit(opening_report, {**original_ref, "opening": None, "ending": ending})
    assert only_ending != both and "opening" not in anchors and anchors["ending"] == ending
    _, updated = submit(
        both,
        {
            **original_ref,
            "revision_id": recent["id"],
            "sha256": sha256(second_text.encode()).hexdigest(),
            "opening": selection(second_text, "门开。"),
        },
    )
    assert updated["revision"] == 2 and "ending" not in updated
    assert (
        get_reading(client, f"{route}/history/{both}")["report"]["chapters"][0]["published_text"]["ending"]
        == ending
    )
