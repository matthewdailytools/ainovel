"""Optional author planning brief and explicit transfer to the author contract."""

import hashlib
import json
from time import time
from typing import Literal

from pydantic import Field, field_validator

from .author_contract import assemble_contract
from .db import DomainError, dumps, uid
from .models import Input

KIND = "project_brief"
VERSION = "project-brief/1"
TransferField = Literal["direction", "ending"]


class BriefFields(Input):
    concept: str = Field(default="", max_length=6000)
    positioning: str = Field(default="", max_length=2000)
    audience: str = Field(default="", max_length=2000)
    scope: str = Field(default="", max_length=2000)
    direction: str = Field(default="", max_length=6000)
    ending: str = Field(default="", max_length=4000)
    last_image: str = Field(default="", max_length=2000)

    @field_validator("*")
    @classmethod
    def trim(cls, text: str):
        return text.strip()


class SaveBrief(Input):
    base_draft_id: str | None = Field(max_length=128)
    fields: BriefFields


class AdoptBrief(Input):
    draft_id: str = Field(min_length=1, max_length=128)
    base_adoption_id: str | None = Field(max_length=128)
    base_author_contract_id: str | None = Field(max_length=128)
    update_fields: list[TransferField] = Field(default_factory=list, max_length=2)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("update_fields")
    @classmethod
    def unique_fields(cls, fields):
        if len(fields) != len(set(fields)):
            raise ValueError("约定字段不能重复选择。")
        return fields


class BriefVersion(Input):
    id: str
    fields: BriefFields
    created: float
    draft_id: str | None = None
    intent_id: str | None = None
    previous_intent_id: str | None = None
    applied_fields: list[TransferField] = Field(default_factory=list)


class AuthorSummary(Input):
    id: str | None
    direction: str
    ending: str


class ReferencingTask(Input):
    id: str
    kind: str
    status: str


class BriefState(Input):
    draft: BriefVersion | None
    adopted: BriefVersion | None
    author_contract: AuthorSummary
    referencing_tasks: list[ReferencingTask]
    referencing_task_count: int


def load_branch(tx, branch_id):
    branch = tx.execute("SELECT id,novel_id,story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    return branch


def latest_intent(tx, branch_id):
    return tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND chapter_id IS NULL AND kind='intent' "
        "AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
        (branch_id,),
    ).fetchone()


def latest_brief(tx, branch_id, stage):
    row = tx.execute(
        "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND chapter_id IS NULL "
        "AND kind=? AND status='accepted' AND json_extract(payload,'$.stage')=? ORDER BY rowid DESC LIMIT 1",
        (branch_id, KIND, stage),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload"])
    if payload.get("version") != VERSION:
        raise DomainError("project_brief_version", "立项简报版本无法读取，旧资料已保留。", 409)
    return {
        "id": row["id"],
        "created": row["created"],
        "fields": payload["fields"],
        "draft_id": payload.get("draft_id"),
        "intent_id": payload.get("intent_id"),
        "previous_intent_id": payload.get("previous_intent_id"),
        "applied_fields": payload.get("applied_fields", []),
    }


def read_brief(tx, branch_id):
    load_branch(tx, branch_id)
    intent = latest_intent(tx, branch_id)
    fields = assemble_contract(json.loads(intent["payload"]) if intent else None)["intent_contract"]
    references, total = [], 0
    if intent:
        rows = tx.execute(
            "SELECT id,kind,status,COUNT(*) OVER() AS total FROM tasks WHERE branch_id=? "
            "AND json_extract(result,'$.author_contract_snapshot.source_artifact_id')=? "
            "ORDER BY created DESC,id DESC LIMIT 10",
            (branch_id, intent["id"]),
        ).fetchall()
        references = [{key: row[key] for key in ("id", "kind", "status")} for row in rows]
        total = rows[0]["total"] if rows else 0
    return {
        "draft": latest_brief(tx, branch_id, "draft"),
        "adopted": latest_brief(tx, branch_id, "adopted"),
        "author_contract": {
            "id": intent["id"] if intent else None,
            "direction": fields["direction"],
            "ending": fields["ending"],
        },
        "referencing_tasks": references,
        "referencing_task_count": total,
    }


def persist(tx, branch, identifier, kind, payload, basis):
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,?,'accepted',?)",
        (identifier, branch["novel_id"], branch["id"], kind, dumps(payload), dumps(basis), time()),
    )


def save_draft(tx, branch_id, data):
    branch = load_branch(tx, branch_id)
    draft = latest_brief(tx, branch_id, "draft")
    if (draft["id"] if draft else None) != data.base_draft_id:
        raise DomainError(
            "project_brief_changed", "已保存草稿在其他窗口更新，请重新读取后核对。本次输入仍保留。", 409
        )
    fields = data.fields.model_dump()
    if not any(fields.values()):
        raise DomainError("project_brief_empty", "请先填写至少一项立项内容。")
    if not draft or fields != draft["fields"]:
        persist(
            tx,
            branch,
            uid(),
            KIND,
            {"version": VERSION, "stage": "draft", "fields": fields},
            {"previous_draft_id": data.base_draft_id},
        )
    return read_brief(tx, branch_id)


def adopt_brief(tx, branch_id, data):
    branch = load_branch(tx, branch_id)
    fingerprint = hashlib.sha256(dumps([branch_id, data.model_dump()]).encode()).hexdigest()
    receipt = tx.execute("SELECT result FROM receipts WHERE key=?", (data.idempotency_key,)).fetchone()
    if receipt:
        prior = json.loads(receipt["result"])
        if prior.get("project_brief_fingerprint") != fingerprint:
            raise DomainError("idempotency", "立项确认标识已用于其他操作。", 409)
        return read_brief(tx, branch_id)
    draft = latest_brief(tx, branch_id, "draft")
    adopted = latest_brief(tx, branch_id, "adopted")
    if (
        not draft
        or draft["id"] != data.draft_id
        or (adopted["id"] if adopted else None) != data.base_adoption_id
    ):
        raise DomainError("project_brief_changed", "立项版本已变化，请重新读取后核对。", 409)
    current = latest_intent(tx, branch_id)
    intent_id = current["id"] if current else None
    if data.update_fields and data.base_author_contract_id != intent_id:
        raise DomainError(
            "author_contract_changed", "创作约定已在别处修改，请核对当前故事方向或终局后再确认。", 409
        )
    brief_id = uid()
    payload = json.loads(current["payload"]) if current else {}
    contract = assemble_contract(payload)["intent_contract"]
    updates = {}
    for name in data.update_fields:
        value = draft["fields"][name]
        if not value:
            raise DomainError("project_brief_empty", "同步到创作约定的字段必须填写内容。", 409)
        if contract[name] != value:
            updates[name] = value
    next_intent_id = intent_id
    if updates:
        contract.update(updates)
        payload.setdefault("text", draft["fields"]["concept"] or updates.get("direction", ""))
        payload["intent_contract"] = contract
        provenance = payload.setdefault("provenance", {})
        provenance.setdefault("origin", "author_project_brief")
        if "direction" in updates:
            provenance["origin"] = "author_project_brief"
        origins = provenance.setdefault("field_sources", {})
        for key in updates:
            origins[key] = {"origin": "author_project_brief", "source_brief_id": brief_id}
        provenance["previous_intent_id"] = intent_id
        next_intent_id = uid()
        persist(
            tx,
            branch,
            next_intent_id,
            "intent",
            payload,
            {"story_revision": branch["story_revision"], "source_brief_id": brief_id},
        )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
    persist(
        tx,
        branch,
        brief_id,
        KIND,
        {
            "version": VERSION,
            "stage": "adopted",
            "fields": draft["fields"],
            "draft_id": draft["id"],
            "intent_id": next_intent_id,
            "previous_intent_id": intent_id,
            "applied_fields": list(updates),
        },
        {"previous_adoption_id": data.base_adoption_id},
    )
    tx.execute(
        "INSERT INTO receipts VALUES(?,?,?)",
        (
            data.idempotency_key,
            brief_id,
            dumps({"project_brief_fingerprint": fingerprint, "brief_id": brief_id}),
        ),
    )
    return read_brief(tx, branch_id)


def register_routes(app, domain):
    path = "/api/branches/{branch_id}/project-brief"

    @app.get(path, response_model=BriefState)
    def get_project_brief(branch_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return read_brief(tx, branch_id)

    @app.put(path, response_model=BriefState)
    def update_project_brief(branch_id: str, data: SaveBrief):
        with domain.db.connect(True) as tx:
            return save_draft(tx, branch_id, data)

    @app.post(path + "/adopt", response_model=BriefState)
    def adopt_project_brief(branch_id: str, data: AdoptBrief):
        with domain.db.connect(True) as tx:
            return adopt_brief(tx, branch_id, data)
