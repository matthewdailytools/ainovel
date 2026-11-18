"""Versioned four-level outline and read-side ledgers for the writing workspace."""

import json
from time import time
from typing import Annotated
from unicodedata import normalize

from pydantic import Field, StringConstraints

from .db import DomainError, dumps, uid
from .models import Input
from .project_brief import confirmed_project_tx
from .setting_review import confirmed_world_basis
from .story_graph import SaveRecord, graph_snapshot, save_record_tx

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class OutlineCardReference(Input):
    session_id: Identifier
    card_id: Identifier
    revision: int = Field(ge=0, strict=True)


class OutlineCard(OutlineCardReference):
    technique: str
    conditions: str
    exceptions: str
    source_title: str


class ChapterPlan(Input):
    chapter_id: Identifier
    goal: Summary
    obstacle: Summary
    turn: Summary
    result: Summary
    setting_ids: list[Identifier] = Field(default_factory=list, max_length=40)
    reference_cards: list[OutlineCardReference] = Field(
        default_factory=list, max_length=8, exclude_if=lambda value: not value
    )


class UnitPlan(Input):
    id: Identifier
    title: Title
    summary: Summary
    chapters: list[ChapterPlan] = Field(default_factory=list, max_length=100)


class VolumePlan(Input):
    id: Identifier
    title: Title
    summary: Summary
    units: list[UnitPlan] = Field(default_factory=list, max_length=100)


WINDOW_CHAPTERS = ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")


class ChapterWindow(Input):
    plant_chapter_id: Identifier
    opens_chapter_id: Identifier
    closes_chapter_id: Identifier


class PromiseWindow(ChapterWindow):
    promise_id: Identifier


class NewPromisePlan(ChapterWindow):
    title: Title
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""


class OutlinePlan(Input):
    summary: Summary
    volumes: list[VolumePlan] = Field(default_factory=list, max_length=100)
    promises: list[PromiseWindow] = Field(default_factory=list, max_length=250)
    new_promises: list[NewPromisePlan] = Field(
        default_factory=list, max_length=250, exclude_if=lambda value: not value
    )


class SaveOutline(Input):
    base_revision: int = Field(ge=0, strict=True)
    confirmation_id: Identifier | None = None
    source_generation_id: Identifier | None = None
    plan: OutlinePlan


class ConfirmOutline(Input):
    base_revision: int = Field(ge=1, strict=True)


def _latest(tx, branch_id, confirmed=False):
    return tx.execute(
        "SELECT * FROM outline_revisions WHERE branch_id=?"
        + (" AND confirmed=1" if confirmed else "")
        + " ORDER BY revision DESC LIMIT 1",
        (branch_id,),
    ).fetchone()


def _decode(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "revision": row["revision"],
        "confirmed": bool(row["confirmed"]),
        "plan": json.loads(row["payload"]),
        "setting_basis": json.loads(row["setting_basis"]),
    }


def _chapter_plans(plan):
    for volume in plan["volumes"]:
        for unit in volume["units"]:
            for chapter in unit["chapters"]:
                yield volume, unit, chapter


def _settings(tx, branch_id, confirmation_id):
    if not confirmation_id:
        return {"confirmation_id": None, "items": []}
    return confirmed_world_basis(tx, branch_id, confirmation_id)


def _card_key(ref):
    return ref["session_id"], ref["card_id"], ref["revision"]


def _outline_card(ref, session):
    from .reference import card_version

    version = card_version(session, ref["card_id"], ref["revision"])
    if not version["card"]["exceptions"].strip():
        raise DomainError("reference_card_incomplete", "挂载手法卡前请填写须替换内容，没有时可明确填写无。")
    return {
        **ref,
        **{key: version["card"][key] for key in ("technique", "conditions", "exceptions")},
        "source_title": version["source"]["title"],
    }


def _freeze_cards(tx, branch_id, references, previous):
    cached = {_card_key(card): card for card in (previous or {}).get("reference_cards", [])}
    frozen = {}
    for ref in references:
        key = _card_key(ref)
        if key not in cached:
            session = tx.execute(
                "SELECT * FROM reference_sessions WHERE id=? AND json_extract(payload,'$.branch_id')=?",
                (ref["session_id"], branch_id),
            ).fetchone()
            if not session:
                raise DomainError("outline_reference", "仅可选用本工作版本的已保存手法卡。", 409)
            cached[key] = _outline_card(ref, {**dict(session), "payload": json.loads(session["payload"])})
        frozen[key] = cached[key]
    return list(frozen.values())


def _validate(graph, plan, basis, confirmed):
    chapters = {row["id"]: row for row in graph["chapters"] if not row["archived"]}
    settings = {item["id"] for item in basis["items"]}
    promises = {
        row["id"]: row["payload"]["title"]
        for row in graph["records"]
        if row["kind"] == "promise" and not row["archived"]
    }
    volumes = plan["volumes"]
    nodes = [volume["id"] for volume in volumes] + [
        unit["id"] for volume in volumes for unit in volume["units"]
    ]
    if len(nodes) != len(set(nodes)):
        raise DomainError("outline_duplicate", "卷和单元编号须唯一。")
    planned = [chapter for _, _, chapter in _chapter_plans(plan)]
    active_ids = [chapter["chapter_id"] for chapter in planned]
    if len(active_ids) != len(set(active_ids)) or not set(active_ids) <= chapters.keys():
        raise DomainError("outline_chapter", "章纲必须引用本工作版本未归档的章节，且每章仅能安排一次。")
    positions = [chapters[identifier]["position"] for identifier in active_ids]
    if positions != sorted(positions):
        raise DomainError("outline_order", "章纲顺序须与章节目录一致。")
    if any(
        len(chapter["setting_ids"]) != len(set(chapter["setting_ids"]))
        or not set(chapter["setting_ids"]) <= settings
        for chapter in planned
    ):
        raise DomainError("outline_setting", "章纲引用的设定须来自所选确认版本，且每章仅引用一次。")
    references = {_card_key(card) for card in basis.get("reference_cards", [])}
    for chapter in planned:
        selected = [_card_key(ref) for ref in chapter.get("reference_cards", [])]
        identities = {(session_id, card_id) for session_id, card_id, _ in selected}
        if len(identities) != len(selected) or not set(selected) <= references:
            raise DomainError("outline_reference", "每章须选用手法卡的一个已保存修订。")
    if confirmed and (
        not volumes
        or any(
            not volume["units"] or any(not unit["chapters"] for unit in volume["units"]) for volume in volumes
        )
    ):
        raise DomainError("outline_incomplete", "确认前须填写总纲，每卷至少一个单元，每单元至少一份章纲。")
    indexed = {identifier: position for position, identifier in enumerate(active_ids)}
    new_promises = plan.get("new_promises", [])
    if len(plan["promises"]) + len(new_promises) > 250:
        raise DomainError("outline_promise", "四级大纲最多安排 250 条伏笔窗口。")
    titles = {_promise_title(title) for title in promises.values()}
    seen = set()
    for window in plan["promises"]:
        promise_id = window["promise_id"]
        if promise_id in seen or promise_id not in promises:
            raise DomainError("outline_promise", "每条未归档伏笔可安排一个窗口。")
        _validate_window(window, indexed)
        seen.add(promise_id)
    for window in new_promises:
        title = _promise_title(window["title"])
        if title in titles:
            raise DomainError(
                "outline_promise_duplicate", f"伏笔「{window['title']}」重名，请使用已有窗口或改名。"
            )
        _validate_window(window, indexed)
        titles.add(title)


def _promise_title(title):
    return normalize("NFKC", title).strip().casefold()


def _validate_window(window, indexed):
    ids = [window[key] for key in WINDOW_CHAPTERS]
    if any(identifier not in indexed for identifier in ids):
        raise DomainError("outline_promise", "伏笔埋设、进入回收窗口和最迟回收的章须在当前大纲内。")
    when = [indexed[identifier] for identifier in ids]
    if when != sorted(when):
        raise DomainError("outline_window", "伏笔埋设、进入回收窗口、最迟回收的章序须依次排列。")


def _insert(tx, branch_id, revision, confirmed, plan, basis):
    identifier = uid()
    tx.execute(
        "INSERT INTO outline_revisions VALUES(?,?,?,?,?,?,?)",
        (identifier, branch_id, revision, int(confirmed), dumps(plan), dumps(basis), time()),
    )


def _check_base(row, base_revision):
    if (row["revision"] if row else 0) != base_revision:
        raise DomainError("outline_conflict", "大纲已在别处更新，请保留编辑内容并重新比较。", 409)


def save_outline(domain, branch_id, data, *, before_write=None):
    with domain.db.connect(True) as tx:
        return save_outline_tx(tx, branch_id, data, before_write=before_write)


def save_outline_tx(tx, branch_id, data, *, before_write=None, frozen_cards=None, sync_source=None):
    if before_write is not None:
        before_write(tx)
    previous = _decode(_latest(tx, branch_id))
    _check_base(previous, data.base_revision)
    payload = data.plan.model_dump()
    basis = _settings(tx, branch_id, data.confirmation_id)
    project = confirmed_project_tx(tx, branch_id)
    basis["project_id"] = project["id"] if project else None
    cards = (
        frozen_cards
        if frozen_cards is not None
        else _freeze_cards(
            tx,
            branch_id,
            (ref for _, _, chapter in _chapter_plans(payload) for ref in chapter.get("reference_cards", [])),
            previous["setting_basis"] if previous else None,
        )
    )
    if cards:
        basis["reference_cards"] = cards
    if sync_source:
        basis["sync_outline_source"] = sync_source
    if data.source_generation_id:
        from .outline_generation import check_candidate_tx

        check_candidate_tx(tx, branch_id, data.source_generation_id, data.confirmation_id)
        basis["generation_id"] = data.source_generation_id
    elif previous and not previous["confirmed"]:
        source = previous["setting_basis"]
        if (
            source.get("generation_id")
            and source["confirmation_id"] == basis["confirmation_id"]
            and source.get("project_id") == basis["project_id"]
        ):
            basis["generation_id"] = source["generation_id"]
    _validate(graph_snapshot(tx, branch_id), payload, basis, False)
    _insert(tx, branch_id, data.base_revision + 1, False, payload, basis)
    return _view(tx, branch_id)


def confirm_outline(domain, branch_id, data, *, before_write=None):
    with domain.db.connect(True) as tx:
        return confirm_outline_tx(tx, branch_id, data, before_write=before_write)


def confirm_outline_tx(tx, branch_id, data, *, before_write=None):
    if before_write is not None:
        before_write(tx)
    row = _latest(tx, branch_id)
    if row and row["confirmed"]:
        receipt = json.loads(row["setting_basis"]).get("outline_story_receipt", {})
        if data.base_revision == receipt.get("draft_revision"):
            return _view(tx, branch_id)
    _check_base(row, data.base_revision)
    if not row["confirmed"]:
        draft = _decode(row)
        basis = draft["setting_basis"]
        _settings(tx, branch_id, basis["confirmation_id"])
        _check_project(tx, branch_id, basis)
        graph = graph_snapshot(tx, branch_id)
        plan = draft["plan"]
        _validate(graph, plan, basis, True)
        basis["outline_story_receipt"] = _materialize_promises(tx, graph, plan, draft["revision"])
        _insert(tx, branch_id, data.base_revision + 1, True, plan, basis)
        from .work_snapshot import checkpoint_tx

        checkpoint_tx(tx, branch_id, "确认四级大纲")
    return _view(tx, branch_id)


def _materialize_promises(tx, graph, plan, draft_revision):
    before = graph["story_revision"]
    created_ids = []
    for pending in plan.pop("new_promises", []):
        identifier = save_record_tx(
            tx,
            graph["branch_id"],
            SaveRecord(
                base_story_revision=before + len(created_ids),
                record={
                    "kind": "promise",
                    "title": pending["title"],
                    "notes": pending.get("notes", ""),
                    "stages": [{"status": "planned"}],
                },
            ),
        )
        plan["promises"].append({"promise_id": identifier, **{key: pending[key] for key in WINDOW_CHAPTERS}})
        created_ids.append(identifier)
    return {
        "draft_revision": draft_revision,
        "before": before,
        "after": before + len(created_ids),
        "created_ids": created_ids,
    }


def _view(tx, branch_id):
    graph = graph_snapshot(tx, branch_id)
    draft = _decode(_latest(tx, branch_id))
    confirmed = _decode(_latest(tx, branch_id, True))
    ledgers = {"settings": [], "promises": []}
    stale = []
    if confirmed:
        plan = confirmed["plan"]
        basis = confirmed["setting_basis"]
        try:
            _settings(tx, branch_id, basis["confirmation_id"])
        except DomainError as error:
            if error.code not in {"conception_snapshot", "conception_changed"}:
                raise
            stale.append("采用设定已有变化")
        try:
            _check_project(tx, branch_id, basis)
        except DomainError as error:
            if error.code != "project_brief_changed":
                raise
            stale.append("采用立项已有变化")
        try:
            _validate(graph, plan, basis, True)
        except DomainError as error:
            stale.append(error.message)
        ledgers = _ledgers(tx, graph, plan, basis, not stale)
    return {
        "draft": draft,
        "confirmed": confirmed,
        "stale": stale,
        "story_revision": graph["story_revision"],
        "chapters": [row for row in graph["chapters"] if not row["archived"]],
        "promise_options": [
            {"id": row["id"], "title": row["payload"]["title"]}
            for row in graph["records"]
            if row["kind"] == "promise" and not row["archived"]
        ],
        **ledgers,
    }


def _check_project(tx, branch_id, basis):
    project = confirmed_project_tx(tx, branch_id)
    if (project["id"] if project else None) != basis.get("project_id"):
        raise DomainError("project_brief_changed", "采用立项已有变化，请重新保存并确认大纲。", 409)


def _ledgers(tx, graph, plan, basis, current):
    branch_id = graph["branch_id"]
    chapter_positions = {row["id"]: row["position"] for row in graph["chapters"] if not row["archived"]}
    progress = tx.execute(
        "SELECT MAX(position) FROM chapters WHERE branch_id=? AND archived=0 AND trim(content)!=''",
        (branch_id,),
    ).fetchone()[0]
    progress = progress or 0
    records = {row["id"]: row for row in graph["records"] if row["kind"] == "promise"}
    settings_by_id = {item["id"]: item for item in basis["items"]}
    passes = {}
    if current and basis["confirmation_id"]:
        rows = tx.execute(
            "SELECT a.id,a.chapter_id,a.payload,a.basis FROM artifacts a JOIN chapters c ON c.id=a.chapter_id "
            "WHERE a.branch_id=? AND a.kind='setting_review' AND a.base_revision=c.revision AND c.archived=0 "
            "AND c.stale=0 AND json_extract(a.basis,'$.rule.confirmation_id')=? ORDER BY a.created,a.id",
            (branch_id, basis["confirmation_id"]),
        )
        for row in rows:
            item_id = json.loads(row["basis"])["rule"]["id"]
            payload = json.loads(row["payload"])
            passes[row["chapter_id"], item_id] = (
                {"report_id": row["id"], "source": payload["evidence"]}
                if payload["result"] == "pass" and payload["evidence"]
                else None
            )
    settings = [
        {
            "chapter_id": chapter["chapter_id"],
            "setting": settings_by_id[identifier],
            "evidence": passes.get((chapter["chapter_id"], identifier)),
        }
        for _, _, chapter in _chapter_plans(plan)
        for identifier in chapter["setting_ids"]
    ]
    promises = []
    for window in plan["promises"]:
        row = records.get(window["promise_id"])
        valid = row and not row["archived"] and not row["invalid_reasons"]
        stages = row["payload"]["stages"] if row else []
        state = stages[-1]["status"] if valid else "stale"
        due = None
        if current and state in {"planned", "planted", "reinforced"}:
            if progress >= chapter_positions[window["closes_chapter_id"]]:
                due = "due"
            elif progress >= chapter_positions[window["opens_chapter_id"]]:
                due = "open"
        promises.append(
            {
                **window,
                "title": row["payload"]["title"] if row else "未找到伏笔记录",
                "state": state,
                "stages": stages,
                "due": due,
            }
        )
    return {"settings": settings, "promises": promises}


def read_outline(domain, branch_id):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        return _view(tx, branch_id)


def chapter_basis(tx, branch_id, chapter_id, outline_id):
    confirmed = _decode(_latest(tx, branch_id, True))
    if not confirmed or confirmed["id"] != outline_id:
        raise DomainError("outline_changed", "已确认大纲发生变化，请重新打开当前章纲。", 409)
    basis = confirmed["setting_basis"]
    _settings(tx, branch_id, basis["confirmation_id"])
    _check_project(tx, branch_id, basis)
    _validate(graph_snapshot(tx, branch_id), confirmed["plan"], basis, True)
    for volume, unit, chapter in _chapter_plans(confirmed["plan"]):
        if chapter["chapter_id"] == chapter_id:
            selected = {_card_key(ref) for ref in chapter.get("reference_cards", [])}
            cards = [
                {key: value for key, value in ref.items() if key != "source_title"}
                for ref in basis.get("reference_cards", [])
                if _card_key(ref) in selected
            ]
            return {
                "id": outline_id,
                "revision": confirmed["revision"],
                "volume": volume["title"],
                "unit": unit["title"],
                "chapter": chapter,
                "setting_confirmation_id": basis["confirmation_id"],
                "settings": [item for item in basis["items"] if item["id"] in chapter["setting_ids"]],
                **({"reference_cards": cards} if cards else {}),
            }
    raise DomainError("outline_chapter", "当前确认大纲未包含本章。", 409)


def register_routes(app, domain):
    endpoint = "/api/branches/{branch_id}/outline-workspace"

    @app.get(endpoint)
    def outline_workspace(branch_id: str):
        return read_outline(domain, branch_id)

    @app.put(endpoint)
    def save_workspace(branch_id: str, data: SaveOutline):
        return save_outline(domain, branch_id, data)

    @app.post(endpoint + "/confirm")
    def confirm_workspace(branch_id: str, data: ConfirmOutline):
        return confirm_outline(domain, branch_id, data)

    @app.get(endpoint + "/revisions/{revision}")
    def outline_revision(branch_id: str, revision: int):
        return _decode(
            domain.db.one(
                "SELECT * FROM outline_revisions WHERE branch_id=? AND revision=?",
                (branch_id, revision),
            )
        )

    @app.get(endpoint + "/setting-options")
    def setting_options(branch_id: str):
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return confirmed_world_basis(tx, branch_id)

    @app.get(endpoint + "/reference-options")
    def reference_options(branch_id: str) -> list[OutlineCard]:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            result = []
            rows = tx.execute(
                "SELECT * FROM reference_sessions WHERE json_extract(payload,'$.branch_id')=? "
                "ORDER BY updated DESC LIMIT 200",
                (branch_id,),
            )
            for row in rows:
                payload = json.loads(row["payload"])
                for card in payload["cards"]:
                    ref = {"session_id": row["id"], "card_id": card["id"], "revision": row["revision"]}
                    try:
                        result.append(_outline_card(ref, {**dict(row), "payload": payload}))
                    except DomainError as error:
                        if error.code != "reference_card_incomplete":
                            raise
            return result
