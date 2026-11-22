"""Chapter-scoped author plans and setting references for the writing page."""

import json
from time import time
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .conception import WORKSPACE, _latest, _read_payload
from .db import DomainError, dumps, uid
from .models import Input
from .workflow_plan_preparation import VERSION as PREPARATION_VERSION, sha


class AuthorOutlineInput(Input):
    base_version: str | None = Field(max_length=200)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class ChapterBasis(Input):
    chapter_id: str
    title: str
    position: int
    revision: int


class SettingEvidence(ChapterBasis):
    start: int
    end: int
    quote: str


class SettingBasis(Input):
    id: str
    title: str
    text: str
    kind: Literal["fact", "world", "conception"]
    source: SettingEvidence | None
    source_current: bool


class ChapterOutline(Input):
    source: Literal["author", "frozen_ai"]
    text: str
    version: str
    task_id: str | None


class WritingContext(Input):
    chapter_id: str
    chapter_revision: int
    outline: ChapterOutline | None
    settings: list[SettingBasis]


class SettingPassage(Input):
    before: str
    quote: str
    after: str


def target_chapter(tx, branch_id, chapter_id):
    chapter = tx.execute("SELECT * FROM chapters WHERE id=? AND archived=0", (chapter_id,)).fetchone()
    if not chapter:
        raise DomainError("not_found", "章节不存在或已归档。", 404)
    if chapter["branch_id"] != branch_id:
        raise DomainError("ownership", "章节不属于目标工作版本。", 403)
    return chapter


def outline_value(source, text, version, task_id=None):
    return {"source": source, "text": text, "version": version, "task_id": task_id}


def frozen_workflow_outline(tx, chapter):
    rows = tx.execute(
        """SELECT t.id, t.result, p.value FROM tasks t, json_each(t.result, '$.plot_preparations') p
        WHERE t.branch_id=? AND t.kind='workflow' AND json_extract(t.payload, '$.mode')='automatic'
        AND json_extract(t.result, '$.workflow_plan_preparation_version')=?
        AND json_extract(p.value, '$.chapter_id')=? AND json_extract(p.value, '$.status')='ready'
        ORDER BY t.created DESC,t.rowid DESC""",
        (chapter["branch_id"], PREPARATION_VERSION, chapter["id"]),
    )
    for row in rows:
        receipt, result = json.loads(row["value"]), json.loads(row["result"])
        text = receipt.get("effective_plot")
        plan = result.get("plan", {})
        original = next((p for p in plan.get("chapters", []) if p["number"] == receipt.get("chapter")), None)
        if (
            receipt.get("version") == PREPARATION_VERSION
            and isinstance(text, str)
            and text.strip()
            and receipt.get("effective_plot_sha256") == sha(text)
            and original is not None
            and receipt.get("original_chapter_sha256") == sha(original)
            and receipt.get("plan_sha256") == sha(plan)
            and receipt.get("prepared_contract")
            and receipt.get("prepared_contract_sha256") == sha(receipt["prepared_contract"])
        ):
            return outline_value("frozen_ai", text, f"{row['id']}:{sha(text)}", row["id"])
    return None


def assisted_outline(tx, chapter):
    rows = tx.execute(
        """SELECT t.id, t.payload, a.payload AS draft FROM artifacts a JOIN tasks t ON t.id=a.task_id
        WHERE a.branch_id=? AND a.chapter_id=? AND a.kind='chapter_draft' AND a.status<>'rejected'
        AND t.branch_id=a.branch_id AND t.kind='workflow' AND json_extract(t.payload, '$.mode')='assisted'
        ORDER BY a.created DESC,a.rowid DESC""",
        (chapter["branch_id"], chapter["id"]),
    )
    for row in rows:
        draft = json.loads(row["draft"])
        audit = draft.get("plot_audit") or draft.get("previous_plot_audit") or {}
        source = audit.get("source") or {}
        if source.get("kind") != "author_plot":
            continue
        matching = [
            plot["plot"]
            for plot in json.loads(row["payload"]).get("plots", [])
            if sha(plot["plot"]) == source.get("text_sha256")
        ]
        if matching:
            return outline_value("author", matching[0], f"{row['id']}:{sha(matching[0])}", row["id"])
    return None


def chapter_outline(tx, chapter):
    author = tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND chapter_id=? AND kind='chapter_outline' "
        "AND status='accepted' ORDER BY created DESC,rowid DESC LIMIT 1",
        (chapter["branch_id"], chapter["id"]),
    ).fetchone()
    if author:
        return outline_value("author", json.loads(author["payload"])["text"], author["id"])
    return assisted_outline(tx, chapter) or frozen_workflow_outline(tx, chapter)


def source_location(tx, chapter, evidence):
    row = tx.execute(
        "SELECT title,position,revision,archived,stale FROM chapters WHERE id=? AND branch_id=?",
        (evidence["chapter_id"], chapter["branch_id"]),
    ).fetchone()
    if not row or row["position"] > chapter["position"]:
        return None, False
    current = row["revision"] == evidence["revision"] and not row["archived"] and not row["stale"]
    return {**evidence, "title": row["title"], "position": row["position"]}, current


def fact_settings(tx, chapter, setting_id):
    rows = tx.execute(
        """SELECT f.*,c.title,c.position,c.revision,c.archived,c.stale AS chapter_stale
        FROM facts f JOIN chapters c ON c.id=f.chapter_id AND c.branch_id=f.branch_id
        WHERE f.branch_id=? AND f.kind='world' AND f.truth='fact' AND c.position<=?
        AND (? IS NULL OR f.id=?)
        ORDER BY (f.stale OR c.stale OR c.archived OR f.source_revision<>c.revision),c.position DESC,f.rowid DESC
        LIMIT 100""",
        (chapter["branch_id"], chapter["position"], setting_id, setting_id),
    )
    for row in rows:
        yield {
            "id": row["id"],
            "kind": "fact",
            "title": row["subject"],
            "text": row["statement"],
            "source": {
                "chapter_id": row["chapter_id"],
                "revision": row["source_revision"],
                "start": row["evidence_start"],
                "end": row["evidence_end"],
                "quote": row["evidence"],
                "title": row["title"],
                "position": row["position"],
            },
            "source_current": (
                row["source_revision"] == row["revision"]
                and not row["archived"]
                and not row["chapter_stale"]
                and not row["stale"]
            ),
        }


def conception_settings(tx, chapter):
    row = _latest(tx, chapter["branch_id"], WORKSPACE)
    if not row:
        return
    workspace = _read_payload(row)
    category = next((c for c in workspace["categories"] if c["id"] == "world" and not c["skipped"]), None)
    for item in category["items"] if category else ():
        if not item["detail"].strip():
            continue
        evidence = workspace["origins"].get(item["id"], {}).get("evidence")
        source, current = source_location(tx, chapter, evidence) if evidence else (None, True)
        if evidence and source is None:
            continue
        yield {
            "id": f"conception:{item['id']}",
            "kind": "conception",
            "title": item["title"],
            "text": item["detail"],
            "source": source,
            "source_current": current,
        }


def adopted_world_setting(tx, chapter):
    row = tx.execute(
        "SELECT id,payload,chapter_id FROM artifacts WHERE branch_id=? AND kind='world' AND status='accepted' "
        "AND (chapter_id=? OR chapter_id IS NULL) ORDER BY (chapter_id IS NULL),created DESC,id DESC LIMIT 1",
        (chapter["branch_id"], chapter["id"]),
    ).fetchone()
    if row and (text := json.loads(row["payload"]).get("text", "")).strip():
        yield {
            "id": f"world:{row['id']}",
            "kind": "world",
            "title": "本章已采用设定" if row["chapter_id"] else "本书已采用设定",
            "text": text,
            "source": None,
            "source_current": True,
        }


def chapter_settings(tx, chapter):
    return [
        *fact_settings(tx, chapter, None),
        *conception_settings(tx, chapter),
        *adopted_world_setting(tx, chapter),
    ]


def chapter_setting(tx, chapter, setting_id):
    if setting_id.startswith("conception:"):
        rows = conception_settings(tx, chapter)
    elif setting_id.startswith("world:"):
        rows = adopted_world_setting(tx, chapter)
    else:
        rows = fact_settings(tx, chapter, setting_id)
    return next((row for row in rows if row["id"] == setting_id), None)


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/chapters/{chapter_id}/writing-context", response_model=WritingContext)
    def read_context(branch_id: str, chapter_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            chapter = target_chapter(tx, branch_id, chapter_id)
            return {
                "chapter_id": chapter_id,
                "chapter_revision": chapter["revision"],
                "outline": chapter_outline(tx, chapter),
                "settings": chapter_settings(tx, chapter),
            }

    @app.get(
        "/api/branches/{branch_id}/chapters/{chapter_id}/writing-context/settings/{setting_id}/source",
        response_model=SettingPassage,
    )
    def setting_source(branch_id: str, chapter_id: str, setting_id: str, revision: int, start: int):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            chapter = target_chapter(tx, branch_id, chapter_id)
            setting = chapter_setting(tx, chapter, setting_id)
            source = setting and setting["source"]
            if not source or source["revision"] != revision or source["start"] != start:
                raise DomainError("source_changed", "设定原文依据已改变，请刷新后再定位。", 409)
            original = tx.execute(
                "SELECT content FROM revisions WHERE chapter_id=? AND revision=?",
                (source["chapter_id"], revision),
            ).fetchone()
            if original:
                encoded = original["content"].encode("utf-16-le")
                begin, end = 2 * start, 2 * source["end"]
                quote = encoded[begin:end].decode("utf-16-le", errors="replace")
                if quote and quote == source["quote"]:
                    return {
                        "before": encoded[max(0, begin - 320) : begin].decode("utf-16-le", errors="ignore"),
                        "quote": quote,
                        "after": encoded[end : end + 320].decode("utf-16-le", errors="ignore"),
                    }
            raise DomainError("source_unavailable", "找不到该修订中的原文位置，可继续查看保存的引文。", 404)

    @app.put(
        "/api/branches/{branch_id}/chapters/{chapter_id}/writing-context/outline",
        response_model=ChapterOutline,
    )
    def save_outline(branch_id: str, chapter_id: str, data: AuthorOutlineInput):
        with domain.db.connect(True) as tx:
            chapter = target_chapter(tx, branch_id, chapter_id)
            before = chapter_outline(tx, chapter)
            if (before["version"] if before else None) != data.base_version:
                raise DomainError("outline_changed", "本章章纲已改变，请刷新后核对。", 409)
            if (
                before
                and before["source"] == "author"
                and not before["task_id"]
                and before["text"] == data.text
            ):
                return before
            branch = tx.execute("SELECT novel_id FROM branches WHERE id=?", (branch_id,)).fetchone()
            outline_id = uid()
            tx.execute(
                "INSERT INTO artifacts(id,novel_id,branch_id,chapter_id,kind,base_revision,payload,basis,status,created) "
                "VALUES(?,?,?,?,'chapter_outline',?,?,?,'accepted',?)",
                (
                    outline_id,
                    branch["novel_id"],
                    branch_id,
                    chapter_id,
                    chapter["revision"],
                    dumps({"text": data.text}),
                    dumps({"previous_outline_version": data.base_version}),
                    time(),
                ),
            )
            return outline_value("author", data.text, outline_id)
