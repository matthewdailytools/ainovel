"""Author-confirmed project briefs and their immutable generation basis."""

import hashlib
import json
from time import time
from typing import Annotated

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


class ConfirmedProject(Input):
    id: Identifier
    revision: int
    idea: IdeaText
    brief: ProjectBrief
    candidate_id: Identifier | None


def confirmed_project_tx(tx, branch_id, identifier=None):
    row = tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY rowid DESC LIMIT 1",
        (branch_id, KIND),
    ).fetchone()
    if identifier is not None and (not row or row["id"] != identifier):
        raise DomainError("project_brief_changed", "已确认立项已更新，请读取最新修订。", 409)
    return {**json.loads(row["payload"]), "id": row["id"]} if row else None


def confirm(domain, branch_id, data, *, before_write=None):
    payload = data.model_dump()
    fingerprint = hashlib.sha256(dumps([KIND, branch_id, payload]).encode()).hexdigest()
    with domain.db.connect(True) as tx:
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
                raise DomainError(
                    "project_brief_source", "创作原话已有变化，请重新分析或按手动立项确认。", 409
                )
        identifier = uid()
        saved = {
            "revision": revision + 1,
            "idea": data.idea,
            "brief": payload["brief"],
            "candidate_id": data.candidate_id,
        }
        tx.execute(
            "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
            "VALUES(?,?,?,?,?,?,'accepted',?)",
            (
                identifier,
                branch["novel_id"],
                branch_id,
                KIND,
                dumps(saved),
                dumps(
                    {
                        "previous_project_id": current["id"] if current else None,
                        "story_revision": branch["story_revision"],
                    }
                ),
                time(),
            ),
        )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        tx.execute(
            "INSERT INTO receipts VALUES(?,?,?)",
            (data.idempotency_key, identifier, dumps({"project_brief": fingerprint})),
        )
        from .work_snapshot import checkpoint_tx

        checkpoint_tx(tx, branch_id, "确认立项")
        return {**saved, "id": identifier}


def register_routes(app, domain):
    path = "/api/branches/{branch_id}/conception/project-brief"

    @app.get(path)
    def get_project(branch_id: str) -> ConfirmedProject | None:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            return confirmed_project_tx(tx, branch_id)

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
