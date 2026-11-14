"""Adopt a book idea without discarding the author's existing commitments."""

from copy import deepcopy
import hashlib
import json
from time import time

from pydantic import Field

from .author_contract import assemble_contract
from .db import DomainError, dumps, uid
from .models import Input


class IdeaAdoptInput(Input):
    text: str = Field(min_length=1, max_length=6000)
    author_idea: str = Field(min_length=1, max_length=6000)
    source_candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    candidate_revision: int = Field(default=0, ge=0)
    base_story_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


def adopt_idea(domain, branch_id, data):
    if not data.text.strip() or not data.author_idea.strip():
        raise DomainError("idea_empty", "请先写下想法或构思方案。")
    fingerprint = hashlib.sha256(dumps([branch_id, data.model_dump()]).encode()).hexdigest()
    with domain.db.connect(True) as tx:
        prior = tx.execute("SELECT result FROM receipts WHERE key=?", (data.idempotency_key,)).fetchone()
        if prior:
            result = json.loads(prior[0])
            if result.get("idea_fingerprint") != fingerprint:
                raise DomainError("idempotency", "采用标识已用于其他构思，请重新提交。", 409)
            return result
        branch = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not branch:
            raise DomainError("not_found", "工作版本不存在。", 404)
        if branch["story_revision"] != data.base_story_revision:
            raise DomainError("story_changed", "本书设定已更新，输入保留；请重新核对当前约定后再采用。", 409)
        source = None
        if data.source_candidate_id:
            source = tx.execute(
                "SELECT a.*,t.payload AS request FROM artifacts a LEFT JOIN tasks t ON t.id=a.task_id "
                "WHERE a.id=?",
                (data.source_candidate_id,),
            ).fetchone()
            if (
                not source
                or source["branch_id"] != branch_id
                or source["chapter_id"] is not None
                or source["kind"] != "advice"
                or source["status"] == "rejected"
                or json.loads(source["request"] or "{}").get("action") != "ideation.explore"
            ):
                raise DomainError("idea_source", "请使用当前工作版本的小说构思候选。", 409)
            if json.loads(source["payload"]).get("_candidate_revision", 0) != data.candidate_revision:
                raise DomainError(
                    "candidate_conflict", "构思候选已在别处修改，本地编辑保留；请重新载入并核对。", 409
                )
        current = tx.execute(
            "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind='intent' "
            "AND chapter_id IS NULL AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
            (branch_id,),
        ).fetchone()
        payload = deepcopy(json.loads(current["payload"])) if current else {}
        fields = assemble_contract(payload)["intent_contract"]
        fields["direction"] = data.text.strip()
        payload.setdefault("text", data.author_idea)
        payload["intent_contract"] = fields
        provenance = deepcopy(payload.get("provenance", {}))
        provenance.update(
            origin="author_adopted_idea",
            previous_intent_id=current["id"] if current else None,
            source_candidate_id=data.source_candidate_id,
            source_candidate_revision=data.candidate_revision if source else None,
            author_idea=data.author_idea,
        )
        provenance.setdefault("field_sources", {})["direction"] = {
            "origin": "author_edited" if source else "author_provided",
            "source_candidate_id": data.source_candidate_id,
        }
        payload["provenance"] = provenance
        artifact_id = uid()
        tx.execute(
            "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
            "VALUES(?,?,?,?,?,?,'accepted',?)",
            (
                artifact_id,
                branch["novel_id"],
                branch_id,
                "intent",
                dumps(payload),
                dumps({"story_revision": data.base_story_revision, "ideation_version": "1.0.0"}),
                time(),
            ),
        )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        result = {"artifact_id": artifact_id, "idea_fingerprint": fingerprint}
        tx.execute("INSERT INTO receipts VALUES(?,?,?)", (data.idempotency_key, artifact_id, dumps(result)))
    return result


def register_routes(app, domain):
    @app.post("/api/branches/{branch_id}/ideas/adopt")
    def adopt(branch_id: str, data: IdeaAdoptInput):
        return adopt_idea(domain, branch_id, data)
