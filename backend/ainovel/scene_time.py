"""Explicit, versioned scene-to-story-time bindings; never inferred from chapter number."""

import json
from time import time
from typing import Literal

from pydantic import Field, model_serializer

from .db import DomainError, dumps, uid
from .story_graph import Strict, graph, graph_snapshot, metadata_revision_compatible, query_context
from .scene_order import SceneOrderAudit, audit_valid

SCHEMA = """
CREATE TABLE IF NOT EXISTS scene_times(
 id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),chapter_id TEXT NOT NULL REFERENCES chapters(id),
 revision INTEGER NOT NULL,payload TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scene_time_history(
 scene_id TEXT NOT NULL REFERENCES scene_times(id),revision INTEGER NOT NULL,payload TEXT NOT NULL,
 archived INTEGER NOT NULL,created REAL NOT NULL,PRIMARY KEY(scene_id,revision));
INSERT OR IGNORE INTO extension_migrations VALUES('scene-time/1',unixepoch());
"""


class SceneTime(Strict):
    order_basis: SceneOrderAudit | None = None
    event_id: str | None = None
    event_revision: int | None = Field(default=None, ge=0)
    title: str = Field(min_length=1, max_length=200)
    source_revision: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    story_order: int | None = None
    story_time: str = Field(default="未知", max_length=200)
    audience: Literal["author", "character", "reader"] = "character"
    character_id: str | None = None
    note: str = Field(default="", max_length=2000)

    @model_serializer(mode="wrap")
    def retain_legacy_serialization(self, handler):
        value = handler(self)
        if self.order_basis is None:
            value.pop("order_basis", None)
        return value


class SaveScene(Strict):
    base_story_revision: int = Field(ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    scene: SceneTime
    archived: bool = False


def event_basis_valid(tx, payload, chapter_id, indexed):
    if not audit_valid(tx, payload, chapter_id, indexed):
        return False
    if payload.get("event_id") is None and payload.get("event_revision") is None:
        return True
    event = indexed.get(payload.get("event_id"))
    return bool(
        event
        and payload.get("event_revision") is not None
        and event["kind"] == "event"
        and not event["invalid_reasons"]
        and event["payload"]["status"] == "actual"
        and event["payload"]["story_order"] == payload["story_order"]
        and metadata_revision_compatible(tx, event, payload["event_revision"])
        and any(
            s["chapter_id"] == chapter_id
            and s["revision"] == payload["source_revision"]
            and max(s["start"], payload["start"]) < min(s["end"], payload["end"])
            for s in event["payload"]["sources"]
        )
    )


def scenes(domain, branch_id, chapter_id):
    chapter = domain.chapter(chapter_id, branch_id)
    rows = domain.db.all(
        "SELECT * FROM scene_times WHERE branch_id=? AND chapter_id=? ORDER BY created,id",
        (branch_id, chapter_id),
    )
    identities = {
        r["id"]
        for r in domain.db.all(
            "SELECT id FROM story_records WHERE branch_id=? AND kind='character' AND archived=0", (branch_id,)
        )
    }
    for row in rows:
        row["payload"] = SceneTime.model_validate_json(row["payload"]).model_dump()
        row["stale"] = row["payload"]["source_revision"] != chapter["revision"] or bool(
            row["payload"]["character_id"] and row["payload"]["character_id"] not in identities
        )
    if any(
        r["payload"].get("order_basis") is not None
        or r["payload"].get("event_id")
        or r["payload"].get("event_revision") is not None
        for r in rows
    ):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            indexed = {r["id"]: r for r in graph_snapshot(tx, branch_id)["records"]}
            for row in rows:
                row["stale"] = row["stale"] or not event_basis_valid(tx, row["payload"], chapter_id, indexed)
    return {
        "chapter_id": chapter_id,
        "source_revision": chapter["revision"],
        "story_revision": domain.branch(branch_id)["story_revision"],
        "scenes": rows,
    }


def save_scene(domain, branch_id, chapter_id, data, identifier=None):
    from .domain import utf16_slice

    creating = identifier is None
    identifier = identifier or uid()
    p = data.scene.model_dump()
    with domain.db.connect(True) as tx:
        chapter = tx.execute(
            "SELECT * FROM chapters WHERE id=? AND branch_id=? AND archived=0", (chapter_id, branch_id)
        ).fetchone()
        branch = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not chapter or not branch:
            raise DomainError("ownership", "章节不属于当前工作版本。", 404)
        old = tx.execute(
            "SELECT * FROM scene_times WHERE id=? AND branch_id=? AND chapter_id=?",
            (identifier, branch_id, chapter_id),
        ).fetchone()
        if not creating and (not old or old["revision"] != data.base_revision):
            raise DomainError("scene_changed", "场景绑定在其他窗口更新，请重新比较。", 409)
        if creating and (data.base_revision is not None or data.archived):
            raise DomainError("scene_changed", "新场景不能指定旧修订或直接归档。")
        previous = json.loads(old["payload"]) if old else {}
        if not data.archived:
            if previous.get("order_basis") is None:
                for key in ("event_id", "event_revision"):
                    if key not in data.scene.model_fields_set and key in previous:
                        p[key] = previous[key]
            if "order_basis" not in data.scene.model_fields_set and previous.get("order_basis") is not None:
                p["order_basis"] = previous["order_basis"]
            if previous.get("order_basis") is not None:
                fixed = (
                    "order_basis",
                    "source_revision",
                    "start",
                    "end",
                    "story_order",
                    "story_time",
                    "event_id",
                    "event_revision",
                )
                if any(p.get(key) != previous.get(key) for key in fixed):
                    raise DomainError(
                        "scene_basis_readonly",
                        "提取依据与范围须保留；需要改变时请归档旧绑定并新建手工范围。",
                        409,
                    )
            elif p.get("order_basis") is not None:
                raise DomainError(
                    "scene_basis_readonly", "事件引用依据只能由提取核对后采用；手工范围不伪造依据。", 409
                )
        if data.archived:
            p = previous
        if branch[0] != data.base_story_revision or (
            not data.archived and chapter["revision"] != p["source_revision"]
        ):
            raise DomainError("story_changed", "正文或故事版本已改变，请重新核对场景范围。", 409)
        if not p["title"].strip() or p["end"] < p["start"]:
            raise DomainError("scene_range", "请填写场景名并核对起止位置。")
        if not data.archived:
            utf16_slice(chapter["content"], p["start"], p["end"])
            if (
                p.get("order_basis") is not None
                or p["event_id"] is not None
                or p["event_revision"] is not None
            ) and not event_basis_valid(
                tx, p, chapter_id, {r["id"]: r for r in graph_snapshot(tx, branch_id)["records"]}
            ):
                raise DomainError(
                    "scene_time_evidence", "事件依据已改变，请重新核对或明确改为作者手动确认时点。", 409
                )
        if not data.archived and p["audience"] == "character" and not p["character_id"]:
            raise DomainError("scene_viewpoint", "人物视角需要明确人物身份。")
        if not data.archived and p["character_id"]:
            identity = tx.execute(
                "SELECT id FROM story_records WHERE id=? AND branch_id=? AND kind='character' AND archived=0",
                (p["character_id"], branch_id),
            ).fetchone()
            if not identity:
                raise DomainError("scene_viewpoint", "视角人物不属于当前工作版本或已归档。", 409)
        if not data.archived:
            for other in tx.execute(
                "SELECT id,payload FROM scene_times WHERE chapter_id=? AND archived=0 AND id!=?",
                (chapter_id, identifier),
            ):
                q = json.loads(other["payload"])
                if q["source_revision"] != p["source_revision"]:
                    continue
                overlap = max(p["start"], q["start"]) < min(p["end"], q["end"])
                point = (
                    (p["start"] == p["end"] and q["start"] <= p["start"] < q["end"])
                    or (q["start"] == q["end"] and p["start"] <= q["start"] < p["end"])
                    or (p["start"] == p["end"] == q["start"] == q["end"])
                )
                if overlap or point:
                    raise DomainError(
                        "scene_overlap", "场景范围重叠，无法确定当前时点；请调整或归档旧绑定。", 409
                    )
        revision = old["revision"] + 1 if old else 0
        tx.execute(
            "INSERT INTO scene_times VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,archived=excluded.archived",
            (identifier, branch_id, chapter_id, revision, dumps(p), int(data.archived), time()),
        )
        tx.execute(
            "INSERT INTO scene_time_history VALUES(?,?,?,?,?)",
            (identifier, revision, dumps(p), int(data.archived), time()),
        )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
    return scenes(domain, branch_id, chapter_id)


def at_position(domain, branch_id, chapter_id, position=0):
    if not chapter_id:
        return None
    value = scenes(domain, branch_id, chapter_id)
    valid = [
        r
        for r in value["scenes"]
        if not r["archived"]
        and not r["stale"]
        and (
            r["payload"]["start"] <= position < r["payload"]["end"]
            or r["payload"]["start"] == r["payload"]["end"] == position
        )
    ]
    if (
        not valid
        and position == len(domain.chapter(chapter_id, branch_id)["content"].encode("utf-16-le")) // 2
    ):
        valid = [
            r
            for r in value["scenes"]
            if not r["archived"]
            and not r["stale"]
            and r["payload"]["start"] < r["payload"]["end"] == position
        ]
    if len(valid) > 1:
        raise DomainError("scene_overlap", "当前正文位置存在重叠场景，请先核对。", 409)
    if not valid and any(r["stale"] and not r["archived"] for r in value["scenes"]):
        raise DomainError(
            "scene_stale",
            "正文、事件依据或视角人物变化后，场景时点尚待复核，请重新确认范围与身份再调用模型。",
            409,
        )
    return valid[0] if valid else None


def scene_context(domain, branch_id, chapter_id, position=0):
    try:
        binding = at_position(domain, branch_id, chapter_id, position)
    except DomainError as error:
        if error.code not in {"scene_stale", "scene_overlap"}:
            raise
        return {
            "scene_binding": None,
            "blocked": error.code,
            "coverage": error.message,
            "states": [],
            "uncertain": [],
            "conflicts": [],
        }
    p = binding["payload"] if binding else {}
    value = query_context(
        domain,
        branch_id,
        chapter_id,
        p.get("story_order"),
        p.get("audience", "author"),
        p.get("character_id"),
        position,
    )
    value["scene_binding"] = binding
    if (
        not binding
        and chapter_id
        and any(not r["archived"] for r in scenes(domain, branch_id, chapter_id)["scenes"])
    ):
        value["unbound_scene_range"] = True
        value["coverage"] += " 当前操作位置位于尚未绑定的场景空缺，故事时点未知，不能按章号补出当前知识。"
    return value


def chapter_context(domain, branch_id, chapter_id):
    """Whole-chapter actions keep each scene's entry/exit knowledge separate."""
    if not chapter_id:
        return None
    value = scenes(domain, branch_id, chapter_id)
    active = sorted(
        [r for r in value["scenes"] if not r["archived"]], key=lambda r: (r["payload"]["start"], r["id"])
    )
    if not active:
        return None
    if any(r["stale"] for r in active):
        raise DomainError("scene_stale", "整章动作包含未复核的场景绑定，请先核对范围和人物。", 409)
    entries = []
    snapshot = graph(domain, branch_id)
    if snapshot["story_revision"] != value["story_revision"]:
        raise DomainError("story_changed", "场景装配期间故事已改变，请重新读取。", 409)
    gaps = []
    cursor = 0
    length = len(domain.chapter(chapter_id, branch_id)["content"].encode("utf-16-le")) // 2
    for row in active:
        p = row["payload"]
        if p["start"] < cursor:
            raise DomainError("scene_overlap", "整章场景范围重叠，请先核对。", 409)
        if p["start"] > cursor:
            gaps.append({"start": cursor, "end": p["start"], "status": "unbound"})

        def at(position):
            return query_context(
                domain,
                branch_id,
                chapter_id,
                p["story_order"],
                p["audience"],
                p["character_id"],
                position,
                snapshot=snapshot,
            )

        entries.append({"binding": row, "entry": at(p["start"]), "exit": at(p["end"])})
        cursor = max(cursor, p["end"])
    if cursor < length:
        gaps.append({"start": cursor, "end": length, "status": "unbound"})
    return {
        "chapter_id": chapter_id,
        "source_revision": value["source_revision"],
        "story_revision": value["story_revision"],
        "scenes": entries,
        "unbound_ranges": gaps,
        "coverage": "各场景独立使用自己的故事时点与信息视角，entry和exit区分进入／离开时可见的保存稿依据；不能把后场景能力提前。空缺保持未绑定。生成后的新位置须重新确认，旧范围不自动套用新稿。",
    }


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/chapters/{chapter_id}/scene-context")
    def whole_context(branch_id: str, chapter_id: str):
        domain.chapter(chapter_id, branch_id)
        return chapter_context(domain, branch_id, chapter_id)

    @app.get("/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/history")
    def history(branch_id: str, chapter_id: str, identifier: str):
        domain.chapter(chapter_id, branch_id)
        domain.db.one(
            "SELECT id FROM scene_times WHERE id=? AND branch_id=? AND chapter_id=?",
            (identifier, branch_id, chapter_id),
        )
        rows = domain.db.all(
            "SELECT * FROM scene_time_history WHERE scene_id=? ORDER BY revision DESC", (identifier,)
        )
        return [{**r, "payload": SceneTime.model_validate_json(r["payload"]).model_dump()} for r in rows]

    @app.get("/api/branches/{branch_id}/chapters/{chapter_id}/scene-times")
    def get_scenes(branch_id: str, chapter_id: str):
        return scenes(domain, branch_id, chapter_id)

    @app.post("/api/branches/{branch_id}/chapters/{chapter_id}/scene-times", status_code=201)
    def create(branch_id: str, chapter_id: str, data: SaveScene):
        return save_scene(domain, branch_id, chapter_id, data)

    @app.put("/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}")
    def update(branch_id: str, chapter_id: str, identifier: str, data: SaveScene):
        return save_scene(domain, branch_id, chapter_id, data, identifier)
