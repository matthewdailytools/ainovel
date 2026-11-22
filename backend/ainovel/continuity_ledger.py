"""Author-confirmed manuscript evidence and chapter plans for serial writing."""

import json
from time import time
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from .db import DomainError, dumps, uid
from .models import Input
from .story_graph import (
    Promise,
    Source,
    decode,
    graph_snapshot,
    metadata_revision_compatible,
    persist_record_tx,
    validate_source,
)
from .writing_context import chapter_setting

ARTIFACT = "continuity_ledger"
VERSION = "continuity-ledger/1"
STAGES = {"setting": ("effective",), "foreshadow": ("planted", "reinforced", "resolved")}
Stage = Literal["effective", "planted", "reinforced", "resolved"]
WINDOW_TASKS = {"open": "window_open", "due": "window_due", "overdue": "window_overdue"}


class Window(Input):
    start_chapter_id: str = Field(min_length=1, max_length=128)
    end_chapter_id: str = Field(min_length=1, max_length=128)


class PlanInput(Input):
    kind: Literal["setting", "foreshadow"]
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4000)
    setting_id: str | None = Field(default=None, min_length=1, max_length=200)
    setting_chapter_id: str | None = Field(default=None, min_length=1, max_length=128)
    affected_chapter_ids: list[str] = Field(default_factory=list, max_length=100)
    windows: dict[Stage, Window | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def plan_fields(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("请填写台账名称。")
        if self.kind == "setting" and (not self.setting_id or not self.setting_chapter_id):
            raise ValueError("设定生效须选择一条设定及其所在章。")
        if self.kind == "foreshadow" and (self.setting_id or self.setting_chapter_id):
            raise ValueError("伏笔计划不能绑定设定生效项。")
        if set(self.windows).difference(STAGES[self.kind]):
            raise ValueError("计划窗口的阶段与台账类型不符。")
        if len(self.affected_chapter_ids) != len(set(self.affected_chapter_ids)):
            raise ValueError("关联章节不能重复。")
        return self


class PlanUpdate(PlanInput):
    base_revision: StrictInt = Field(ge=0)


class PlanCreate(PlanInput):
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128)


class LedgerChapter(Input):
    id: str
    title: str
    position: int
    revision: int


class LedgerSource(Source):
    source_current: bool


class LedgerSetting(Input):
    id: str
    kind: Literal["fact", "world", "conception"]
    text: str
    source: Source | None


class EvidenceInput(Input):
    base_revision: StrictInt = Field(ge=0)
    stage: Stage
    source: Source
    note: str = Field(default="", max_length=2000)
    expected_setting: LedgerSetting | None = None


class LedgerStage(Input):
    stage: Stage
    window: Window | None
    window_status: Literal["none", "future", "open", "due", "overdue", "invalid"]
    source: Source | None
    source_current: bool
    additional_sources: list[LedgerSource]
    setting_basis: LedgerSetting | None
    review_status: Literal["unrecorded", "current", "stale", "outside_window"]
    review_reason: str
    note: str
    repair_blocked: bool


class LedgerEntry(Input):
    id: str
    revision: int
    kind: Literal["setting", "foreshadow"]
    title: str
    detail: str
    setting_id: str | None
    setting_chapter_id: str | None
    affected_chapter_ids: list[str]
    affected_chapters: list[LedgerChapter]
    impacted_chapters: list[LedgerChapter]
    stages: list[LedgerStage]
    closed: bool
    author_closed: bool
    setting_basis: LedgerSetting | None
    graph_warning: str


class LedgerReminder(Input):
    kind: Literal["recheck", "window_open", "window_due", "window_overdue"]
    entry_id: str
    stage: Stage
    title: str
    chapter_id: str


class ContinuityLedger(Input):
    story_revision: int
    progress_position: int
    chapters: list[LedgerChapter]
    entries: list[LedgerEntry]
    reminders: list[LedgerReminder]


def branch_chapters(tx, branch_id):
    return {
        row["id"]: dict(row)
        for row in tx.execute(
            "SELECT id,branch_id,title,position,revision FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position",
            (branch_id,),
        )
    }


def writing_progress(tx, branch_id):
    spaces = " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
    return tx.execute(
        "SELECT COALESCE(MAX(position),0) FROM chapters WHERE branch_id=? AND archived=0 AND trim(content,?)<>''",
        (branch_id, spaces),
    ).fetchone()[0]


def decode_version(row):
    value = json.loads(row["payload"])
    if value.get("version") != VERSION:
        raise DomainError("ledger_version", "台账版本无法识别，请使用兼容版本读取。", 409)
    return value


def ledger_versions(tx, branch_id, identifier):
    rows = tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(payload,'$.entry_id')=? ORDER BY rowid DESC",
        (branch_id, ARTIFACT, identifier),
    )
    return [decode_version(row) for row in rows]


def latest_metadata(tx, branch_id):
    rows = tx.execute(
        "SELECT payload FROM artifacts WHERE rowid IN ("
        "SELECT MAX(rowid) FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "GROUP BY json_extract(payload,'$.entry_id')) ORDER BY rowid",
        (branch_id, ARTIFACT),
    )
    return {value["entry_id"]: value for value in map(decode_version, rows)}


def promise_rows(tx, branch_id):
    return tx.execute(
        "SELECT * FROM story_records WHERE branch_id=? AND kind='promise' AND archived=0 ORDER BY created,id",
        (branch_id,),
    )


def dependency_changes(tx, branch_id):
    rows = tx.execute(
        "SELECT d.* FROM story_record_dependencies d JOIN story_records r ON d.record_id=r.id "
        "WHERE r.branch_id=? AND r.kind='promise' AND r.archived=0",
        (branch_id,),
    ).fetchall()
    if not rows:
        return {}
    records = {
        row["id"]: row for row in graph_snapshot(tx, branch_id, include_chapter_stale=False)["records"]
    }
    invalid = {}
    for row in rows:
        parent = records.get(row["parent_id"])
        if (
            not parent
            or parent["invalid_reasons"]
            or not metadata_revision_compatible(tx, parent, row["parent_revision"])
        ):
            invalid.setdefault(row["record_id"], set()).add(row["parent_id"])
    return invalid


def promise_stages(payload):
    # An older promise may have several reinforcements. The latest recorded
    # occurrence is shown here; original and superseded receipts stay in its history.
    return {
        stage["status"]: {
            "source": stage["sources"][0] if stage["sources"] else None,
            "additional_sources": stage["sources"][1:],
            "note": stage["note"],
            "event_id": stage["event_id"],
        }
        for stage in payload["stages"]
        if stage["status"] in STAGES["foreshadow"]
    }


def plan_for(promise, metadata):
    if not promise:
        return metadata["plan"]
    setup = metadata["plan"] if metadata else {}
    return {
        "kind": "foreshadow",
        "title": promise["payload"]["title"],
        "detail": promise["payload"]["notes"],
        "setting_id": None,
        "setting_chapter_id": None,
        "affected_chapter_ids": setup.get("affected_chapter_ids", []),
        "windows": setup.get("windows", {}),
    }


def current_record(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT * FROM story_records WHERE id=? AND branch_id=? AND kind='promise' AND archived=0",
        (identifier, branch_id),
    ).fetchone()
    promise = decode(row) if row else None
    saved = tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "AND json_extract(payload,'$.entry_id')=? ORDER BY rowid DESC LIMIT 1",
        (branch_id, ARTIFACT, identifier),
    ).fetchone()
    metadata = decode_version(saved) if saved else None
    if not promise and (not metadata or metadata["plan"]["kind"] != "setting"):
        raise DomainError("not_found", "当前作品中没有这个台账条目。", 404)
    return row, promise, metadata


def setting_snapshot(tx, chapters, plan):
    anchor = chapters.get(plan["setting_chapter_id"])
    if not anchor:
        return None
    setting = chapter_setting(tx, anchor, plan["setting_id"])
    if not setting or not setting["source_current"]:
        return None
    source = setting["source"]
    if source:
        source = {key: source[key] for key in ("chapter_id", "revision", "start", "end", "quote")}
    return {"id": setting["id"], "kind": setting["kind"], "text": setting["text"], "source": source}


def limits(chapters, window):
    if window:
        start = chapters.get(window["start_chapter_id"])
        end = chapters.get(window["end_chapter_id"])
        if start and end and start["position"] <= end["position"]:
            return start, end
    return None


def window_state(chapters, window, progress):
    if not window:
        return "none"
    bounds = limits(chapters, window)
    if not bounds:
        return "invalid"
    start, end = bounds
    if progress < start["position"]:
        return "future"
    if progress < end["position"]:
        return "open"
    return "due" if progress == end["position"] else "overdue"


def source_state(chapters, source, window, window_status, basis, setting, is_setting):
    if not source:
        reason = "所关联的设定当前无法核对，请先处理设定。" if is_setting and not setting else ""
        return False, "unrecorded", reason
    if not source_available(chapters, source):
        return False, "stale", "原文修订已变更或来源章已归档，请重新定位保存稿。"
    if is_setting and (setting is None or basis.get("setting") != setting):
        return True, "stale", "设定或设定的原文依据已变化，请重新核对。"
    if window_status == "invalid":
        return True, "stale", "计划窗口失效，请先核对目录并修订窗口。"
    if window and not in_window(chapters, window, chapters[source["chapter_id"]]["position"]):
        return True, "outside_window", "原文仍可定位，当前计划窗口已异于证据所在章。"
    if basis.get("window") != window:
        return True, "stale", "原文仍可定位，已采用计划改变，请按新窗口重新核对。"
    return True, "current", ""


def source_available(chapters, source):
    chapter = chapters.get(source["chapter_id"])
    return bool(chapter and chapter["revision"] == source["revision"])


def in_window(chapters, window, position):
    bounds = limits(chapters, window)
    return bool(bounds and bounds[0]["position"] <= position <= bounds[1]["position"])


def chapter_summary(chapter):
    return {key: chapter[key] for key in ("id", "title", "position", "revision")}


def target_for(chapters, stage, progress):
    source = stage["source"]
    window = stage["window"]
    bounds = limits(chapters, window)
    if stage["review_status"] == "stale" and source and source["chapter_id"] in chapters:
        original = chapters[source["chapter_id"]]
        if not window or in_window(chapters, window, original["position"]):
            return original["id"]
    if bounds:
        start, end = bounds
        target = max(start["position"], min(progress, end["position"]))
        return next((c["id"] for c in chapters.values() if c["position"] == target), start["id"])
    return None


def render_entry(tx, chapters, progress, identifier, promise, metadata, invalid_references=()):
    plan = plan_for(promise, metadata)
    version = promise["revision"] if promise else metadata["revision"]
    by_stage = promise_stages(promise["payload"]) if promise else metadata.get("evidence", {})
    bases = metadata.get("basis", {}) if metadata else {}
    is_setting = plan["kind"] == "setting"
    setting = setting_snapshot(tx, chapters, plan) if is_setting else None
    stages = []
    impacted = set()
    affected = set(plan["affected_chapter_ids"])
    general_dependencies = promise["payload"]["depends_on"] if promise else []
    for name in STAGES[plan["kind"]]:
        window = plan["windows"].get(name)
        proof = by_stage.get(name, {})
        source = proof.get("source")
        window_status = window_state(chapters, window, progress)
        basis = bases.get(name, {})
        current, review, reason = source_state(
            chapters, source, window, window_status, basis, setting, is_setting
        )
        additional = [
            {**ref, "source_current": source_available(chapters, ref)}
            for ref in proof.get("additional_sources", [])
        ]
        stale_secondary = [ref for ref in additional if not ref["source_current"]]
        invalid_dependency = set(invalid_references).intersection(
            [*general_dependencies, proof.get("event_id")]
        )
        if review == "current" and stale_secondary:
            review, reason = "stale", "同一阶段的其他原文有变更或已归档，请连同主引用重新核对。"
        if review == "current" and invalid_dependency:
            review, reason = "stale", "所关联的故事记录已改变，请先到故事记录核对。"
        stage = {
            "stage": name,
            "window": window,
            "window_status": window_status,
            "source": source,
            "source_current": current,
            "additional_sources": additional,
            "setting_basis": basis.get("setting"),
            "review_status": review,
            "review_reason": reason,
            "note": proof.get("note", ""),
            "repair_blocked": bool(invalid_dependency) or (is_setting and setting is None),
        }
        if review in {"stale", "outside_window"}:
            impacted.update(affected)
            if not current or window_status == "invalid" or review == "outside_window" or not stale_secondary:
                impacted.add(source["chapter_id"])
            impacted.update(ref["chapter_id"] for ref in stale_secondary)
            impacted.add(target_for(chapters, stage, progress))
        stages.append(stage)
    terminal = promise["payload"]["stages"][-1]["status"] if promise else ""
    author_closed = terminal in {"retained", "abandoned"}
    closed = (
        (author_closed or stages[-1]["review_status"] == "current")
        and not impacted
        and not invalid_references
    )
    return {
        "id": identifier,
        "revision": version,
        **{
            key: plan[key]
            for key in ("kind", "title", "detail", "setting_id", "setting_chapter_id", "affected_chapter_ids")
        },
        "affected_chapters": [chapter_summary(c) for key, c in chapters.items() if key in affected],
        "impacted_chapters": [chapter_summary(c) for key, c in chapters.items() if key in impacted],
        "stages": stages,
        "closed": closed,
        "author_closed": author_closed,
        "setting_basis": setting,
        "graph_warning": "存在待核对的关联故事记录。" if invalid_references else "",
    }


def reminders(entries, chapters, progress):
    tasks = []
    for entry in entries:
        for stage in entry["stages"]:
            if stage["repair_blocked"]:
                continue
            kind = None
            if stage["review_status"] in {"stale", "outside_window"}:
                kind = "recheck"
            elif (
                stage["review_status"] == "unrecorded"
                and not entry["closed"]
                and not entry["author_closed"]
                and not stage["review_reason"]
            ):
                kind = WINDOW_TASKS.get(stage["window_status"])
            chapter_id = target_for(chapters, stage, progress) if kind else None
            if chapter_id:
                tasks.append(
                    {
                        "kind": kind,
                        "entry_id": entry["id"],
                        "stage": stage["stage"],
                        "title": entry["title"],
                        "chapter_id": chapter_id,
                    }
                )
    return tasks


def branch_row(tx, branch_id):
    branch = tx.execute("SELECT id,novel_id,story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    return branch


def ledger_snapshot(tx, branch_id):
    branch = branch_row(tx, branch_id)
    chapters = branch_chapters(tx, branch_id)
    progress = writing_progress(tx, branch_id)
    versions = latest_metadata(tx, branch_id)
    invalid = dependency_changes(tx, branch_id)
    entries = [
        render_entry(tx, chapters, progress, identifier, None, entry)
        for identifier, entry in versions.items()
        if entry["plan"]["kind"] == "setting"
    ]
    entries.extend(
        render_entry(
            tx,
            chapters,
            progress,
            row["id"],
            decode(row),
            versions.get(row["id"]),
            invalid.get(row["id"], ()),
        )
        for row in promise_rows(tx, branch_id)
    )
    return {
        "story_revision": branch["story_revision"],
        "progress_position": progress,
        "chapters": [chapter_summary(c) for c in chapters.values()],
        "entries": entries,
        "reminders": reminders(entries, chapters, progress),
    }


def plan_input(data):
    return data.model_dump(exclude={"base_revision", "client_request_id"})


def chosen_plan(tx, chapters, data, previous=None):
    plan = plan_input(data)
    if previous and previous["kind"] != plan["kind"]:
        raise DomainError("ledger_kind", "台账类型不可修改。", 409)
    previous_affected = previous["affected_chapter_ids"] if previous else []
    if any(key not in chapters and key not in previous_affected for key in plan["affected_chapter_ids"]):
        raise DomainError("ledger_chapter", "关联章节必须属于本工作版本且未归档。", 409)
    previous_windows = previous["windows"] if previous else {}
    for stage, window in plan["windows"].items():
        if window and window != previous_windows.get(stage) and not limits(chapters, window):
            raise DomainError(
                "ledger_window", "窗口首尾必须是本工作版本的有效章节，且起点不能晚于终点。", 409
            )
    if plan["kind"] == "setting":
        target = (plan["setting_id"], plan["setting_chapter_id"])
        before = (previous["setting_id"], previous["setting_chapter_id"]) if previous else None
        if target != before and setting_snapshot(tx, chapters, plan) is None:
            raise DomainError("ledger_setting", "请选择当前章节可核对的设定依据。", 409)
    return plan


def keep_version(tx, branch, identifier, metadata, snapshot):
    value = {**metadata, "entry_id": identifier, "version": VERSION, "snapshot": snapshot}
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,'{}','accepted',?)",
        (uid(), branch["novel_id"], branch["id"], ARTIFACT, dumps(value), time()),
    )
    return snapshot


def save_plan(tx, branch_id, data, identifier=None):
    branch = branch_row(tx, branch_id)
    chapters = branch_chapters(tx, branch_id)
    if identifier:
        row, promise, metadata = current_record(tx, branch_id, identifier)
        before = plan_for(promise, metadata)
        revision = promise["revision"] if promise else metadata["revision"]
        if data.base_revision != revision:
            raise DomainError("ledger_changed", "台账已有更新，请刷新后重新核对。", 409)
    else:
        identifier, row, promise, metadata, before, revision = uid(), None, None, None, None, -1
    plan = chosen_plan(tx, chapters, data, before)
    evidence = metadata.get("evidence", {}) if metadata else {}
    if evidence and any(plan[key] != before[key] for key in ("setting_id", "setting_chapter_id")):
        raise DomainError("ledger_setting", "这条记录已有生效证据，关联另一条设定请新建台账。", 409)
    if plan["kind"] == "foreshadow":
        payload = (
            promise["payload"]
            if promise
            else Promise(kind="promise", title=plan["title"], stages=[{"status": "planned"}]).model_dump()
        )
        payload.update(title=plan["title"], notes=plan["detail"])
        revision = persist_record_tx(tx, branch_id, identifier, payload, row, refresh_dependencies=False)
        promise = {"payload": payload, "revision": revision}
    else:
        revision += 1
    state = {
        "plan": plan,
        "revision": revision,
        "evidence": evidence,
        "basis": metadata.get("basis", {}) if metadata else {},
    }
    if not row and isinstance(data, PlanCreate) and data.client_request_id:
        state["client_request_id"] = data.client_request_id
    invalid = dependency_changes(tx, branch_id).get(identifier, ()) if promise else ()
    result = render_entry(tx, chapters, writing_progress(tx, branch_id), identifier, promise, state, invalid)
    return keep_version(tx, branch, identifier, state, result)


def update_promise_stage(payload, stage, source, note):
    stages = payload["stages"]
    same = [index for index, old in enumerate(stages) if old["status"] == stage]
    if same:
        selected = stages[same[-1]]
        # Explicit confirmation replaces this stage's primary and secondary
        # evidence. The previous stage remains in the version history.
        selected.update(sources=[source], note=note)
        return
    if len(stages) >= 100:
        raise DomainError("ledger_stage_limit", "该伏笔已达阶段记录上限，请先在故事记录整理。", 409)
    ordered = ("planned", *STAGES["foreshadow"], "retained", "abandoned")
    target = next(
        (index for index, old in enumerate(stages) if ordered.index(old["status"]) > ordered.index(stage)),
        len(stages),
    )
    stages.insert(target, {"status": stage, "sources": [source], "note": note, "event_id": None})


def save_evidence(tx, branch_id, identifier, data):
    branch = branch_row(tx, branch_id)
    chapters = branch_chapters(tx, branch_id)
    row, promise, metadata = current_record(tx, branch_id, identifier)
    plan = plan_for(promise, metadata)
    version = promise["revision"] if promise else metadata["revision"]
    if data.base_revision != version:
        raise DomainError("ledger_changed", "台账已有更新，请刷新后再复检。", 409)
    if data.stage not in STAGES[plan["kind"]]:
        raise DomainError("ledger_stage", "复检阶段与台账类型不符。", 409)
    source = data.source.model_dump()
    if not source["quote"].strip():
        raise DomainError("evidence", "请选取有正文内容的原文依据。", 409)
    chapter = validate_source(tx, branch_id, source)
    window = plan["windows"].get(data.stage)
    if window and not in_window(chapters, window, chapter["position"]):
        raise DomainError("ledger_window", "来源章或计划窗口需要调整，原证据及核对记录已保留。", 409)
    setting = setting_snapshot(tx, chapters, plan) if plan["kind"] == "setting" else None
    if plan["kind"] == "setting" and setting is None:
        raise DomainError("ledger_setting", "设定原文已变化，请先核对设定，旧证据仍可查看。", 409)
    if data.expected_setting is not None and data.expected_setting.model_dump() != setting:
        raise DomainError("ledger_setting", "准备复检期间设定已改变，请刷新并查看最新设定。", 409)
    note = data.note.strip()
    evidence = metadata.get("evidence", {}) if metadata else {}
    invalid = ()
    if promise:
        payload = promise["payload"]
        stage = promise_stages(payload).get(data.stage, {})
        invalid = dependency_changes(tx, branch_id).get(identifier, ())
        if set(invalid).intersection([*payload["depends_on"], stage.get("event_id")]):
            raise DomainError("ledger_dependency", "请先到故事记录核对已变更的关联，再复检这条证据。", 409)
        update_promise_stage(payload, data.stage, source, note)
        version = persist_record_tx(tx, branch_id, identifier, payload, row, refresh_dependencies=False)
        promise = {"payload": payload, "revision": version}
    else:
        evidence[data.stage] = {"source": source, "note": note}
        version += 1
    basis = metadata.get("basis", {}) if metadata else {}
    basis[data.stage] = {"window": window, "setting": setting}
    state = {"plan": plan, "revision": version, "evidence": evidence, "basis": basis}
    result = render_entry(tx, chapters, writing_progress(tx, branch_id), identifier, promise, state, invalid)
    return keep_version(tx, branch, identifier, state, result)


def entry_history(tx, branch_id, identifier):
    branch_row(tx, branch_id)
    chapters = branch_chapters(tx, branch_id)
    versions = ledger_versions(tx, branch_id, identifier)
    snapshots = {value["revision"]: value["snapshot"] for value in versions}
    rows = tx.execute(
        "SELECT h.* FROM story_record_history h JOIN story_records r ON r.id=h.record_id "
        "WHERE h.record_id=? AND r.branch_id=? AND r.kind='promise' ORDER BY h.revision DESC",
        (identifier, branch_id),
    )
    for row in rows:
        revision = row["revision"]
        if revision in snapshots:
            continue
        metadata = next((v for v in versions if v["revision"] <= revision), None)
        promise = {**dict(row), "payload": Promise.model_validate_json(row["payload"]).model_dump()}
        snapshot = render_entry(tx, chapters, 0, identifier, promise, metadata)
        for stage in snapshot["stages"]:
            if stage["source"]:
                stage["review_reason"] = "原故事记录已保存该证据，未保存当时的台账复核结论。"
        snapshots[revision] = snapshot
    if not snapshots:
        raise DomainError("not_found", "当前作品中没有这个台账条目。", 404)
    return [snapshots[revision] for revision in sorted(snapshots, reverse=True)]


def created_plan(tx, branch_id, data):
    if data.client_request_id:
        row = tx.execute(
            "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
            "AND json_extract(payload,'$.client_request_id')=?",
            (branch_id, ARTIFACT, data.client_request_id),
        ).fetchone()
        if row:
            record = decode_version(row)
            if record["plan"] != plan_input(data):
                raise DomainError("idempotency", "本次保存标识已经用于另一份计划，请重新提交。", 409)
            return record["snapshot"]
    return save_plan(tx, branch_id, data)


def register_routes(app, domain):
    prefix = "/api/branches/{branch_id}/continuity-ledger"

    @app.get(prefix, response_model=ContinuityLedger)
    def read_ledger(branch_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return ledger_snapshot(tx, branch_id)

    @app.post(prefix + "/entries", status_code=201, response_model=LedgerEntry)
    def create_entry(branch_id: str, data: PlanCreate):
        with domain.db.connect(True) as tx:
            return created_plan(tx, branch_id, data)

    @app.put(prefix + "/entries/{identifier}/plan", response_model=LedgerEntry)
    def change_plan(branch_id: str, identifier: str, data: PlanUpdate):
        with domain.db.connect(True) as tx:
            return save_plan(tx, branch_id, data, identifier)

    @app.put(prefix + "/entries/{identifier}/evidence", response_model=LedgerEntry)
    def confirm_evidence(branch_id: str, identifier: str, data: EvidenceInput):
        with domain.db.connect(True) as tx:
            return save_evidence(tx, branch_id, identifier, data)

    @app.get(prefix + "/entries/{identifier}/history", response_model=list[LedgerEntry])
    def history(branch_id: str, identifier: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return entry_history(tx, branch_id, identifier)
