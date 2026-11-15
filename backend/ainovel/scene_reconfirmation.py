"""Explicit author reconfirmation of an existing extracted scene; no model execution."""

from copy import deepcopy
import hashlib
import json
from time import time
from typing import Annotated, Literal

from fastapi import Path
from pydantic import Field, StrictInt, field_validator

from .db import DomainError, dumps
from .scene_order import EventWitness, payload_sha
from .story_graph import Source, Strict, graph_snapshot, validate_record

VERSION = "scene-author-reconfirmation/1"
PAYLOAD_HASH_VERSION = "scene-time-public-payload/1"
Hash = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]


class AuthorReconfirmation(Strict):
    version: Literal["scene-author-reconfirmation/1"] = VERSION
    authority: Literal["author_confirmation_not_model_review"] = "author_confirmation_not_model_review"
    client_request_id: str = Field(min_length=8, max_length=128)
    request_sha256: Hash
    reason: str = Field(min_length=1, max_length=2000)
    confirmed_at: float
    source_revision: int = Field(ge=0)
    content_sha256: Hash
    previous_scene_revision: int = Field(ge=0)
    scene_revision: int = Field(ge=0)
    base_story_revision: int = Field(ge=0)
    story_revision: int = Field(ge=0)
    previous_payload_sha256: Hash
    previous_raw_json_sha256: Hash
    previous_story_time: str
    previous_refs: list[EventWitness] = Field(min_length=1, max_length=60)
    current_refs: list[EventWitness] = Field(min_length=1, max_length=60)
    time_evidence: list[Source] = Field(default_factory=list, max_length=30)


class CurrentEventWitness(EventWitness):
    event_revision: StrictInt = Field(ge=0)
    story_order: StrictInt
    record_sha256: Hash


class TimeEvidence(Source):
    revision: StrictInt = Field(ge=0)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)


class ReconfirmationInput(Strict):
    client_request_id: str = Field(min_length=8, max_length=128)
    base_story_revision: StrictInt = Field(ge=0)
    base_revision: StrictInt = Field(ge=0)
    base_payload_sha256: Hash
    source_revision: StrictInt = Field(ge=0)
    content_sha256: Hash
    reason: str = Field(min_length=1, max_length=2000)
    refs: list[CurrentEventWitness] = Field(min_length=1, max_length=60)
    story_time: str = Field(min_length=1, max_length=200)
    time_evidence: list[TimeEvidence] = Field(default_factory=list, max_length=30)

    @field_validator("reason", "story_time")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请明确填写人工确认理由和故事时间；未知可写未知。")
        return value


def raw_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def owned_scene(tx, branch_id, chapter_id, identifier):
    row = tx.execute(
        "SELECT s.* FROM scene_times s JOIN chapters c ON c.id=s.chapter_id "
        "WHERE s.id=? AND s.branch_id=? AND s.chapter_id=? AND c.branch_id=?",
        (identifier, branch_id, chapter_id, branch_id),
    ).fetchone()
    if not row:
        raise DomainError("ownership", "场景绑定不属于当前章节或工作版本。", 404)
    return row


def receipt(domain, branch_id, chapter_id, identifier, key):
    with domain.db.connect() as tx:
        owned_scene(tx, branch_id, chapter_id, identifier)
        row = tx.execute(
            "SELECT scene_id,result FROM scene_reconfirmation_receipts WHERE key=?", (key,)
        ).fetchone()
        if row and row["scene_id"] != identifier:
            raise DomainError("idempotency", "人工重新核对标识已用于另一场景。", 409)
        return {
            "scene_id": identifier,
            "client_request_id": key,
            "receipt": json.loads(row["result"]) if row else None,
        }


def preview(domain, branch_id, chapter_id, identifier):
    from .domain import utf16_slice
    from .scene_time import SceneTime, event_basis_valid

    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        row = owned_scene(tx, branch_id, chapter_id, identifier)
        p = SceneTime.model_validate_json(row["payload"]).model_dump()
        chapter = tx.execute(
            "SELECT * FROM chapters WHERE id=? AND branch_id=?", (chapter_id, branch_id)
        ).fetchone()
        branch = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
        if row["archived"] or not chapter or chapter["archived"]:
            raise DomainError("scene_reconfirmation_scope", "仅重新核对未归档的正式提取场景。", 409)
        if p["source_revision"] != chapter["revision"]:
            raise DomainError("story_changed", "正文来源已改变，不能按旧范围刷新事件引用。", 409)
        audit = p.get("order_basis")
        if (
            not audit
            or audit["kind"] != "event_refs"
            or not audit["refs"]
            or p.get("event_id") is not None
            or p.get("event_revision") is not None
        ):
            raise DomainError(
                "scene_reconfirmation_scope", "仅刷新已有明确事件引用；手工或未知顺序不升级。", 409
            )
        indexed = {r["id"]: r for r in graph_snapshot(tx, branch_id)["records"]}
        refs = []
        for old in audit["refs"]:
            event = indexed.get(old["event_id"])
            if (
                not event
                or event["kind"] != "event"
                or event["archived"]
                or event["invalid_reasons"]
                or event["payload"]["status"] != "actual"
                or event["payload"]["story_order"] != p["story_order"]
            ):
                raise DomainError("scene_time_evidence", "当前事件尚未有效重核，不能刷新场景引用。", 409)
            validate_record(tx, branch_id, event["id"], event["payload"])
            refs.append(
                {
                    **old,
                    "event_revision": event["revision"],
                    "story_order": event["payload"]["story_order"],
                    "record_sha256": payload_sha(event["payload"]),
                }
            )
        checked = deepcopy(p)
        checked["order_basis"]["refs"] = refs
        if not event_basis_valid(tx, checked, chapter_id, indexed):
            raise DomainError("scene_time_evidence", "原范围的实际事件覆盖和顺序无法核实。", 409)
        quote = utf16_slice(chapter["content"], p["start"], p["end"])[1]
        return {
            "version": VERSION,
            "authority": "author_confirmation_not_model_review",
            "scene_id": identifier,
            "payload_hash_version": PAYLOAD_HASH_VERSION,
            "scene": {
                **dict(row),
                "payload": p,
                "payload_sha256": payload_sha(p),
                "payload_hash_version": PAYLOAD_HASH_VERSION,
            },
            "source": {
                "chapter_id": chapter_id,
                "revision": chapter["revision"],
                "start": p["start"],
                "end": p["end"],
                "quote": quote,
            },
            "request_basis": {
                "base_story_revision": branch[0],
                "base_revision": row["revision"],
                "base_payload_sha256": payload_sha(p),
                "source_revision": chapter["revision"],
                "content_sha256": raw_sha(chapter["content"]),
                "refs": refs,
                "story_time": p["story_time"],
                "time_evidence": [],
            },
        }


def reconfirm(domain, branch_id, chapter_id, identifier, data):
    from .domain import utf16_slice
    from .scene_time import SceneTime, event_basis_valid

    request_sha = payload_sha(
        {
            "branch_id": branch_id,
            "chapter_id": chapter_id,
            "scene_id": identifier,
            "request": data.model_dump(),
        }
    )
    with domain.db.connect(True) as tx:
        old = owned_scene(tx, branch_id, chapter_id, identifier)
        prior = tx.execute(
            "SELECT * FROM scene_reconfirmation_receipts WHERE key=?", (data.client_request_id,)
        ).fetchone()
        if prior:
            if prior["scene_id"] != identifier or prior["request_sha256"] != request_sha:
                raise DomainError("idempotency", "人工重新核对标识的目标或请求不同，未重复写入。", 409)
            return json.loads(prior["result"])
        original_raw_payload = json.loads(old["payload"])
        p = SceneTime.model_validate(original_raw_payload).model_dump()
        chapter = tx.execute(
            "SELECT * FROM chapters WHERE id=? AND branch_id=?", (chapter_id, branch_id)
        ).fetchone()
        branch = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
        if old["archived"] or not chapter or chapter["archived"]:
            raise DomainError("scene_reconfirmation_scope", "仅重新核对未归档的正式提取场景。", 409)
        if (
            branch[0] != data.base_story_revision
            or old["revision"] != data.base_revision
            or payload_sha(p) != data.base_payload_sha256
        ):
            raise DomainError("scene_changed", "场景或故事版本已改变，请重新读取后核对。", 409)
        if (
            chapter["revision"] != data.source_revision
            or p["source_revision"] != data.source_revision
            or raw_sha(chapter["content"]) != data.content_sha256
        ):
            raise DomainError("story_changed", "正文来源版本或内容已改变，未重新核对旧场景。", 409)
        utf16_slice(chapter["content"], p["start"], p["end"])
        audit = p.get("order_basis")
        if (
            not audit
            or audit["kind"] != "event_refs"
            or not audit["refs"]
            or p.get("event_id") is not None
            or p.get("event_revision") is not None
        ):
            raise DomainError(
                "scene_reconfirmation_scope",
                "仅刷新已有明确事件引用；手工或未知顺序场景不借此改为已知。",
                409,
            )
        original_ids = [(r["ref"], r["event_id"]) for r in audit["refs"]]
        submitted = [r.model_dump() for r in data.refs]
        if original_ids != [(r["ref"], r["event_id"]) for r in submitted] or len(
            {r["event_id"] for r in submitted}
        ) != len(submitted):
            raise DomainError(
                "scene_reconfirmation_refs", "必须逐项保留原事件引用身份和顺序，不能增删或替换。", 409
            )
        indexed = {r["id"]: r for r in graph_snapshot(tx, branch_id)["records"]}
        for ref in submitted:
            event = indexed.get(ref["event_id"])
            if (
                not event
                or event["kind"] != "event"
                or event["archived"]
                or event["invalid_reasons"]
                or event["payload"]["status"] != "actual"
                or event["payload"]["story_order"] != p["story_order"]
            ):
                raise DomainError("scene_time_evidence", "引用必须是当前有效、实际发生且同顺序的事件。", 409)
            if (
                ref["event_revision"] != event["revision"]
                or ref["story_order"] != event["payload"]["story_order"]
                or ref["record_sha256"] != payload_sha(event["payload"])
            ):
                raise DomainError("scene_time_evidence", "事件当前修订或内容hash已改变，请重新核对。", 409)
            validate_record(tx, branch_id, event["id"], event["payload"])
        if data.story_time != p["story_time"] and not data.time_evidence:
            raise DomainError(
                "scene_time_evidence", "更改描述性时间需要原场景范围内的连续原文，并由作者明确确认。", 409
            )
        for evidence in data.time_evidence:
            if (
                evidence.chapter_id != chapter_id
                or evidence.revision != p["source_revision"]
                or not (p["start"] <= evidence.start < evidence.end <= p["end"])
                or utf16_slice(chapter["content"], evidence.start, evidence.end)[1] != evidence.quote
            ):
                raise DomainError("scene_time_evidence", "时间依据须逐字对应原冻结场景内的当前正文。", 409)
        if p["character_id"]:
            identity = indexed.get(p["character_id"])
            if (
                not identity
                or identity["kind"] != "character"
                or identity["archived"]
                or identity["invalid_reasons"]
            ):
                raise DomainError("scene_viewpoint", "原视角人物已失效，不能借此刷新场景。", 409)
        if p["audience"] == "character" and not p["character_id"]:
            raise DomainError("scene_viewpoint", "人物视角缺少原人物身份，未重新核对。", 409)
        previous = deepcopy(p)
        p["order_basis"]["refs"] = submitted
        p["story_time"] = data.story_time
        if not event_basis_valid(tx, p, chapter_id, indexed):
            raise DomainError("scene_time_evidence", "事件原文覆盖或同顺序范围无法核实，未放宽原校验。", 409)
        # A new conflicting active range must not become valid through this route.
        for other in tx.execute(
            "SELECT payload FROM scene_times WHERE chapter_id=? AND archived=0 AND id!=?",
            (chapter_id, identifier),
        ):
            q = json.loads(other["payload"])
            if q["source_revision"] == p["source_revision"] and max(p["start"], q["start"]) < min(
                p["end"], q["end"]
            ):
                raise DomainError("scene_overlap", "当前活动场景范围已重叠，请先核对原范围。", 409)
        now = time()
        p["author_reconfirmed"] = AuthorReconfirmation(
            client_request_id=data.client_request_id,
            request_sha256=request_sha,
            reason=data.reason,
            confirmed_at=now,
            source_revision=data.source_revision,
            content_sha256=data.content_sha256,
            previous_scene_revision=old["revision"],
            scene_revision=old["revision"] + 1,
            base_story_revision=branch[0],
            story_revision=branch[0] + 1,
            previous_payload_sha256=payload_sha(previous),
            previous_raw_json_sha256=raw_sha(old["payload"]),
            previous_story_time=previous["story_time"],
            previous_refs=previous["order_basis"]["refs"],
            current_refs=submitted,
            time_evidence=data.time_evidence,
        ).model_dump()
        p = SceneTime.model_validate(p).model_dump()
        revision = old["revision"] + 1
        # Frozen generation coverage is historical JSON, not the current refs.
        # Preserve absent optional keys too; the public hash is normalized separately.
        if "covered_events" in original_raw_payload["order_basis"]:
            p["order_basis"]["covered_events"] = deepcopy(
                original_raw_payload["order_basis"]["covered_events"]
            )
        else:
            p["order_basis"].pop("covered_events", None)
        public_payload = SceneTime.model_validate(p).model_dump()
        stored = dumps(p)
        result = {
            "version": VERSION,
            "authority": "author_confirmation_not_model_review",
            "scene_id": identifier,
            "client_request_id": data.client_request_id,
            "request_sha256": request_sha,
            "payload_hash_version": PAYLOAD_HASH_VERSION,
            "old_revision": old["revision"],
            "revision": revision,
            "old_payload_sha256": payload_sha(previous),
            "payload_sha256": payload_sha(public_payload),
            "old_raw_json_sha256": raw_sha(old["payload"]),
            "raw_json_sha256": raw_sha(stored),
            "base_story_revision": branch[0],
            "story_revision": branch[0] + 1,
            "source_revision": data.source_revision,
            "content_sha256": data.content_sha256,
            "previous_refs": previous["order_basis"]["refs"],
            "current_refs": submitted,
            "previous_story_time": previous["story_time"],
            "story_time": data.story_time,
            "time_evidence": [e.model_dump() for e in data.time_evidence],
            "reason": data.reason,
            "covered_events_unchanged": True,
        }
        tx.execute("UPDATE scene_times SET revision=?,payload=? WHERE id=?", (revision, stored, identifier))
        tx.execute("INSERT INTO scene_time_history VALUES(?,?,?,?,?)", (identifier, revision, stored, 0, now))
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        tx.execute(
            "INSERT INTO scene_reconfirmation_receipts VALUES(?,?,?,?,?)",
            (data.client_request_id, identifier, request_sha, dumps(result), now),
        )
        return result


def register_routes(app, domain):
    @app.get(
        "/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/reconfirmation-preview"
    )
    def read_preview(branch_id: str, chapter_id: str, identifier: str):
        return preview(domain, branch_id, chapter_id, identifier)

    @app.post("/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/reconfirm")
    def author_reconfirm(branch_id: str, chapter_id: str, identifier: str, data: ReconfirmationInput):
        return reconfirm(domain, branch_id, chapter_id, identifier, data)

    @app.get("/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/reconfirmations/{key}")
    def read_receipt(
        branch_id: str,
        chapter_id: str,
        identifier: str,
        key: Annotated[str, Path(min_length=8, max_length=128)],
    ):
        return receipt(domain, branch_id, chapter_id, identifier, key)
