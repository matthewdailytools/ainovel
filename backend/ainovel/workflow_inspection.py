"""Version-bound manuscript inspection for the ten M6 checks."""

import hashlib
import json
import re
import unicodedata
from time import time
from functools import partial
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .author_contract import assemble_contract
from .db import DomainError, dumps, uid
from .domain import utf16_slice
from .manual_finalization import _known_issues, _manifest
from .models import Input
from .outline_workspace import _chapter_plans, _view
from .project_brief import confirmed_project_tx
from .reference import card_version
from .scene_time import SceneTime, event_basis_valid
from .setting_review import confirmed_world_basis
from .story_graph import Source, graph_snapshot, sources

KIND = "workflow_inspection"
VERSION = "m6-inspection/1"
COUNT_VERSION = "正文有效字符/1，排除 Markdown 标题行、常见排版标记、空白和不可见字符，计入标点"
Scope = Literal["chapter", "volume", "book"]
CheckId = Literal["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
LABELS = {
    "01": "立项及作者约定与正文",
    "02": "章纲必达与章稿",
    "03": "人物身份、关系与状态",
    "04": "故事时间、地点与事件因果",
    "05": "设定规则、数值与资源",
    "06": "视角、人物所知与读者披露",
    "07": "伏笔、悬念与承诺",
    "08": "已确认引用与改稿影响",
    "09": "字数、章数与篇幅",
    "10": "节奏、回报与章节衔接",
}


class AuthorReview(Input):
    check_id: CheckId
    status: Literal["green", "yellow", "red", "not_applicable"]
    basis_note: Text
    reason: Text
    evidence: list[Source] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def needs_evidence(self):
        if self.status in {"green", "red"} and not self.evidence:
            raise ValueError("确认通过或冲突时须引用所查正文。")
        return self


class LengthTargets(Input):
    min_chars: int | None = Field(default=None, strict=True, ge=0)
    max_chars: int | None = Field(default=None, strict=True, ge=1)
    expected_chapters: int | None = Field(default=None, strict=True, ge=1, le=1000)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.min_chars is not None and self.max_chars is not None and self.min_chars > self.max_chars:
            raise ValueError("篇幅下限不能高于上限。")
        return self


class InspectionInput(Input):
    scope: Scope = "book"
    scope_id: str | None = Field(default=None, max_length=128)
    basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    author_reviews: list[AuthorReview] = Field(default_factory=list, max_length=10)
    length_targets: LengthTargets | None = None


def _sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def body_chars(text):
    text = re.sub(r"(?m)^ {0,3}(?:#{1,6}[ \t]+.*|(?:[-*_][ \t]*){3,}|(?:`{3,}|~{3,}).*)$", "", text)
    text = re.sub(r"!\[[^\]\n]*\]\([^\n)]*\)", "", text)
    text = re.sub(r"\[([^\]\n]+)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"(?m)^ {0,3}(?:>[ \t]*|[-+*][ \t]+|\d+[.)][ \t]+)", "", text)
    text = re.sub(r"(\*{1,3}|_{1,3}|~~|`{1,2})(\S[^\n]*?)(?<!\s)\1", r"\2", text)
    return sum(not char.isspace() and unicodedata.category(char)[0] != "C" for char in text)


def _scope(tx, branch_id, outline, scope, scope_id):
    if (scope == "book") != (scope_id is None) or scope_id == "":
        raise DomainError("inspection_scope", "章或卷须提供对应编号，全书范围无需编号。")
    chapters = [
        dict(row)
        for row in tx.execute(
            "SELECT * FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position,id", (branch_id,)
        )
    ]
    if scope == "book":
        return chapters
    if scope == "chapter":
        selected = [chapter for chapter in chapters if chapter["id"] == scope_id]
    else:
        plan = (outline["confirmed"] or {}).get("plan", {"volumes": []})
        volume = next((row for row in plan["volumes"] if row["id"] == scope_id), None)
        ids = {row["chapter_id"] for _, _, row in _chapter_plans({"volumes": [volume]})} if volume else set()
        selected = [chapter for chapter in chapters if chapter["id"] in ids]
    if not selected:
        raise DomainError("inspection_target", "目标章或已确认卷已经移出当前范围。", 409)
    return selected


def _item(check_id, summary, basis, status="pending", *, manifest, scope):
    digest_basis = basis
    if scope != "book":
        # Keep the original ID for provenance while comparing scoped plan content.
        digest_basis = {key: value for key, value in basis.items() if key != "outline_id"}
        if "outline" in digest_basis:
            digest_basis["outline"] = {
                key: value for key, value in digest_basis["outline"].items() if key != "outline_id"
            }
    return {
        "check_id": check_id,
        "title": LABELS[check_id],
        "status": status,
        "summary": summary,
        "basis": basis,
        "basis_sha256": _sha([VERSION, check_id, manifest, digest_basis]),
    }


def _graph_rows(graph, ids, scope):
    result = []
    for row in graph["records"]:
        if row["archived"]:
            continue
        refs = sources(row["payload"])
        if scope == "book" or not refs or any(ref["chapter_id"] in ids for ref in refs):
            result.append(
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    **row["payload"],
                    "invalid_reasons": row["invalid_reasons"],
                }
            )
    return result


def _record_status(rows):
    if any(row["invalid_reasons"] for row in rows):
        return "outdated"
    if any(row.get("status", row.get("truth")) == "conflict" and sources(row) for row in rows):
        return "red"
    return "pending"


def _rule_reports(tx, branch_id, chapters, confirmation_id):
    if not confirmation_id:
        return []
    indexed = {chapter["id"]: chapter for chapter in chapters}
    latest = {}
    for row in tx.execute(
        "SELECT id,chapter_id,base_revision,basis,payload FROM artifacts "
        "WHERE branch_id=? AND kind='setting_review' AND status='accepted' ORDER BY rowid",
        (branch_id,),
    ):
        chapter = indexed.get(row["chapter_id"])
        if not chapter or row["base_revision"] != chapter["revision"] or chapter["stale"]:
            continue
        rule = json.loads(row["basis"])["rule"]
        if rule["confirmation_id"] != confirmation_id:
            continue
        result = json.loads(row["payload"])
        latest[chapter["id"], rule["id"]] = {
            "report_id": row["id"],
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "rule": rule,
            **result,
        }
    return list(latest.values())


def _card_references(tx, branch_id, chapters):
    indexed = {chapter["id"]: chapter for chapter in chapters}
    result = []
    for row in tx.execute(
        "SELECT a.id,a.chapter_id,a.basis,r.result FROM artifacts a JOIN receipts r ON a.id=r.artifact_id "
        "WHERE a.branch_id=? AND a.kind='chapter_draft' AND a.status='accepted' "
        "AND json_type(a.basis,'$.reference_session.card_references')='array' ORDER BY a.rowid,r.rowid",
        (branch_id,),
    ):
        chapter = indexed.get(row["chapter_id"])
        revision = json.loads(row["result"]).get("revision")
        if not chapter or revision != chapter["revision"]:
            continue
        selected = json.loads(row["basis"])["reference_session"]["card_references"]
        for ref in selected:
            session = tx.execute(
                "SELECT * FROM reference_sessions WHERE id=?", (ref["session_id"],)
            ).fetchone()
            resolved = None
            if session:
                try:
                    resolved = card_version(
                        {**dict(session), "payload": json.loads(session["payload"])},
                        ref["card_id"],
                        ref["revision"],
                    )
                except DomainError as error:
                    if error.code not in {"reference_card", "reference_card_incomplete"}:
                        raise
            result.append(
                {
                    **ref,
                    "artifact_id": row["id"],
                    "chapter_id": chapter["id"],
                    "chapter_revision": revision,
                    "source": resolved["source"] if resolved else None,
                }
            )
    return result


def _length(manifest, targets):
    count = sum(row["chars"] for row in manifest)
    problems = []
    if not manifest or any(not row["chars"] for row in manifest):
        problems.append("所查范围有缺章或空稿")
    if targets:
        if targets.expected_chapters is not None and len(manifest) != targets.expected_chapters:
            problems.append("章数偏离冻结目标")
        if (targets.min_chars is not None and count < targets.min_chars) or (
            targets.max_chars is not None and count > targets.max_chars
        ):
            problems.append("正文字符数超出冻结目标")
    measured = f"{len(manifest)} 章、{count} 个有效字符。"
    if problems:
        return "red", measured + "；".join(problems) + "。"
    if targets and any(value is not None for value in targets.model_dump().values()):
        return "green", measured + "符合本次配置目标。"
    return "pending", measured + "请对照采用的篇幅目标。"


def _scene_rows(tx, branch_id, chapters, graph):
    revisions = {chapter["id"]: chapter["revision"] for chapter in chapters}
    records = {row["id"]: row for row in graph["records"]}
    result = []
    for row in tx.execute(
        "SELECT * FROM scene_times WHERE branch_id=? AND archived=0 ORDER BY id",
        (branch_id,),
    ):
        chapter_id = row["chapter_id"]
        if chapter_id not in revisions:
            continue
        payload = SceneTime.model_validate_json(row["payload"]).model_dump()
        character = records.get(payload["character_id"])
        stale = (
            payload["source_revision"] != revisions[chapter_id]
            or bool(payload["character_id"] and (not character or character["archived"]))
            or not event_basis_valid(tx, payload, chapter_id, records)
        )
        result.append(
            {
                "id": row["id"],
                "revision": row["revision"],
                "chapter_id": chapter_id,
                "payload": payload,
                "stale": stale,
            }
        )
    return result


def _evaluate(tx, branch_id, scope, scope_id, targets=None):
    outline = _view(tx, branch_id)
    chapters = _scope(tx, branch_id, outline, scope, scope_id)
    graph = graph_snapshot(tx, branch_id)
    ids = {chapter["id"] for chapter in chapters}
    manifest = _manifest(chapters)
    for row, chapter in zip(manifest, chapters):
        row.update(chars=body_chars(chapter["content"]), stale=bool(chapter["stale"]))

    project = confirmed_project_tx(tx, branch_id)
    intent = tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind='intent' AND status='accepted' "
        "ORDER BY rowid DESC LIMIT 1",
        (branch_id,),
    ).fetchone()
    intent_basis = (
        {
            "id": intent["id"],
            **assemble_contract(json.loads(intent["payload"])),
        }
        if intent
        else None
    )
    confirmed = outline["confirmed"]
    plans = (
        [
            {"volume": volume["title"], "unit": unit["title"], **row}
            for volume, unit, row in _chapter_plans(confirmed["plan"])
            if row["chapter_id"] in ids
        ]
        if confirmed
        else []
    )
    plan_basis = {
        "outline_id": confirmed["id"] if confirmed else None,
        "stale": outline["stale"],
        "plans": plans,
    }
    world = confirmed_world_basis(tx, branch_id)
    reviews = _rule_reports(tx, branch_id, chapters, world["confirmation_id"])
    covered = {(row["chapter_id"], row["rule"]["id"]) for row in reviews if row["result"] == "pass"}
    rules = {(row["chapter_id"], key) for row in plans for key in row["setting_ids"]}
    failures = [row for row in reviews if row["result"] == "fail"]
    if any(row["rule"].get("setting_policy", {}).get("strength") != "soft" for row in failures):
        rule_status = "red"
    elif outline["stale"]:
        rule_status = "outdated"
    elif failures:
        rule_status = "pending"
    elif rules and rules <= covered:
        rule_status = "green"
    else:
        rule_status = "pending"
    records = _graph_rows(graph, ids, scope)
    people = [row for row in records if row["kind"] in {"character", "state", "relationship"}]
    events = [row for row in records if row["kind"] == "event"]
    knowledge = [row for row in records if row.get("category") == "knowledge"]
    scenes = _scene_rows(tx, branch_id, chapters, graph)
    scenes_stale = any(row["stale"] for row in scenes)
    promises = [row for row in records if row["kind"] == "promise"]
    windows = [
        row
        for row in outline["promises"]
        if scope == "book"
        or {row["plant_chapter_id"], row["opens_chapter_id"], row["closes_chapter_id"]} & ids
    ]
    promise_status = _record_status(promises)
    if promise_status != "outdated" and promises:
        if all(row["stages"][-1]["status"] in {"resolved", "retained", "abandoned"} for row in promises):
            promise_status = "green"
        elif scope == "book" or any(row["due"] for row in windows):
            promise_status = "yellow"
    issues, _ = _known_issues(tx, branch_id, graph["story_revision"], chapters)
    invalid = [
        {"id": row["id"], "title": row["title"], "issues": row["invalid_reasons"]}
        for row in records
        if row["invalid_reasons"]
    ]
    impacted = [row["id"] for row in manifest if row["stale"]]
    references = _card_references(tx, branch_id, chapters)
    broken_cards = [row for row in references if not row["source"]]
    length_status, length_summary = _length(manifest, targets)
    item = partial(_item, manifest=manifest, scope=scope)
    items = [
        item(
            "01",
            "对照本范围实际正文核对采用的立项、终局及作者锁定条件。",
            {"project": project, "intent": intent_basis},
        ),
        item(
            "02",
            "逐章核对目标、阻碍、转折、结果与跨章承接。",
            plan_basis,
            "outdated" if outline["stale"] else "pending",
        ),
        item(
            "03",
            "结合维护的人物与关系记录核对身份、状态及变化。",
            {"records": people},
            _record_status(people),
        ),
        item(
            "04",
            "对照实际事件顺序、场景位置及因果依据。",
            {"events": events, "scenes": scenes},
            "outdated" if scenes_stale else _record_status(events),
        ),
        item(
            "05",
            f"计划引用 {len(rules)} 项，已有 {len(rules & covered)} 项的当前相符证据。"
            + ("发现可调整设定的偏离，请核对取舍。" if failures and rule_status == "pending" else ""),
            {
                "confirmation_id": world["confirmation_id"],
                "rules": world["items"],
                "reports": reviews,
                "tracked": sorted(rules),
                "outline_id": plan_basis["outline_id"],
                "outline_stale": outline["stale"],
            },
            rule_status,
        ),
        item(
            "06",
            "结合叙述视角、获知时点与读者披露计划定位证据。",
            {"knowledge": knowledge, "scenes": scenes},
            "outdated" if scenes_stale else _record_status(knowledge),
        ),
        item(
            "07",
            f"当前范围关联 {len(promises)} 条已维护承诺，按埋设、回收窗口与阶段记录核对。",
            {"records": promises, "windows": windows, "outline_id": plan_basis["outline_id"]},
            promise_status,
        ),
        item(
            "08",
            f"待复检章节 {len(impacted)} 章，失效记录 {len(invalid)} 条，"
            f"当前严重意见 {len(issues)} 条，手法卡引用 {len(references)} 条。",
            {
                "impacted_chapters": impacted,
                "invalid_records": invalid,
                "review_blockers": issues,
                "outline_id": plan_basis["outline_id"],
                "card_references": references,
            },
            "red" if issues else "outdated" if impacted or invalid or broken_cards else "pending",
        ),
        item(
            "09",
            length_summary,
            {"method": COUNT_VERSION, "project_length": (project or {}).get("brief", {}).get("length")},
            length_status,
        ),
        item(
            "10",
            "逐章或逐单元核对转折、推进、回报和连续低推进段。",
            {"outline": plan_basis, "scenes": scenes, "events": events},
        ),
    ]
    return {
        "version": VERSION,
        "scope": scope,
        "scope_id": scope_id,
        "chapters": manifest,
        "items": items,
        "basis_sha256": _sha([scope, scope_id, [item["basis_sha256"] for item in items]]),
    }, chapters


def _apply_author_reviews(items, reviews, chapters):
    indexed = {row["id"]: row for row in chapters}
    checks = {row["check_id"]: row for row in items}
    seen = set()
    for review in reviews:
        if review.check_id in seen:
            raise DomainError("inspection_duplicate", "同一检查只能提交一份本轮作者核对。")
        seen.add(review.check_id)
        for evidence in review.evidence:
            chapter = indexed.get(evidence.chapter_id)
            if not chapter or chapter["revision"] != evidence.revision:
                raise DomainError("inspection_evidence", "作者核对须引用本范围的当前章稿。", 409)
            if (
                not evidence.quote.strip()
                or utf16_slice(chapter["content"], evidence.start, evidence.end)[1] != evidence.quote
            ):
                raise DomainError("inspection_evidence", "作者核对引文与冻结正文范围不一致。", 409)
        item = checks[review.check_id]
        item["author_review"] = review.model_dump()
        if item["status"] == "pending" or review.status == "red":
            item["status"] = review.status
        elif item["status"] == "green" and review.status == "yellow":
            item["status"] = review.status


def _latest(tx, branch_id, scope, scope_id):
    return tx.execute(
        "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(payload,'$.scope')=? AND json_extract(payload,'$.scope_id') IS ? "
        "ORDER BY rowid DESC LIMIT 1",
        (branch_id, KIND, scope, scope_id),
    ).fetchone()


def _effective(row, preview):
    report = json.loads(row["payload"])
    current = {item["check_id"]: item["basis_sha256"] for item in preview["items"]} if preview else {}
    for item in report["items"]:
        matched = current.get(item["check_id"]) == item["basis_sha256"]
        item["current_status"] = item["status"] if matched else "outdated"
    return {
        **report,
        "id": row["id"],
        "created": row["created"],
        "current": bool(preview and report["basis_sha256"] == preview["basis_sha256"]),
    }


def latest_book_inspection(tx, branch_id):
    row = _latest(tx, branch_id, "book", None)
    return _effective(row, _evaluate(tx, branch_id, "book", None)[0]) if row else None


def create_inspection(domain, branch_id, data):
    with domain.db.connect(True) as tx:
        report, chapters = _evaluate(tx, branch_id, data.scope, data.scope_id, data.length_targets)
        if report["basis_sha256"] != data.basis_sha256:
            raise DomainError("inspection_changed", "对账依据已变化，请重新读取当前范围。", 409)
        _apply_author_reviews(report["items"], data.author_reviews, chapters)
        report["length_targets"] = data.length_targets.model_dump() if data.length_targets else None
        identifier = uid()
        created = time()
        tx.execute(
            "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
            "SELECT ?,novel_id,id,?,?,?,'accepted',? FROM branches WHERE id=?",
            (
                identifier,
                KIND,
                dumps(report),
                dumps({"basis_sha256": report["basis_sha256"]}),
                created,
                branch_id,
            ),
        )
        return _effective({"id": identifier, "created": created, "payload": dumps(report)}, report)


def register_routes(app, domain):
    endpoint = "/api/branches/{branch_id}/workflow-inspections"

    @app.get(endpoint)
    def inspections(branch_id: str, scope: Scope = "book", scope_id: str | None = None):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            preview, _ = _evaluate(tx, branch_id, scope, scope_id)
            row = _latest(tx, branch_id, scope, scope_id)
            history = [
                dict(item)
                for item in tx.execute(
                    "SELECT id,created FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
                    "AND json_extract(payload,'$.scope')=? AND json_extract(payload,'$.scope_id') IS ? "
                    "ORDER BY rowid DESC LIMIT 30",
                    (branch_id, KIND, scope, scope_id),
                )
            ]
            return {
                "preview": preview,
                "latest": _effective(row, preview) if row else None,
                "history": history,
            }

    @app.post(endpoint, status_code=201)
    def inspect(branch_id: str, data: InspectionInput):
        return create_inspection(domain, branch_id, data)

    @app.get(endpoint + "/{inspection_id}")
    def inspection(branch_id: str, inspection_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            row = tx.execute(
                "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND kind=? AND id=? AND status='accepted'",
                (branch_id, KIND, inspection_id),
            ).fetchone()
            if not row:
                raise DomainError("not_found", "体检单不存在。", 404)
            report = json.loads(row["payload"])
            try:
                preview, _ = _evaluate(tx, branch_id, report["scope"], report["scope_id"])
            except DomainError as error:
                if error.code != "inspection_target":
                    raise
                preview = None
            return _effective(row, preview)
