"""Calculate release-aware reading metrics from an explicitly scoped import."""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import timedelta
from hashlib import sha256
import json
from statistics import median
from time import time
from typing import Annotated, Literal

from fastapi import File, Query, UploadFile
from pydantic import AwareDatetime, Field, StringConstraints, TypeAdapter, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from .db import DomainError, dumps, uid
from .domain import utf16_slice
from .models import Input

FORMAT = "ainovel-reading/1"
REPORT = "reading_metrics"
MAX_BYTES = 8 * 1024 * 1024
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Definition = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000_000)]
AccessMode = Literal["shared_opening", "reader_eligibility", "unknown"]
ReportedAccessMode = Literal["shared_opening", "shared_opening_assumed", "reader_eligibility", "unknown"]


class PublishedChapter(Input):
    chapter_id: Label
    published_at: AwareDatetime
    opens_at: AwareDatetime | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_opening(self):
        if self.opens_at is not None and self.opens_at < self.published_at:
            raise ValueError("章节可读时间不能早于发布时间。")
        return self

    @property
    def opening(self):
        return self.opens_at if self.opens_at is not None else self.published_at


class PublishedTextSelection(Input):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)
    quote: str = Field(min_length=1, max_length=4000)


class _PublishedTextSelections(Input):
    opening: PublishedTextSelection | None = Field(default=None, exclude_if=lambda value: value is None)
    ending: PublishedTextSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class BindPublishedText(_PublishedTextSelections):
    chapter_id: Label
    revision_id: Label
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_anchor(self):
        if self.opening is None and self.ending is None:
            raise ValueError("请至少选择发布稿的章首或章末原文。")
        return self


class PublishedText(_PublishedTextSelections):
    status: Literal["verified", "unavailable"]
    revision_id: str
    revision: int
    sha256: str


class ReportPublishedChapter(PublishedChapter):
    title_at_import: str | None = Field(default=None, exclude_if=lambda value: value is None)
    release_position: int | None = Field(default=None, exclude_if=lambda value: value is None)
    published_text: PublishedText | None = Field(default=None, exclude_if=lambda value: value is None)


class PublicationRevision(Input):
    id: str
    revision: int
    created: float
    cause: str


class PublicationRevisionText(Input):
    id: str
    revision: int
    content: str
    sha256: str


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


class ReaderAccess(Input):
    chapter_id: Label
    reader_key: Label
    available_from: AwareDatetime | None


class CompletionCoverage(Input):
    chapter_id: Label
    coverage: Literal["observed", "not_observed"]
    definition: Definition | None = None


class Cohort(Input):
    started_from: AwareDatetime
    started_before: AwareDatetime
    channel: Label | None = None


class EventsImport(ReadingImport):
    mode: Literal["events"]
    window_hours: int = Field(default=168, strict=True, ge=1, le=24 * 90)
    read_definition: Definition
    completion_definition: Definition | None = None
    completion_coverage: list[CompletionCoverage] = Field(default_factory=list, max_length=1000)
    cohort: Cohort | None = None
    access_mode: AccessMode | None = None
    reader_access: list[ReaderAccess] | None = Field(default=None, max_length=50_000)
    events: list[ReaderEvent] = Field(max_length=50_000)

    @model_validator(mode="after")
    def validate_events(self):
        if self.cohort and not (
            self.observed_from <= self.cohort.started_from < self.cohort.started_before <= self.observed_until
        ):
            raise ValueError("首读批次必须落在导入观察范围内。")
        if self.reader_access is not None and self.access_mode not in {None, "reader_eligibility"}:
            raise ValueError("提供读者资格时须使用 reader_eligibility 口径。")
        opened = {chapter.chapter_id: chapter.opening for chapter in self.chapters}
        completion = {row.chapter_id: row for row in self.completion_coverage}
        if len(completion) != len(self.completion_coverage) or not completion.keys() <= opened.keys():
            raise ValueError("同一章节只能声明一次完读采集覆盖，章节须在发布清单中。")
        if any(
            row.coverage == "observed" and row.definition is None and self.completion_definition is None
            for row in completion.values()
        ):
            raise ValueError("确认完读采集覆盖时，须提供该章或通用的完读事件定义。")
        access = {}
        for row in self.reader_access or []:
            key = (row.chapter_id, row.reader_key)
            if row.chapter_id not in opened or key in access:
                raise ValueError("同一章节与读者只能记录一条资格，章节须在发布清单中。")
            if row.available_from is not None and row.available_from > self.extracted_at:
                raise ValueError("读者有权时间不能晚于数据提取时间。")
            access[key] = row.available_from
        for event in self.events:
            if event.chapter_id not in opened:
                raise ValueError("阅读事件包含未列在发布清单的章节。")
            if (
                not self.observed_from <= event.at <= self.observed_until
                or event.at < opened[event.chapter_id]
            ):
                raise ValueError("阅读事件时间超出观察范围或早于该章可读时间。")
            key = (event.chapter_id, event.reader_key)
            if key in access and (access[key] is None or event.at < access[key]):
                raise ValueError("阅读事件与对应读者的目标章资格记录矛盾。")
            coverage = completion.get(event.chapter_id)
            if event.kind == "finish" and coverage and coverage.coverage == "not_observed":
                raise ValueError("声明未采集完读的章节不能同时提供完读事件。")
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
        opened = {chapter.chapter_id: chapter.opening for chapter in self.chapters}
        count_ids = {entry.chapter_id for entry in self.counts}
        if len(count_ids) != len(self.counts) or not count_ids <= opened.keys():
            raise ValueError("汇总记录须使用已列出的章节标识，每章至多一条。")
        if any(
            entry.unique_readers and opened[entry.chapter_id] > self.observed_until for entry in self.counts
        ):
            raise ValueError("观察期内尚未开放的章节不能有正数读者。")
        if any(metric.chapter_id and metric.chapter_id not in opened for metric in self.native_metrics):
            raise ValueError("平台原生指标引用了未列在发布清单的章节。")
        return self


IMPORT = TypeAdapter(Annotated[EventsImport | AggregateImport, Field(discriminator="mode")])


class AccessExclusions(Input):
    excluded_ineligible: int | None = Field(default=None, exclude_if=lambda value: value is None)
    excluded_access_unknown: int | None = Field(default=None, exclude_if=lambda value: value is None)


class ChannelFollow(AccessExclusions):
    channel: str | None
    numerator: int
    denominator: int
    rate: float | None
    excluded_incomplete: int
    excluded_prior_or_tied: int


class Metric(AccessExclusions):
    kind: Literal["adjacent_follow", "first_chapter_retention", "chapter_completion", "aggregate_ratio"]
    chapter_id: str
    target_chapter_id: str | None = None
    numerator: int | None
    denominator: int | None
    rate: float | None
    excluded_incomplete: int = 0
    excluded_prior_or_tied: int = 0
    channel_breakdown: list[ChannelFollow] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


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


class ReportedCompletionCoverage(Input):
    chapter_id: str
    coverage: Literal["observed", "not_observed", "event_evidence", "unknown"]
    definition: str | None


class ReadingReport(Input):
    format: Literal["ainovel-reading/1"]
    mode: Literal["events", "aggregate"]
    source: str
    release_version: str
    timezone: str
    extracted_at: AwareDatetime
    observed_from: AwareDatetime
    observed_until: AwareDatetime
    chapters: list[ReportPublishedChapter]
    publication_changes: list[PublicationChange] = Field(default_factory=list)
    read_definition: str
    completion_definition: str | None = None
    completion_coverage: list[ReportedCompletionCoverage] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    cohort: Cohort | None = None
    access_mode: ReportedAccessMode | None = Field(default=None, exclude_if=lambda value: value is None)
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
    source_report_id: str | None = Field(default=None, exclude_if=lambda value: value is None)


def _metric(kind, chapter_id, numerator, denominator, *, target=None, incomplete=0, prior=0, **coverage):
    return Metric(
        kind=kind,
        chapter_id=chapter_id,
        target_chapter_id=target,
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator if numerator is not None and denominator else None,
        excluded_incomplete=incomplete,
        excluded_prior_or_tied=prior,
        **coverage,
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


def _access_counts(outcomes, has_coverage):
    return (
        {
            "excluded_ineligible": outcomes["ineligible"],
            "excluded_access_unknown": outcomes["unknown"],
        }
        if has_coverage
        else {}
    )


def _transition(data, reads, source, target, kind, window, access, by_channel=None):
    source_id, target_id = source.chapter_id, target.chapter_id
    totals = Counter()
    for reader, first in reads[source_id].items():
        if not _eligible(data, first):
            continue
        destination = reads[target_id].get(reader)
        start = max(first[0], target.opening)
        key = (target_id, reader)
        if destination and destination[0] <= first[0]:
            outcome = "prior"
        elif access is not None and key not in access:
            outcome = "unknown"
        elif access is not None and access[key] is None:
            outcome = "ineligible"
        else:
            if access is not None:
                start = max(start, access[key])
            if data.observed_until - start < window:
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
        channel_breakdown=(
            _channel_breakdown(by_channel, has_coverage=access is not None)
            if by_channel is not None
            else None
        ),
        **_access_counts(totals, access is not None),
    )


def _completion(data, starts, finishes, chapter_id, window, observed):
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
        "chapter_completion",
        chapter_id,
        numerator if observed or numerator else None,
        denominator,
        incomplete=incomplete,
    )


def _channel_breakdown(counts, *, has_coverage=False):
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
                **_access_counts(outcomes, has_coverage),
            )
        )
    return rows


def _diagnose(data, transitions):
    measured = [row for row in transitions if row.rate is not None and not row.excluded_access_unknown]
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
            channel_breakdown=row.channel_breakdown,
        )
        for row in sorted(measured, key=lambda row: row.rate)
        if row.rate < baseline
    ]


def _event_report(data):
    reads, starts, finishes = _walk(data)
    window = timedelta(hours=data.window_hours)
    mode = data.access_mode
    if mode is None:
        if data.reader_access is not None:
            mode = "reader_eligibility"
        elif any(
            change.kind == "access" and change.at <= data.observed_until
            for change in data.publication_changes
        ):
            mode = "unknown"
        else:
            mode = "shared_opening_assumed"
    access = (
        {(row.chapter_id, row.reader_key): row.available_from for row in data.reader_access or []}
        if mode in {"reader_eligibility", "unknown"}
        else None
    )
    first = data.chapters[0]
    adjacent = [
        _transition(data, reads, left, right, "adjacent_follow", window, access, defaultdict(Counter))
        for left, right in zip(data.chapters, data.chapters[1:])
    ]
    metrics = adjacent + [
        _transition(data, reads, first, target, "first_chapter_retention", window, access)
        for target in data.chapters[1:]
    ]
    declared = {row.chapter_id: row for row in data.completion_coverage}
    completion_coverage = []
    for chapter in data.chapters:
        identifier = chapter.chapter_id
        row = declared.get(identifier)
        completion_coverage.append(
            ReportedCompletionCoverage(
                chapter_id=identifier,
                coverage=row.coverage
                if row
                else ("event_evidence" if finishes.get(identifier) else "unknown"),
                definition=(row.definition if row else None) or data.completion_definition,
            )
        )
    metrics += [
        _completion(data, starts, finishes, row.chapter_id, window, row.coverage == "observed")
        for row in completion_coverage
    ]
    notes = [
        "追读和留存按发布清单的章节顺序计算。窗口从源章首读、目标章开放与读者获得资格三者较晚时点起算。",
        "按匿名读者键在同一发布版本去重。来源章首读时已有目标章阅读或两者时间相同的读者单列排除。",
        "渠道按来源章首次有效阅读划分。限定单一渠道的样本排除同时来自多个渠道的首读。",
        "率为空表示无可计算数据。观察差异只能作为待验证的原因线索，须结合渠道、发布时间、收费或修订等变化判断。",
    ]
    access_notes = {
        "reader_eligibility": "由导入的读者资格确定观察机会。明确无权、资格未知与窗口未满分别计数，均不进入分母。",
        "shared_opening": "数据源声明发布批次共同开放。所有读者的可读时间使用章节开放时间。",
        "shared_opening_assumed": "源文件未提供读者资格且未记录可读范围变化，暂按章节共同开放假设统计。数据无法验证个别读者可读性。",
        "unknown": "读者资格覆盖未知，源文件不足以建立完整观察机会。章间追读和留存的分母暂不可用。",
    }
    notes.insert(2, access_notes[mode])
    if mode == "reader_eligibility" and any(row.excluded_access_unknown for row in adjacent):
        notes.append("存在资格未知读者的章对保留可核对样本及渠道分解，暂不用于掉点基线。")
    if min(chapter.published_at for chapter in data.chapters) < data.observed_from:
        notes.append("观察期晚于部分章节发布，首读和既往阅读仅依据本次可见历史。")
    notes.append(
        "只有来源明确确认采集覆盖的章节，才将观察窗内无匹配完读计为零。采集覆盖未声明时，已有完读仅代表可见样本。"
    )
    if any(row.definition is None for row in completion_coverage):
        notes.append("部分章节没有可核对的完读事件或阈值定义。")
    return {
        "access_mode": mode,
        "event_rows": len(data.events),
        "distinct_readers": len({event.reader_key for event in data.events}),
        "event_channels": sorted({event.channel for event in data.events}),
        "completion_coverage": completion_coverage,
        "metrics": metrics,
        "diagnostics": _diagnose(data, adjacent),
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
    return ReadingReport(
        **data.model_dump(exclude={"events", "reader_access", "access_mode", "completion_coverage"}),
        **derived,
    )


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


def _insert_report(tx, branch_id, report, basis):
    identifier = uid()
    inserted = tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "SELECT ?,novel_id,id,?,?,?,'accepted',? FROM branches WHERE id=?",
        (identifier, REPORT, report.model_dump_json(), dumps(basis), time(), branch_id),
    )
    if not inserted.rowcount:
        raise DomainError("not_found", "工作版本不存在。", 404)
    return identifier


def store_import(domain, branch_id, blob):
    domain.branch(branch_id)
    data = parse_import(blob)
    report = calculate_report(data)
    with domain.db.connect(True) as tx:
        titles = {
            row["id"]: row["title"]
            for row in tx.execute("SELECT id,title FROM chapters WHERE branch_id=?", (branch_id,))
        }
        if any(chapter.chapter_id not in titles for chapter in report.chapters):
            raise DomainError("reading_import_scope", "发布清单包含不属于本工作版本的章节。", 409)
        for position, chapter in enumerate(report.chapters, 1):
            chapter.title_at_import = titles[chapter.chapter_id]
            chapter.release_position = position
        identifier = _insert_report(
            tx, branch_id, report, {"format": FORMAT, "source_sha256": sha256(blob).hexdigest()}
        )
    return ImportedReport(id=identifier, report=report)


def _stored_report(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT id,payload,basis FROM artifacts WHERE branch_id=? AND id=? AND kind=? AND status='accepted'",
        (branch_id, identifier, REPORT),
    ).fetchone()
    if row is None:
        raise DomainError("not_found", "所选追读报告不存在。", 404)
    return row


def _report_chapter(tx, branch_id, identifier, chapter_id):
    row = _stored_report(tx, branch_id, identifier)
    report = ReadingReport.model_validate_json(row["payload"])
    chapter = next((entry for entry in report.chapters if entry.chapter_id == chapter_id), None)
    if (
        chapter is None
        or not tx.execute(
            "SELECT 1 FROM chapters WHERE branch_id=? AND id=?", (branch_id, chapter_id)
        ).fetchone()
    ):
        raise DomainError("reading_publication_scope", "所选章节不在本工作版本的发布清单中。", 404)
    return row, report, chapter


def _revision(tx, branch_id, chapter_id, revision_id):
    row = tx.execute(
        "SELECT r.id,r.revision,r.content FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
        "WHERE c.branch_id=? AND r.chapter_id=? AND r.id=?",
        (branch_id, chapter_id, revision_id),
    ).fetchone()
    if row is None:
        raise DomainError("reading_publication_revision", "本发布章节的所选保存修订不可用。", 404)
    return row


def _matches_selection(content, selection):
    try:
        _, selected, _ = utf16_slice(content, selection.start, selection.end)
    except DomainError:
        return False
    return bool(selected.strip()) and selected == selection.quote


def _report_view(tx, branch_id, identifier, report):
    bound = {
        chapter.chapter_id: chapter.published_text for chapter in report.chapters if chapter.published_text
    }
    if not bound:
        return ImportedReport(id=identifier, report=report)
    for source in bound.values():
        source.status = "unavailable"
    for row in tx.execute(
        "SELECT r.id,r.chapter_id,r.revision,r.content FROM json_each(?) ids "
        "JOIN revisions r ON r.id=ids.value JOIN chapters c ON c.id=r.chapter_id "
        "WHERE c.branch_id=?",
        (dumps([source.revision_id for source in bound.values()]), branch_id),
    ):
        source = bound.get(row["chapter_id"])
        if (
            source
            and row["id"] == source.revision_id
            and row["revision"] == source.revision
            and sha256(row["content"].encode()).hexdigest() == source.sha256
            and all(
                _matches_selection(row["content"], item) for item in (source.opening, source.ending) if item
            )
        ):
            source.status = "verified"
    return ImportedReport(id=identifier, report=report)


def bind_published_text(domain, branch_id, identifier, data):
    # An omitted selection preserves an existing anchor. An explicit null clears it.
    fingerprint = sha256(dumps([data.model_dump(), sorted(data.model_fields_set)]).encode()).hexdigest()
    with domain.db.connect(True) as tx:
        row, report, chapter = _report_chapter(tx, branch_id, identifier, data.chapter_id)
        repeated = tx.execute(
            "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
            "AND json_extract(basis,'$.source_report_id')=? "
            "AND json_extract(basis,'$.publication_binding_sha256')=?",
            (branch_id, REPORT, identifier, fingerprint),
        ).fetchone()
        if repeated:
            return _report_view(
                tx, branch_id, repeated["id"], ReadingReport.model_validate_json(repeated["payload"])
            )
        revision = _revision(tx, branch_id, data.chapter_id, data.revision_id)
        content = revision["content"]
        if sha256(content.encode()).hexdigest() != data.sha256:
            raise DomainError(
                "reading_publication_digest", "保存修订的内容摘要不同，请重新打开所选版本。", 409
            )
        previous = chapter.published_text
        anchors = {
            edge: getattr(previous, edge)
            if previous and previous.revision_id == data.revision_id and edge not in data.model_fields_set
            else getattr(data, edge)
            for edge in ("opening", "ending")
        }
        if any(selection and not _matches_selection(content, selection) for selection in anchors.values()):
            raise DomainError("reading_publication_selection", "选区与所选保存修订的原文不一致。")
        publication = PublishedText(
            status="verified",
            revision_id=revision["id"],
            revision=revision["revision"],
            sha256=data.sha256,
            **anchors,
        )
        if previous == publication:
            return _report_view(tx, branch_id, identifier, report)
        chapter.published_text = publication
        basis = {
            **json.loads(row["basis"]),
            "source_report_id": identifier,
            "publication_binding_sha256": fingerprint,
        }
        derived_id = _insert_report(tx, branch_id, report, basis)
        return _report_view(tx, branch_id, derived_id, report)


def register_routes(app, domain):
    route = "/api/branches/{branch_id}/reading-metrics"
    history = route + "/history"
    saved = history + "/{identifier}"
    published_text = saved + "/published-text"
    publication_revisions = published_text + "/chapters/{chapter_id}/revisions"

    @app.get(route)
    def latest_reading_metrics(branch_id: str) -> ReadingMetricsState:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            row = tx.execute(
                "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
                "ORDER BY rowid DESC LIMIT 1",
                (branch_id, REPORT),
            ).fetchone()
            latest = (
                _report_view(tx, branch_id, row["id"], ReadingReport.model_validate_json(row["payload"]))
                if row
                else None
            )
        return ReadingMetricsState(latest=latest)

    @app.get(history)
    def reading_history(
        branch_id: str, before_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None
    ) -> list[ReadingHistoryEntry]:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            cursor = (
                tx.execute(
                    "SELECT rowid FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
                    (before_id, branch_id, REPORT),
                ).fetchone()
                if before_id
                else None
            )
            if before_id and not cursor:
                raise DomainError("reading_report_missing", "找不到此工作版本的追读报告。", 404)
            before = cursor["rowid"] if cursor else None
            return [
                dict(row)
                for row in tx.execute(
                    "SELECT id,json_extract(payload,'$.release_version') AS release_version, "
                    "json_extract(payload,'$.mode') AS mode,json_extract(payload,'$.extracted_at') AS extracted_at, "
                    "json_extract(basis,'$.source_report_id') AS source_report_id "
                    "FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
                    "AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT 50",
                    (branch_id, REPORT, before, before),
                )
            ]

    @app.get(saved)
    def read_saved_report(branch_id: str, identifier: str) -> ImportedReport:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            row = _stored_report(tx, branch_id, identifier)
            return _report_view(tx, branch_id, identifier, ReadingReport.model_validate_json(row["payload"]))

    @app.post(published_text, status_code=201)
    def save_published_text(branch_id: str, identifier: str, data: BindPublishedText) -> ImportedReport:
        return bind_published_text(domain, branch_id, identifier, data)

    @app.get(publication_revisions)
    def list_publication_revisions(
        branch_id: str,
        identifier: str,
        chapter_id: str,
        before_revision: Annotated[int | None, Query(ge=0)] = None,
    ) -> list[PublicationRevision]:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            _report_chapter(tx, branch_id, identifier, chapter_id)
            return [
                dict(row)
                for row in tx.execute(
                    "SELECT id,revision,created,cause FROM revisions WHERE chapter_id=? "
                    "AND (? IS NULL OR revision<?) ORDER BY revision DESC LIMIT 50",
                    (chapter_id, before_revision, before_revision),
                )
            ]

    @app.get(publication_revisions + "/{revision_id}")
    def read_publication_revision(
        branch_id: str, identifier: str, chapter_id: str, revision_id: str
    ) -> PublicationRevisionText:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            _report_chapter(tx, branch_id, identifier, chapter_id)
            revision = _revision(tx, branch_id, chapter_id, revision_id)
            return PublicationRevisionText(
                **dict(revision), sha256=sha256(revision["content"].encode()).hexdigest()
            )

    @app.post(route + "/import", status_code=201)
    async def import_reading_metrics(branch_id: str, file: Annotated[UploadFile, File()]) -> ImportedReport:
        blob = await file.read(MAX_BYTES + 1)
        if len(blob) > MAX_BYTES:
            raise DomainError("reading_import_size", "追读文件超过 8 MiB。", 413)
        return await run_in_threadpool(store_import, domain, branch_id, blob)
