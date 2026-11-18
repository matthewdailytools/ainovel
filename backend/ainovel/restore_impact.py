"""Measure restoration effects against the same checks used by current reports."""

from contextlib import contextmanager
import json
from typing import Literal

from .book_completion import KIND as COMPLETION, _basis as completion_basis
from .db import DomainError
from .manual_finalization import _state as finalization_state
from .models import Input
from .setting_review import confirmed_world_basis
from .workflow_inspection import KIND as INSPECTION, Scope, _effective, _evaluate, _rule_reports


class AffectedReport(Input):
    id: str
    kind: Literal["workflow_inspection", "book_completion", "author_finalization", "setting_review"]
    scope: Scope
    scope_id: str | None
    check_ids: list[str]


def report_basis(tx, branch_id):
    """Capture the currently valid checks of each locally signed report."""
    rows = tx.execute(
        "SELECT id,kind,CASE WHEN kind=? THEN payload END AS payload,basis,created "
        "FROM artifacts WHERE branch_id=? AND status='accepted' AND kind IN (?,?) ORDER BY id",
        (INSPECTION, branch_id, INSPECTION, COMPLETION),
    )
    result = []
    previews = {}
    completion = None
    for row in rows:
        if row["kind"] == COMPLETION:
            if completion is None:
                completion = completion_basis(tx, branch_id)[0]
            if json.loads(row["basis"])["basis_sha256"] == completion:
                result.append(_report(row["id"], COMPLETION, "book", None))
            continue
        report = json.loads(row["payload"])
        scope = report["scope"], report["scope_id"]
        if scope not in previews:
            try:
                previews[scope] = _evaluate(tx, branch_id, *scope)[0]
            except DomainError as error:
                if error.code != "inspection_target":
                    raise
                previews[scope] = None
        current = _effective(row, previews[scope])
        check_ids = [item["check_id"] for item in current["items"] if item["current_status"] != "outdated"]
        if check_ids:
            result.append(_report(row["id"], INSPECTION, *scope, check_ids))

    if tx.execute(
        "SELECT 1 FROM assessments WHERE branch_id=? AND category='author_finalization' LIMIT 1",
        (branch_id,),
    ).fetchone():
        story = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
        finalization = finalization_state(tx, branch_id, story)
        if finalization["author_finalized"]:
            result.append(_report(finalization["record"]["id"], "author_finalization", "book", None))

    if tx.execute(
        "SELECT 1 FROM artifacts WHERE branch_id=? AND kind='setting_review' AND status='accepted' LIMIT 1",
        (branch_id,),
    ).fetchone():
        chapters = tx.execute(
            "SELECT id,revision,stale FROM chapters WHERE branch_id=? AND archived=0", (branch_id,)
        )
        world = confirmed_world_basis(tx, branch_id)
        for row in _rule_reports(tx, branch_id, chapters, world["confirmation_id"]):
            result.append(_report(row["report_id"], "setting_review", "chapter", row["chapter_id"]))
    return sorted(
        result, key=lambda report: (report["kind"], report["scope"], report["scope_id"] or "", report["id"])
    )


def _report(identifier, kind, scope, scope_id, checks=()):
    return {"id": identifier, "kind": kind, "scope": scope, "scope_id": scope_id, "check_ids": list(checks)}


@contextmanager
def forecast(tx):
    """Use the real mutation logic without publishing drafts, reports or sync changes."""
    tx.execute("SAVEPOINT restore_impact")
    try:
        yield
    finally:
        tx.execute("ROLLBACK TO restore_impact")
        tx.execute("RELEASE restore_impact")


def invalidated_reports(before, after):
    indexed = {(report["kind"], report["id"]): set(report["check_ids"]) for report in after}
    affected = []
    for report in before:
        remaining = indexed.get((report["kind"], report["id"]))
        lost = [
            check_id for check_id in report["check_ids"] if remaining is None or check_id not in remaining
        ]
        if remaining is None or lost:
            affected.append({**report, "check_ids": lost})
    return affected
