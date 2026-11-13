"""Author-confirmed long-form records. Story order and narration order stay separate."""

from typing import Annotated, Literal
from copy import deepcopy
from time import time

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .db import DomainError, dumps, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS story_records(
 id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES branches(id),
 kind TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS story_records_branch ON story_records(branch_id,archived,kind);
CREATE TABLE IF NOT EXISTS story_record_history(
 record_id TEXT NOT NULL REFERENCES story_records(id),revision INTEGER NOT NULL,
 payload TEXT NOT NULL,archived INTEGER NOT NULL,created REAL NOT NULL,
 PRIMARY KEY(record_id,revision));
CREATE TABLE IF NOT EXISTS story_record_dependencies(
 record_id TEXT NOT NULL REFERENCES story_records(id),parent_id TEXT NOT NULL REFERENCES story_records(id),
 parent_revision INTEGER NOT NULL,PRIMARY KEY(record_id,parent_id));
INSERT OR IGNORE INTO extension_migrations VALUES('story-graph/1',unixepoch());
"""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    chapter_id: str
    revision: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)


class BaseRecord(Strict):
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=4000)
    sources: list[Source] = Field(default_factory=list, max_length=30)
    depends_on: list[str] = Field(default_factory=list, max_length=50)
    volume: str = Field(default="", max_length=100)


class Character(BaseRecord):
    kind: Literal["character"]
    aliases: list[str] = Field(default_factory=list, max_length=30)
    profile: str = Field(default="", max_length=8000)


class Event(BaseRecord):
    kind: Literal["event"]
    thread: str = Field(default="主线", max_length=100)
    story_order: int | None = None
    story_time: str = Field(default="未知", max_length=200)
    status: Literal["planned", "actual", "unknown", "conflict"] = "planned"
    participants: list[str] = Field(default_factory=list, max_length=50)
    after: list[str] = Field(default_factory=list, max_length=50)
    # Multiple source locations are presentations of ONE event, never repeated mutations.


class State(BaseRecord):
    kind: Literal["state"]
    character_id: str
    event_id: str
    category: Literal["knowledge", "capability", "resource", "injury", "other"]
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=4000)
    audience: Literal["author", "character", "reader"]
    truth: Literal["actual", "planned", "unknown", "conflict"] = "actual"
    until_event_id: str | None = None


class PromiseStage(Strict):
    status: Literal["planned", "planted", "reinforced", "resolved", "retained", "abandoned"]
    event_id: str | None = None
    sources: list[Source] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=2000)


class Promise(BaseRecord):
    kind: Literal["promise"]
    thread: str = Field(default="主线", max_length=100)
    stages: list[PromiseStage] = Field(min_length=1, max_length=100)


class Relationship(BaseRecord):
    kind: Literal["relationship"]
    from_id: str
    to_id: str
    relation: str = Field(min_length=1, max_length=500)
    event_id: str | None = None
    truth: Literal["actual", "planned", "unknown", "conflict"] = "planned"


Record = Annotated[Character | Event | State | Promise | Relationship, Field(discriminator="kind")]
RECORD = TypeAdapter(Record)


class SaveRecord(Strict):
    base_story_revision: int = Field(ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    record: Record
    archived: bool = False


def decode(row):
    return {**dict(row), "payload": RECORD.validate_json(row["payload"]).model_dump()}


def references(payload):
    refs = list(payload["depends_on"])
    kind = payload["kind"]
    if kind == "event":
        refs += payload["participants"] + payload["after"]
    elif kind == "state":
        refs += [payload["character_id"], payload["event_id"]]
        if payload["until_event_id"]:
            refs.append(payload["until_event_id"])
    elif kind == "promise":
        refs += [s["event_id"] for s in payload["stages"] if s["event_id"]]
    elif kind == "relationship":
        refs += [payload["from_id"], payload["to_id"]]
        if payload["event_id"]:
            refs.append(payload["event_id"])
    return set(refs)


def sources(payload):
    result = list(payload["sources"])
    if payload["kind"] == "promise":
        result += [source for stage in payload["stages"] for source in stage["sources"]]
    return result


def metadata_extension(before, after):
    """Added evidence/aliases keep the same identity and assertions, never historical corrections."""
    if before["kind"] != after["kind"]:
        return False
    ignored = {"sources"}
    if before["kind"] == "character":
        ignored.add("aliases")
        if not set(before["aliases"]).issubset(after["aliases"]):
            return False
    if {k: v for k, v in before.items() if k not in ignored} != {
        k: v for k, v in after.items() if k not in ignored
    }:
        return False

    def key(s):
        return (s["chapter_id"], s["revision"], s["start"], s["end"], s["quote"])

    return {key(s) for s in before["sources"]}.issubset({key(s) for s in after["sources"]})


def graph(domain, branch_id):
    with domain.db.connect() as conn:
        conn.execute("BEGIN")
        return graph_snapshot(conn, branch_id)


def graph_snapshot(conn, branch_id):
    branch = conn.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    rows = [
        decode(r)
        for r in conn.execute(
            "SELECT * FROM story_records WHERE branch_id=? ORDER BY created,id", (branch_id,)
        )
    ]
    chapters = {
        c["id"]: dict(c)
        for c in conn.execute(
            "SELECT id,revision,archived,position,title,stale FROM chapters WHERE branch_id=?", (branch_id,)
        )
    }
    indexed = {r["id"]: r for r in rows}
    dependencies = conn.execute(
        "SELECT d.* FROM story_record_dependencies d JOIN story_records r ON r.id=d.record_id WHERE r.branch_id=?",
        (branch_id,),
    )
    for row in rows:
        invalid = []
        for source in sources(row["payload"]):
            c = chapters.get(source["chapter_id"])
            if not c or c["revision"] != source["revision"] or c["archived"] or c["stale"]:
                invalid.append("来源正文版本已改变或待复检")
        row["invalid_reasons"] = invalid
    for dep in dependencies:
        parent = indexed.get(dep["parent_id"])
        if not parent or parent["revision"] != dep["parent_revision"]:
            compatible = False
            if parent and not parent["archived"]:
                chain = [
                    RECORD.validate_json(r["payload"]).model_dump()
                    for r in conn.execute(
                        "SELECT payload FROM story_record_history WHERE record_id=? AND revision>=? AND revision<=? ORDER BY revision",
                        (parent["id"], dep["parent_revision"], parent["revision"]),
                    )
                ]
                compatible = len(chain) == parent["revision"] - dep["parent_revision"] + 1 and all(
                    metadata_extension(a, b) for a, b in zip(chain, chain[1:])
                )
            if compatible:
                indexed[dep["record_id"]].setdefault("compatible_dependency_updates", []).append(
                    {
                        "parent_id": parent["id"],
                        "from_revision": dep["parent_revision"],
                        "to_revision": parent["revision"],
                        "reason": "仅追加来源或人物别名，原身份及断言保持。",
                    }
                )
            else:
                indexed[dep["record_id"]]["invalid_reasons"].append("关联记录版本已改变，需重新确认")
    # Propagate invalid dependencies without discarding historical records.
    for _ in rows:
        changed = False
        for row in rows:
            if row["invalid_reasons"]:
                continue
            if any(
                ref not in indexed or indexed[ref]["archived"] or indexed[ref]["invalid_reasons"]
                for ref in references(row["payload"])
            ):
                row["invalid_reasons"].append("依赖记录已归档或失效")
                changed = True
        if not changed:
            break
    return {
        "branch_id": branch_id,
        "story_revision": branch["story_revision"],
        "records": rows,
        "chapters": list(chapters.values()),
        "coverage": "作者确认的结构化记录；空缺保持未知。旧设定及事实为待整理资料，不自动映射人物身份。",
    }


def validate_record(tx, branch_id, identifier, payload):
    from .domain import utf16_slice

    if not payload["title"].strip():
        raise DomainError("story_record", "请填写记录名称。")
    rows = {
        r["id"]: decode(r)
        for r in tx.execute("SELECT * FROM story_records WHERE branch_id=? AND archived=0", (branch_id,))
    }
    if identifier in references(payload):
        raise DomainError("story_cycle", "记录不能依赖自身。")
    for ref in references(payload):
        if ref not in rows:
            raise DomainError("ownership", "关联须属于当前工作版本且尚未归档。", 409)
    for source in sources(payload):
        chapter = tx.execute(
            "SELECT * FROM chapters WHERE id=? AND branch_id=? AND archived=0",
            (source["chapter_id"], branch_id),
        ).fetchone()
        if not chapter or chapter["revision"] != source["revision"]:
            raise DomainError("revision_conflict", "来源章节已改变或不属于当前版本。", 409)
        if utf16_slice(chapter["content"], source["start"], source["end"])[1] != source["quote"]:
            raise DomainError("evidence", "来源选区必须逐字对应保存稿。", 409)

    def require_kind(ref, kind):
        if rows[ref]["kind"] != kind:
            raise DomainError("story_reference", "关联类型不符，需要" + kind)

    kind = payload["kind"]
    if kind == "character":
        names = [payload["title"], *payload["aliases"]]
        if any(not n.strip() or len(n) > 200 for n in names) or len(set(names)) != len(names):
            raise DomainError("character_alias", "姓名和别名须非空且不重复。")
        occupied = {
            n
            for r in rows.values()
            if r["id"] != identifier and r["kind"] == "character"
            for n in [r["payload"]["title"], *r["payload"]["aliases"]]
        }
        if set(names) & occupied:
            raise DomainError("character_alias", "姓名或别名已用于其他人物；请明确身份后再保存。", 409)
    elif kind == "event":
        for ref in payload["participants"]:
            require_kind(ref, "character")
        for ref in payload["after"]:
            require_kind(ref, "event")
            prior = rows[ref]["payload"].get("story_order")
            if prior is not None and payload["story_order"] is not None and prior >= payload["story_order"]:
                raise DomainError("story_order", "先行事件的故事顺序须早于当前事件。")
        if payload["status"] == "actual" and not payload["sources"]:
            raise DomainError("evidence", "已发生事件需要原文依据。")
    elif kind == "state":
        require_kind(payload["character_id"], "character")
        require_kind(payload["event_id"], "event")
        start = rows[payload["event_id"]]["payload"]
        if payload["truth"] == "actual" and (not payload["sources"] or start["status"] != "actual"):
            raise DomainError("evidence", "实际状态须关联已发生事件并提供原文。")
        if payload["until_event_id"]:
            require_kind(payload["until_event_id"], "event")
            end = rows[payload["until_event_id"]]["payload"]["story_order"]
            if end is None or start["story_order"] is None or end <= start["story_order"]:
                raise DomainError("story_order", "状态失效事件须有明确且更晚的故事顺序。")
    elif kind == "relationship":
        require_kind(payload["from_id"], "character")
        require_kind(payload["to_id"], "character")
        if payload["event_id"]:
            require_kind(payload["event_id"], "event")
        if payload["truth"] == "actual" and not payload["sources"]:
            raise DomainError("evidence", "实际关系需要原文依据。")
        if (
            payload["truth"] == "actual"
            and payload["event_id"]
            and rows[payload["event_id"]]["payload"]["status"] != "actual"
        ):
            raise DomainError("evidence", "实际关系绑定的事件须已经发生。")
    else:
        for stage in payload["stages"]:
            if stage["event_id"]:
                require_kind(stage["event_id"], "event")
            if stage["status"] in {"planted", "reinforced", "resolved"} and not stage["sources"]:
                raise DomainError("evidence", "埋设、强化和回收须分别关联原文。")
            if stage["status"] in {"retained", "abandoned"} and not stage["note"].strip():
                raise DomainError("promise_decision", "保留或放弃须记录作者理由。")
    all_payloads = {ref: r["payload"] for ref, r in rows.items()}
    all_payloads[identifier] = payload

    def visit(ref, path, done):
        if ref in path:
            raise DomainError("story_cycle", "事件或记录依赖构成循环。")
        if ref in done:
            return
        for parent in references(all_payloads[ref]):
            visit(parent, path | {ref}, done)
        done.add(ref)

    visit(identifier, set(), set())


def save_record(domain, branch_id, data, identifier=None):
    domain.branch(branch_id)
    creating = identifier is None
    identifier = identifier or uid()
    payload = data.record.model_dump()
    with domain.db.connect(True) as tx:
        branch = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
        if branch[0] != data.base_story_revision:
            raise DomainError("story_changed", "故事版本已改变，输入保留，请刷新比较。", 409)
        old = tx.execute(
            "SELECT * FROM story_records WHERE id=? AND branch_id=?", (identifier, branch_id)
        ).fetchone()
        if not creating and (not old or old["revision"] != data.base_revision):
            raise DomainError("record_changed", "记录已改变，请重新载入。", 409)
        if creating and (data.base_revision is not None or data.archived):
            raise DomainError("story_record", "新记录不能指定旧修订或直接归档。")
        if old and old["kind"] != payload["kind"]:
            raise DomainError("story_record", "记录类型不可改变，请新建对应记录。")
        validate_record(tx, branch_id, identifier, payload)
        revision = old["revision"] + 1 if old else 0
        tx.execute(
            "INSERT INTO story_records VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "revision=excluded.revision,payload=excluded.payload,archived=excluded.archived,updated=excluded.updated",
            (
                identifier,
                branch_id,
                payload["kind"],
                revision,
                dumps(payload),
                int(data.archived),
                time(),
                time(),
            ),
        )
        tx.execute(
            "INSERT INTO story_record_history VALUES(?,?,?,?,?)",
            (identifier, revision, dumps(payload), int(data.archived), time()),
        )
        tx.execute("DELETE FROM story_record_dependencies WHERE record_id=?", (identifier,))
        for parent in references(payload):
            parent_revision = tx.execute(
                "SELECT revision FROM story_records WHERE id=?", (parent,)
            ).fetchone()[0]
            tx.execute(
                "INSERT INTO story_record_dependencies VALUES(?,?,?)", (identifier, parent, parent_revision)
            )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
    return graph(domain, branch_id)


def query_context(
    domain,
    branch_id,
    chapter_id=None,
    story_order=None,
    audience="author",
    character_id=None,
    text_position=0,
    snapshot=None,
):
    value = snapshot if snapshot is not None else graph(domain, branch_id)
    chapter_positions = {c["id"]: c["position"] for c in value["chapters"]}
    at_position = chapter_positions.get(chapter_id, 1_000_000)
    valid = {}
    uncertain = []

    def visible(source):
        position = chapter_positions.get(source["chapter_id"], 1_000_000)
        return position < at_position or (position == at_position and source["end"] <= text_position)

    for row in value["records"]:
        if row["archived"] or row["invalid_reasons"]:
            continue
        p = row["payload"]
        # Earlier narration only: a flash-forward cannot grant knowledge to an earlier scene.
        visible_sources = [s for s in p["sources"] if visible(s)]
        if p["sources"] and not visible_sources:
            continue
        item = deepcopy(row)
        item["payload"]["sources"] = visible_sources
        valid[row["id"]] = item
    events = {k: r["payload"] for k, r in valid.items() if r["kind"] == "event"}
    promises = []
    for row in valid.values():
        if row["kind"] != "promise":
            continue
        item = deepcopy(row)
        item["payload"]["stages"] = [
            stage
            for stage in row["payload"]["stages"]
            if not any(
                chapter_positions.get(s["chapter_id"], 1_000_000) > at_position
                or (chapter_positions.get(s["chapter_id"]) == at_position and s["end"] > text_position)
                for s in stage["sources"]
            )
            and (
                not stage["event_id"]
                or (
                    stage["event_id"] in events
                    and (
                        story_order is None
                        or (
                            events[stage["event_id"]]["story_order"] is not None
                            and events[stage["event_id"]]["story_order"] <= story_order
                        )
                    )
                )
            )
        ]
        if item["payload"]["stages"]:
            promises.append(item)
    states = []
    for row in valid.values():
        p = row["payload"]
        if (
            p["kind"] != "state"
            or p["audience"] != audience
            or (character_id and p["character_id"] != character_id)
        ):
            continue
        event = events.get(p["event_id"])
        if not event or event["status"] != "actual" or p["truth"] != "actual":
            uncertain.append(row)
            continue
        order = event["story_order"]
        if story_order is None or order is None:
            uncertain.append(row)
            continue
        end_event = events.get(p["until_event_id"])
        if p["until_event_id"] and not end_event:
            uncertain.append(row)
            continue
        if order <= story_order and (not end_event or end_event["story_order"] > story_order):
            states.append(row)
    # A later explicit story event supersedes an older assertion of the same slot.
    # Assertions at the SAME story order remain conflicting, never last-write-wins.
    groups = {}
    for row in states:
        p = row["payload"]
        key = (p["character_id"], p["category"], p["key"], p["audience"])
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        latest = max(events[r["payload"]["event_id"]]["story_order"] for r in group)
        groups[key] = [r for r in group if events[r["payload"]["event_id"]]["story_order"] == latest]
    states = [r for group in groups.values() for r in group]
    conflicts = [group for group in groups.values() if len({r["payload"]["value"] for r in group}) > 1]
    return {
        "states": states,
        "conflicts": conflicts,
        "uncertain": uncertain,
        "plans": [
            r
            for r in valid.values()
            if (r["kind"] == "event" and r["payload"]["status"] == "planned")
            or (r["kind"] in {"state", "relationship"} and r["payload"]["truth"] == "planned")
        ]
        + [r for r in promises if r["payload"]["stages"][-1]["status"] == "planned"],
        "characters": [r for r in valid.values() if r["kind"] == "character"],
        "events": [
            r
            for r in valid.values()
            if r["kind"] == "event"
            and (
                story_order is None
                or r["payload"]["story_order"] is None
                or r["payload"]["story_order"] <= story_order
            )
        ],
        "promises": promises,
        "relationships": [
            r
            for r in valid.values()
            if r["kind"] == "relationship"
            and (
                not r["payload"]["event_id"]
                or (
                    r["payload"]["event_id"] in events
                    and (
                        story_order is None
                        or (
                            events[r["payload"]["event_id"]]["story_order"] is not None
                            and events[r["payload"]["event_id"]]["story_order"] <= story_order
                        )
                    )
                )
            )
        ],
        "coverage": "仅采用有效且在目标叙述位置之前取证的记录。未知故事顺序不推断人物状态；冲突不自动裁定。状态为绝对陈述，不重复累计资源。",
    }


def completion_promises(domain, branch_id):
    return promise_status(graph(domain, branch_id))


def promise_status(snapshot):
    rows = [r for r in snapshot["records"] if r["kind"] == "promise" and not r["archived"]]
    unresolved = [
        r
        for r in rows
        if r["invalid_reasons"]
        or r["payload"]["stages"][-1]["status"] not in {"resolved", "retained", "abandoned"}
    ]
    return {
        "count": len(rows),
        "unresolved": unresolved,
        "closed": not unresolved,
        "coverage": "仅核对作者已维护的伏笔／承诺清单，不宣称自动发现所有遗漏。保留及放弃保留作者理由。",
    }


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/story-graph")
    def get_graph(branch_id: str):
        return graph(domain, branch_id)

    @app.post("/api/branches/{branch_id}/story-records", status_code=201)
    def create_record(branch_id: str, data: SaveRecord):
        return save_record(domain, branch_id, data)

    @app.put("/api/branches/{branch_id}/story-records/{identifier}")
    def update_record(branch_id: str, identifier: str, data: SaveRecord):
        return save_record(domain, branch_id, data, identifier)

    @app.get("/api/branches/{branch_id}/story-records/{identifier}/history")
    def history(branch_id: str, identifier: str):
        domain.db.one("SELECT id FROM story_records WHERE id=? AND branch_id=?", (identifier, branch_id))
        return [
            {**r, "payload": RECORD.validate_json(r["payload"]).model_dump()}
            for r in domain.db.all(
                "SELECT * FROM story_record_history WHERE record_id=? ORDER BY revision DESC", (identifier,)
            )
        ]

    @app.get("/api/branches/{branch_id}/story-context")
    def at_time(
        branch_id: str,
        chapter_id: str | None = None,
        story_order: int | None = None,
        audience: Literal["author", "character", "reader"] = Query("author"),
        character_id: str | None = None,
        text_position: int = Query(0, ge=0),
    ):
        if chapter_id:
            domain.chapter(chapter_id, branch_id)
        return query_context(
            domain, branch_id, chapter_id, story_order, audience, character_id, text_position
        )
