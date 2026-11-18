"""Preview and commit a deliberate restoration from saved chapter history."""

from hashlib import sha256
import json
from typing import Literal

from pydantic import Field

from .conception import CONFIRMATION, _latest as latest_setting, _read_payload, setting_is_adopted
from .db import DomainError, dumps
from .domain import utf16_slice
from .models import Input
from .outline_workspace import WINDOW_CHAPTERS, _chapter_plans, _decode, _latest as latest_outline
from .restore_impact import AffectedReport, forecast, invalidated_reports, report_basis
from .story_graph import graph_snapshot, references as record_references, sources
from .workflow_inspection import _card_references


class ChapterRestoreInput(Input):
    basis_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RestoreRevision(Input):
    id: str
    revision: int
    content: str
    sha256: str


class RestoreSource(Input):
    chapter_id: str
    revision: int
    start: int
    end: int
    quote: str


class RestoreReference(Input):
    kind: Literal["outline", "setting", "reference_card", "story_record", "fact", "scene_time"]
    id: str
    title: str
    source: RestoreSource | None


class RestorePromiseWindow(Input):
    plant_chapter_id: str
    opens_chapter_id: str
    closes_chapter_id: str


class RestorePromise(Input):
    id: str
    title: str
    status: str
    sources: list[RestoreSource]
    window: RestorePromiseWindow | None


class ChapterRestorePreview(Input):
    chapter_id: str
    title: str
    current: RestoreRevision
    target: RestoreRevision
    basis_sha256: str
    references: list[RestoreReference]
    promises: list[RestorePromise]
    affected_reports: list[AffectedReport]


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _revision(row):
    return {key: row[key] for key in ("id", "revision", "content")} | {"sha256": _digest(row["content"])}


def _source(chapter, start, end, expected=None):
    if start is None or end is None or start >= end:
        return None
    try:
        _, quote, _ = utf16_slice(chapter["content"], start, end)
    except DomainError:
        return None
    if expected is not None and quote != expected:
        return None
    return {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": start,
        "end": end,
        "quote": quote,
    }


def _bound_sources(chapter, records):
    return [
        bound
        for source in records
        if (source["chapter_id"], source["revision"]) == (chapter["id"], chapter["revision"])
        and (bound := _source(chapter, source["start"], source["end"], source["quote"])) is not None
    ]


def _reference(identifier, kind, title, source=None):
    return {"kind": kind, "id": identifier, "title": title, "source": source}


def _outline_references(tx, chapter):
    branch_id = chapter["branch_id"]
    outline = _decode(latest_outline(tx, branch_id, True))
    plans = []
    refs = []
    windows = {}
    if outline:
        settings = {item["id"]: item["title"] for item in outline["setting_basis"].get("items", [])}
        for volume, unit, plan in _chapter_plans(outline["plan"]):
            if plan["chapter_id"] == chapter["id"]:
                plans.append(plan)
                refs.append(_reference(outline["id"], "outline", f"{volume['title']} / {unit['title']}"))
                refs.extend(
                    _reference(identifier, "setting", settings[identifier])
                    for identifier in plan["setting_ids"]
                    if identifier in settings
                )
        for window in outline["plan"]["promises"]:
            identifier = window["promise_id"]
            addresses = {key: window[key] for key in WINDOW_CHAPTERS}
            if chapter["id"] in addresses.values():
                windows[identifier] = addresses
    planned, adopted = _card_references(tx, branch_id, [chapter], plans)
    frozen = {
        card.get("version_id"): card
        for card in (outline or {}).get("setting_basis", {}).get("reference_cards", [])
        if card.get("version_id")
    }
    by_id = {}
    for card in [*planned, *adopted]:
        identifier = card.get("version_id") or f"{card['session_id']}:{card['card_id']}:{card['revision']}"
        title = frozen.get(identifier, {}).get("technique") or (card["source"] or {}).get("title")
        by_id[identifier] = _reference(identifier, "reference_card", title or f"手法卡 {card['card_id']}")
    return [*refs, *by_id.values()], windows


def _other_references(tx, chapter):
    branch_id = chapter["branch_id"]
    if row := latest_setting(tx, branch_id, CONFIRMATION):
        setting = _read_payload(row)
        items = {
            item["id"]: item
            for category in setting["categories"]
            if not category["skipped"]
            for item in category["items"]
            if setting_is_adopted(item.get("adoption_state"))
        }
        for entry in setting.get("sources", []):
            if item := items.get(entry["item_id"]):
                for source in _bound_sources(chapter, [entry["evidence"]]):
                    yield _reference(item["id"], "setting", item["title"], source)
    for row in tx.execute(
        "SELECT id,subject,evidence_start,evidence_end,evidence FROM facts "
        "WHERE chapter_id=? AND source_revision=? AND stale=0 ORDER BY id",
        (chapter["id"], chapter["revision"]),
    ):
        yield _reference(
            row["id"],
            "fact",
            row["subject"],
            _source(chapter, row["evidence_start"], row["evidence_end"], row["evidence"]),
        )
    for row in tx.execute(
        "SELECT id,payload FROM scene_times WHERE chapter_id=? AND archived=0 ORDER BY id", (chapter["id"],)
    ):
        scene = json.loads(row["payload"])
        if scene["source_revision"] == chapter["revision"]:
            yield _reference(
                row["id"], "scene_time", scene["title"], _source(chapter, scene["start"], scene["end"])
            )


def _references(tx, chapter, graph):
    refs, windows = _outline_references(tx, chapter)
    refs.extend(_other_references(tx, chapter))
    records = [row for row in graph["records"] if not row["archived"]]
    citations = {row["id"]: _bound_sources(chapter, sources(row["payload"])) for row in records}
    affected = {identifier for identifier, bound in citations.items() if bound}
    dependents = {row["id"]: record_references(row["payload"]) for row in records}
    while descendants := {
        identifier
        for identifier, parents in dependents.items()
        if identifier not in affected and parents & affected
    }:
        affected.update(descendants)
    promises = []
    for row in records:
        identifier, payload = row["id"], row["payload"]
        bound = citations[identifier]
        if row["kind"] == "promise":
            if identifier in affected or identifier in windows:
                promises.append(
                    {
                        "id": identifier,
                        "title": payload["title"],
                        "status": payload["stages"][-1]["status"],
                        "sources": bound,
                        "window": windows.get(identifier),
                    }
                )
        elif identifier in affected:
            refs.extend(
                _reference(identifier, "story_record", payload["title"], source)
                for source in bound or (None,)
            )
    return refs, promises


def _basis(tx, chapter_id, revision_id):
    chapter = tx.execute("SELECT * FROM chapters WHERE id=? AND archived=0", (chapter_id,)).fetchone()
    if not chapter:
        raise DomainError("not_found", "章节不存在。", 404)
    row = tx.execute(
        "SELECT * FROM revisions WHERE id=? AND chapter_id=?", (revision_id, chapter_id)
    ).fetchone()
    if not row:
        raise DomainError("not_found", "找不到属于此章的历史修订。", 404)
    current = tx.execute(
        "SELECT * FROM revisions WHERE chapter_id=? AND revision=?", (chapter_id, chapter["revision"])
    ).fetchone()
    if not current or current["content"] != chapter["content"]:
        raise DomainError("chapter_restore_history", "当前章稿无法和保存历史核对。", 409)
    graph = graph_snapshot(tx, chapter["branch_id"])
    refs, promises = _references(tx, chapter, graph)
    checks = report_basis(tx, chapter["branch_id"])
    preview = {
        "chapter_id": chapter_id,
        "title": chapter["title"],
        "current": _revision(current),
        "target": _revision(row),
        "references": refs,
        "promises": promises,
    }
    identifiers = [
        (part["id"], part["revision"], part["sha256"]) for part in (preview["current"], preview["target"])
    ]
    preview["basis_sha256"] = _digest(
        dumps([chapter_id, chapter["title"], graph["story_revision"], identifiers, refs, promises, checks])
    )
    return chapter, preview, checks


def _restore(tx, domain, chapter, preview):
    domain.revise_tx(tx, chapter, preview["target"]["content"], "恢复历史为新版本")


def register_routes(app, domain):
    endpoint = "/api/chapters/{chapter_id}/history/{revision_id}"

    @app.get(endpoint + "/restore-preview")
    def preview_chapter_restore(chapter_id: str, revision_id: str) -> ChapterRestorePreview:
        with domain.db.connect(True) as tx:
            chapter, preview, checks = _basis(tx, chapter_id, revision_id)
            impacted = []
            if checks and preview["current"]["id"] != revision_id:
                with forecast(tx):
                    _restore(tx, domain, chapter, preview)
                    impacted = invalidated_reports(checks, report_basis(tx, chapter["branch_id"]))
            return {**preview, "affected_reports": impacted}

    @app.post(endpoint + "/restore")
    def restore_chapter_revision(chapter_id: str, revision_id: str, data: ChapterRestoreInput):
        with domain.db.connect(True) as tx:
            chapter, preview, _ = _basis(tx, chapter_id, revision_id)
            if data.basis_sha256 != preview["basis_sha256"]:
                raise DomainError("chapter_restore_changed", "当前章稿或引用已变化，请重新预览后恢复。", 409)
            if preview["current"]["id"] == revision_id:
                raise DomainError("chapter_restore_target", "当前章稿已是所选修订。", 409)
            _restore(tx, domain, chapter, preview)
            return dict(tx.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
