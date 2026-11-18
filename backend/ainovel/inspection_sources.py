"""Read the chapter evidence recorded in a signed M6 report."""

import json
from hashlib import sha256
from itertools import chain
from typing import Annotated

from fastapi import Query
from pydantic import Field, ValidationError

from .db import DomainError
from .domain import utf16_slice
from .manuscript_exchange import source_revision
from .models import Input
from .story_graph import Source
from .workflow_archive import read_archived_workflow
from .workflow_inspection import KIND, CheckId, InspectionSettingUse, inspection_setting_use_matches


PAGE_SIZE = 24
CONTEXT_CHARS = 240


class _SourceLocator(Source):
    quote: str | None = Field(default=None, min_length=1, max_length=4000)
    setting_use: InspectionSettingUse | None = None
    state_record_id: str | None = None


class InspectionEvidenceAddress(Input):
    index: int
    label: str
    chapter_id: str
    chapter_title: str
    revision: int
    start: int
    end: int


class InspectionEvidenceContext(Input):
    address: InspectionEvidenceAddress
    revision_id: str
    before: str
    quote: str
    after: str


class InspectionEvidencePage(Input):
    entries: list[InspectionEvidenceAddress]
    total: int
    previous_offset: int | None = None
    context: InspectionEvidenceContext | None = None


def _rows(value):
    return (row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def _proposed_sources(item):
    review = item.get("author_review") or {}
    for source in _rows(review.get("evidence")):
        yield "作者核对", source
    basis = item["basis"]
    conflict_records = set()
    for group in _rows(basis.get("state_conflicts")):
        for side in _rows(group.get("sides")):
            identifier = side.get("record_id")
            if not isinstance(identifier, str):
                continue
            conflict_records.add(identifier)
            label = f"{side.get('title') or item['title']} · {side.get('value', '')} · 记录 {identifier}"
            for source in _rows(side.get("sources")):
                yield label, {**source, "state_record_id": identifier}
    for key in ("records", "events", "knowledge"):
        for record in _rows(basis.get(key)):
            if key == "records" and isinstance(record.get("id"), str) and record["id"] in conflict_records:
                continue
            for source in _rows(record.get("sources")):
                yield record.get("title") or item["title"], source
            for stage in _rows(record.get("stages")):
                for source in _rows(stage.get("sources")):
                    yield record.get("title") or item["title"], source
    for scene in _rows(basis.get("scenes")):
        payload = scene.get("payload")
        if isinstance(payload, dict):
            yield (
                payload.get("title") or "场景时点",
                {
                    "chapter_id": scene.get("chapter_id"),
                    "revision": payload.get("source_revision"),
                    "start": payload.get("start"),
                    "end": payload.get("end"),
                },
            )
    if item["check_id"] == "05":
        for review in _rows(basis.get("reports")):
            evidence = review.get("evidence")
            rule = review.get("rule")
            if not isinstance(evidence, dict) or not isinstance(rule, dict):
                continue
            yield (
                rule.get("title") or item["title"],
                {
                    "chapter_id": review.get("chapter_id"),
                    "revision": review.get("revision"),
                    "start": evidence.get("start"),
                    "end": evidence.get("end"),
                    "quote": evidence.get("text"),
                },
            )
    if item["check_id"] == "08":
        for use in _rows(basis.get("author_setting_uses")):
            evidence = use.get("evidence")
            if not isinstance(evidence, dict) or not all(
                (evidence.get("revision_id"), evidence.get("quote_sha256"), use.get("chapter_sha256"))
            ):
                continue
            yield (
                f"作者确认设定使用 · {use.get('title') or use.get('item_id', '')}",
                {
                    "chapter_id": evidence.get("chapter_id"),
                    "start": evidence.get("start"),
                    "end": evidence.get("end"),
                    "revision": use.get("revision"),
                    "setting_use": use,
                },
            )


def _review_blocker_sources(tx, branch_id, item, bindings):
    for blocker in _rows(item["basis"].get("review_blockers")):
        chapter_id, revision, quote = (blocker.get(key) for key in ("chapter_id", "revision", "quote"))
        if not isinstance(chapter_id, str) or type(revision) is not int or not isinstance(quote, str):
            continue
        if not quote.strip() or len(quote) > 4000:
            continue
        original = _original_revision(tx, branch_id, chapter_id, revision, bindings)
        if not original:
            continue
        content = original[1]
        position = content.find(quote)
        if position < 0 or content.find(quote, position + 1) >= 0:
            continue
        start = len(content[:position].encode("utf-16-le")) // 2
        yield (
            "当前严重意见",
            {
                "chapter_id": chapter_id,
                "revision": revision,
                "start": start,
                "end": start + len(quote.encode("utf-16-le")) // 2,
                "quote": quote,
            },
        )


def _addresses(tx, branch_id, item, bindings):
    result = []
    used = set()
    for label, value in chain(
        _proposed_sources(item), _review_blocker_sources(tx, branch_id, item, bindings)
    ):
        try:
            source = _SourceLocator.model_validate(value)
        except ValidationError:
            continue
        key = (
            source.chapter_id,
            source.revision,
            source.start,
            source.end,
            source.quote,
            source.setting_use.version_id if source.setting_use else None,
            source.state_record_id,
        )
        if source.start >= source.end or key in used:
            continue
        used.add(key)
        result.append((label if isinstance(label, str) else item["title"], source))
    return result


def _original_revision(tx, branch_id, chapter_id, revision, bindings):
    expected = bindings.get(chapter_id) if bindings is not None else None
    if expected:
        original = source_revision(tx, branch_id, expected["revision_id"])
        if (
            not original
            or original.chapter_id != chapter_id
            or original.revision != expected["revision"]
            or original.sha256 != expected["sha256"]
        ):
            return None
        while original.revision > revision and original.parent_id:
            parent = source_revision(tx, branch_id, original.parent_id)
            if not parent or parent.chapter_id != chapter_id or parent.revision != original.revision - 1:
                return None
            original = parent
        return (original.id, original.content) if original.revision == revision else None
    if bindings is not None:
        return None
    row = tx.execute(
        "SELECT r.id,r.content FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
        "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
        (branch_id, chapter_id, revision),
    ).fetchone()
    return (row["id"], row["content"]) if row else None


def _source_context(tx, branch_id, address, source, bindings):
    proof = source.setting_use
    if proof:
        from .setting_use_desktop import read_version
        from .settings_sync import read_settings_version

        confirmed = read_version(tx, branch_id, proof.version_id)
        settings = read_settings_version(tx, branch_id, proof.settings_version_id)
        row = source_revision(tx, branch_id, proof.evidence.revision_id)
        original = (
            (row.id, row.content)
            if row
            and (row.chapter_id, row.revision, row.sha256)
            == (source.chapter_id, source.revision, proof.chapter_sha256)
            and sha256(row.content.encode("utf-8")).hexdigest() == proof.chapter_sha256
            and inspection_setting_use_matches(proof, confirmed, settings)
            else None
        )
    else:
        original = _original_revision(tx, branch_id, source.chapter_id, source.revision, bindings)
    if original:
        try:
            before, quote, after = utf16_slice(original[1], source.start, source.end)
        except DomainError:
            pass
        else:
            if (source.quote is None or quote == source.quote) and (
                proof is None
                or (
                    quote.strip()
                    and len(quote) <= 4000
                    and sha256(quote.encode("utf-8")).hexdigest() == proof.evidence.quote_sha256
                )
            ):
                return InspectionEvidenceContext(
                    address=address,
                    revision_id=original[0],
                    before=before[-CONTEXT_CHARS:],
                    quote=quote,
                    after=after[:CONTEXT_CHARS],
                )
    raise DomainError(
        "inspection_source_missing", "原章稿修订或原文选区无法核实，请从原工作版本或独立备份恢复。", 404
    )


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/workflow-inspections/{inspection_id}/source-context/{check_id}")
    def inspection_sources(
        branch_id: str,
        inspection_id: str,
        check_id: CheckId,
        offset: Annotated[int, Query(ge=0)] = 0,
        source_index: Annotated[int | None, Query(ge=0)] = None,
    ) -> InspectionEvidencePage:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            native = tx.execute(
                "SELECT payload FROM artifacts WHERE branch_id=? AND id=? AND kind=? AND status='accepted'",
                (branch_id, inspection_id, KIND),
            ).fetchone()
            bindings = None
            if native:
                report = json.loads(native["payload"])
            else:
                archived = next(iter(read_archived_workflow(tx, branch_id, KIND, inspection_id)), None)
                if not archived:
                    raise DomainError("inspection_missing", "找不到此版本的体检单。", 404)
                saved = json.loads(archived["payload"])
                report = saved["report"]
                bindings = {row["id"]: row for row in saved["chapter_bindings"]}
            item = next(row for row in report["items"] if row["check_id"] == check_id)
            sources = _addresses(tx, branch_id, item, bindings)
            titles = {chapter["id"]: chapter["title"] for chapter in report["chapters"]}

            def address(index):
                label, source = sources[index]
                data = source.model_dump(exclude={"quote", "setting_use", "state_record_id"})
                return InspectionEvidenceAddress(
                    **data,
                    index=index,
                    label=label,
                    chapter_title=titles.get(source.chapter_id, f"章节 {source.chapter_id}"),
                )

            if source_index is not None:
                if source_index >= len(sources):
                    raise DomainError("inspection_source_missing", "此检查项没有这处正文依据。", 404)
                return InspectionEvidencePage(
                    entries=[],
                    total=len(sources),
                    context=_source_context(
                        tx, branch_id, address(source_index), sources[source_index][1], bindings
                    ),
                )
            return InspectionEvidencePage(
                entries=[address(index) for index in range(offset, min(offset + PAGE_SIZE, len(sources)))],
                total=len(sources),
                previous_offset=max(0, offset - PAGE_SIZE) if offset > 0 else None,
            )
