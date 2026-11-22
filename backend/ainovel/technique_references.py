"""Chapter citations to a saved reference card and its immutable source range."""

import json
from itertools import islice
from time import time
from typing import Annotated

from fastapi import Query
from pydantic import Field, StrictInt

from .db import DomainError, dumps, uid
from .models import Input
from .reference import ReferenceCard

ARTIFACT = "chapter_technique_reference"
VERSION = "chapter-technique-reference/1"
WRITING_ACTIONS = {"writing.advise", "writing.rewrite_selection", "writing.expand_plot"}


class Passage(Input):
    start: int
    end: int
    before: str
    quote: str
    after: str


class SourceUnit(Input):
    unit_id: str
    unit_title: str
    book_id: str
    title: str
    file_hash: str


class CardSource(Input):
    unit: SourceUnit
    passages: list[Passage]
    truncated: bool = False


class TechniqueReferenceInput(Input):
    session_id: str = Field(min_length=1, max_length=128)
    session_revision: StrictInt = Field(ge=0)
    card_id: str = Field(min_length=1, max_length=128)
    evidence_start: StrictInt | None = Field(default=None, ge=0)
    application_note: str = Field(default="", max_length=1000)


class TechniqueReference(Input):
    id: str
    chapter_id: str
    session_id: str
    session_revision: int
    current_session_revision: int | None
    card: ReferenceCard
    unit: SourceUnit
    evidence_start: int
    evidence_end: int
    application_note: str


def _chapter(tx, branch, chapter):
    row = tx.execute(
        "SELECT c.id,b.novel_id FROM chapters c JOIN branches b ON b.id=c.branch_id "
        "WHERE c.branch_id=? AND c.id=? AND c.archived=0",
        (branch, chapter),
    ).fetchone()
    if not row:
        raise DomainError("ownership", "请选择当前工作版本中尚未归档的章节。", 404)
    return row


def _session_card(tx, identifier, revision, card_id, *, current_only=False):
    row = tx.execute("SELECT revision,payload FROM reference_sessions WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise DomainError("not_found", "找不到参考会话。", 404)
    payload = json.loads(row["payload"])
    if revision == row["revision"]:
        cards = payload["cards"]
    elif current_only:
        raise DomainError("reference_changed", "手法卡已有新修订，请载入后再引用。旧引用仍可查阅。", 409)
    else:
        cards = next((h["cards"] for h in payload.get("history", []) if h["revision"] == revision), ())
    card = next((card for card in cards if card["id"] == card_id), None)
    if not card:
        raise DomainError("not_found", "此修订中没有这张手法卡。", 404)
    return ReferenceCard.model_validate(card).model_dump(), payload


def _unit(tx, identifier, file_hash):
    row = tx.execute(
        """SELECT u.id AS unit_id,u.title AS unit_title,u.book_id,b.title,b.file_hash,u.content
        FROM source_units u JOIN books b ON b.id=u.book_id WHERE u.id=?""",
        (identifier,),
    ).fetchone()
    if not row or row["file_hash"] != file_hash:
        raise DomainError("reference_source", "该导入版本的原文暂不可用。已固定的引文仍可查看。", 409)
    values = dict(row)
    return {key: values[key] for key in SourceUnit.model_fields}, values["content"]


def _utf16_length(value):
    return len(value.encode("utf-16-le")) // 2


def _passage(content, start, end, quote):
    from .domain import utf16_slice

    before, exact, after = utf16_slice(content, start, end)
    if exact != quote:
        raise DomainError("reference_source", "固定选区与导入原文不符，请核对来源。", 409)
    return {"start": start, "end": end, "before": before[-140:], "quote": exact, "after": after[:140]}


def _sources(tx, payload):
    from .domain import utf16_slice

    source = payload["source"]
    unit, content = _unit(tx, source["unit_id"], source["file_hash"])
    _, selected, _ = utf16_slice(content, source["start"], source["end"])
    if selected != source["quote"]:
        raise DomainError("reference_source", "参考会话的选段与保存原文不符。", 409)
    return unit, content, source, selected


def _indices(text, quote):
    index = text.find(quote)
    while index >= 0:
        yield index
        index = text.find(quote, index + 1)


def _locate_sources(tx, identifier, revision, card_id):
    card, payload = _session_card(tx, identifier, revision, card_id)
    unit, content, source, selected = _sources(tx, payload)
    quote = card["evidence"]
    indices = list(islice(_indices(selected, quote), 13))
    if not indices:
        raise DomainError("evidence", "此卡的引文不在已保存的选段中。", 409)
    starts = [source["start"] + _utf16_length(selected[:index]) for index in indices[:12]]
    return {
        "unit": unit,
        "passages": [_passage(content, start, start + _utf16_length(quote), quote) for start in starts],
        "truncated": len(indices) > 12,
    }


def _payload(row):
    saved = json.loads(row["payload"])
    if saved["version"] != VERSION:
        raise DomainError("technique_version", "此手法引用由其他版本保存。", 409)
    return saved


def _public(tx, row):
    saved = _payload(row)
    session = tx.execute(
        "SELECT revision FROM reference_sessions WHERE id=?", (saved["session_id"],)
    ).fetchone()
    return {
        "id": row["id"],
        "chapter_id": row["chapter_id"],
        **{key: value for key, value in saved.items() if key != "version"},
        "current_session_revision": session["revision"] if session else None,
    }


def _freeze_pin(tx, branch, chapter, data):
    target = _chapter(tx, branch, chapter)
    card, payload = _session_card(tx, data.session_id, data.session_revision, data.card_id, current_only=True)
    if payload.get("branch_id") and payload["branch_id"] != branch:
        raise DomainError("ownership", "这张卡当前绑定了其他工作版本。", 403)
    if not card["technique"].strip() or not card["replacement"].strip():
        raise DomainError("technique_ready", "请先写清可迁移写法及应用时须替换的内容。", 409)
    if not card["evidence"].strip():
        raise DomainError("evidence", "手法卡需要非空的逐字原文。", 409)
    unit, content, source, selected = _sources(tx, payload)
    start = data.evidence_start
    if start is None:
        indices = list(islice(_indices(selected, card["evidence"]), 2))
        if len(indices) != 1:
            raise DomainError("evidence_location", "请先定位并选定本次引用的原文位置。", 409)
        start = source["start"] + _utf16_length(selected[: indices[0]])
    end = start + _utf16_length(card["evidence"])
    if start < source["start"] or end > source["end"]:
        raise DomainError("evidence_location", "引用位置必须在会话所选的原文范围内。", 409)
    _passage(content, start, end, card["evidence"])
    saved = {
        "version": VERSION,
        "session_id": data.session_id,
        "session_revision": data.session_revision,
        "card": card,
        "unit": unit,
        "evidence_start": start,
        "evidence_end": end,
        "application_note": data.application_note.strip(),
    }
    wire = dumps(saved)
    old = tx.execute(
        "SELECT id,chapter_id,payload FROM artifacts WHERE branch_id=? AND chapter_id=? "
        "AND kind=? AND status='accepted' AND payload=?",
        (branch, chapter, ARTIFACT, wire),
    ).fetchone()
    if old:
        return _public(tx, old)
    identifier = uid()
    tx.execute(
        """INSERT INTO artifacts(id,novel_id,branch_id,chapter_id,kind,payload,basis,status,created)
        VALUES(?,?,?,?,?,?,'{}','accepted',?)""",
        (identifier, target["novel_id"], branch, chapter, ARTIFACT, wire, time()),
    )
    return _public(tx, {"id": identifier, "chapter_id": chapter, "payload": wire})


def _pin(tx, branch, chapter, identifier):
    row = tx.execute(
        """SELECT * FROM artifacts WHERE id=? AND branch_id=? AND chapter_id=?
        AND kind=? AND status='accepted'""",
        (identifier, branch, chapter, ARTIFACT),
    ).fetchone()
    if not row:
        raise DomainError("ownership", "该手法引用不属于当前章节。", 404)
    return row


def writing_rules(tx, branch, request):
    chapter = request.get("chapter_id")
    identifiers = request["technique_reference_ids"]
    if not chapter or request["action"] not in WRITING_ACTIONS or len(identifiers) != len(set(identifiers)):
        raise DomainError("technique_scope", "仅可在本章写作辅助中逐项选择手法引用。", 409)
    _chapter(tx, branch, chapter)
    rules = []
    for identifier in identifiers:
        pin = _payload(_pin(tx, branch, chapter, identifier))
        card = pin["card"]
        rules.append(
            {
                "reference_id": identifier,
                "session_id": pin["session_id"],
                "session_revision": pin["session_revision"],
                "card_id": card["id"],
                **{key: card[key] for key in ("technique", "conditions", "exceptions", "replacement")},
                "application_note": pin["application_note"],
            }
        )
    return rules


def register_routes(app, domain):
    prefix = "/api/branches/{branch_id}/chapters/{chapter_id}/technique-references"

    @app.get("/api/reference-sessions/{session_id}/cards/{card_id}/source", response_model=CardSource)
    def source(session_id: str, card_id: str, revision: Annotated[int, Query(ge=0)]):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return _locate_sources(tx, session_id, revision, card_id)

    @app.get(prefix, response_model=list[TechniqueReference])
    def chapter_references(branch_id: str, chapter_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            _chapter(tx, branch_id, chapter_id)
            rows = tx.execute(
                "SELECT id,chapter_id,payload FROM artifacts WHERE branch_id=? AND chapter_id=? "
                "AND kind=? AND status='accepted' ORDER BY rowid DESC",
                (branch_id, chapter_id, ARTIFACT),
            )
            return [_public(tx, row) for row in rows]

    @app.post(prefix, response_model=TechniqueReference, status_code=201)
    def cite_card(branch_id: str, chapter_id: str, data: TechniqueReferenceInput):
        with domain.db.connect(True) as tx:
            return _freeze_pin(tx, branch_id, chapter_id, data)

    @app.get(prefix + "/{identifier}/source", response_model=CardSource)
    def cited_source(branch_id: str, chapter_id: str, identifier: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            _chapter(tx, branch_id, chapter_id)
            saved = _payload(_pin(tx, branch_id, chapter_id, identifier))
            unit, content = _unit(tx, saved["unit"]["unit_id"], saved["unit"]["file_hash"])
            source = _passage(
                content, saved["evidence_start"], saved["evidence_end"], saved["card"]["evidence"]
            )
            return {"unit": unit, "passages": [source]}
