"""Author-confirmed project briefs and their immutable generation basis."""

import hashlib
import json
from time import time
from typing import Annotated, Literal

from fastapi import Query
from pydantic import Field, StringConstraints, field_validator

from .db import DomainError, dumps, uid
from .genre_catalog import get_catalog
from .ideation_structure import Detail
from .models import Input

KIND = "project_brief"
DRAFT_KIND = "project_brief_draft"
IdeaText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ProjectBrief(Input):
    position: Detail
    track: Detail
    genre_ids: list[Identifier] = Field(min_length=1, max_length=3)
    audience: Detail
    distinction: Detail
    length: Detail
    cadence: str = Field(default="", max_length=700)
    ending: Detail
    final_image: str = Field(default="", max_length=700)

    @field_validator("genre_ids")
    @classmethod
    def valid_genres(cls, identifiers):
        catalogue = {entry["id"] for entry in get_catalog()["genres"]}
        if len(set(identifiers)) != len(identifiers) or not set(identifiers) <= catalogue:
            raise ValueError("请选择互不重复的现有题材，主类型放在第一位。")
        return identifiers


class ConfirmProject(Input):
    base_revision: int = Field(ge=0, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=128)
    idea: IdeaText
    brief: ProjectBrief
    candidate_id: Identifier | None = None
    impact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ConfirmedProject(Input):
    id: Identifier
    revision: int
    idea: IdeaText
    brief: ProjectBrief
    candidate_id: Identifier | None


class ProjectImpactTarget(Input):
    kind: Literal["settings", "settings_draft", "outline", "outline_draft", "inspection", "completion", "run"]
    id: Identifier
    revision: int | None = None
    scope: Literal["chapter", "volume", "book"] | None = None
    title: str | None = None
    status: str | None = None


class ProjectRevisionImpact(Input):
    project_id: Identifier | None
    base_revision: int
    basis_sha256: str
    targets: list[ProjectImpactTarget]


def confirmed_project_tx(tx, branch_id, identifier=None):
    row = tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY rowid DESC LIMIT 1",
        (branch_id, KIND),
    ).fetchone()
    if identifier is not None and (not row or row["id"] != identifier):
        raise DomainError("project_brief_changed", "已确认立项已更新，请读取最新修订。", 409)
    return {**json.loads(row["payload"]), "id": row["id"]} if row else None


def _revision_impact(tx, branch_id, project):
    from .book_completion import KIND as COMPLETION
    from .conception import BUNDLE, CONFIRMATION, revision_tx
    from .seven_stage_run import KIND as SEVEN_STAGE
    from .workflow_inspection import KIND as INSPECTION

    project_id = project["id"] if project else None
    revision = project["revision"] if project else 0
    targets = []
    if project_id:
        settings = tx.execute(
            "SELECT id,json_extract(payload,'$.revision') AS revision,"
            "json_extract(payload,'$.project_confirmation_id') AS project_id FROM artifacts "
            "WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY created DESC,rowid DESC LIMIT 1",
            (branch_id, CONFIRMATION),
        ).fetchone()
        if settings and settings["project_id"] == project_id:
            targets.append({"kind": "settings", "id": settings["id"], "revision": settings["revision"]})
        settings_drafts = tx.execute(
            "SELECT a.id FROM artifacts a JOIN branches b ON b.id=a.branch_id "
            "JOIN tasks t ON t.id=a.task_id AND json_extract(t.result,'$.artifact_id')=a.id "
            "WHERE a.branch_id=? AND a.kind=? AND a.status='candidate' "
            "AND json_extract(a.basis,'$.project_confirmation_id')=? "
            "AND json_extract(a.basis,'$.conception_revision')=? "
            "AND json_extract(a.basis,'$.story_revision')=b.story_revision ORDER BY a.created,a.id",
            (branch_id, BUNDLE, project_id, revision_tx(tx, branch_id)),
        )
        targets.extend({"kind": "settings_draft", "id": row["id"]} for row in settings_drafts)
        outlines = tx.execute(
            "SELECT id,revision,confirmed,json_extract(setting_basis,'$.project_id') AS project_id "
            "FROM outline_revisions WHERE branch_id=:branch AND revision IN ("
            "SELECT MAX(revision) FROM outline_revisions WHERE branch_id=:branch UNION "
            "SELECT MAX(revision) FROM outline_revisions WHERE branch_id=:branch AND confirmed=1) "
            "ORDER BY confirmed DESC,revision DESC",
            {"branch": branch_id},
        ).fetchall()
        for row in outlines:
            if row["project_id"] == project_id:
                targets.append(
                    {
                        "kind": "outline" if row["confirmed"] else "outline_draft",
                        "id": row["id"],
                        "revision": row["revision"],
                    }
                )
        outline_id = next((row["id"] for row in outlines if row["confirmed"]), None)
        inspections = tx.execute(
            """
            WITH reports AS (
              SELECT id,payload,json_extract(payload,'$.scope') AS scope,
                     json_extract(payload,'$.scope_id') AS scope_id,
                     ROW_NUMBER() OVER (
                       PARTITION BY json_extract(payload,'$.scope'),json_extract(payload,'$.scope_id')
                       ORDER BY rowid DESC
                     ) AS latest
              FROM artifacts WHERE branch_id=:branch AND kind=:kind AND status='accepted'
            ), volumes AS (
              SELECT json_extract(v.value,'$.id') AS id,json_extract(v.value,'$.title') AS title
              FROM outline_revisions o,json_each(o.payload,'$.volumes') v WHERE o.id=:outline
            )
            SELECT r.id,r.scope,COALESCE(c.title,v.title) AS title FROM reports r
            LEFT JOIN chapters c ON r.scope='chapter' AND c.id=r.scope_id
                                   AND c.branch_id=:branch AND c.archived=0
            LEFT JOIN volumes v ON r.scope='volume' AND v.id=r.scope_id
            WHERE r.latest=1 AND (r.scope='book' OR c.id IS NOT NULL OR v.id IS NOT NULL)
              AND EXISTS (
                SELECT 1 FROM json_each(r.payload,'$.items') i
                WHERE json_extract(i.value,'$.check_id')='01'
                  AND json_extract(i.value,'$.basis.project.id')=:project
              )
            ORDER BY r.scope,r.scope_id,r.id
            """,
            {"branch": branch_id, "kind": INSPECTION, "outline": outline_id, "project": project_id},
        )
        targets.extend({"kind": "inspection", **dict(row)} for row in inspections)
        completion = tx.execute(
            "SELECT id,json_extract(payload,'$.manifest.project_id') AS project_id FROM artifacts "
            "WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid DESC LIMIT 1",
            (branch_id, COMPLETION),
        ).fetchone()
        if completion and completion["project_id"] == project_id:
            targets.append({"kind": "completion", "id": completion["id"]})
        runs = tx.execute(
            "SELECT id,status FROM tasks WHERE branch_id=? AND kind=? "
            "AND status IN ('queued','running','waiting_child','paused','needs_review','blocked_runtime') "
            "AND json_extract(result,'$.seven_stage_run.project_id')=? ORDER BY id",
            (branch_id, SEVEN_STAGE, project_id),
        )
        targets.extend({"kind": "run", **dict(row)} for row in runs)
    return {
        "project_id": project_id,
        "base_revision": revision,
        "basis_sha256": hashlib.sha256(
            dumps([branch_id, project_id, revision, targets]).encode()
        ).hexdigest(),
        "targets": targets,
    }


def confirm(domain, branch_id, data, *, before_write=None):
    with domain.db.connect(True) as tx:
        return confirm_tx(tx, branch_id, data, before_write=before_write)


def confirm_tx(tx, branch_id, data, *, before_write=None, sync_source=None, archive_source_id=None):
    payload = data.model_dump(exclude={"impact_sha256"})
    fingerprint = hashlib.sha256(dumps([KIND, branch_id, payload]).encode()).hexdigest()
    if before_write is not None:
        before_write(tx)
    branch = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    prior = tx.execute(
        "SELECT artifact_id,result FROM receipts WHERE key=?", (data.idempotency_key,)
    ).fetchone()
    if prior:
        if json.loads(prior["result"]).get("project_brief") != fingerprint:
            raise DomainError("idempotency", "提交标识已用于其他立项内容。", 409)
        row = tx.execute("SELECT payload FROM artifacts WHERE id=?", (prior["artifact_id"],)).fetchone()
        return {**json.loads(row["payload"]), "id": prior["artifact_id"]}
    current = confirmed_project_tx(tx, branch_id)
    revision = current["revision"] if current else 0
    if revision != data.base_revision:
        raise DomainError("project_brief_changed", "立项已在其他窗口确认，请核对后再提交。", 409)
    if data.impact_sha256 and data.impact_sha256 != _revision_impact(tx, branch_id, current)["basis_sha256"]:
        raise DomainError("project_impact_changed", "立项修订影响已变化，请重新核对下游记录再确认。", 409)
    if data.candidate_id:
        candidate = tx.execute(
            "SELECT payload,basis FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
            (data.candidate_id, branch_id, DRAFT_KIND),
        ).fetchone()
        if not candidate:
            raise DomainError("project_brief_source", "立项草案不属于当前工作版本。", 409)
        basis = json.loads(candidate["basis"])
        if basis["story_revision"] != branch["story_revision"] or basis["project_revision"] != revision:
            raise DomainError("project_brief_changed", "这份 AI 立项草案的依据已更新，请重新生成。", 409)
        if json.loads(candidate["payload"])["idea"] != data.idea:
            raise DomainError("project_brief_source", "创作原话已有变化，请重新分析或按手动立项确认。", 409)
    identifier = uid()
    saved = {
        "revision": revision + 1,
        "idea": data.idea,
        "brief": payload["brief"],
        "candidate_id": data.candidate_id,
    }
    basis = {
        "previous_project_id": current["id"] if current else None,
        "story_revision": branch["story_revision"],
    }
    checkpoint = "确认立项"
    if sync_source:
        basis["sync_project_source"] = sync_source
        checkpoint = "同步采用立项"
    if archive_source_id:
        basis["manuscript_project_source"] = {"revision_id": archive_source_id}
        checkpoint = "从稿件包恢复立项"
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,?,'accepted',?)",
        (
            identifier,
            branch["novel_id"],
            branch_id,
            KIND,
            dumps(saved),
            dumps(basis),
            time(),
        ),
    )
    tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
    tx.execute(
        "INSERT INTO receipts VALUES(?,?,?)",
        (data.idempotency_key, identifier, dumps({"project_brief": fingerprint})),
    )
    from .work_snapshot import checkpoint_tx

    checkpoint_tx(tx, branch_id, checkpoint)
    return {**saved, "id": identifier}


def register_routes(app, domain):
    path = "/api/branches/{branch_id}/conception/project-brief"

    @app.get(path)
    def get_project(branch_id: str) -> ConfirmedProject | None:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            return confirmed_project_tx(tx, branch_id)

    @app.get(path + "/impact")
    def preview_project_impact(
        branch_id: str, base_revision: Annotated[int, Query(ge=0)]
    ) -> ProjectRevisionImpact:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            if not tx.execute("SELECT 1 FROM branches WHERE id=?", (branch_id,)).fetchone():
                raise DomainError("not_found", "工作版本不存在。", 404)
            project = confirmed_project_tx(tx, branch_id)
            if (project["revision"] if project else 0) != base_revision:
                raise DomainError("project_brief_changed", "立项已在其他窗口确认，请核对后再提交。", 409)
            return _revision_impact(tx, branch_id, project)

    @app.put(path)
    def confirm_project(branch_id: str, data: ConfirmProject) -> ConfirmedProject:
        return confirm(domain, branch_id, data)


async def generate(worker, frozen):
    catalogue = get_catalog()["genres"]
    contract = ProjectBrief.model_json_schema()
    contract["properties"]["genre_ids"]["items"]["enum"] = [entry["id"] for entry in catalogue]
    return await worker.call(
        {
            "action": "conception.generate.project",
            "作者创作思路": frozen["idea"],
            "可选题材": [{"id": entry["id"], "name": entry["name"]} for entry in catalogue],
            "要求": (
                "生成供作者编辑的立项建议。position 写核心定位，track 写赛道，genre_ids 首项为主类型，"
                "其余为辅类型。audience 写读者定位，distinction 写差异化，length 写篇幅目标，"
                "cadence 写更新计划或留空，ending 写终局，final_image 写最后一镜或留空。"
                "尊重作者原话、已采用创作约定和明确限制。尚无结局时提出一个可编辑的具体方案。"
            ),
        },
        schema=ProjectBrief,
        context={},
        max_output=4096,
        structure_repairs=1,
        contract=contract,
    )
