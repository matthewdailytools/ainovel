"""Immutable M7 previews, evidence gates and formal delivery archives."""

import hashlib
import json
from io import BytesIO
from time import time
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import Response
from pydantic import Field

from .chapter_item_check import _changed_sources, _reports, _sources
from .continuity_ledger import ledger_snapshot
from .db import DomainError, dumps, uid
from .domain import utf16_slice
from .manual_finalization import _state as author_finalization_state
from .models import Input
from .project_brief import latest_brief
from .story_graph import graph_snapshot
from .writing_context import chapter_outline

PREVIEW = "delivery_package"
CONFIRMATION = "delivery_confirmation"
VERSION = "delivery/1"
GateId = Literal["manuscript", "ending", "characters", "promises", "consistency", "materials"]
MaterialKey = Literal["manuscript", "settings", "synopsis", "sample"]
GATES = {
    "manuscript": "稿件齐全与通读",
    "ending": "主冲突与终局",
    "characters": "关键人物与关系",
    "promises": "伏笔与读者承诺",
    "consistency": "一致性与复检",
    "materials": "交付材料核验",
}
MATERIALS = {
    "manuscript": ("正文", "manuscript.txt"),
    "settings": ("设定册", "settings.md"),
    "synopsis": ("简介", "synopsis.md"),
    "sample": ("试读", "sample.txt"),
}
DEFINITION_TITLES = {
    "world": "世界设定",
    "characters": "人物",
    "outline": "大纲",
    "intent": "创作约定",
    "reader": "频道与题材",
}


class ReviewNote(Input):
    manuscript: str = Field(default="", max_length=4000)
    ending: str = Field(default="", max_length=4000)
    characters: str = Field(default="", max_length=4000)
    promises: str = Field(default="", max_length=4000)
    consistency: str = Field(default="", max_length=4000)
    materials: str = Field(default="", max_length=4000)


class QuoteInput(Input):
    chapter_id: str = Field(min_length=1, max_length=128)
    quote: str = Field(min_length=1, max_length=4000)


class ReviewSources(Input):
    ending: list[QuoteInput] = Field(default_factory=list, max_length=100)
    characters: list[QuoteInput] = Field(default_factory=list, max_length=100)
    promises: list[QuoteInput] = Field(default_factory=list, max_length=100)
    consistency: list[QuoteInput] = Field(default_factory=list, max_length=100)


class DeliveryInput(Input):
    synopsis: str = Field(default="", max_length=6000)
    spoiler_scope: str = Field(default="", max_length=2000)
    sample_start_chapter_id: str | None = Field(default=None, max_length=128)
    sample_end_chapter_id: str | None = Field(default=None, max_length=128)
    notes: ReviewNote = Field(default_factory=ReviewNote)
    sources: ReviewSources = Field(default_factory=ReviewSources)


class ConfirmInput(Input):
    base_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)


class PreviewInput(ConfirmInput, DeliveryInput):
    pass


class ChapterManifest(Input):
    id: str
    title: str
    position: int
    revision: int
    sha256: str


class Evidence(QuoteInput):
    revision: int
    start: int
    end: int
    title: str
    position: int
    origin: Literal["author", "ledger", "chapter_check", "review"]


class Gate(Input):
    id: GateId
    label: str
    status: Literal["pass", "pending", "blocked"]
    reason: str
    note: str
    sources: list[Evidence]
    evidence: list[str]


class FileManifest(Input):
    key: MaterialKey
    name: str
    label: str
    sha256: str
    bytes: int
    preview: str


class Package(Input):
    id: str
    created: float
    basis_sha256: str
    status: Literal["draft", "ready", "finished", "outdated"]
    finished: bool
    chapters: list[ChapterManifest]
    gates: list[Gate]
    files: list[FileManifest]
    input: DeliveryInput


class DeliveryState(Input):
    basis_sha256: str
    chapters: list[ChapterManifest]
    packages: list[Package]


def digest(data):
    return hashlib.sha256(
        data.encode("utf-8") if isinstance(data, str) else dumps(data).encode("utf-8")
    ).hexdigest()


def chapter_checks(tx, chapters):
    rows = []
    for chapter in chapters:
        seen = set()
        for check_id, spec, report in _reports(tx, chapter["id"]):
            if check_id in seen:
                continue
            seen.add(check_id)
            current, _ = _sources(tx, chapter, spec)
            changed = _changed_sources(current, report["sources"])
            rows.append(
                {
                    "chapter_id": chapter["id"],
                    "chapter_title": chapter["title"],
                    "check_id": check_id,
                    "title": spec["title"],
                    "report_id": report["id"],
                    "status": "stale" if changed else report["result"],
                    "evidence": report["evidence"],
                    "sources": report["sources"],
                    "reason": report["reason"],
                }
            )
    return rows


def live_basis(tx, branch_id):
    branch = tx.execute(
        "SELECT b.*,n.title AS novel_title FROM branches b JOIN novels n ON n.id=b.novel_id WHERE b.id=?",
        (branch_id,),
    ).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    chapters = [
        dict(row)
        for row in tx.execute(
            "SELECT * FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position,id", (branch_id,)
        )
    ]
    final = author_finalization_state(tx, branch_id, branch["story_revision"])
    graph = graph_snapshot(tx, branch_id, include_chapter_stale=False)
    chapter_ids = {chapter["id"] for chapter in chapters}
    definitions = [
        {**dict(row), "payload": json.loads(row["payload"])}
        for row in tx.execute(
            """SELECT id,kind,chapter_id,payload FROM (
                SELECT *,ROW_NUMBER() OVER (
                    PARTITION BY kind,chapter_id ORDER BY created DESC,id DESC) AS rank
                FROM artifacts WHERE branch_id=? AND status='accepted'
                AND kind IN ('intent','reader','world','characters','outline','conception_completion')
            ) WHERE rank=1 ORDER BY kind,chapter_id,id""",
            (branch_id,),
        )
        if not row["chapter_id"] or row["chapter_id"] in chapter_ids
    ]
    facts = [
        dict(row)
        for row in tx.execute(
            """SELECT f.id,f.subject,f.statement,f.source_revision,f.evidence,f.chapter_id
            FROM facts f JOIN chapters c ON c.id=f.chapter_id AND c.branch_id=f.branch_id
            WHERE f.branch_id=? AND f.kind='world' AND f.truth='fact' AND c.archived=0
            AND f.stale=0 AND f.source_revision=c.revision ORDER BY c.position,f.id""",
            (branch_id,),
        )
    ]
    ledger = ledger_snapshot(tx, branch_id)
    basis = {
        "version": VERSION,
        "branch_id": branch_id,
        "novel_title": branch["novel_title"],
        "story_revision": branch["story_revision"],
        "chapters": final["chapter_manifest"],
        "stale_chapter_ids": [chapter["id"] for chapter in chapters if chapter["stale"]],
        "definitions": definitions,
        "project_brief": latest_brief(tx, branch_id, "adopted"),
        "outlines": [
            {"chapter_id": chapter["id"], **outline}
            for chapter in chapters
            if (outline := chapter_outline(tx, chapter))
        ],
        "facts": facts,
        "story_records": [r for r in graph["records"] if not r["archived"]],
        "ledger": ledger["entries"],
        "checks": chapter_checks(tx, chapters),
        "legacy_authored_finalization": final["record"],
        "legacy_blockers": final["deterministic_blockers"],
        "known_issues": final["known_issues"],
        "unresolved_issue_ids": final["unresolved_issue_ids"],
    }
    return branch, basis, {c["id"]: c["content"] for c in chapters}


def compile_sources(request_sources, chapters, texts, *, origin="author"):
    rows, errors = {}, {}
    by_id = {c["id"]: c for c in chapters}
    for key, inputs in request_sources.items():
        selected, failures = [], []
        seen = set()
        for source in inputs:
            chapter_id, quote = source["chapter_id"], source["quote"]
            if (chapter_id, quote) in seen:
                continue
            seen.add((chapter_id, quote))
            chapter = by_id.get(chapter_id)
            text = texts.get(chapter_id, "")
            index = text.find(quote)
            if not chapter or not quote.strip() or index < 0 or text.find(quote, index + 1) >= 0:
                failures.append("引文应能在所选保存章节中逐字且唯一定位，请核对原文。")
                continue
            start = len(text[:index].encode("utf-16-le")) // 2
            selected.append(
                {
                    **source,
                    "revision": chapter["revision"],
                    "title": chapter["title"],
                    "position": chapter["position"],
                    "start": start,
                    "end": start + len(quote.encode("utf-16-le")) // 2,
                    "origin": origin,
                }
            )
        rows[key], errors[key] = selected, failures[:1]
    return rows, errors


def bound_evidence(source, by_id, texts, origin):
    if not source:
        return None
    chapter = by_id.get(source["chapter_id"])
    if not chapter or chapter["revision"] != source["revision"] or not source["quote"].strip():
        return None
    try:
        quote = utf16_slice(texts[chapter["id"]], source["start"], source["end"])[1]
    except DomainError:
        return None
    if quote != source["quote"]:
        return None
    return {
        **{key: source[key] for key in ("chapter_id", "revision", "start", "end", "quote")},
        "title": chapter["title"],
        "position": chapter["position"],
        "origin": origin,
    }


def collected_proofs(basis, texts):
    by_id = {chapter["id"]: chapter for chapter in basis["chapters"]}
    sources = {"promises": [], "consistency": []}
    descriptions = {"promises": [], "consistency": []}
    graph = {row["id"]: row for row in basis["story_records"]}
    for entry in basis["ledger"]:
        key = "promises" if entry["kind"] == "foreshadow" else "consistency"
        descriptions[key].append(f"{entry['title']} · 台账修订 {entry['revision']}")
        for stage in entry["stages"]:
            if stage["review_status"] != "current":
                continue
            for ref in [stage["source"], *stage.get("additional_sources", [])]:
                if evidence := bound_evidence(ref, by_id, texts, "ledger"):
                    sources[key].append(evidence)
            if stage["note"]:
                descriptions[key].append(f"{entry['title']} · 作者核对 {stage['note']}")
        if entry["kind"] == "foreshadow" and entry["closed"] and entry["id"] in graph:
            choice = graph[entry["id"]]["payload"]["stages"][-1]
            if choice["status"] in {"retained", "abandoned"}:
                status = "保留" if choice["status"] == "retained" else "放弃"
                descriptions[key].append(f"{entry['title']} · 作者确认{status} · {choice['note']}")
    for check in basis["checks"]:
        descriptions["consistency"].append(
            f"{check['chapter_title']}／{check['title']} · 单项校验报告 {check['report_id']} · "
            + ("原依据已变化" if check["status"] == "stale" else check["reason"])
        )
        if check["status"] == "stale":
            continue
        for quote in check["evidence"]:
            source = check["sources"].get(quote["source"])
            if source and (evidence := bound_evidence({**source, **quote}, by_id, texts, "chapter_check")):
                sources["consistency"].append(evidence)
    dispositions = {
        d["issue_id"]: d["reason"]
        for d in (basis["legacy_authored_finalization"] or {}).get("dispositions", [])
    }
    for issue in basis["known_issues"]:
        source = {"chapter_id": issue["chapter_id"], "quote": issue["quote"]}
        quote, _ = compile_sources({"review": [source]}, basis["chapters"], texts, origin="review")
        sources["consistency"].extend(quote["review"])
        detail = f"{issue['title']} · 严重审阅意见 · {issue['reason']} · 引文 {issue['quote']}"
        if issue["id"] not in basis["unresolved_issue_ids"] and issue["id"] in dispositions:
            detail += " · 作者裁定 " + dispositions[issue["id"]]
        descriptions["consistency"].append(detail)
    return sources, descriptions


def validity(basis, data, citations, quote_errors, sample, texts):
    chapters = basis["chapters"]
    issues = {
        "manuscript": [
            row["message"]
            for row in basis["legacy_blockers"]
            if row["id"] in {"no_chapters", "chapter_structure", "blank_chapters", "chapter_length"}
        ],
        "promises": [
            "伏笔或承诺待处理：" + entry["title"]
            for entry in basis["ledger"]
            if entry["kind"] == "foreshadow" and not entry["closed"]
        ],
        "consistency": [
            check["chapter_title"] + "／" + check["title"] + "待处理"
            for check in basis["checks"]
            if check["status"] != "pass"
        ],
    }
    explained = {c["chapter_id"] for c in citations["consistency"]}
    # Passing a scoped item may leave the rest of the chapter, including the
    # effect of earlier edits, outside its reviewed inputs.
    missing = set(basis["stale_chapter_ids"]) - explained
    if missing:
        issues["consistency"].append(
            "请逐章重新核对并引用当前保存稿：" + "、".join(c["title"] for c in chapters if c["id"] in missing)
        )
    issues["consistency"].extend(
        "严重审阅意见：" + issue["title"] + "／" + issue["reason"]
        for issue in basis["known_issues"]
        if issue["id"] in basis["unresolved_issue_ids"]
    )
    issues["consistency"].extend(
        "设定生效待核对：" + entry["title"]
        for entry in basis["ledger"]
        if entry["kind"] == "setting"
        and not entry["closed"]
        and any(stage["window"] or stage["source"] or stage.get("review_reason") for stage in entry["stages"])
    )

    intent = next((item for item in basis["definitions"] if item["kind"] == "intent"), None)
    contract = intent["payload"].get("intent_contract") or {} if intent else {}
    documents = [
        f"{material['name']} · {material['bytes']} 字节 · SHA-256 {material['sha256']}"
        for material in data["files"]
    ]
    known_sources, known_evidence = collected_proofs(basis, texts)
    evidence = {
        "manuscript": [f"{c['position']}. {c['title']} · 保存修订 {c['revision']}" for c in chapters],
        "ending": [
            *([f"已采用终局：{contract['ending']}"] if contract.get("ending") else []),
            *[f"锁定结果：{value}" for value in contract.get("fixed_results", [])],
        ],
        "characters": [
            row["payload"]["title"] + " · 故事记录 " + row["id"]
            for row in basis["story_records"]
            if row["kind"] in {"character", "relationship"} and not row["invalid_reasons"]
        ],
        **known_evidence,
        "materials": documents,
    }
    pending = {
        "ending": "请提供主冲突和终局的保存稿原文。" if not citations["ending"] else "",
        "characters": "请提供关键人物去向或关系的保存稿原文。" if not citations["characters"] else "",
        "materials": "请填写简介、明确剧透范围并选择有效试读区间。"
        if not data["synopsis"].strip() or not data["spoiler_scope"].strip() or not sample
        else "",
    }
    for key, label in GATES.items():
        note = data["notes"][key].strip()
        blocking = issues.get(key, []) + quote_errors.get(key, [])
        waiting = pending.get(key) or ("请记录作者核对依据。" if not note else "")
        yield {
            "id": key,
            "label": label,
            "status": "blocked" if blocking else "pending" if waiting else "pass",
            "reason": "；".join(blocking) if blocking else waiting or "作者确认及本版本依据已记录。",
            "note": note,
            "sources": [*citations.get(key, []), *known_sources.get(key, [])],
            "evidence": evidence[key],
        }


def section_text(title, contents):
    return f"## {title}\n\n{contents.strip()}\n" if contents.strip() else ""


def setting_book(basis):
    sections = []
    for row in basis["definitions"]:
        payload = row["payload"]
        text = payload.get("text", "")
        if row["kind"] == "conception_completion":
            for category in payload["categories"]:
                if not category["skipped"]:
                    details = "\n\n".join(
                        "**" + item["title"] + "**\n\n" + item["detail"] for item in category["items"]
                    )
                    sections.append(section_text(category["title"], details))
        elif row["kind"] == "intent":
            fields = payload.get("intent_contract") or {}
            titles = ("故事方向", "终局", "锁定结果", "禁止项")
            rendered = (
                fields.get("direction", ""),
                fields.get("ending", ""),
                "；".join(fields.get("fixed_results", [])),
                "；".join(fields.get("prohibitions", [])),
            )
            sections.append(
                section_text(
                    "创作约定",
                    "\n\n".join(f"**{name}** {value}" for name, value in zip(titles, rendered) if value)
                    or text,
                )
            )
        elif row["kind"] == "reader":
            tags = "、".join(payload.get("genres", []))
            sections.append(
                section_text(
                    "频道与题材", "\n\n".join(filter(None, [payload.get("audience", ""), tags, text]))
                )
            )
        else:
            sections.append(section_text(DEFINITION_TITLES[row["kind"]], text))
    if brief := basis["project_brief"]:
        fields = brief["fields"]
        brief_titles = {
            "concept": "创作思路",
            "positioning": "核心定位",
            "audience": "目标读者",
            "scope": "篇幅与更新计划",
            "direction": "故事方向",
            "ending": "预定结局",
            "last_image": "最后一镜",
        }
        sections.append(
            section_text(
                "已确认立项简报",
                "\n\n".join(f"**{brief_titles[key]}** {value}" for key, value in fields.items() if value),
            )
        )
    for row in basis["story_records"]:
        if row["invalid_reasons"]:
            continue
        payload = row["payload"]
        details = [payload.get(key, "") for key in ("profile", "notes", "relation", "value")]
        sections.append(section_text(payload["title"], "\n\n".join(dict.fromkeys(filter(None, details)))))
    for row in basis["facts"]:
        sections.append(section_text(row["subject"], row["statement"] + "\n\n原文  " + row["evidence"]))
    chapter_titles = {c["id"]: c["title"] for c in basis["chapters"]}
    for row in basis["outlines"]:
        sections.append(section_text("章纲 · " + chapter_titles[row["chapter_id"]], row["text"]))
    for row in basis["ledger"]:
        details = [row["detail"]]
        stage_titles = {"effective": "设定生效", "planted": "埋设", "reinforced": "推进", "resolved": "回收"}
        for stage in row["stages"]:
            if stage["source"] and stage["review_status"] == "current":
                details.append(stage_titles[stage["stage"]] + "  " + stage["source"]["quote"])
        sections.append(section_text(row["title"], "\n\n".join(filter(None, details))))
    if not any(sections):
        sections.append("本快照没有已采用的设定条目。\n")
    return f"# {basis['novel_title']} · 设定册\n\n" + "\n".join(filter(None, sections))


def sample_chapters(chapters, data):
    ids = [chapter["id"] for chapter in chapters]
    first, last = data["sample_start_chapter_id"], data["sample_end_chapter_id"]
    if first in ids and last in ids:
        start, end = ids.index(first), ids.index(last)
        if end >= start:
            return chapters[start : end + 1]
    return []


def manuscript(chapters, texts):
    return "\n\n".join(f"{c['title']}\n\n{texts[c['id']]}" for c in chapters) + ("\n" if chapters else "")


def material_texts(chapters, texts, supplemental, sample_ids):
    chosen = set(sample_ids)
    return {
        "manuscript": manuscript(chapters, texts),
        "settings": supplemental["settings"],
        "synopsis": supplemental["synopsis"],
        "sample": manuscript([c for c in chapters if c["id"] in chosen], texts),
    }


def material_manifest(files):
    return [
        {
            "key": key,
            "label": MATERIALS[key][0],
            "name": MATERIALS[key][1],
            "sha256": digest(body),
            "bytes": len(body.encode("utf-8")),
            "preview": body[:2000],
        }
        for key, body in files.items()
    ]


def make_snapshot(basis, texts, data):
    chapters = basis["chapters"]
    trial = sample_chapters(chapters, data)
    sample_ids = [chapter["id"] for chapter in trial]
    supplemental = {
        "settings": setting_book(basis),
        "synopsis": "# " + basis["novel_title"] + " · 简介\n\n" + data["synopsis"].strip() + "\n",
    }
    files = material_texts(chapters, texts, supplemental, sample_ids)
    manifest = material_manifest(files)
    sources, quote_errors = compile_sources(data["sources"], chapters, texts)
    gates = list(validity(basis, {**data, "files": manifest}, sources, quote_errors, trial, texts))
    return {
        "version": VERSION,
        "basis_sha256": digest(basis),
        "basis": basis,
        "chapters": chapters,
        "sample_chapter_ids": sample_ids,
        "supplemental": supplemental,
        "files": manifest,
        "input": data,
        "gates": gates,
    }


def read_package(tx, branch_id, package_id):
    row = tx.execute(
        "SELECT id,created,payload FROM artifacts WHERE branch_id=? AND id=? AND kind=? AND status='accepted'",
        (branch_id, package_id, PREVIEW),
    ).fetchone()
    if not row:
        raise DomainError("not_found", "当前工作版本中没有这份交付预览。", 404)
    snapshot = json.loads(row["payload"])
    if snapshot.get("version") != VERSION:
        raise DomainError("delivery_version", "交付版本不兼容，旧交付资料已保留。", 409)
    if (
        snapshot["chapters"] != snapshot["basis"]["chapters"]
        or digest(snapshot["basis"]) != snapshot["basis_sha256"]
    ):
        raise DomainError("delivery_integrity", "交付依据与原清单不一致，请先核对作品备份。", 409)
    return {"id": row["id"], "created": row["created"], **snapshot}


def confirmation(tx, branch_id, package_id):
    return tx.execute(
        "SELECT id FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(payload,'$.package_id')=? LIMIT 1",
        (branch_id, CONFIRMATION, package_id),
    ).fetchone()


def all_gates_pass(snapshot):
    return [gate["id"] for gate in snapshot["gates"]] == list(GATES) and all(
        gate["status"] == "pass" for gate in snapshot["gates"]
    )


def package_view(tx, branch_id, snapshot, live_sha):
    finished = bool(confirmation(tx, branch_id, snapshot["id"]))
    status = (
        "outdated"
        if live_sha != snapshot["basis_sha256"]
        else "finished"
        if finished
        else "ready"
        if all_gates_pass(snapshot)
        else "draft"
    )
    return {
        **{
            key: snapshot[key]
            for key in ("id", "created", "basis_sha256", "chapters", "gates", "files", "input")
        },
        "status": status,
        "finished": finished,
    }


def save_artifact(tx, branch, identifier, kind, payload):
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,'{}','accepted',?)",
        (identifier, branch["novel_id"], branch["id"], kind, dumps(payload), time()),
    )


def operation_receipt(tx, branch_id, operation, data, package_id=None):
    key = digest([branch_id, operation, data.idempotency_key])
    fingerprint = digest([package_id, data.model_dump()])
    row = tx.execute("SELECT result FROM receipts WHERE key=?", (key,)).fetchone()
    if row:
        prior = json.loads(row["result"])
        if prior.get("delivery_fingerprint") != fingerprint:
            raise DomainError("idempotency", "这个交付确认标识已用于其他输入。", 409)
        package_id = prior["package_id"]
    else:
        package_id = None
    return key, fingerprint, package_id


def record_receipt(tx, key, fingerprint, artifact_id, package_id):
    tx.execute(
        "INSERT INTO receipts VALUES(?,?,?)",
        (key, artifact_id, dumps({"delivery_fingerprint": fingerprint, "package_id": package_id})),
    )


def require_basis(expected, live):
    if expected != live:
        raise DomainError("delivery_changed", "正文、资料或核对依据已改变，请重新读取并生成交付预览。", 409)


def preview_delivery(tx, branch_id, data):
    branch, basis, texts = live_basis(tx, branch_id)
    live = digest(basis)
    key, fingerprint, old = operation_receipt(tx, branch_id, "preview", data)
    if old:
        return package_view(tx, branch_id, read_package(tx, branch_id, old), live)
    require_basis(data.base_sha256, live)
    snapshot = make_snapshot(basis, texts, data.model_dump(exclude={"base_sha256", "idempotency_key"}))
    identifier = uid()
    save_artifact(tx, branch, identifier, PREVIEW, snapshot)
    record_receipt(tx, key, fingerprint, identifier, identifier)
    return package_view(tx, branch_id, read_package(tx, branch_id, identifier), live)


def finalize_delivery(tx, branch_id, package_id, data):
    branch, basis, _ = live_basis(tx, branch_id)
    live = digest(basis)
    snapshot = read_package(tx, branch_id, package_id)
    key, fingerprint, old = operation_receipt(tx, branch_id, "confirm", data, package_id)
    if old:
        return package_view(tx, branch_id, snapshot, live)
    require_basis(data.base_sha256, live)
    require_basis(snapshot["basis_sha256"], live)
    if not all_gates_pass(snapshot):
        raise DomainError("delivery_blocked", "六项均通过后才可确认正式完本。", 409)
    # Read from the same frozen revisions before declaring the package complete.
    frozen_materials(tx, branch_id, snapshot)
    existing = confirmation(tx, branch_id, package_id)
    confirmation_id = existing["id"] if existing else uid()
    if not existing:
        save_artifact(
            tx,
            branch,
            confirmation_id,
            CONFIRMATION,
            {"version": VERSION, "package_id": package_id, "basis_sha256": live},
        )
    record_receipt(tx, key, fingerprint, confirmation_id, package_id)
    return package_view(tx, branch_id, snapshot, live)


def frozen_materials(tx, branch_id, snapshot):
    texts = {}
    for chapter in snapshot["chapters"]:
        row = tx.execute(
            """SELECT r.content FROM revisions r JOIN chapters c ON c.id=r.chapter_id
            WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?""",
            (branch_id, chapter["id"], chapter["revision"]),
        ).fetchone()
        if not row or digest(row["content"]) != chapter["sha256"]:
            raise DomainError("delivery_integrity", "保存修订无法通过交付清单校验，请先核对作品备份。", 409)
        texts[chapter["id"]] = row["content"]
    files = material_texts(
        snapshot["chapters"], texts, snapshot["supplemental"], snapshot["sample_chapter_ids"]
    )
    if material_manifest(files) != snapshot["files"]:
        raise DomainError("delivery_integrity", "交付材料与原清单不一致，请先核对作品备份。", 409)
    return files


def delivery_zip(snapshot, files):
    manifest = {
        "version": VERSION,
        "package_id": snapshot["id"],
        "basis_sha256": snapshot["basis_sha256"],
        "chapters": snapshot["chapters"],
        "sample_chapter_ids": snapshot["sample_chapter_ids"],
        "spoiler_scope": snapshot["input"]["spoiler_scope"],
        "files": [{key: value for key, value in f.items() if key != "preview"} for f in snapshot["files"]],
        "definition_ids": [r["id"] for r in snapshot["basis"]["definitions"]],
        "outline_versions": [
            {"chapter_id": o["chapter_id"], "version": o["version"], "source": o["source"]}
            for o in snapshot["basis"]["outlines"]
        ],
        "project_brief_id": (snapshot["basis"]["project_brief"] or {}).get("id"),
        "story_record_revisions": [
            {key: r[key] for key in ("id", "revision")} for r in snapshot["basis"]["story_records"]
        ],
        "ledger_revisions": [
            {key: r[key] for key in ("id", "revision")} for r in snapshot["basis"]["ledger"]
        ],
        "check_report_ids": [c["report_id"] for c in snapshot["basis"]["checks"]],
        "gates": snapshot["gates"],
    }
    archive = BytesIO()
    contents = [(MATERIALS[key][1], value) for key, value in files.items()]
    contents.append(("manifest.json", dumps(manifest) + "\n"))
    with ZipFile(archive, "w") as bundle:
        for name, text in contents:
            entry = ZipInfo(name)
            entry.compress_type = ZIP_DEFLATED
            bundle.writestr(entry, text.encode("utf-8"))
    return archive.getvalue()


def register_routes(app, domain):
    path = "/api/branches/{branch_id}/delivery"

    @app.get(path, response_model=DeliveryState)
    def delivery_state(branch_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            _, basis, _ = live_basis(tx, branch_id)
            live = digest(basis)
            rows = tx.execute(
                "SELECT id FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid DESC",
                (branch_id, PREVIEW),
            )
            return {
                "basis_sha256": live,
                "chapters": basis["chapters"],
                "packages": [
                    package_view(tx, branch_id, read_package(tx, branch_id, row["id"]), live) for row in rows
                ],
            }

    @app.post(path + "/packages", status_code=201, response_model=Package)
    def preview(branch_id: str, data: PreviewInput):
        with domain.db.connect(True) as tx:
            return preview_delivery(tx, branch_id, data)

    @app.get(path + "/packages/{package_id}", response_model=Package)
    def saved_package(branch_id: str, package_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            snapshot = read_package(tx, branch_id, package_id)
            _, basis, _ = live_basis(tx, branch_id)
            return package_view(tx, branch_id, snapshot, digest(basis))

    @app.post(path + "/packages/{package_id}/finalize", response_model=Package)
    def confirm(branch_id: str, package_id: str, data: ConfirmInput):
        with domain.db.connect(True) as tx:
            return finalize_delivery(tx, branch_id, package_id, data)

    @app.get(path + "/packages/{package_id}/files/{key}", response_class=Response)
    def material(branch_id: str, package_id: str, key: MaterialKey):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            snapshot = read_package(tx, branch_id, package_id)
            files = frozen_materials(tx, branch_id, snapshot)
        return Response(
            content=files[key],
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{MATERIALS[key][1]}"'},
        )

    @app.get(path + "/packages/{package_id}/download", response_class=Response)
    def archive(branch_id: str, package_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            snapshot = read_package(tx, branch_id, package_id)
            if not confirmation(tx, branch_id, package_id):
                raise DomainError("delivery_blocked", "正式完本后可下载完整交付包。", 409)
            files = frozen_materials(tx, branch_id, snapshot)
        return Response(
            content=delivery_zip(snapshot, files),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="delivery-{package_id}.zip"'},
        )
