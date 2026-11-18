"""Versioned four-level outline and read-side ledgers for the writing workspace."""

from copy import deepcopy
import json
from time import time
from typing import Annotated, Literal
from unicodedata import normalize

from fastapi import Query
from pydantic import Field, StringConstraints

from .db import DomainError, dumps, uid
from .models import Input
from .project_brief import confirmed_project_tx
from .reference_portability import HashId
from .setting_review import confirmed_outline_settings, confirmed_world_basis
from .story_graph import SaveRecord, graph_snapshot, save_record_tx

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class OutlineCardReference(Input):
    session_id: Identifier
    card_id: Identifier
    revision: int = Field(ge=0, strict=True)
    version_id: HashId | None = Field(default=None, exclude_if=lambda value: value is None)


class SelectedOutlineCardReference(OutlineCardReference):
    version_id: HashId


class OutlineCard(OutlineCardReference):
    technique: str
    conditions: str
    exceptions: str
    source_title: str


class OutlineCardOption(OutlineCard):
    pinned: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)


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
    fulfillment: Literal["author_required"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


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
    acknowledge_required_promises: bool = Field(default=False, strict=True)
    impact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


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
    return confirmed_outline_settings(tx, branch_id, confirmation_id)


def _card_key(ref):
    return ref["session_id"], ref["card_id"], ref["revision"], ref.get("version_id")


def _outline_card(version, *, pin=True):
    if version.assessment != "ready":
        raise DomainError("reference_card_incomplete", "请选择已完成的手法卡修订。", 409)
    return {
        "session_id": version.session_id,
        "card_id": version.card_id,
        "revision": version.revision,
        **({"version_id": version.id} if pin else {}),
        **version.card.model_dump(include={"technique", "conditions", "exceptions"}),
        "source_title": version.citation.title,
    }


def selected_outline_cards(tx, branch_id, *, include_pin_status=False):
    from .reference_sync import selectable_reference_cards

    return [
        {**_outline_card(version), **({"pinned": True} if include_pin_status and pinned else {})}
        for version, pinned in selectable_reference_cards(tx, branch_id)
    ]


def freeze_selected_outline_cards(tx, branch_id, references):
    selected = {_card_key(card): card for card in selected_outline_cards(tx, branch_id)}
    keys = [_card_key(ref) for ref in references]
    if len({key[:2] for key in keys}) != len(keys):
        raise DomainError("outline_reference", "每张手法卡只可选择一个正式修订。", 409)
    if any(key not in selected for key in keys):
        raise DomainError("reference_changed", "所选手法卡已变化或不可用，请重新核对正式修订。", 409)
    return [selected[key] for key in keys]


def validate_frozen_reference_cards(cards, lookup):
    for card in cards:
        if not (identifier := card.get("version_id")):
            continue
        source = lookup(identifier)
        if not source:
            raise DomainError("outline_reference", "缺少大纲引用的准确手法卡来源，待正式修订到齐。", 409)
        if _outline_card(source) != card:
            raise DomainError("outline_reference", "大纲手法卡地址或冻结摘要与原正式修订不符。", 409)


def _freeze_cards(tx, branch_id, references, previous):
    from .reference_sync import reference_card_at

    cached = {_card_key(card): card for card in (previous or {}).get("reference_cards", [])}
    frozen = {}
    for ref in references:
        key = _card_key(ref)
        if key not in cached:
            session_id, card_id, revision, version_id = key
            version = reference_card_at(tx, branch_id, session_id, card_id, revision, version_id=version_id)
            cached[key] = _outline_card(version, pin=version_id is not None)
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
        identities = {key[:2] for key in selected}
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


def required_promise_windows(plan):
    return {
        window["promise_id"]: window
        for window in (plan or {}).get("promises", [])
        if window.get("fulfillment") == "author_required"
    }


def _required_promises_changed(before, after):
    return required_promise_windows(before) != required_promise_windows(after) or any(
        window.get("fulfillment") == "author_required" for window in after.get("new_promises", [])
    )


def save_outline(domain, branch_id, data, *, before_write=None, include_setting_ledger=False):
    with domain.db.connect(True) as tx:
        view = save_outline_tx(tx, branch_id, data, before_write=before_write)
        return _with_setting_ledger(tx, branch_id, view) if include_setting_ledger else view


def save_outline_tx(
    tx, branch_id, data, *, before_write=None, frozen_cards=None, sync_source=None, archive_source=None
):
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
        from .reference_sync import read_reference_version

        validate_frozen_reference_cards(
            cards, lambda identifier: read_reference_version(tx, branch_id, identifier)
        )
    if sync_source:
        basis["sync_outline_source"] = sync_source
    if archive_source:
        basis["manuscript_outline_source"] = archive_source
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


def confirm_outline(domain, branch_id, data, *, before_write=None, include_setting_ledger=False):
    with domain.db.connect(True) as tx:
        view = confirm_outline_tx(tx, branch_id, data, before_write=before_write)
        return _with_setting_ledger(tx, branch_id, view) if include_setting_ledger else view


def confirm_outline_tx(tx, branch_id, data, *, before_write=None, source_confirmed=False):
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
        previous = _decode(_latest(tx, branch_id, True))
        requirement_changed = _required_promises_changed(previous["plan"] if previous else None, plan)
        if requirement_changed and not (data.acknowledge_required_promises or source_confirmed):
            raise DomainError("outline_required_promises", "请核对必达承诺变更后再确认大纲。", 409)
        _validate(graph, plan, basis, True)
        if data.impact_sha256:
            from .outline_confirmation_impact import confirmation_impact

            impact = confirmation_impact(tx, branch_id, draft, previous, graph)
            if data.impact_sha256 != impact["basis_sha256"]:
                raise DomainError("outline_impact_changed", "大纲确认影响已更新，请重新核对后确认。", 409)
        _write_confirmation(
            tx, branch_id, draft, graph, requirement_changed, source_confirmed=source_confirmed
        )
        from .work_snapshot import checkpoint_tx

        checkpoint_tx(
            tx,
            branch_id,
            "从稿件包恢复四级大纲" if basis.get("manuscript_outline_source") else "确认四级大纲",
        )
    return _view(tx, branch_id)


def _write_confirmation(tx, branch_id, draft, graph, requirement_changed, *, source_confirmed=False):
    """Write the adopted plan without publishing a separate checkpoint."""
    plan = deepcopy(draft["plan"])
    basis = {**draft["setting_basis"]}
    basis["outline_story_receipt"] = _materialize_promises(tx, graph, plan, draft["revision"])
    if requirement_changed:
        basis["required_promises_confirmation"] = "formal_source" if source_confirmed else "author"
    _insert(tx, branch_id, draft["revision"] + 1, True, plan, basis)


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
        plan["promises"].append(
            {
                "promise_id": identifier,
                **{key: pending[key] for key in WINDOW_CHAPTERS},
                **({"fulfillment": pending["fulfillment"]} if pending.get("fulfillment") else {}),
            }
        )
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
        **(
            {"requires_promise_confirmation": True}
            if draft
            and not draft["confirmed"]
            and _required_promises_changed(confirmed["plan"] if confirmed else None, draft["plan"])
            else {}
        ),
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
    author_uses = {}
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
        if any(item.get("category_id") for item in settings_by_id.values()):
            from .outline_sync import read_outline_head
            from .setting_use import current_outline_uses
            from .settings_sync import settings_source_id

            author_uses = current_outline_uses(
                tx,
                branch_id,
                read_outline_head(tx, branch_id),
                settings_source_id(tx, branch_id, basis["confirmation_id"]),
            )
    settings = [
        {
            "chapter_id": chapter["chapter_id"],
            "setting": settings_by_id[identifier],
            "evidence": passes.get((chapter["chapter_id"], identifier)),
            **(
                {"author_evidence": proof}
                if (proof := author_uses.get((chapter["chapter_id"], identifier)))
                else {}
            ),
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
        required = window.get("fulfillment") == "author_required"
        opens = chapter_positions.get(window["opens_chapter_id"])
        closes = chapter_positions.get(window["closes_chapter_id"])
        fulfilled = (
            required
            and state == "resolved"
            and opens is not None
            and closes is not None
            and any(
                source["chapter_id"] in chapter_positions
                and opens <= chapter_positions[source["chapter_id"]] <= closes
                for source in stages[-1]["sources"]
            )
        )
        due = None
        if (required and not fulfilled) or (current and state in {"planned", "planted", "reinforced"}):
            if closes is not None and progress >= closes:
                due = "due"
            elif opens is not None and progress >= opens:
                due = "open"
        promises.append(
            {
                **window,
                "title": row["payload"]["title"] if row else "未找到伏笔记录",
                "state": state,
                "stages": stages,
                "due": due,
                **({"fulfillment_met": bool(fulfilled)} if required else {}),
            }
        )
    return {"settings": settings, "promises": promises}


def _with_setting_ledger(tx, branch_id, view):
    from .domain import utf16_slice
    from .setting_use import _verified_quote
    from .setting_use_portability import SettingUseVersion

    adopted = confirmed_outline_settings(tx, branch_id)["items"]
    entries = {setting["id"]: {"setting": setting, "chapters": {}} for setting in adopted}
    if not entries:
        return {**view, "setting_ledger": []}
    chapter_titles = {row["id"]: row["title"] for row in view["chapters"]}
    planned = {}
    for plan in view["settings"]:
        setting_id, chapter_id = plan["setting"]["id"], plan["chapter_id"]
        if setting_id not in entries:
            continue
        planned[chapter_id, setting_id] = plan
        entries[setting_id]["chapters"][chapter_id] = {
            "chapter_id": chapter_id,
            "chapter_title": chapter_titles.get(chapter_id, chapter_id),
            "planned": "outdated" if view["stale"] else "current",
            "evidence": None,
        }
    witnesses = tx.execute(
        """
        WITH candidates AS (
          SELECT a.id,a.chapter_id,a.kind,a.payload,a.base_revision,c.title AS chapter_title,
            CASE a.kind WHEN 'setting_use' THEN json_extract(a.payload,'$.item_id')
              ELSE json_extract(a.basis,'$.rule.id') END AS setting_id,
            a.rowid AS sequence
          FROM artifacts a JOIN chapters c ON c.id=a.chapter_id AND c.branch_id=a.branch_id
          WHERE a.branch_id=? AND a.status='accepted' AND (
            (a.kind='setting_use' AND json_extract(a.payload,'$.action')='confirmed') OR
            (a.kind='setting_review' AND json_extract(a.payload,'$.result')='pass'
              AND json_type(a.payload,'$.evidence')='object')
          )
        ), latest AS (
          SELECT *,ROW_NUMBER() OVER(PARTITION BY chapter_id,setting_id,kind ORDER BY sequence DESC) AS rank
          FROM candidates
        )
        SELECT * FROM latest WHERE rank=1 ORDER BY sequence DESC
        """,
        (branch_id,),
    )
    for witness in witnesses:
        chapter_id, setting_id = witness["chapter_id"], witness["setting_id"]
        entry = entries.get(setting_id)
        method = "author" if witness["kind"] == "setting_use" else "rule"
        if not entry or (method == "author") != ("category_id" in entry["setting"]):
            continue
        plan = planned.get((chapter_id, setting_id)) or {}
        quote, revision_id = None, None
        artifact_id = witness["id"]
        if method == "author":
            current = plan.get("author_evidence")
            version = (
                SettingUseVersion.model_validate(current["version"])
                if current
                else SettingUseVersion.model_validate_json(witness["payload"])
            )
            artifact_id = version.id
            revision_id = version.evidence.revision_id
            try:
                quote = current["quote"] if current else _verified_quote(tx, branch_id, version)
            except DomainError as error:
                if error.code != "setting_use_evidence":
                    raise
        else:
            current = plan.get("evidence")
            if current:
                artifact_id = current["report_id"]
            source = tx.execute(
                "SELECT r.id,r.content,a.payload FROM artifacts a "
                "JOIN revisions r ON r.chapter_id=a.chapter_id AND r.revision=a.base_revision "
                "WHERE a.id=? AND a.branch_id=?",
                (artifact_id, branch_id),
            ).fetchone()
            if source:
                revision_id = source["id"]
                selected = json.loads(source["payload"])["evidence"]
                if type(selected.get("start")) is int and type(selected.get("end")) is int:
                    try:
                        _, original, _ = utf16_slice(source["content"], selected["start"], selected["end"])
                        if original.strip() and original == selected.get("text"):
                            quote = original
                    except DomainError:
                        pass
        location = entry["chapters"].setdefault(
            chapter_id,
            {"chapter_id": chapter_id, "chapter_title": witness["chapter_title"], "planned": None},
        )
        location["evidence"] = {
            "method": method,
            "status": ("current" if current else "outdated") if quote else "unavailable",
            "quote": quote,
            "revision_id": revision_id,
            "report_id": artifact_id if method == "rule" else None,
        }
    return {
        **view,
        "setting_ledger": [
            {**entry, "chapters": list(entry["chapters"].values())} for entry in entries.values()
        ],
    }


def read_outline(domain, branch_id, *, include_setting_ledger=False):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        view = _view(tx, branch_id)
        return _with_setting_ledger(tx, branch_id, view) if include_setting_ledger else view


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
    from .outline_confirmation_impact import OutlineConfirmationImpact, read_confirmation_impact

    endpoint = "/api/branches/{branch_id}/outline-workspace"

    @app.get(endpoint)
    def outline_workspace(branch_id: str):
        return read_outline(domain, branch_id, include_setting_ledger=True)

    @app.put(endpoint)
    def save_workspace(branch_id: str, data: SaveOutline):
        return save_outline(domain, branch_id, data, include_setting_ledger=True)

    @app.post(endpoint + "/confirm")
    def confirm_workspace(branch_id: str, data: ConfirmOutline):
        return confirm_outline(domain, branch_id, data, include_setting_ledger=True)

    @app.get(endpoint + "/confirmation-impact")
    def confirm_impact(
        branch_id: str, base_revision: Annotated[int, Query(ge=1)]
    ) -> OutlineConfirmationImpact:
        return read_confirmation_impact(domain, branch_id, base_revision)

    @app.get(endpoint + "/revisions/{revision}")
    def outline_revision(branch_id: str, revision: int):
        return _decode(
            domain.db.one(
                "SELECT * FROM outline_revisions WHERE branch_id=? AND revision=?",
                (branch_id, revision),
            )
        )

    @app.get(endpoint + "/setting-options")
    def setting_options(branch_id: str, scope: Literal["outline", "world"] = "outline"):
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            read = confirmed_world_basis if scope == "world" else confirmed_outline_settings
            return read(tx, branch_id)

    @app.get(endpoint + "/reference-options")
    def reference_options(branch_id: str) -> list[OutlineCardOption]:
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return selected_outline_cards(tx, branch_id, include_pin_status=True)
