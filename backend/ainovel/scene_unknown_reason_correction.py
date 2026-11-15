"""Explicit correction of an unknown-order explanation, never a time decision."""

from copy import deepcopy
import json
from time import time
from typing import Annotated, Literal

from fastapi import Path
from pydantic import Field, StrictInt, field_validator

from .db import DomainError, dumps
from .scene_order import payload_sha
from .scene_reconfirmation import PAYLOAD_HASH_VERSION, raw_sha, owned_scene
from .story_graph import Source, Strict, graph_snapshot

VERSION = "scene-unknown-reason-correction/1"
AUTHORITY = "explicit_correction_not_model_or_literary_review"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def text_valid(value):
    if not value.strip():
        raise ValueError("请填写非空理由、原文和提交标识。")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("文本含不完整Unicode字符。") from error
    return value


class CorrectionEvidence(Source):
    revision: StrictInt = Field(ge=0)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    content_sha256: Hash

    @field_validator("quote", "chapter_id")
    @classmethod
    def valid_quote(cls, value):
        return text_valid(value)


class UnknownReasonCorrectionInput(Strict):
    version: Literal["scene-unknown-reason-correction/1"]
    client_request_id: str = Field(min_length=8, max_length=128)
    actor: Literal["author", "authorized_operator"]
    base_payload_sha256: Hash
    source_revision: StrictInt = Field(ge=0)
    content_sha256: Hash
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[CorrectionEvidence] = Field(min_length=1, max_length=30)

    @field_validator("reason", "client_request_id")
    @classmethod
    def valid_text(cls, value):
        return text_valid(value)


class UnknownReasonCorrected(Strict):
    version: Literal["scene-unknown-reason-correction/1"] = VERSION
    authority: Literal["explicit_correction_not_model_or_literary_review"] = AUTHORITY
    actor: Literal["author", "authorized_operator"]
    scene_id: str
    client_request_id: str
    request_sha256: Hash
    reason: str
    previous_reason: str
    original_reason: str
    previous_scene_revision: int
    scene_revision: int
    base_story_revision: int
    story_revision: int
    source_revision: int
    content_sha256: Hash
    previous_payload_sha256: Hash
    previous_raw_json_sha256: Hash
    evidence: list[CorrectionEvidence]
    corrected_at: float
    story_time_inferred: Literal[False] = False
    model_review_performed: Literal[False] = False
    literary_review_performed: Literal[False] = False


def check_scope(tx, branch_id, chapter_id, row):
    from .domain import utf16_slice
    from .scene_time import SceneTime, event_basis_valid

    chapter = tx.execute(
        "SELECT * FROM chapters WHERE id=? AND branch_id=?", (chapter_id, branch_id)
    ).fetchone()
    p = SceneTime.model_validate_json(row["payload"]).model_dump()
    if row["archived"] or not chapter or chapter["archived"]:
        raise DomainError("scene_reason_scope", "仅更正未归档的已有提取场景未知原因。", 409)
    if p["source_revision"] != chapter["revision"] or chapter["stale"]:
        raise DomainError("story_changed", "正文来源已改变或待复核，未更正旧场景。", 409)
    audit = p.get("order_basis")
    if (
        not audit
        or audit["kind"] != "unknown"
        or p["story_order"] is not None
        or p.get("event_id") is not None
        or p.get("event_revision") is not None
        or p.get("author_reconfirmed") is not None
    ):
        raise DomainError("scene_reason_scope", "仅更正unknown原因，不能建立、升级或刷新已知时点。", 409)
    quote = utf16_slice(chapter["content"], p["start"], p["end"])[1]
    indexed = {r["id"]: r for r in graph_snapshot(tx, branch_id)["records"]}
    if not event_basis_valid(tx, p, chapter_id, indexed):
        raise DomainError("scene_time_evidence", "事件或原文依据已失效，不能借改理由刷新引用。", 409)
    if p["audience"] == "character" and not p["character_id"]:
        raise DomainError("scene_viewpoint", "人物视角缺少原人物身份。", 409)
    if p["character_id"]:
        identity = indexed.get(p["character_id"])
        if (
            not identity
            or identity["kind"] != "character"
            or identity["archived"]
            or identity["invalid_reasons"]
        ):
            raise DomainError("scene_viewpoint", "原视角身份已失效，未更正场景理由。", 409)
    return chapter, p, quote


def preview(domain, branch_id, chapter_id, identifier):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        row = owned_scene(tx, branch_id, chapter_id, identifier)
        chapter, p, quote = check_scope(tx, branch_id, chapter_id, row)
        story_revision = tx.execute(
            "SELECT story_revision FROM branches WHERE id=?", (branch_id,)
        ).fetchone()[0]
        return {
            "version": VERSION,
            "authority": AUTHORITY,
            "scene_id": identifier,
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
                "content_sha256": raw_sha(chapter["content"]),
            },
            "request_basis": {
                "base_story_revision": story_revision,
                "base_revision": row["revision"],
                "base_payload_sha256": payload_sha(p),
                "source_revision": chapter["revision"],
                "content_sha256": raw_sha(chapter["content"]),
            },
        }


def receipt(domain, branch_id, chapter_id, identifier, key):
    with domain.db.connect() as tx:
        owned_scene(tx, branch_id, chapter_id, identifier)
        row = tx.execute(
            "SELECT scene_id,result FROM scene_unknown_reason_correction_receipts WHERE key=?", (key,)
        ).fetchone()
        if row and row["scene_id"] != identifier:
            raise DomainError("idempotency", "该更正标识属于另一场景。", 409)
        return {
            "scene_id": identifier,
            "client_request_id": key,
            "receipt": json.loads(row["result"]) if row else None,
        }


def correct(domain, branch_id, chapter_id, data, identifier):
    from .domain import utf16_slice
    from .scene_time import SceneTime, scenes

    if identifier is None or data.archived:
        raise DomainError("scene_reason_scope", "更正理由不能新建或归档场景。", 409)
    correction = data.unknown_reason_correction
    try:
        request_sha = payload_sha(
            {
                "branch_id": branch_id,
                "chapter_id": chapter_id,
                "scene_id": identifier,
                "request": data.model_dump(),
            }
        )
    except UnicodeError as error:
        raise DomainError("scene_reason_evidence", "更正输入含不完整Unicode字符。", 422) from error
    with domain.db.connect(True) as tx:
        old = owned_scene(tx, branch_id, chapter_id, identifier)
        prior = tx.execute(
            "SELECT * FROM scene_unknown_reason_correction_receipts WHERE key=?",
            (correction.client_request_id,),
        ).fetchone()
        if prior:
            if prior["scene_id"] != identifier or prior["request_sha256"] != request_sha:
                raise DomainError("idempotency", "更正标识的目标或请求不同，未重复保存。", 409)
        else:
            chapter, previous, _ = check_scope(tx, branch_id, chapter_id, old)
            branch = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
            if (
                old["revision"] != data.base_revision
                or branch != data.base_story_revision
                or payload_sha(previous) != correction.base_payload_sha256
            ):
                raise DomainError("scene_changed", "场景或故事版本已改变，请重新读取更正依据。", 409)
            if (
                correction.source_revision != chapter["revision"]
                or raw_sha(chapter["content"]) != correction.content_sha256
            ):
                raise DomainError("story_changed", "完整正文版本或SHA已改变，未更正旧场景。", 409)
            submitted = data.scene.model_dump()
            for key in ("unknown_reason_corrected", "author_reconfirmed", "order_basis"):
                if key not in data.scene.model_fields_set and key in previous:
                    submitted[key] = deepcopy(previous[key])
            if submitted != previous:
                raise DomainError(
                    "scene_reason_mixed_edit",
                    "更正原因须保持场景所有原字段；不能混改范围、视角、引用或时点。",
                    409,
                )
            anchored = False
            for evidence in correction.evidence:
                source = tx.execute(
                    "SELECT * FROM chapters WHERE id=? AND branch_id=? AND archived=0",
                    (evidence.chapter_id, branch_id),
                ).fetchone()
                if not source:
                    raise DomainError("ownership", "更正依据不属于本工作版本保存稿。", 409)
                if (
                    source["revision"] != evidence.revision
                    or source["stale"]
                    or raw_sha(source["content"]) != evidence.content_sha256
                ):
                    raise DomainError("story_changed", "引用的完整正文版本或SHA已改变。", 409)
                if (
                    evidence.end <= evidence.start
                    or utf16_slice(source["content"], evidence.start, evidence.end)[1] != evidence.quote
                ):
                    raise DomainError("scene_reason_evidence", "依据须逐字对应保存稿的UTF16连续原文。", 409)
                anchored = anchored or (
                    evidence.chapter_id == chapter_id
                    and previous["start"] <= evidence.start < evidence.end <= previous["end"]
                )
            if not anchored:
                raise DomainError("scene_reason_evidence", "至少一条依据须来自原场景冻结范围。", 409)
            p = deepcopy(json.loads(old["payload"]))
            now = time()
            proof = UnknownReasonCorrected(
                actor=correction.actor,
                scene_id=identifier,
                client_request_id=correction.client_request_id,
                request_sha256=request_sha,
                reason=correction.reason,
                previous_reason=previous["order_basis"]["reason"],
                original_reason=previous.get("unknown_reason_corrected", {}).get(
                    "original_reason", previous["order_basis"]["reason"]
                ),
                previous_scene_revision=old["revision"],
                scene_revision=old["revision"] + 1,
                base_story_revision=branch,
                story_revision=branch + 1,
                source_revision=chapter["revision"],
                content_sha256=correction.content_sha256,
                previous_payload_sha256=payload_sha(previous),
                previous_raw_json_sha256=raw_sha(old["payload"]),
                evidence=correction.evidence,
                corrected_at=now,
            ).model_dump()
            p["order_basis"]["reason"] = correction.reason
            p["unknown_reason_corrected"] = proof
            public = SceneTime.model_validate(p).model_dump()
            stored = dumps(p)
            result = {**proof, "payload_sha256": payload_sha(public), "raw_json_sha256": raw_sha(stored)}
            tx.execute(
                "UPDATE scene_times SET revision=?,payload=? WHERE id=?",
                (old["revision"] + 1, stored, identifier),
            )
            tx.execute(
                "INSERT INTO scene_time_history VALUES(?,?,?,?,?)",
                (identifier, old["revision"] + 1, stored, 0, now),
            )
            tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
            tx.execute(
                "INSERT INTO scene_unknown_reason_correction_receipts VALUES(?,?,?,?,?)",
                (correction.client_request_id, identifier, request_sha, dumps(result), now),
            )
    return scenes(domain, branch_id, chapter_id)


def register_routes(app, domain):
    @app.get(
        "/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/unknown-reason-correction-preview"
    )
    def read_preview(branch_id: str, chapter_id: str, identifier: str):
        return preview(domain, branch_id, chapter_id, identifier)

    @app.get(
        "/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/unknown-reason-corrections/{key}"
    )
    def read_receipt(
        branch_id: str,
        chapter_id: str,
        identifier: str,
        key: Annotated[str, Path(min_length=8, max_length=128)],
    ):
        return receipt(domain, branch_id, chapter_id, identifier, key)


def model_context(context):
    """Only current scene bindings: history/evidence stays local, not story knowledge."""
    if not isinstance(context, dict):
        return context

    def bindings(value):
        longform = value.get("longform")
        if isinstance(longform, dict) and isinstance(longform.get("scene_binding"), dict):
            yield longform["scene_binding"]
        bundle = value.get("chapter_scenes")
        if isinstance(bundle, dict) and isinstance(bundle.get("scenes"), list):
            for scene in bundle["scenes"]:
                if isinstance(scene, dict) and isinstance(scene.get("binding"), dict):
                    yield scene["binding"]

    if not any(
        isinstance(b.get("payload"), dict) and b["payload"].get("unknown_reason_corrected") is not None
        for b in bindings(context)
    ):
        return context
    result = deepcopy(context)
    for binding in bindings(result):
        payload = binding.get("payload")
        if not isinstance(payload, dict) or payload.get("unknown_reason_corrected") is None:
            continue
        try:
            proof = UnknownReasonCorrected.model_validate(payload["unknown_reason_corrected"])
        except ValueError as error:
            raise DomainError(
                "scene_reason_receipt_readonly", "场景更正来源无法确认，未发送模型。", 409
            ) from error
        if (
            proof.scene_id != binding.get("id")
            or payload.get("story_order") is not None
            or payload.get("order_basis", {}).get("kind") != "unknown"
        ):
            raise DomainError(
                "scene_reason_receipt_readonly", "场景更正身份或未知范围不一致，未发送模型。", 409
            )
        payload.pop("unknown_reason_corrected")
        payload["unknown_reason_correction_notice"] = {
            "version": VERSION,
            "authority": AUTHORITY,
            "actor": proof.actor,
            "still_unknown": True,
            "story_time_inferred": False,
            "model_review_performed": False,
            "literary_review_performed": False,
        }
    return result
