"""Carry author-reviewed assistant text into a version-bound workbench inbox."""

from collections import Counter
from hashlib import sha256
import json
from time import time

from pydantic import Field, field_validator

from .assistant import Digest, Stage, TARGETS
from .assistant_context import _chapter, snapshot
from .db import DomainError, dumps, uid
from .models import Input
from .project_portability import Identifier

SCHEMA = """
CREATE TABLE IF NOT EXISTS novel_assistant_deliveries(
 id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),
 turn_id TEXT NOT NULL REFERENCES novel_assistant_turns(id),card_id TEXT NOT NULL,
 stage TEXT NOT NULL,scope_id TEXT,field TEXT NOT NULL,target_label TEXT NOT NULL,
 field_label TEXT NOT NULL,value TEXT NOT NULL,before TEXT NOT NULL,
 basis_sha256 TEXT NOT NULL,created REAL NOT NULL,
 prior_id TEXT REFERENCES novel_assistant_deliveries(id));
CREATE UNIQUE INDEX IF NOT EXISTS novel_assistant_delivery_target
 ON novel_assistant_deliveries(branch_id,turn_id,card_id,stage,COALESCE(scope_id,''),field,basis_sha256);
CREATE UNIQUE INDEX IF NOT EXISTS novel_assistant_delivery_prior
 ON novel_assistant_deliveries(prior_id);
CREATE INDEX IF NOT EXISTS novel_assistant_delivery_inbox
 ON novel_assistant_deliveries(branch_id,stage,created DESC);
"""

PROJECT_FIELDS = {
    "idea": "创作思路",
    "position": "核心定位",
    "track": "赛道",
    "audience": "读者定位",
    "distinction": "差异化",
    "length": "篇幅目标",
    "cadence": "更新计划",
    "ending": "终局",
    "final_image": "最后一镜",
}
OUTLINE_CONTAINER_FIELDS = {"title": "名称", "summary": "纲要"}
OUTLINE_CHAPTER_FIELDS = {"goal": "目标", "obstacle": "阻碍", "turn": "转折", "result": "结果"}
COMPLETION_FIELDS = {
    "intro": "作品简介",
    "ending": "终局兑现说明",
    "characters": "人物收束说明",
    "promises": "伏笔回收说明",
    "read_note": "通读记录",
}


class DeliveryField(Input):
    id: str
    label: str


class DeliveryDestination(Input):
    scope_id: Identifier | None
    label: str
    fields: list[DeliveryField]


class DeliveryDestinations(Input):
    options: list[DeliveryDestination]


class PreviewDelivery(Input):
    turn_id: Identifier
    card_id: Identifier
    stage: Stage
    scope_id: Identifier | None
    field: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=20_000)

    @field_validator("value")
    @classmethod
    def valid_text(cls, value):
        if not value.strip():
            raise ValueError("请填写要送达工作台的建议。")
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("建议须为有效 UTF-8 文字。") from error
        return value


class SaveDelivery(PreviewDelivery):
    basis_sha256: Digest


class DeliveryPreview(Input):
    stage: Stage
    scope_id: Identifier | None
    field: str
    target_label: str
    field_label: str
    before: str
    value: str
    basis_sha256: Digest


class Delivery(DeliveryPreview):
    id: Identifier
    turn_id: Identifier
    card_id: Identifier
    created: float
    basis_current: bool


class Deliveries(Input):
    deliveries: list[Delivery]


def _option(scope_id, label, fields):
    return {
        "scope_id": scope_id,
        "label": label,
        "fields": [{"id": identifier, "label": title} for identifier, title in fields.items()],
    }


def _active_chapters(tx, branch_id):
    return {
        row["id"]: row["title"]
        for row in tx.execute(
            "SELECT id,title FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position,id",
            (branch_id,),
        )
    }


def _outline_destinations(tx, branch_id):
    from .outline_workspace import _decode, _latest

    options = [_option(None, "总纲", {"summary": "总纲"})]
    draft = _decode(_latest(tx, branch_id))
    if not draft:
        return options
    chapters = _active_chapters(tx, branch_id)
    for volume in draft["plan"]["volumes"]:
        options.append(_option(volume["id"], f"卷纲 · {volume['title']}", OUTLINE_CONTAINER_FIELDS))
        for unit in volume["units"]:
            options.append(_option(unit["id"], f"单元纲 · {unit['title']}", OUTLINE_CONTAINER_FIELDS))
            for chapter in unit["chapters"]:
                chapter_id = chapter["chapter_id"]
                if chapter_id in chapters:
                    options.append(
                        _option(chapter_id, f"章纲 · {chapters[chapter_id]}", OUTLINE_CHAPTER_FIELDS)
                    )
    counts = Counter(option["scope_id"] for option in options)
    return [option for option in options if counts[option["scope_id"]] == 1]


def _inspection_destinations(tx, branch_id):
    from .outline_workspace import _decode, _latest
    from .workflow_inspection import LABELS

    fields = {identifier: f"{identifier} {title} · 判定理由" for identifier, title in LABELS.items()}
    options = [_option(None, "全书体检", fields)]
    confirmed = _decode(_latest(tx, branch_id, True))
    if confirmed:
        chapter_ids = {
            row["id"] for row in tx.execute("SELECT id FROM chapters WHERE branch_id=?", (branch_id,))
        }
        for volume in confirmed["plan"]["volumes"]:
            if volume["id"] not in chapter_ids:
                options.append(_option(volume["id"], f"卷 · {volume['title']}", fields))
    options.extend(
        _option(identifier, f"章 · {title}", fields)
        for identifier, title in _active_chapters(tx, branch_id).items()
    )
    return options


def delivery_destinations(tx, branch_id, stage):
    if not tx.execute("SELECT 1 FROM branches WHERE id=?", (branch_id,)).fetchone():
        raise DomainError("not_found", "找不到当前工作版本。", 404)
    if stage == "M1":
        return [_option(None, "整书立项", PROJECT_FIELDS)]
    if stage == "M2":
        return [
            _option(row["id"], f"拆解会话 · {row['title']}", {"goal": "拆解目标"})
            for row in tx.execute(
                "SELECT id,json_extract(payload,'$.source.title') AS title FROM reference_sessions "
                "WHERE json_extract(payload,'$.branch_id')=? ORDER BY updated DESC,id",
                (branch_id,),
            )
        ]
    if stage == "M3":
        from .conception import _workspace

        return [
            _option(
                item["id"], f"{category['title']} · {item['title']}", {"title": "条目名称", "detail": "内容"}
            )
            for category in _workspace(tx, branch_id)["categories"]
            for item in category["items"]
        ]
    if stage == "M4":
        return _outline_destinations(tx, branch_id)
    if stage == "M5":
        return [
            _option(identifier, title, {"text": "正文"})
            for identifier, title in _active_chapters(tx, branch_id).items()
        ]
    if stage == "M6":
        return _inspection_destinations(tx, branch_id)
    return [_option(None, "全书完本材料", COMPLETION_FIELDS)]


def _before(tx, branch_id, stage, scope_id, field, basis):
    sources = {source["kind"]: source for source in basis["sources"]}
    if stage == "M1":
        project = sources.get("project", {})
        return project.get("idea", "") if field == "idea" else project.get("brief", {}).get(field, "")
    if stage == "M2":
        return sources["reference"]["goal"]
    if stage == "M3":
        return sources["settings"]["items"][0].get(field, "")
    if stage == "M4":
        return sources.get("outline", {}).get("selected", {}).get(field, "")
    if stage == "M5":
        return _chapter(tx, branch_id, scope_id)["content"]
    if stage == "M6":
        from .workflow_inspection import _latest

        inspection = sources.get("inspection") or sources["inspection_preview"]
        latest = _latest(tx, branch_id, inspection["scope"], scope_id)
        if latest:
            report = json.loads(latest["payload"])
            if report["basis_sha256"] == inspection["basis_sha256"]:
                check = next(row for row in report["items"] if row["check_id"] == field)
                return (check.get("author_review") or {}).get("reason", "")
        return ""
    from .book_completion import _latest

    latest = _latest(tx, branch_id, sources["completion"]["basis_sha256"])
    if not latest or not latest["current"]:
        return ""
    value = latest["review"].get(field, "")
    return value.get("note", "") if isinstance(value, dict) else value


def _stale():
    return DomainError(
        "novel_assistant_delivery_stale", "建议来源或目标依据已改变，请重新查看后再送达。", 409
    )


SLOT_WHERE = "WHERE branch_id=? AND turn_id=? AND card_id=? AND stage=? AND scope_id IS ? AND field=?"


def _slot(branch_id, data):
    return (branch_id, data.turn_id, data.card_id, data.stage, data.scope_id, data.field)


def _latest_delivery(tx, branch_id, data):
    row = tx.execute(
        f"SELECT id FROM novel_assistant_deliveries {SLOT_WHERE} ORDER BY rowid DESC LIMIT 1",
        _slot(branch_id, data),
    ).fetchone()
    return row["id"] if row else None


def _preview(tx, branch_id, data, prior_id):
    from .novel_assistant import destination_basis, read_card

    card = read_card(tx, branch_id, data.turn_id, data.card_id)
    source_basis = destination_basis(tx, branch_id, card["stage"], card["scope_id"])
    if card["stage"] == "M4" and card["scope_id"] is not None:
        if not any(
            option["scope_id"] == card["scope_id"]
            for option in delivery_destinations(tx, branch_id, card["stage"])
        ):
            raise _stale()
    options = delivery_destinations(tx, branch_id, data.stage)
    target = next((option for option in options if option["scope_id"] == data.scope_id), None)
    if not target:
        raise DomainError("novel_assistant_delivery_target", "找不到可接收建议的当前对象。", 404)
    field = next((field for field in target["fields"] if field["id"] == data.field), None)
    if not field:
        raise DomainError("novel_assistant_delivery_field", "当前对象没有这个可接收建议的字段。")
    if len(data.value) > TARGETS[data.stage][data.field]:
        raise DomainError("novel_assistant_delivery_value", "建议超过目标字段长度，请缩短后再送达。")
    basis = snapshot(tx, branch_id, data.stage, data.scope_id)
    before = _before(tx, branch_id, data.stage, data.scope_id, data.field, basis)
    target_label, field_label = target["label"], field["label"]
    digest = sha256(
        dumps(
            [
                branch_id,
                data.turn_id,
                data.card_id,
                card["basis_sha256"],
                source_basis,
                prior_id,
                data.stage,
                data.scope_id,
                data.field,
                basis["sha256"],
                target_label,
                field_label,
                before,
                data.value,
            ]
        ).encode()
    ).hexdigest()
    return {
        "stage": data.stage,
        "scope_id": data.scope_id,
        "field": data.field,
        "target_label": target_label,
        "field_label": field_label,
        "before": before,
        "value": data.value,
        "basis_sha256": digest,
    }


def _public(tx, row):
    result = {key: row[key] for key in Delivery.model_fields if key != "basis_current"}
    if tx.execute("SELECT 1 FROM novel_assistant_deliveries WHERE prior_id=?", (row["id"],)).fetchone():
        return {**result, "basis_current": False}
    data = PreviewDelivery(**{key: row[key] for key in PreviewDelivery.model_fields})
    try:
        current = _preview(tx, row["branch_id"], data, row["prior_id"])["basis_sha256"] == row["basis_sha256"]
    except DomainError:
        current = False
    return {**result, "basis_current": current}


def register_routes(app, domain):
    endpoint = "/api/branches/{branch_id}/novel-assistant"

    @app.get(endpoint + "/destinations")
    def list_novel_assistant_destinations(branch_id: Identifier, stage: Stage) -> DeliveryDestinations:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return {"options": delivery_destinations(tx, branch_id, stage)}

    @app.post(endpoint + "/deliveries/preview")
    def preview_novel_assistant_delivery(branch_id: Identifier, data: PreviewDelivery) -> DeliveryPreview:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return _preview(tx, branch_id, data, _latest_delivery(tx, branch_id, data))

    @app.post(endpoint + "/deliveries", status_code=201)
    def save_novel_assistant_delivery(branch_id: Identifier, data: SaveDelivery) -> Delivery:
        with domain.db.connect(True) as tx:
            receipt = tx.execute(
                f"SELECT * FROM novel_assistant_deliveries {SLOT_WHERE} AND basis_sha256=?",
                (*_slot(branch_id, data), data.basis_sha256),
            ).fetchone()
            if receipt:
                if receipt["value"] != data.value:
                    raise DomainError(
                        "novel_assistant_delivery_duplicate",
                        "已送达的回执对应其他文字，请重新预览。",
                        409,
                    )
                return _public(tx, receipt)
            prior_id = _latest_delivery(tx, branch_id, data)
            try:
                preview = _preview(tx, branch_id, data, prior_id)
            except DomainError as error:
                if error.code in {"assistant_scope", "novel_assistant_delivery_target"}:
                    raise _stale() from error
                raise
            if preview["basis_sha256"] != data.basis_sha256:
                raise _stale()
            identifier, created = uid(), time()
            entry = {
                **preview,
                "id": identifier,
                "turn_id": data.turn_id,
                "card_id": data.card_id,
                "created": created,
            }
            tx.execute(
                "INSERT INTO novel_assistant_deliveries "
                "(id,branch_id,turn_id,card_id,stage,scope_id,field,target_label,field_label,value,before,"
                "basis_sha256,created,prior_id) VALUES (:id,:branch_id,:turn_id,:card_id,:stage,:scope_id,:field,"
                ":target_label,:field_label,:value,:before,:basis_sha256,:created,:prior_id)",
                {**entry, "branch_id": branch_id, "prior_id": prior_id},
            )
            return {**entry, "basis_current": True}

    @app.get(endpoint + "/deliveries")
    def list_novel_assistant_deliveries(branch_id: Identifier, stage: Stage | None = None) -> Deliveries:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            if not tx.execute("SELECT 1 FROM branches WHERE id=?", (branch_id,)).fetchone():
                raise DomainError("not_found", "找不到当前工作版本。", 404)
            rows = tx.execute(
                "SELECT * FROM novel_assistant_deliveries WHERE branch_id=? AND (? IS NULL OR stage=?) "
                "ORDER BY rowid DESC",
                (branch_id, stage, stage),
            )
            return {"deliveries": [_public(tx, row) for row in rows]}

    @app.get(endpoint + "/deliveries/{identifier}")
    def get_novel_assistant_delivery(branch_id: Identifier, identifier: Identifier) -> Delivery:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            row = tx.execute(
                "SELECT * FROM novel_assistant_deliveries WHERE branch_id=? AND id=?", (branch_id, identifier)
            ).fetchone()
            if not row:
                raise DomainError("not_found", "找不到这条已送达建议。", 404)
            return _public(tx, row)
