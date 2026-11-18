"""Explain what a saved outline draft will change before the author confirms it."""

from hashlib import sha256
from typing import Literal

from .db import DomainError, dumps
from .models import Input
from .outline_workspace import (
    _chapter_plans,
    _check_base,
    _check_project,
    _decode,
    _latest,
    _required_promises_changed,
    _settings,
    _validate,
    _write_confirmation,
)
from .restore_impact import AffectedReport, forecast, invalidated_reports, report_basis
from .story_graph import graph_snapshot


ChapterField = Literal["placement", "goal", "obstacle", "turn", "result", "settings", "reference_cards"]
OutlineField = Literal["summary", "organization", "settings", "promises"]
_EMPTY_PLAN = {"summary": "", "volumes": [], "promises": []}


class OutlineChapterChange(Input):
    chapter_id: str
    title: str
    action: Literal["added", "removed", "changed"]
    fields: list[ChapterField]


class OutlineFrozenCandidate(Input):
    id: str
    kind: Literal["chapter_draft", "text_patch"]
    chapter_id: str
    title: str


class OutlineConfirmationImpact(Input):
    draft_id: str
    base_revision: int
    confirmed_id: str | None
    confirmed_revision: int | None
    basis_sha256: str
    outline_fields: list[OutlineField]
    chapter_changes: list[OutlineChapterChange]
    frozen_candidates: list[OutlineFrozenCandidate]
    affected_reports: list[AffectedReport]


def _chapter_values(plan, basis):
    settings = {item["id"]: item for item in basis.get("items", [])}
    return {
        chapter["chapter_id"]: {
            "placement": [index, volume["id"], volume["title"], unit["id"], unit["title"]],
            **{key: chapter[key] for key in ("goal", "obstacle", "turn", "result")},
            "settings": {identifier: settings.get(identifier) for identifier in chapter["setting_ids"]},
            "reference_cards": chapter.get("reference_cards", []),
        }
        for index, (volume, unit, chapter) in enumerate(_chapter_plans(plan))
    }


def _organization(plan):
    return [
        (
            volume["id"],
            volume["title"],
            volume["summary"],
            [(unit["id"], unit["title"], unit["summary"]) for unit in volume["units"]],
        )
        for volume in plan["volumes"]
    ]


def _outline_fields(plan, basis):
    return {
        "summary": plan["summary"],
        "organization": _organization(plan),
        "settings": basis.get("confirmation_id"),
        "promises": (plan["promises"], plan.get("new_promises", [])),
    }


def _frozen_candidates(tx, branch_id, previous, graph):
    if not previous:
        return []
    basis = previous["setting_basis"]
    try:
        _settings(tx, branch_id, basis["confirmation_id"])
        _check_project(tx, branch_id, basis)
        _validate(graph, previous["plan"], basis, True)
    except DomainError:
        # A previously invalid outline already blocks its candidates.
        return []
    return [
        dict(row)
        for row in tx.execute(
            "SELECT a.id,a.kind,c.id AS chapter_id,c.title FROM artifacts a JOIN chapters c ON c.id=a.chapter_id "
            "WHERE a.branch_id=? AND c.branch_id=a.branch_id AND c.archived=0 AND a.base_revision=c.revision "
            "AND a.status='candidate' AND a.kind IN ('chapter_draft','text_patch') "
            "AND json_extract(a.basis,'$.outline.id')=? ORDER BY c.position,a.created,a.id",
            (branch_id, previous["id"]),
        )
    ]


def _affected_reports(tx, branch_id, draft, previous, graph):
    if not tx.execute(
        "SELECT 1 FROM artifacts WHERE branch_id=? AND status='accepted' "
        "AND kind IN ('workflow_inspection','book_completion') LIMIT 1",
        (branch_id,),
    ).fetchone():
        return []
    kinds = {"workflow_inspection", "book_completion"}
    before = [row for row in report_basis(tx, branch_id) if row["kind"] in kinds]
    if not before:
        return []
    requirement_changed = _required_promises_changed(previous["plan"] if previous else None, draft["plan"])
    with forecast(tx):
        _write_confirmation(tx, branch_id, draft, graph, requirement_changed)
        return invalidated_reports(before, report_basis(tx, branch_id))


def confirmation_impact(tx, branch_id, draft, previous, graph):
    old_plan = previous["plan"] if previous else _EMPTY_PLAN
    old_basis = previous["setting_basis"] if previous else {}
    plan, basis = draft["plan"], draft["setting_basis"]
    before = _chapter_values(old_plan, old_basis)
    after = _chapter_values(plan, basis)
    titles = {chapter["id"]: chapter["title"] for chapter in graph["chapters"]}
    changes = []
    for identifier in dict.fromkeys([*before, *after]):
        if identifier not in before:
            action, fields = "added", []
        elif identifier not in after:
            action, fields = "removed", []
        else:
            action = "changed"
            fields = [key for key, value in after[identifier].items() if before[identifier][key] != value]
            if not fields:
                continue
        changes.append(
            {
                "chapter_id": identifier,
                "title": titles.get(identifier, identifier),
                "action": action,
                "fields": fields,
            }
        )
    old_fields = _outline_fields(old_plan, old_basis)
    impact = {
        "draft_id": draft["id"],
        "base_revision": draft["revision"],
        "confirmed_id": previous["id"] if previous else None,
        "confirmed_revision": previous["revision"] if previous else None,
        "outline_fields": [
            key for key, value in _outline_fields(plan, basis).items() if old_fields[key] != value
        ],
        "chapter_changes": changes,
        "frozen_candidates": _frozen_candidates(tx, branch_id, previous, graph),
        "affected_reports": _affected_reports(tx, branch_id, draft, previous, graph),
    }
    return {**impact, "basis_sha256": sha256(dumps([branch_id, impact]).encode()).hexdigest()}


def read_confirmation_impact(domain, branch_id, base_revision):
    with domain.db.connect(True) as tx:
        row = _latest(tx, branch_id)
        _check_base(row, base_revision)
        if row is None or row["confirmed"]:
            raise DomainError("outline_impact_draft", "没有待确认的已保存大纲，请重新读取。", 409)
        draft = _decode(row)
        basis = draft["setting_basis"]
        _settings(tx, branch_id, basis["confirmation_id"])
        _check_project(tx, branch_id, basis)
        graph = graph_snapshot(tx, branch_id)
        _validate(graph, draft["plan"], basis, True)
        previous = _decode(_latest(tx, branch_id, True))
        return confirmation_impact(tx, branch_id, draft, previous, graph)
