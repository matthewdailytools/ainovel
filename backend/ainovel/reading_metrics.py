"""Calculate release-aware reading metrics from an explicitly scoped import."""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import timedelta
from hashlib import sha256
from statistics import median
from typing import Annotated, Literal

from fastapi import File, UploadFile
from pydantic import AwareDatetime, Field, StringConstraints, TypeAdapter, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from .db import DomainError
from .models import Input

FORMAT = "ainovel-reading/1"
REPORT = "reading_metrics"
MAX_BYTES = 8 * 1024 * 1024
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Definition = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000_000)]


class PublishedChapter(Input):
    chapter_id: Label
    published_at: AwareDatetime


class PublicationChange(Input):
    chapter_id: Label
    kind: Literal["access", "recommendation", "revision"]
    at: AwareDatetime
    note: Definition


class ReadingImport(Input):
    format: Literal["ainovel-reading/1"]
    source: Label
    release_version: Label
    timezone: Label
    extracted_at: AwareDatetime
    observed_from: AwareDatetime
    observed_until: AwareDatetime
    chapters: list[PublishedChapter] = Field(min_length=1, max_length=1000)
    publication_changes: list[PublicationChange] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def validate_period(self):
        if not self.observed_from < self.observed_until <= self.extracted_at:
            raise ValueError("观察起点、截止点和提取时间顺序无效。")
        if len({chapter.chapter_id for chapter in self.chapters}) != len(self.chapters):
            raise ValueError("同一发布版本的章节标识须唯一。")
        publication = {chapter.chapter_id: chapter.published_at for chapter in self.chapters}
        if any(
            change.chapter_id not in publication
            or not publication[change.chapter_id] <= change.at <= self.extracted_at
            for change in self.publication_changes
        ):
            raise ValueError("发布变化须关联本次清单，时间须在该章发布后、数据提取前。")
        return self


class ReaderEvent(Input):
    chapter_id: Label
    reader_key: Label
    channel: Label
    at: AwareDatetime
    kind: Literal["read", "start", "finish"]


class Cohort(Input):
    started_from: AwareDatetime
    started_before: AwareDatetime
    channel: Label | None = None


class EventsImport(ReadingImport):
    mode: Literal["events"]
    window_hours: int = Field(default=168, strict=True, ge=1, le=24 * 90)
    read_definition: Definition
    completion_definition: Definition | None = None
    cohort: Cohort | None = None
    events: list[ReaderEvent] = Field(max_length=50_000)

    @model_validator(mode="after")
    def validate_events(self):
        if self.cohort and not (
            self.observed_from <= self.cohort.started_from < self.cohort.started_before <= self.observed_until
        ):
            raise ValueError("首读批次必须落在导入观察范围内。")
        published = {chapter.chapter_id: chapter.published_at for chapter in self.chapters}
        for event in self.events:
            if event.chapter_id not in published:
                raise ValueError("阅读事件包含未列在发布清单的章节。")
            if (
                not self.observed_from <= event.at <= self.observed_until
                or event.at < published[event.chapter_id]
            ):
                raise ValueError("阅读事件时间超出观察范围或早于该章发布时间。")
        return self


class AggregateCount(Input):
    chapter_id: Label
    unique_readers: Count | None


class NativeMetric(Input):
    name: Label
    definition: Definition
    value: float = Field(allow_inf_nan=False, strict=True)
    unit: Label
    chapter_id: Label | None = None


class AggregateImport(ReadingImport):
    mode: Literal["aggregate"]
    read_definition: Definition
    counts: list[AggregateCount] = Field(max_length=1000)
    native_metrics: list[NativeMetric] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_counts(self):
        published = {chapter.chapter_id: chapter.published_at for chapter in self.chapters}
        count_ids = {entry.chapter_id for entry in self.counts}
        if len(count_ids) != len(self.counts) or not count_ids <= published.keys():
            raise ValueError("汇总记录须使用已列出的章节标识，每章至多一条。")
        if any(
            entry.unique_readers and published[entry.chapter_id] > self.observed_until
            for entry in self.counts
        ):
            raise ValueError("观察期内尚未发布的章节不能有正数读者。")
        if any(metric.chapter_id and metric.chapter_id not in published for metric in self.native_metrics):
            raise ValueError("平台原生指标引用了未列在发布清单的章节。")
        return self


IMPORT = TypeAdapter(Annotated[EventsImport | AggregateImport, Field(discriminator="mode")])


class Metric(Input):
    kind: Literal["adjacent_follow", "first_chapter_retention", "chapter_completion", "aggregate_ratio"]
    chapter_id: str
    target_chapter_id: str | None = None
    numerator: int | None
    denominator: int | None
    rate: float | None
    excluded_incomplete: int = 0
    excluded_prior_or_tied: int = 0


class ChannelFollow(Input):
    channel: str | None
    numerator: int
    denominator: int
    rate: float | None
    excluded_incomplete: int
    excluded_prior_or_tied: int


class ReadingDiagnosis(Input):
    chapter_id: str
    target_chapter_id: str
    rate: float
    numerator: int
    denominator: int
    baseline_rate: float
    baseline_pairs: int
    publication_gap_hours: float
    median_publication_gap_hours: float
    changes: list[PublicationChange]
    channel_breakdown: list[ChannelFollow] | None = None


class ReadingReport(Input):
    format: Literal["ainovel-reading/1"]
    mode: Literal["events", "aggregate"]
    source: str
    release_version: str
    timezone: str
    extracted_at: AwareDatetime
    observed_from: AwareDatetime
    observed_until: AwareDatetime
    chapters: list[PublishedChapter]
    publication_changes: list[PublicationChange] = Field(default_factory=list)
    read_definition: str
    completion_definition: str | None = None
    cohort: Cohort | None = None
    window_hours: int | None = None
    event_rows: int | None = None
    distinct_readers: int | None = None
    event_channels: list[str] = Field(default_factory=list)
    counts: list[AggregateCount] = Field(default_factory=list)
    native_metrics: list[NativeMetric] = Field(default_factory=list)
    metrics: list[Metric]
    diagnostics: list[ReadingDiagnosis] = Field(default_factory=list)
    notes: list[str]


class ImportedReport(Input):
    id: str
    report: ReadingReport


class ReadingMetricsState(Input):
    latest: ImportedReport | None


class ReadingHistoryEntry(Input):
    id: str
    release_version: str
    mode: Literal["events", "aggregate"]
    extracted_at: AwareDatetime


def _metric(kind, chapter_id, numerator, denominator, *, target=None, incomplete=0, prior=0):
    return Metric(
        kind=kind,
        chapter_id=chapter_id,
        target_chapter_id=target,
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator if numerator is not None and denominator else None,
        excluded_incomplete=incomplete,
        excluded_prior_or_tied=prior,
    )


def _walk(data):
    reads, starts, finishes = (defaultdict(dict) for _ in range(3))
    for event in data.events:
        key = event.reader_key
        if event.kind == "finish":
            finishes[event.chapter_id].setdefault(key, []).append(event.at)
            continue
        rows = (reads if event.kind == "read" else starts)[event.chapter_id]
        before = rows.get(key)
        if before is None or event.at < before[0]:
            rows[key] = (event.at, {event.channel})
        elif event.at == before[0]:
            before[1].add(event.channel)
    for rows in finishes.values():
        for timestamps in rows.values():
            timestamps.sort()
    return reads, starts, finishes


def _eligible(data, earliest):
    at, channels = earliest
    cohort = data.cohort
    return cohort is None or (
        cohort.started_from <= at < cohort.started_before
        and (cohort.channel is None or channels == {cohort.channel})
    )


def _transition(data, reads, source, target, kind, window, by_channel=None):
    source_id, target_id = source.chapter_id, target.chapter_id
    totals = Counter()
    for reader, first in reads[source_id].items():
        if not _eligible(data, first):
            continue
        destination = reads[target_id].get(reader)
        start = max(first[0], target.published_at)
        if destination and destination[0] <= first[0]:
            outcome = "prior"
        elif data.observed_until - start < window:
            outcome = "incomplete"
        elif destination and destination[0] - start <= window:
            outcome = "followed"
        else:
            outcome = "did_not_follow"
        totals[outcome] += 1
        if by_channel is not None:
            channels = first[1]
            channel = next(iter(channels)) if len(channels) == 1 else None
            by_channel[channel][outcome] += 1
    return _metric(
        kind,
        source_id,
        totals["followed"],
        totals["followed"] + totals["did_not_follow"],
        target=target_id,
        incomplete=totals["incomplete"],
        prior=totals["prior"],
    )


def _completion(data, starts, finishes, chapter_id, window, covered):
    numerator = denominator = incomplete = 0
    for reader, first in starts[chapter_id].items():
        if not _eligible(data, first):
            continue
        if data.observed_until - first[0] < window:
            incomplete += 1
            continue
        denominator += 1
        timestamps = finishes[chapter_id].get(reader, [])
        index = bisect_left(timestamps, first[0])
        numerator += index < len(timestamps) and timestamps[index] - first[0] <= window
    return _metric(
        "chapter_completion", chapter_id, numerator if covered else None, denominator, incomplete=incomplete
    )


def _channel_breakdown(counts):
    rows = []
    for channel in sorted(counts, key=lambda name: (name is None, name or "")):
        outcomes = counts[channel]
        numerator = outcomes["followed"]
        denominator = numerator + outcomes["did_not_follow"]
        rows.append(
            ChannelFollow(
                channel=channel,
                numerator=numerator,
                denominator=denominator,
                rate=numerator / denominator if denominator else None,
                excluded_incomplete=outcomes["incomplete"],
                excluded_prior_or_tied=outcomes["prior"],
            )
        )
    return rows


def _diagnose(data, transitions, channel_counts):
    measured = [row for row in transitions if row.rate is not None]
    if len(measured) < 2:
        return []
    times = {chapter.chapter_id: chapter.published_at for chapter in data.chapters}
    gaps = {
        (row.chapter_id, row.target_chapter_id): (
            times[row.target_chapter_id] - times[row.chapter_id]
        ).total_seconds()
        / 3600
        for row in measured
    }
    baseline = median(row.rate for row in measured)
    usual_gap = median(gaps.values())
    changes = defaultdict(list)
    for change in data.publication_changes:
        if change.at <= data.observed_until:
            changes[change.chapter_id].append(change)
    return [
        ReadingDiagnosis(
            chapter_id=row.chapter_id,
            target_chapter_id=row.target_chapter_id,
            rate=row.rate,
            numerator=row.numerator,
            denominator=row.denominator,
            baseline_rate=baseline,
            baseline_pairs=len(measured),
            publication_gap_hours=gaps[row.chapter_id, row.target_chapter_id],
            median_publication_gap_hours=usual_gap,
            changes=sorted(
                [*changes[row.chapter_id], *changes[row.target_chapter_id]], key=lambda change: change.at
            ),
            channel_breakdown=_channel_breakdown(channel_counts[row.chapter_id, row.target_chapter_id]),
        )
        for row in sorted(measured, key=lambda row: row.rate)
        if row.rate < baseline
    ]


def _event_report(data):
    reads, starts, finishes = _walk(data)
    window = timedelta(hours=data.window_hours)
    first = data.chapters[0]
    adjacent = []
    channel_counts = {}
    for left, right in zip(data.chapters, data.chapters[1:]):
        counts = defaultdict(Counter)
        channel_counts[left.chapter_id, right.chapter_id] = counts
        adjacent.append(_transition(data, reads, left, right, "adjacent_follow", window, counts))
    metrics = adjacent + [
        _transition(data, reads, first, target, "first_chapter_retention", window)
        for target in data.chapters[1:]
    ]
    metrics += [
        _completion(
            data,
            starts,
            finishes,
            chapter.chapter_id,
            window,
            data.completion_definition is not None or bool(finishes.get(chapter.chapter_id)),
        )
        for chapter in data.chapters
    ]
    notes = [
        "追读和留存以发行清单中的章节顺序计算。窗口从源章首个有效阅读与目标章发布时间较晚者起算。",
        "按匿名读者键在同一发布版本去重。来源章首读时已有目标章阅读或两者时间相同的读者单列排除。",
        "区分完整和未到期的观察窗口。发布时间代表导入数据中的共同可读时点，按读者限制访问时应先划分可访问样本。",
        "渠道按来源章首次有效阅读划分。限定单一渠道的样本排除同时来自多个渠道的首读。",
        "率为空表示无可计算数据。观察差异只能作为待验证的原因线索，须结合渠道、发布时间、收费或修订等变化判断。",
    ]
    if min(chapter.published_at for chapter in data.chapters) < data.observed_from:
        notes.append("观察期晚于部分章节发布，首读和既往阅读仅依据本次可见历史。")
    if data.completion_definition is None:
        notes.append("导入源未说明完读事件或阈值的定义。未提供完读事件的章节显示无可计算数据。")
    return {
        "event_rows": len(data.events),
        "distinct_readers": len({event.reader_key for event in data.events}),
        "event_channels": sorted({event.channel for event in data.events}),
        "metrics": metrics,
        "diagnostics": _diagnose(data, adjacent, channel_counts),
        "notes": notes,
    }


def _aggregate_report(data):
    counts = {row.chapter_id: row.unique_readers for row in data.counts}
    metrics = [
        _metric(
            "aggregate_ratio",
            left.chapter_id,
            counts.get(right.chapter_id),
            counts.get(left.chapter_id),
            target=right.chapter_id,
        )
        for left, right in zip(data.chapters, data.chapters[1:])
    ]
    return {
        "metrics": metrics,
        "notes": [
            "仅提供同期各章独立读人数及其比值。缺少可关联的读者记录，无法计算同批读者的追读、留存或完读率。",
            "章节发布时间可能影响观察机会。分母为零或任一人数缺失时，比值无可计算数据。",
            "平台原生指标按导入名称和定义展示，和本地计算指标使用各自的口径。",
        ],
    }


def calculate_report(data):
    derived = _event_report(data) if isinstance(data, EventsImport) else _aggregate_report(data)
    return ReadingReport(**data.model_dump(exclude={"events"}), **derived)


def parse_import(blob):
    try:
        return IMPORT.validate_json(blob.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise DomainError("reading_import_encoding", "请上传 UTF-8 编码的追读数据 JSON 文件。") from error
    except ValidationError as error:
        fields = error.errors(include_input=False, include_url=False, include_context=False)
        descriptions = []
        for issue in fields[:5]:
            location = ".".join(str(part) for part in issue["loc"])
            descriptions.append(f"{location or '文件'} {issue['msg']}")
        raise DomainError("reading_import_format", "追读文件格式无效。 " + " ".join(descriptions)) from error


def store_import(domain, branch_id, blob):
    branch = domain.branch(branch_id)
    data = parse_import(blob)
    chapters = {row["id"] for row in domain.db.all("SELECT id FROM chapters WHERE branch_id=?", (branch_id,))}
    if any(chapter.chapter_id not in chapters for chapter in data.chapters):
        raise DomainError("reading_import_scope", "发布清单包含不属于本工作版本的章节。", 409)
    report = calculate_report(data)
    artifact = domain.stage(
        {"novel_id": branch["novel_id"], "branch_id": branch_id},
        REPORT,
        report.model_dump(mode="json"),
        {"format": FORMAT, "source_sha256": sha256(blob).hexdigest()},
        on_stage=lambda tx, identifier: tx.execute(
            "UPDATE artifacts SET status='accepted' WHERE id=?", (identifier,)
        ),
    )
    return ImportedReport(id=artifact["id"], report=report)


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/reading-metrics")
    def latest_reading_metrics(branch_id: str) -> ReadingMetricsState:
        domain.branch(branch_id)
        rows = domain.db.all(
            "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
            "ORDER BY created DESC,id DESC LIMIT 1",
            (branch_id, REPORT),
        )
        if rows:
            return ReadingMetricsState(
                latest=ImportedReport(
                    id=rows[0]["id"],
                    report=ReadingReport.model_validate_json(rows[0]["payload"]),
                ),
            )
        return ReadingMetricsState(latest=None)

    @app.get("/api/branches/{branch_id}/reading-metrics/history")
    def reading_history(branch_id: str) -> list[ReadingHistoryEntry]:
        domain.branch(branch_id)
        return domain.db.all(
            "SELECT id,json_extract(payload,'$.release_version') AS release_version, "
            "json_extract(payload,'$.mode') AS mode,json_extract(payload,'$.extracted_at') AS extracted_at "
            "FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid DESC LIMIT 50",
            (branch_id, REPORT),
        )

    @app.get("/api/branches/{branch_id}/reading-metrics/history/{identifier}")
    def read_saved_report(branch_id: str, identifier: str) -> ImportedReport:
        row = domain.db.one(
            "SELECT id,payload FROM artifacts WHERE branch_id=? AND id=? AND kind=? AND status='accepted'",
            (branch_id, identifier, REPORT),
        )
        return ImportedReport(id=row["id"], report=ReadingReport.model_validate_json(row["payload"]))

    @app.post("/api/branches/{branch_id}/reading-metrics/import", status_code=201)
    async def import_reading_metrics(branch_id: str, file: Annotated[UploadFile, File()]) -> ImportedReport:
        blob = await file.read(MAX_BYTES + 1)
        if len(blob) > MAX_BYTES:
            raise DomainError("reading_import_size", "追读文件超过 8 MiB。", 413)
        return await run_in_threadpool(store_import, domain, branch_id, blob)
