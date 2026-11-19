"""Version-bound manuscript inspection for the ten M6 checks."""

import hashlib
import json
import re
import unicodedata
from time import time
from functools import cache, partial
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .author_contract import assemble_contract
from .conception import (
    CONFIRMATION,
    _latest as latest_conception_revision,
    _read_payload,
    _workspace,
    setting_is_adopted,
)
from .db import DomainError, dumps, uid
from .domain import utf16_slice
from .manual_finalization import _known_issues, _manifest
from .models import Input
from .outline_workspace import _chapter_plans, _view
from .project_brief import confirmed_project_tx
from .scene_time import SceneTime, event_basis_valid
from .setting_review import confirmed_world_basis, outline_setting_items
from .setting_use_portability import Digest, Identifier
from .settings_portability import SettingsEvidence
from .story_graph import Source, graph_snapshot, known_state_conflicts, sources

KIND = "workflow_inspection"
LENGTH_TARGET_KIND = "inspection_length_target"
VERSION = "m6-inspection/1"
SOFT_DEVIATION_POLICY = "soft-deviation-review/1"
MAX_AUTHOR_EVIDENCE = 30
MAX_SOFT_DEVIATIONS = 1000
COUNT_VERSION = "正文有效字符/2，统一换行，排除 Markdown 标题行、常见排版标记、空白和不可见字符，计入标点"
Scope = Literal["chapter", "volume", "book"]
CheckId = Literal["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ReportId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
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
    evidence: list[Source] = Field(default_factory=list, max_length=MAX_AUTHOR_EVIDENCE + MAX_SOFT_DEVIATIONS)
    source_candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    author_overrides_candidate: bool = Field(default=False, strict=True)
    soft_deviation_report_ids: list[ReportId] = Field(
        default_factory=list, max_length=MAX_SOFT_DEVIATIONS, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def needs_evidence(self):
        if self.status in {"green", "red"} and not self.evidence:
            raise ValueError("确认通过或冲突时须引用所查正文。")
        if self.author_overrides_candidate and (self.source_candidate_id is None or self.status != "green"):
            raise ValueError("作者裁定须引用候选，并明确给出本次通过结论。")
        if self.soft_deviation_report_ids and (
            self.check_id != "05"
            or self.status != "green"
            or len(set(self.soft_deviation_report_ids)) != len(self.soft_deviation_report_ids)
        ):
            raise ValueError("软设定偏离回执须各引用一次，并用于第 05 项的作者通过裁定。")
        if len(self.evidence) > MAX_AUTHOR_EVIDENCE + len(self.soft_deviation_report_ids):
            raise ValueError("常规引文最多 30 处，每条软设定偏离可追加一处反证。")
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


class SaveLengthTargets(Input):
    scope: Scope = "book"
    scope_id: str | None = Field(default=None, max_length=128)
    base_target_id: str | None = Field(default=None, max_length=128)
    targets: LengthTargets

    @model_validator(mode="after")
    def require_constraint(self):
        if all(value is None for value in self.targets.model_dump().values()):
            raise ValueError("至少填写一项数值篇幅目标。")
        return self


class InspectionInput(Input):
    scope: Scope = "book"
    scope_id: str | None = Field(default=None, max_length=128)
    basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    author_reviews: list[AuthorReview] = Field(default_factory=list, max_length=10)
    length_targets: LengthTargets | None = None


class InspectionSettingUse(Input):
    version_id: Digest
    outline_version_id: Identifier
    settings_version_id: Identifier
    item_id: Identifier
    title: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0, strict=True)
    chapter_sha256: Digest
    evidence: SettingsEvidence


def inspection_setting_uses(basis):
    rows = basis.get("author_setting_uses", [])
    if not isinstance(rows, list):
        raise ValueError("设定正文使用依据格式无效。")
    return [InspectionSettingUse.model_validate(row) for row in rows]


def inspection_setting_use_matches(source, version, settings):
    return bool(
        version
        and settings
        and version.action == "confirmed"
        and version.id == source.version_id
        and version.outline_version_id == source.outline_version_id
        and version.settings_version_id == settings.id == source.settings_version_id
        and version.item_id == source.item_id
        and version.chapter_sha256 == source.chapter_sha256
        and version.evidence == source.evidence
        and any(
            item["id"] == source.item_id and item["title"] == source.title and item.get("category_id")
            for item in outline_setting_items(
                [category.model_dump() for category in settings.categories],
                {item_id for item_id, origin in settings.origins.items() if origin.evidence},
            )
        )
    )


def _setting_uses(tx, branch_id, outline, ids):
    from .manuscript_exchange import source_revision

    selected = []
    originals = {}
    for row in outline["settings"]:
        if row["chapter_id"] not in ids or not (proof := row.get("author_evidence")):
            continue
        version = proof["version"]
        evidence = version["evidence"]
        source_id = evidence["revision_id"]
        if source_id not in originals:
            originals[source_id] = source_revision(tx, branch_id, source_id)
        original = originals[source_id]
        if (
            not original
            or original.chapter_id != row["chapter_id"]
            or original.sha256 != version["chapter_sha256"]
        ):
            continue
        selected.append(
            InspectionSettingUse(
                version_id=version["id"],
                outline_version_id=version["outline_version_id"],
                settings_version_id=version["settings_version_id"],
                item_id=version["item_id"],
                title=row["setting"]["title"],
                revision=original.revision,
                chapter_sha256=version["chapter_sha256"],
                evidence=evidence,
            ).model_dump()
        )
    return selected


def _sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def body_chars(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?m)^ {0,3}(?:#{1,6}[ \t]+.*|(?:[-*_][ \t]*){3,}|(?:`{3,}|~{3,}).*)$", "", text)
    text = re.sub(r"!\[[^\]\n]*\]\([^\n)]*\)", "", text)
    text = re.sub(r"\[([^\]\n]+)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"(?m)^(?: {0,3}(?:>[ \t]*|[-+*][ \t]+|\d+[.)][ \t]+))+", "", text)
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


def _item(check_id, summary, basis, status="pending", *, manifest, scope, can_mark_not_applicable=False):
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
        "can_mark_not_applicable": can_mark_not_applicable,
        "basis_sha256": _sha([VERSION, check_id, manifest, digest_basis]),
    }


def _pacing_plan(volumes, chapter_ids):
    """Freeze each applicable outline summary once, in outline order."""
    arrangements = []
    for volume in volumes:
        units = []
        for unit in volume["units"]:
            selected = [row["chapter_id"] for row in unit["chapters"] if row["chapter_id"] in chapter_ids]
            if selected:
                units.append(
                    {**{key: unit[key] for key in ("id", "title", "summary")}, "chapter_ids": selected}
                )
        if units:
            arrangements.append({**{key: volume[key] for key in ("id", "title", "summary")}, "units": units})
    return arrangements


def _scope_promise_windows(outline, ids, scope):
    if scope == "book":
        return outline["promises"], set()
    positions = {row["id"]: row["position"] for row in outline["chapters"]}
    selected_positions = {positions[identifier] for identifier in ids}
    windows, placed = [], set()
    for window in outline["promises"]:
        anchors = [window[key] for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")]
        ordered = [positions[identifier] for identifier in anchors if identifier in positions]
        if len(ordered) == 3 and ordered == sorted(ordered):
            placed.add(window["promise_id"])
            _, opens, closes = ordered
            if any(opens <= position <= closes for position in selected_positions):
                windows.append(window)
                continue
        if ids.intersection(anchors):
            windows.append(window)
    return windows, placed


def _graph_rows(graph, ids, scope, planned_promises, local_promises):
    result = []
    for row in graph["records"]:
        if row["archived"]:
            continue
        refs = sources(row["payload"])
        if (
            scope == "book"
            or row["id"] in local_promises
            or any(ref["chapter_id"] in ids for ref in refs)
            or (not refs and row["id"] not in planned_promises)
        ):
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


def _card_references(tx, branch_id, chapters, plans):
    from .reference_sync import read_reference_version, reference_card_at

    @cache
    def resolve(session_id, card_id, revision, version_id):
        if version_id:
            version = read_reference_version(tx, branch_id, version_id)
            if version and (version.session_id, version.card_id, version.revision) != (
                session_id,
                card_id,
                revision,
            ):
                return None
        else:
            try:
                version = reference_card_at(tx, branch_id, session_id, card_id, revision)
            except DomainError as error:
                if error.code != "reference_citation":
                    raise
                return None
        return version if version and version.assessment == "ready" else None

    def bind(chapter_id, addresses):
        bound = {}
        for ref in addresses:
            session_id, card_id, revision = (ref[key] for key in ("session_id", "card_id", "revision"))
            version_id = ref.get("version_id")
            version = resolve(session_id, card_id, revision, version_id)
            resolved_id = version.id if version else version_id
            bound[(session_id, card_id, revision, resolved_id)] = {
                "session_id": session_id,
                "card_id": card_id,
                "revision": revision,
                **({"version_id": resolved_id} if resolved_id else {}),
                "chapter_id": chapter_id,
                # Inspection archives carry a source address, never an external excerpt.
                "source": version.citation.model_dump() if version else None,
            }
        return bound.values()

    planned = [
        row for chapter in plans for row in bind(chapter["chapter_id"], chapter.get("reference_cards", []))
    ]
    indexed = {chapter["id"]: chapter for chapter in chapters}
    adopted = []
    for row in tx.execute(
        "SELECT a.id,a.chapter_id,a.basis,r.result FROM artifacts a JOIN receipts r ON a.id=r.artifact_id "
        "WHERE a.branch_id=? AND a.kind IN ('chapter_draft','text_patch') AND a.status='accepted' "
        "AND (json_type(a.basis,'$.reference_session.card_references')='array' "
        "OR json_type(a.basis,'$.outline.reference_cards')='array') ORDER BY a.rowid,r.rowid",
        (branch_id,),
    ):
        chapter = indexed.get(row["chapter_id"])
        revision = json.loads(row["result"]).get("revision")
        if not chapter or revision != chapter["revision"]:
            continue
        basis = json.loads(row["basis"])
        selected = (basis.get("reference_session") or {}).get("card_references", [])
        chapter_plan = (basis.get("outline") or {}).get("reference_cards", [])
        adopted.extend(
            {**ref, "artifact_id": row["id"], "chapter_revision": revision}
            for ref in bind(chapter["id"], [*selected, *chapter_plan])
        )
    return planned, adopted


def current_length_target(tx, branch_id, scope, scope_id, *, include_cleared=False):
    row = tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(payload,'$.scope')=? AND json_extract(payload,'$.scope_id') IS ? ORDER BY rowid DESC LIMIT 1",
        (branch_id, LENGTH_TARGET_KIND, scope, scope_id),
    ).fetchone()
    if not row:
        return None
    stored = json.loads(row["payload"])
    if stored["targets"] is None and not include_cleared:
        return None
    return {"id": row["id"], "revision": stored["revision"], "targets": stored["targets"]}


def _save_length_target(tx, branch_id, scope, scope_id, current, targets):
    identifier = uid()
    revision = current["revision"] + 1 if current else 1
    payload = {"scope": scope, "scope_id": scope_id, "revision": revision, "targets": targets}
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "SELECT ?,novel_id,id,?,?,?,'accepted',? FROM branches WHERE id=?",
        (
            identifier,
            LENGTH_TARGET_KIND,
            dumps(payload),
            dumps({"previous_target_id": current["id"] if current else None}),
            time(),
            branch_id,
        ),
    )


def length_result(manifest, targets):
    count = sum(row["chars"] for row in manifest)
    problems = []
    if not manifest or any(not row["chars"] for row in manifest):
        problems.append("所查范围有缺章或空稿")
    if targets:
        if targets["expected_chapters"] is not None and len(manifest) != targets["expected_chapters"]:
            problems.append("章数偏离冻结目标")
        if (targets["min_chars"] is not None and count < targets["min_chars"]) or (
            targets["max_chars"] is not None and count > targets["max_chars"]
        ):
            problems.append("正文字符数超出冻结目标")
    measured = f"{len(manifest)} 章、{count} 个有效字符。"
    if problems:
        return "red", measured + "；".join(problems) + "。"
    if targets:
        return "green", measured + "符合本次配置目标。"
    return "pending", measured + "请对照采用的篇幅目标。"


def length_assessment(tx, branch_id, scope, manifest, confirmed_outline, length_revision):
    target = length_revision["targets"] if length_revision else None
    status, summary = length_result(manifest, target)
    if scope == "chapter":
        return status, summary, []
    rows = tx.execute(
        "SELECT id,payload FROM (SELECT id,payload,ROW_NUMBER() OVER("
        "PARTITION BY json_extract(payload,'$.scope'),json_extract(payload,'$.scope_id') "
        "ORDER BY rowid DESC) AS rank FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND (json_extract(payload,'$.scope')='chapter' OR "
        "(?='book' AND json_extract(payload,'$.scope')='volume'))) WHERE rank=1",
        (branch_id, LENGTH_TARGET_KIND, scope),
    )
    targets = {}
    for row in rows:
        payload = json.loads(row["payload"])
        targets[payload["scope"], payload["scope_id"]] = {
            "id": row["id"],
            "revision": payload["revision"],
            "targets": payload["targets"],
        }
    if not targets:
        return status, summary, []

    active = {row["id"]: row for row in manifest}
    ranges = []
    if scope == "book" and confirmed_outline:
        for volume in confirmed_outline["plan"]["volumes"]:
            members = [
                active[chapter_id]
                for _, _, chapter in _chapter_plans({"volumes": [volume]})
                if (chapter_id := chapter["chapter_id"]) in active
            ]
            if members:
                ranges.append(("volume", volume["id"], volume["title"], members))
    ranges.extend(("chapter", row["id"], row["title"], [row]) for row in manifest)
    descendants = []
    for child_scope, identifier, title, members in ranges:
        child = targets.get((child_scope, identifier))
        if child is None:
            continue
        if child["targets"] is None:
            child_status, detail = "cleared", "已清除本范围的数值目标。"
        else:
            child_status, detail = length_result(members, child["targets"])
        descendants.append(
            {
                "scope": child_scope,
                "scope_id": identifier,
                "title": title,
                "chapter_ids": [row["id"] for row in members],
                "length_target": child,
                "status": child_status,
                "summary": detail,
            }
        )
    failures = sum(row["status"] == "red" for row in descendants)
    if failures:
        status = "red"
        summary += f"其中 {failures} 个章或卷未达到各自已采用的篇幅目标。"
    return status, summary, descendants


def _world_needs_confirmation(tx, branch_id):
    previous = latest_conception_revision(tx, branch_id, CONFIRMATION)
    versions = [_workspace(tx, branch_id), *([_read_payload(previous)] if previous else [])]
    return any(
        item["detail"].strip()
        for version in versions
        for category in version["categories"]
        if category["id"] == "world" and not category["skipped"]
        for item in category["items"]
        if setting_is_adopted(item.get("adoption_state"))
    )


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


def _viewpoint_basis(knowledge, scenes, graph):
    anchors = {row[key] for row in knowledge for key in ("event_id", "until_event_id")} - {None}
    people = {row["character_id"] for row in knowledge} | {
        row["payload"]["character_id"] for row in scenes if row["payload"]["character_id"]
    }
    events = []
    names = {}
    for row in graph["records"]:
        payload = row["payload"]
        if row["id"] in anchors and row["kind"] == "event":
            events.append(
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    **{key: payload[key] for key in ("title", "status", "story_time", "story_order")},
                    "stale": bool(row["archived"] or row["invalid_reasons"]),
                }
            )
        if row["id"] in people and row["kind"] == "character":
            names[row["id"]] = payload["title"]
    return {
        "knowledge": knowledge,
        "scenes": scenes,
        **({"knowledge_events": events} if events else {}),
        **({"viewpoint_names": names} if names else {}),
    }


def _evaluate(tx, branch_id, scope, scope_id, *, roll_up=True):
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
    pacing_plan = _pacing_plan(confirmed["plan"]["volumes"] if confirmed else [], ids)
    world = confirmed_world_basis(tx, branch_id)
    world_needs_confirmation = not world["confirmation_id"] and _world_needs_confirmation(tx, branch_id)
    reviews = _rule_reports(tx, branch_id, chapters, world["confirmation_id"])
    covered = {(row["chapter_id"], row["rule"]["id"]) for row in reviews if row["result"] == "pass"}
    world_ids = {rule["id"] for rule in world["items"]}
    rules = {(row["chapter_id"], key) for row in plans for key in row["setting_ids"] if key in world_ids}
    failures = [row for row in reviews if row["result"] == "fail"]
    if any(row["rule"].get("setting_policy", {}).get("strength") != "soft" for row in failures):
        rule_status = "red"
    elif outline["stale"] or world_needs_confirmation:
        rule_status = "outdated"
    elif failures:
        rule_status = "yellow"
    elif rules and rules <= covered:
        rule_status = "green"
    else:
        rule_status = "pending"
    windows, planned_promises = _scope_promise_windows(outline, ids, scope)
    records = _graph_rows(graph, ids, scope, planned_promises, {window["promise_id"] for window in windows})
    people = [row for row in records if row["kind"] in {"character", "state", "relationship"}]
    state_conflicts = known_state_conflicts(graph, ids)
    people_basis = {
        "records": people,
        **({"state_conflicts": state_conflicts} if state_conflicts else {}),
    }
    events = [row for row in records if row["kind"] == "event"]
    knowledge = [row for row in records if row.get("category") == "knowledge"]
    scenes = _scene_rows(tx, branch_id, chapters, graph)
    scenes_stale = any(row["stale"] for row in scenes)
    promises = [row for row in records if row["kind"] == "promise"]
    required_open = [
        window
        for window in windows
        if window.get("fulfillment") == "author_required" and not window.get("fulfillment_met")
    ]
    required_overdue = [window for window in required_open if window["due"] == "due"]
    promise_summary = f"当前范围关联 {len(promises)} 条已维护承诺，按埋设、回收窗口与阶段记录核对。"
    promise_status = _record_status(promises)
    if promise_status != "outdated" and promises:
        if all(row["stages"][-1]["status"] in {"resolved", "retained", "abandoned"} for row in promises):
            promise_status = "green"
        elif scope == "book" or any(row["due"] for row in windows):
            promise_status = "yellow"
    if required_overdue:
        promise_status = "red"
        promise_summary += "必达承诺到期仍缺回收窗口内的有效原文：" + "、".join(
            row["title"] for row in required_overdue
        )
    elif required_open:
        if promise_status != "outdated":
            promise_status = "yellow"
        promise_summary += "待兑现的必达承诺：" + "、".join(row["title"] for row in required_open)
    issues, _ = _known_issues(tx, branch_id, graph["story_revision"], chapters)
    invalid = [
        {"id": row["id"], "title": row["title"], "issues": row["invalid_reasons"]}
        for row in records
        if row["invalid_reasons"]
    ]
    impacted = [row["id"] for row in manifest if row["stale"]]
    planned_references, references = _card_references(tx, branch_id, chapters, plans)
    setting_uses = _setting_uses(tx, branch_id, outline, ids)
    broken_cards = [row for row in [*planned_references, *references] if not row["source"]]
    length_revision = current_length_target(tx, branch_id, scope, scope_id, include_cleared=True)
    length_target = length_revision if length_revision and length_revision["targets"] is not None else None
    length_status, length_summary, descendant_lengths = length_assessment(
        tx, branch_id, scope, manifest, confirmed, length_revision
    )
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
            (
                f"同一故事时点有 {len(state_conflicts)} 组相互矛盾的人物状态，请核对双方原文并更正记录。"
                if state_conflicts
                else "结合维护的人物与关系记录核对身份、状态及变化。"
            ),
            people_basis,
            "red" if state_conflicts else _record_status(people),
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
            + (
                "发现可调整设定的偏离，可修稿复检或逐条引用原文并记录取舍。"
                if rule_status == "yellow"
                else ""
            ),
            {
                "confirmation_id": world["confirmation_id"],
                "rules": world["items"],
                "reports": reviews,
                "tracked": sorted(rules),
                "outline_id": plan_basis["outline_id"],
                "outline_stale": outline["stale"],
                "world_needs_confirmation": world_needs_confirmation,
                # Reclassifying a frozen soft finding requires reassessment only for the affected check.
                **({"soft_deviation_policy": SOFT_DEVIATION_POLICY} if rule_status == "yellow" else {}),
            },
            rule_status,
            can_mark_not_applicable=(
                not world["items"]
                and not rules
                and not reviews
                and not outline["stale"]
                and not world_needs_confirmation
            ),
        ),
        item(
            "06",
            "结合叙述视角、获知时点与读者披露计划定位证据。",
            _viewpoint_basis(knowledge, scenes, graph),
            "outdated" if scenes_stale else _record_status(knowledge),
        ),
        item(
            "07",
            promise_summary,
            {"records": promises, "windows": windows, "outline_id": plan_basis["outline_id"]},
            promise_status,
            can_mark_not_applicable=not promises and not windows and not outline["stale"],
        ),
        item(
            "08",
            f"待复检章节 {len(impacted)} 章，失效记录 {len(invalid)} 条，"
            f"当前严重意见 {len(issues)} 条，章纲计划手法卡 {len(planned_references)} 条，"
            f"已采用稿依据 {len(references)} 条。"
            + (f"已确认设定使用 {len(setting_uses)} 条。" if setting_uses else ""),
            {
                "impacted_chapters": impacted,
                "invalid_records": invalid,
                "review_blockers": issues,
                "outline_id": plan_basis["outline_id"],
                **({"planned_card_references": planned_references} if planned_references else {}),
                "card_references": references,
                **(
                    {
                        "author_setting_use_outline_id": setting_uses[0]["outline_version_id"],
                        "author_setting_uses": setting_uses,
                    }
                    if setting_uses
                    else {}
                ),
            },
            "red" if issues else "outdated" if impacted or invalid or broken_cards else "pending",
        ),
        item(
            "09",
            length_summary,
            {
                "method": COUNT_VERSION,
                "project_length": (project or {}).get("brief", {}).get("length"),
                "length_target": length_revision,
                **({"descendant_length_targets": descendant_lengths} if descendant_lengths else {}),
            },
            length_status,
        ),
        item(
            "10",
            "对照适用卷纲和单元安排，核对转折、推进、回报和连续低推进段。",
            {"outline": plan_basis, "pacing_plan": pacing_plan, "scenes": scenes, "events": events},
        ),
    ]
    findings = _scoped_red_findings(tx, branch_id, scope, ids) if roll_up else {}
    for index, check in enumerate(items):
        linked = findings.get(check["check_id"])
        if not linked:
            continue
        items[index] = item(
            check["check_id"],
            check["summary"] + f" 本范围有 {len(linked)} 条章或卷红项待复检。",
            {**check["basis"], "scoped_findings": linked},
            "red",
        )
    return {
        "version": VERSION,
        "scope": scope,
        "scope_id": scope_id,
        "chapters": manifest,
        "length_target": length_target,
        "items": items,
        "basis_sha256": _sha([scope, scope_id, [item["basis_sha256"] for item in items]]),
    }, chapters


def _contains_source(citation, original):
    if (
        citation.chapter_id != original.chapter_id
        or citation.revision != original.revision
        or not citation.start <= original.start < original.end <= citation.end
    ):
        return False
    try:
        return (
            utf16_slice(citation.quote, original.start - citation.start, original.end - citation.start)[1]
            == original.quote
        )
    except DomainError:
        return False


def validate_soft_deviation_review(basis, review):
    reports = basis.get("reports")
    if not isinstance(reports, list) or not all(isinstance(row, dict) for row in reports):
        raise DomainError("inspection_soft_deviation", "软设定偏离的原对账依据无效。", 409)
    failed = [row for row in reports if row.get("result") == "fail"]
    failures = {row["report_id"]: row for row in failed if isinstance(row.get("report_id"), str)}
    if not failures or len(failures) != len(failed) or set(review.soft_deviation_report_ids) != set(failures):
        raise DomainError("inspection_soft_deviation", "通过前须明确核对本范围的全部软设定偏离。", 409)
    for row in failures.values():
        rule = row.get("rule")
        policy = rule.get("setting_policy") if isinstance(rule, dict) else None
        if not isinstance(policy, dict) or policy.get("strength") != "soft":
            raise DomainError("inspection_soft_deviation", "作者裁定须关联已采用的软设定。", 409)
        evidence = row["evidence"] if isinstance(row.get("evidence"), dict) else {}
        try:
            original = Source(
                chapter_id=row.get("chapter_id"),
                revision=row.get("revision"),
                start=evidence.get("start"),
                end=evidence.get("end"),
                quote=evidence.get("text"),
            )
        except ValidationError as error:
            raise DomainError("inspection_soft_deviation", "软设定偏离缺少有效原文，须复检。", 409) from error
        if not original.quote.strip() or not any(
            _contains_source(source, original) for source in review.evidence
        ):
            raise DomainError("inspection_soft_deviation", "本次裁定须逐条引用覆盖偏离位置的当前正文。", 409)


def _apply_author_reviews(tx, branch_id, report, reviews, chapters):
    indexed = {row["id"]: row for row in chapters}
    checks = {row["check_id"]: row for row in report["items"]}
    seen = set()
    for review in reviews:
        if review.check_id in seen:
            raise DomainError("inspection_duplicate", "同一检查只能提交一份本轮作者核对。")
        seen.add(review.check_id)
        item = checks[review.check_id]
        if review.check_id == "03" and item["basis"].get("state_conflicts") and review.status != "red":
            raise DomainError(
                "inspection_state_conflict", "同一故事时点仍有相互矛盾的人物状态，请先更正记录再签认。", 409
            )
        if review.check_id in {"04", "06"} and item["status"] == "outdated" and review.status == "green":
            raise DomainError(
                "inspection_story_outdated", "事件、场景或知情依据已过期，请先核对后再确认通过。", 409
            )
        if review.status == "not_applicable" and not item["can_mark_not_applicable"]:
            raise DomainError("inspection_required", f"{item['title']}须核对，不能标为不适用。")
        for evidence in review.evidence:
            chapter = indexed.get(evidence.chapter_id)
            if not chapter or chapter["revision"] != evidence.revision:
                raise DomainError("inspection_evidence", "作者核对须引用本范围的当前章稿。", 409)
            if (
                not evidence.quote.strip()
                or utf16_slice(chapter["content"], evidence.start, evidence.end)[1] != evidence.quote
            ):
                raise DomainError("inspection_evidence", "作者核对引文与冻结正文范围不一致。", 409)
        soft_deviations_resolved = (
            item["check_id"] == "05"
            and item["status"] == "yellow"
            and item["basis"].get("soft_deviation_policy") == SOFT_DEVIATION_POLICY
            and review.status == "green"
        )
        if soft_deviations_resolved:
            validate_soft_deviation_review(item["basis"], review)
        elif review.soft_deviation_report_ids:
            raise DomainError("inspection_soft_deviation", "当前检查没有可按软设定处理的偏离。", 409)
        recorded = review.model_dump()
        if review.source_candidate_id:
            from .inspection_semantic import candidate_for_review_tx

            candidate = candidate_for_review_tx(
                tx, branch_id, review.source_candidate_id, report, review.check_id
            )
            if (
                review.status == "green"
                and candidate["result"] != "pass"
                and (not review.author_overrides_candidate or review.reason == candidate["reason"])
            ):
                raise DomainError(
                    "inspection_candidate_override",
                    "候选指出冲突或证据不足；确认通过前须填写作者自己的裁定理由。",
                )
            recorded["candidate_result"] = candidate["result"]
        item["author_review"] = recorded
        if item["status"] == "pending" or review.status == "red" or soft_deviations_resolved:
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


def _same_length_inputs(report, preview):
    historical = report["chapters"]
    current = preview["chapters"]
    if (report["scope"], report["scope_id"]) != (preview["scope"], preview["scope_id"]):
        return False
    if historical == current or len(historical) != len(current):
        return False
    return all(
        {key: value for key, value in previous.items() if key != "stale"}
        == {key: value for key, value in latest.items() if key != "stale"}
        for previous, latest in zip(historical, current, strict=True)
    )


def _same_exemption_scope(report, preview):
    return (report["scope"], report["scope_id"]) == (preview["scope"], preview["scope_id"]) and [
        row["id"] for row in report["chapters"]
    ] == [row["id"] for row in preview["chapters"]]


def _direct_check_basis(check, report):
    basis = check["basis"]
    if "scoped_findings" not in basis:
        return basis, check["basis_sha256"]
    direct = {key: value for key, value in basis.items() if key != "scoped_findings"}
    digest = _item(
        check["check_id"], check["summary"], direct, manifest=report["chapters"], scope=report["scope"]
    )["basis_sha256"]
    return direct, digest


def _matching_check(item, latest, report, preview, *, direct=False):
    if not latest:
        return False
    if direct:
        if report["version"] != preview["version"]:
            return False
        old_basis, old_digest = _direct_check_basis(item, report)
        new_basis, new_digest = _direct_check_basis(latest, preview)
    else:
        old_basis, old_digest = item["basis"], item["basis_sha256"]
        new_basis, new_digest = latest["basis"], latest["basis_sha256"]
    # Continuity changes can require a new report without changing the frozen length calculation.
    return old_digest == new_digest or (
        item["check_id"] == "09" and old_basis == new_basis and _same_length_inputs(report, preview)
    )


def _effective(row, preview, *, direct_only=False):
    report = json.loads(row["payload"])
    current = {item["check_id"]: item for item in preview["items"]} if preview else {}
    invalid_exemption = False
    for item in report["items"]:
        latest = current.get(item["check_id"], {})
        verdict = item["status"]
        inherited = bool(item["basis"].get("scoped_findings"))
        author_verdict = (item.get("author_review") or {}).get("status")
        if direct_only and inherited and author_verdict != "red" and latest.get("status") != "red":
            # A signed local pass can close a direct finding while the chapter finding stays red.
            verdict = (
                "green"
                if author_verdict == "green" and latest.get("status") in {"green", "pending"}
                else "outdated"
            )
        direct_red = (
            report["scope"] == "volume" and verdict == "red" and (not inherited or author_verdict == "red")
        )
        matched = _matching_check(item, latest, report, preview, direct=direct_only or direct_red)
        allowed = item["status"] != "not_applicable" or latest.get("can_mark_not_applicable", False)
        if (
            not matched
            and preview
            and item["status"] == "not_applicable"
            and allowed
            and not (item.get("author_review") or {}).get("evidence")
        ):
            # These exemptions depend on absence of applicable material in the same chapter range.
            matched = _same_exemption_scope(report, preview)
        if not allowed:
            invalid_exemption = True
        item["current_status"] = verdict if matched and allowed else "outdated"
    return {
        **report,
        "id": row["id"],
        "created": row["created"],
        "current": bool(
            preview and not invalid_exemption and report["basis_sha256"] == preview["basis_sha256"]
        ),
    }


def _scoped_red_findings(tx, branch_id, scope, chapter_ids):
    if scope == "chapter":
        return {}
    affected = tx.execute(
        """
        SELECT DISTINCT json_extract(payload,'$.scope') AS scope,
                        json_extract(payload,'$.scope_id') AS scope_id
        FROM artifacts
        WHERE branch_id=? AND kind=? AND status='accepted'
          AND json_extract(payload,'$.scope') IN ('chapter','volume')
          AND EXISTS (
              SELECT 1 FROM json_each(payload,'$.items') AS check_item
              WHERE json_extract(check_item.value,'$.status')='red'
          )
        ORDER BY scope,scope_id
        """,
        (branch_id, KIND),
    ).fetchall()
    findings = {}
    for source in affected:
        source_scope, source_id = source["scope"], source["scope_id"]
        if scope == "volume" and (source_scope != "chapter" or source_id not in chapter_ids):
            continue
        try:
            preview, _ = _evaluate(tx, branch_id, source_scope, source_id, roll_up=False)
        except DomainError as error:
            if error.code != "inspection_target":
                raise
            continue
        reports = tx.execute(
            "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
            "AND json_extract(payload,'$.scope')=? AND json_extract(payload,'$.scope_id')=? "
            "ORDER BY rowid",
            (branch_id, KIND, source_scope, source_id),
        )
        decisions = {}
        for row in reports:
            for item in _effective(row, preview, direct_only=True)["items"]:
                check_id = item["check_id"]
                verdict = item["current_status"]
                author_verdict = (item.get("author_review") or {}).get("status")
                if verdict == "red":
                    decisions[check_id] = {
                        "scope": source_scope,
                        "scope_id": source_id,
                        "report_id": row["id"],
                    }
                elif verdict in {"green", "not_applicable"} and verdict == author_verdict:
                    decisions.pop(check_id, None)
        for check_id, decision in decisions.items():
            findings.setdefault(check_id, []).append(decision)
    return findings


def latest_book_inspection(tx, branch_id, *, include_current_red=False):
    row = _latest(tx, branch_id, "book", None)
    if not row:
        return None
    preview, _ = _evaluate(tx, branch_id, "book", None)
    result = _effective(row, preview)
    if include_current_red:
        result["current_red_checks"] = [
            item["check_id"] for item in preview["items"] if item["status"] == "red"
        ]
    return result


def create_inspection(domain, branch_id, data, *, before_write=None):
    with domain.db.connect(True) as tx:
        if before_write is not None:
            before_write(tx)
        report, chapters = _evaluate(tx, branch_id, data.scope, data.scope_id)
        if report["basis_sha256"] != data.basis_sha256:
            raise DomainError("inspection_changed", "对账依据已变化，请重新读取当前范围。", 409)
        target = report["length_target"]
        if data.length_targets:
            requested = data.length_targets.model_dump()
            if target and requested != target["targets"]:
                raise DomainError("inspection_length_changed", "请先保存新的篇幅目标，再生成体检单。", 409)
            if not target and any(value is not None for value in requested.values()):
                # Initial numeric targets and the first report may be saved together.
                current = current_length_target(
                    tx, branch_id, data.scope, data.scope_id, include_cleared=True
                )
                _save_length_target(tx, branch_id, data.scope, data.scope_id, current, requested)
                report, chapters = _evaluate(tx, branch_id, data.scope, data.scope_id)
                target = report["length_target"]
        _apply_author_reviews(tx, branch_id, report, data.author_reviews, chapters)
        report["length_targets"] = target["targets"] if target else None
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
        from .workflow_archive import archive_origin, list_archived_workflow

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
            history.extend(
                {**{key: item[key] for key in ("id", "created", "scope", "scope_id")}, **archive_origin(item)}
                for item in list_archived_workflow(
                    tx, branch_id, KIND, 30, scope=None if scope == "book" else scope, scope_id=scope_id
                )
            )
            history.sort(key=lambda item: -item["created"])
            return {
                "preview": preview,
                "latest": _effective(row, preview) if row else None,
                "history": history[:30],
            }

    @app.post(endpoint, status_code=201)
    def inspect(branch_id: str, data: InspectionInput):
        return create_inspection(domain, branch_id, data)

    @app.put(endpoint + "/length-targets")
    def save_length_targets(branch_id: str, data: SaveLengthTargets):
        with domain.db.connect(True) as tx:
            _scope(tx, branch_id, _view(tx, branch_id), data.scope, data.scope_id)
            current = current_length_target(tx, branch_id, data.scope, data.scope_id, include_cleared=True)
            active = current if current and current["targets"] is not None else None
            if data.base_target_id != (active["id"] if active else None):
                raise DomainError("inspection_length_changed", "篇幅目标已在别处更新，请重新读取。", 409)
            targets = data.targets.model_dump()
            if not active or active["targets"] != targets:
                _save_length_target(tx, branch_id, data.scope, data.scope_id, current, targets)
            preview, _ = _evaluate(tx, branch_id, data.scope, data.scope_id)
            row = _latest(tx, branch_id, data.scope, data.scope_id)
            return {"preview": preview, "latest": _effective(row, preview) if row else None}

    @app.get(endpoint + "/{inspection_id}")
    def inspection(branch_id: str, inspection_id: str):
        from .workflow_archive import (
            archive_origin,
            archived_workflow,
            current_sources_match,
            inspection_revalidation,
        )

        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            row = tx.execute(
                "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND kind=? AND id=? AND status='accepted'",
                (branch_id, KIND, inspection_id),
            ).fetchone()
            if not row:
                archived = archived_workflow(tx, branch_id, KIND, inspection_id)
                if not archived:
                    raise DomainError("not_found", "体检单不存在。", 404)
                record = json.loads(archived["payload"])
                row = {
                    "id": archived["id"],
                    "created": archived["created"],
                    "payload": dumps(record["report"]),
                }
            else:
                record = None
            report = json.loads(row["payload"])
            try:
                preview, _ = _evaluate(tx, branch_id, report["scope"], report["scope_id"])
            except DomainError as error:
                if error.code != "inspection_target":
                    raise
                preview = None
            value = _effective(row, preview)
            if record:
                reconciles = bool(
                    value["current"]
                    and current_sources_match(tx, branch_id, preview["chapters"], record["chapter_bindings"])
                )
                value.update(
                    **archive_origin(archived),
                    chapter_bindings=record["chapter_bindings"],
                    basis_reconciles=reconciles,
                    revalidation_request=inspection_revalidation(report, preview) if reconciles else None,
                    current=False,
                )
                for item in value["items"]:
                    item["current_status"] = "outdated"
            return value
