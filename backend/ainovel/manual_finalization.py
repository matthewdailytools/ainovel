"""Version-bound author finalization, independent of model production and benefit claims."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal
import unicodedata
from time import time

from fastapi import FastAPI
from pydantic import Field, StrictBool, field_validator

from .db import Database, DomainError, dumps
from .models import Input

if TYPE_CHECKING:
    from .domain import Domain


class FinalizationChecklist(Input):
    manuscript_read: StrictBool
    main_conflict_closed: StrictBool
    character_outcomes_reviewed: StrictBool
    promises_addressed: StrictBool


class FinalizationDisposition(Input):
    issue_id: str = Field(min_length=24, max_length=24, pattern=r"^[0-9a-f]+$")
    decision: Literal["not_an_error", "intentional_choice"]
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("请写明至少10字的裁定理由及依据。")
        return value.strip()


class ManualFinalizationInput(Input):
    base_story_revision: int = Field(ge=0)
    base_manifest_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    checklist: FinalizationChecklist
    note: str = Field(min_length=10, max_length=10_000)
    dispositions: list[FinalizationDisposition] = Field(default_factory=list, max_length=500)

    @field_validator("note")
    @classmethod
    def meaningful_note(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("请写明至少10字的定稿说明、结局及保留事项。")
        return value.strip()


def _rows(conn, sql, args=()):
    return [dict(row) for row in conn.execute(sql, args)]


def _manifest(chapters):
    return [
        {
            "id": c["id"],
            "position": c["position"],
            "title": c["title"],
            "revision": c["revision"],
            "sha256": hashlib.sha256(c["content"].encode()).hexdigest(),
        }
        for c in chapters
    ]


def _known_issues(conn, branch_id, story_revision, chapters):
    """Only opinions whose frozen text is the currently saved manuscript can block confirmation.

    Rejected or unadopted alternative prose is never substituted for the manuscript.
    Older opinions remain stored but are not silently treated as current findings.
    """
    by_id = {c["id"]: c for c in chapters}
    by_position = {c["position"]: c for c in chapters}
    found = {}
    historical = 0

    def append(finding, chapter, source):
        if finding.get("severity") != "blocker":
            return
        quote, reason = finding.get("quote", ""), finding.get("reason", "")
        if not quote or quote not in chapter["content"] or not reason:
            return
        key = hashlib.sha256(dumps([chapter["id"], chapter["revision"], quote, reason]).encode()).hexdigest()[
            :24
        ]
        if key not in found:
            found[key] = {
                "id": key,
                "chapter_id": chapter["id"],
                "position": chapter["position"],
                "title": chapter["title"],
                "revision": chapter["revision"],
                "quote": quote,
                "reason": reason,
                "repair": finding.get("repair", ""),
                "sources": [],
            }
        found[key]["sources"].append(source)

    for artifact in _rows(
        conn, "SELECT * FROM artifacts WHERE branch_id=? AND kind IN ('review','chapter_draft')", (branch_id,)
    ):
        chapter = by_id.get(artifact["chapter_id"])
        if not chapter:
            continue
        payload, basis = json.loads(artifact["payload"]), json.loads(artifact["basis"])
        report = payload if artifact["kind"] == "review" else payload.get("review", {})
        if not isinstance(report, dict) or not any(
            f.get("severity") == "blocker" for f in report.get("findings", [])
        ):
            continue
        current = artifact["base_revision"] == chapter["revision"]
        if artifact["kind"] == "review":
            current = current and basis.get("story_revision", story_revision) == story_revision
        else:
            current = current and payload.get("text") == chapter["content"]
            if artifact["status"] == "accepted" and payload.get("text") == chapter["content"]:
                receipts = _rows(conn, "SELECT result FROM receipts WHERE artifact_id=?", (artifact["id"],))
                current = any(
                    json.loads(r["result"]).get("revision") == chapter["revision"] for r in receipts
                )
            elif current:
                current = basis.get("story_revision", story_revision) == story_revision
        if not current:
            historical += 1
            continue
        for finding in report.get("findings", []):
            append(finding, chapter, {"kind": artifact["kind"], "artifact_id": artifact["id"]})

    for task in _rows(
        conn, "SELECT id,result FROM tasks WHERE branch_id=? AND kind='workflow'", (branch_id,)
    ):
        result = json.loads(task["result"])
        reports = [
            result.get("completion", {}),
            *result.get("volume_reports", []),
            *result.get("aggregate_reports", []),
        ]
        for report in reports:
            if not isinstance(report, dict) or not any(
                f.get("severity") == "blocker" for f in report.get("findings", [])
            ):
                continue
            versions = report.get("source_versions", [])
            current = result.get("expected_story_revision") == story_revision and all(
                s.get("id") in by_id and by_id[s["id"]]["revision"] == s.get("revision") for s in versions
            )
            if not current:
                historical += 1
                continue
            for finding in report.get("findings", []):
                chapter = by_position.get(finding.get("chapter"))
                if chapter:
                    append(
                        finding,
                        chapter,
                        {
                            "kind": "workflow_report",
                            "task_id": task["id"],
                            "scope": report.get("coverage", ""),
                        },
                    )
    return list(found.values()), historical


def _state(conn, branch_id, story_revision):
    chapters = _rows(
        conn, "SELECT * FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position,id", (branch_id,)
    )
    tasks = _rows(
        conn,
        "SELECT payload FROM tasks WHERE branch_id=? AND kind='workflow' ORDER BY created DESC LIMIT 1",
        (branch_id,),
    )
    spec = json.loads(tasks[0]["payload"]) if tasks else {}
    expected = spec.get("chapters", max((c["position"] for c in chapters), default=0))
    positions = [c["position"] for c in chapters]
    blockers = []
    if not chapters:
        blockers.append({"id": "no_chapters", "message": "尚无正文章节，不能确认定稿。"})
    if positions != list(range(1, expected + 1)):
        blockers.append(
            {
                "id": "chapter_structure",
                "message": f"章目录与连续1—{expected}章不一致，存在缺章、重复序号或计划范围差异。",
            }
        )
    blank = [
        c["position"]
        for c in chapters
        if not any(not ch.isspace() and unicodedata.category(ch)[0] != "C" for ch in c["content"])
    ]
    stale = [c["position"] for c in chapters if c["stale"]]
    if blank:
        blockers.append(
            {
                "id": "blank_chapters",
                "message": "以下章节为空白或只有不可见字符：" + "、".join(map(str, blank)),
            }
        )
    if stale:
        blockers.append(
            {
                "id": "stale_chapters",
                "message": "以下章节受前文变更影响，尚待复检：" + "、".join(map(str, stale)),
            }
        )
    if "min_chars" in spec and "max_chars" in spec:
        wrong_length = [
            c["position"]
            for c in chapters
            if not spec["min_chars"] <= sum(not ch.isspace() for ch in c["content"]) <= spec["max_chars"]
        ]
        if wrong_length:
            blockers.append(
                {
                    "id": "chapter_length",
                    "message": f"以下章节未满足已配置的{spec['min_chars']}—{spec['max_chars']}非空白字符："
                    + "、".join(map(str, wrong_length)),
                }
            )
    issues, historical = _known_issues(conn, branch_id, story_revision, chapters)
    records = _rows(
        conn,
        "SELECT * FROM assessments WHERE branch_id=? AND category='author_finalization' ORDER BY created DESC,id DESC LIMIT 1",
        (branch_id,),
    )
    record = records[0] if records else None
    payload = json.loads(record["minutes"]) if record else {}
    manifest = _manifest(chapters)
    matches = bool(
        record and record["story_revision"] == story_revision and payload.get("chapter_manifest") == manifest
    )
    dispositions = {d["issue_id"]: d for d in payload.get("dispositions", [])} if matches else {}
    unresolved = [i["id"] for i in issues if i["id"] not in dispositions]
    finalized = bool(matches and not blockers and not unresolved and record["result"] == "passed")
    status = (
        "finalized"
        if finalized
        else "outdated"
        if record and not matches
        else "blocked"
        if blockers or unresolved
        else "unconfirmed"
    )
    return {
        "branch_id": branch_id,
        "base_story_revision": story_revision,
        "author_finalized": finalized,
        "status": status,
        "chapter_count": len(chapters),
        "expected_chapters": expected,
        "deterministic_blockers": blockers,
        "known_issues": issues,
        "unresolved_issue_ids": unresolved,
        "historical_reports_not_current": historical,
        "record": {
            "id": record["id"],
            "story_revision": record["story_revision"],
            "note": record["note"],
            "created": record["created"],
            "checklist": payload.get("checklist", {}),
            "dispositions": payload.get("dispositions", []),
        }
        if record
        else None,
        "chapter_manifest": manifest,
        "manifest_sha256": hashlib.sha256(dumps(manifest).encode()).hexdigest(),
        "note": "作者定稿是对本版本的人工判断，独立于模型生产完成和工程／创作／收益三层验收；不调用模型、不修改正文。",
    }


def finalization_status(db: Database, branch_id: str, story_revision: int, chapters=None):
    # Use the database snapshot, rather than a caller's earlier chapter list.
    with db.connect() as conn:
        conn.execute("BEGIN")
        row = conn.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not row:
            raise DomainError("not_found", "工作版本不存在。", 404)
        return _state(conn, branch_id, row[0])


def confirm_finalization(domain: Domain, branch_id: str, data: ManualFinalizationInput):
    key = hashlib.sha256(
        ("author-finalization:" + branch_id + ":" + data.idempotency_key).encode()
    ).hexdigest()[:32]
    with domain.db.connect(True) as conn:
        branch = conn.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not branch:
            raise DomainError("not_found", "工作版本不存在。", 404)
        previous = conn.execute("SELECT * FROM assessments WHERE id=?", (key,)).fetchone()
        if previous:
            if (
                previous["category"] != "author_finalization"
                or json.loads(previous["minutes"]).get("request") != data.model_dump()
            ):
                raise DomainError("idempotency", "这个确认标识已用于不同定稿内容。", 409)
            return {**_state(conn, branch_id, branch[0]), "replayed": True}
        if branch[0] != data.base_story_revision:
            raise DomainError("story_changed", "正文或设定已变化，请刷新后重新审阅定稿范围。", 409)
        state = _state(conn, branch_id, branch[0])
        if data.base_manifest_sha256 != state["manifest_sha256"]:
            raise DomainError("manuscript_changed", "章目录或正文与刚才审阅的范围不同，请刷新后再确认。", 409)
        if state["deterministic_blockers"]:
            raise DomainError(
                "finalization_blocked", "存在缺章、空白、失效或篇幅等确定性问题，须修复后再确认。", 409
            )
        if not all(data.checklist.model_dump().values()):
            raise DomainError("finalization_checklist", "请先逐项确认通读、主冲突、人物去向和承诺处理。")
        supplied = {d.issue_id: d for d in data.dispositions}
        if len(supplied) != len(data.dispositions):
            raise DomainError("finalization_disposition", "同一问题不能重复裁定。")
        current_ids = {i["id"] for i in state["known_issues"]}
        if set(supplied) != current_ids:
            raise DomainError(
                "finalization_issues", "当前版本的严重意见尚未逐项裁定，或问题清单已改变；请刷新核对。", 409
            )
        payload = {
            "format_version": 1,
            "request": data.model_dump(),
            "checklist": data.checklist.model_dump(),
            "dispositions": [d.model_dump() for d in data.dispositions],
            "chapter_manifest": state["chapter_manifest"],
            "known_issue_ids": sorted(current_ids),
        }
        conn.execute(
            "INSERT INTO assessments VALUES(?,?,?,?,?,?,?,?)",
            (key, branch_id, branch[0], "author_finalization", "passed", data.note, dumps(payload), time()),
        )
        return {**_state(conn, branch_id, branch[0]), "replayed": False}


def register_routes(app: FastAPI, domain: Domain):
    @app.get("/api/branches/{branch_id}/finalization")
    def author_finalization(branch_id: str):
        branch = domain.branch(branch_id)
        return finalization_status(domain.db, branch_id, branch["story_revision"])

    @app.post("/api/branches/{branch_id}/finalization", status_code=201)
    def confirm_author_finalization(branch_id: str, data: ManualFinalizationInput):
        return confirm_finalization(domain, branch_id, data)
