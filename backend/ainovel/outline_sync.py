"""Portable M4 confirmations, frozen card excerpts, and initial promise plans."""

import json
from typing import Literal

from pydantic import ValidationError

from .db import DomainError
from .manuscript_exchange import Identifier
from .models import Input
from .outline_portability import OutlineVersion
from .outline_workspace import (
    ConfirmOutline,
    SaveOutline,
    _card_key,
    _chapter_plans,
    _decode,
    _latest,
    _validate,
    confirm_outline_tx,
    save_outline_tx,
)
from .project_sync import FORMAT, read_project_head, read_project_version
from .settings_sync import project_source_id, read_settings_head, read_settings_version, settings_source_id
from .story_graph import RECORD, Promise, SaveRecord, save_record_tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS synced_outline_versions(
 branch_id TEXT NOT NULL REFERENCES branches(id), id TEXT NOT NULL,
 payload TEXT NOT NULL, device_id TEXT NOT NULL, change_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 PRIMARY KEY(branch_id,id));
"""


class OutlineVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["outline-version"]
    branch_id: Identifier
    version: OutlineVersion


class OutlineHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["outline-head"]
    branch_id: Identifier
    revision_id: Identifier


def version_object(branch_id, version):
    return OutlineVersionObject(format=FORMAT, kind="outline-version", branch_id=branch_id, version=version)


def head_object(branch_id, version_id):
    return OutlineHeadObject(format=FORMAT, kind="outline-head", branch_id=branch_id, revision_id=version_id)


def parse_outline_object(branch_id, object_id, payload, tombstone):
    is_version = object_id.startswith("outline-version:")
    try:
        schema = OutlineVersionObject if is_version else OutlineHeadObject
        value = schema.model_validate(payload)
    except ValidationError as error:
        raise DomainError("sync_outline", "大纲内容未通过正式版本格式校验。", 409) from error
    expected = f"outline-version:{value.version.id}" if is_version else f"outline:{branch_id}"
    if tombstone or value.branch_id != branch_id or object_id != expected:
        raise DomainError("sync_outline", "正式大纲不可删除，且须属于同一作品工作版本。", 409)
    if is_version:
        validate_version(value.version)
    return value


def _planned_seed(record):
    return (
        isinstance(record, Promise)
        and not record.sources
        and not record.depends_on
        and len(record.stages) == 1
        and record.stages[0].status == "planned"
        and not record.stages[0].event_id
        and not record.stages[0].sources
    )


def validate_version(version):
    plan = version.plan.model_dump()
    chapter_plans = [chapter for _, _, chapter in _chapter_plans(plan)]
    promised = {window["promise_id"] for window in plan["promises"]}
    if version.parent_id == version.id or version.plan.new_promises:
        raise DomainError("sync_outline", "仅可同步已经确认的大纲和正式伏笔窗口。", 409)
    if not set(version.promise_sources) <= promised or not all(
        _planned_seed(seed) for seed in version.promise_sources.values()
    ):
        raise DomainError("sync_outline_promise", "大纲仅携带不含事件和正文证据的初始伏笔计划。", 409)
    cards = [card.model_dump() for card in version.reference_cards]
    selected = {_card_key(ref) for chapter in chapter_plans for ref in chapter.get("reference_cards", [])}
    keys = {_card_key(card) for card in cards}
    if (
        keys != selected
        or len(cards) != len(keys)
        or not all(
            card["technique"].strip() and card["conditions"].strip() and card["exceptions"].strip()
            for card in cards
        )
    ):
        raise DomainError("sync_outline_reference", "大纲手法卡缺少对应冻结的内容、适用条件或例外。", 409)
    settings = {key for chapter in chapter_plans for key in chapter["setting_ids"]}
    if settings and not version.settings_version_id:
        raise DomainError("sync_outline_settings", "大纲须包含所挂载设定的稳定版本。", 409)
    graph = {
        "chapters": [
            {"id": chapter["chapter_id"], "position": index, "archived": False}
            for index, chapter in enumerate(chapter_plans, 1)
        ],
        "records": [
            {"id": identifier, "kind": "promise", "archived": False, "payload": {"title": identifier}}
            for identifier in promised
        ],
    }
    _validate(graph, plan, {"items": [{"id": key} for key in settings], "reference_cards": cards}, True)


def validate_version_sources(version, project_lookup, settings_lookup, chapter_lookup=None):
    if version.project_version_id and not project_lookup(version.project_version_id):
        raise DomainError("sync_outline_project", "大纲引用的立项版本尚未到齐。", 409)
    if version.settings_version_id:
        settings = settings_lookup(version.settings_version_id)
        if not settings or settings.project_version_id != version.project_version_id:
            raise DomainError("sync_outline_settings", "大纲引用的设定版本或对应立项尚未到齐。", 409)
    if chapter_lookup and any(
        not chapter_lookup(chapter["chapter_id"])
        for _, _, chapter in _chapter_plans(version.plan.model_dump())
    ):
        raise DomainError("sync_outline_chapters", "大纲引用的章节目录尚未到齐。", 409)


def _source_id(row):
    basis = json.loads(row["setting_basis"])
    source = basis.get("sync_outline_source") or basis.get("manuscript_outline_source")
    return source["revision_id"] if source else row["id"]


def _native_seeds(tx, branch_id, plan):
    return {
        window["promise_id"]: seed
        for window in plan["promises"]
        if (seed := _native_seed(tx, branch_id, window["promise_id"])) is not None
    }


def _native_seed(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT h.payload FROM story_record_history h JOIN story_records r ON r.id=h.record_id "
        "WHERE r.branch_id=? AND r.id=? AND h.revision=0 AND h.archived=0",
        (branch_id, identifier),
    ).fetchone()
    if row and _planned_seed(record := RECORD.validate_json(row["payload"])):
        return record
    return None


def _native_version(tx, branch_id, row, parent):
    outline = _decode(row)
    basis, plan = outline["setting_basis"], outline["plan"]
    return OutlineVersion(
        id=outline["id"],
        parent_id=parent,
        project_version_id=project_source_id(tx, branch_id, basis.get("project_id")),
        settings_version_id=settings_source_id(tx, branch_id, basis.get("confirmation_id")),
        plan=plan,
        reference_cards=basis.get("reference_cards", []),
        promise_sources=_native_seeds(tx, branch_id, plan),
    )


def _outline_rows(tx, branch_id):
    return tx.execute(
        "SELECT * FROM outline_revisions WHERE branch_id=? AND confirmed=1 ORDER BY revision",
        (branch_id,),
    )


def _cached_version(tx, branch_id, version_id):
    return tx.execute(
        "SELECT * FROM synced_outline_versions WHERE branch_id=? AND id=?", (branch_id, version_id)
    ).fetchone()


def read_outline_version(tx, branch_id, version_id):
    if cached := _cached_version(tx, branch_id, version_id):
        return OutlineVersion.model_validate_json(cached["payload"])
    if archived := tx.execute(
        "SELECT payload FROM manuscript_outline_versions WHERE branch_id=? AND id=?",
        (branch_id, version_id),
    ).fetchone():
        return OutlineVersion.model_validate_json(archived["payload"])
    parent = None
    for row in _outline_rows(tx, branch_id):
        current = _source_id(row)
        if row["id"] == current == version_id:
            return _native_version(tx, branch_id, row, parent)
        parent = current
    return None


def read_outline_head(tx, branch_id):
    row = _latest(tx, branch_id, True)
    return _source_id(row) if row else None


def has_outline_source(tx, branch_id, version_id):
    return _cached_version(tx, branch_id, version_id) is not None


def current_outline_source(tx, branch_id):
    identifier = read_outline_head(tx, branch_id)
    cached = _cached_version(tx, branch_id, identifier) if identifier else None
    return {key: cached[key] for key in ("id", "device_id", "change_id", "sequence")} if cached else None


def draft_outline_objects(tx, branch_id):
    from .settings_sync import _parents_first

    parent = None
    versions = {
        row["id"]: OutlineVersion.model_validate_json(row["payload"])
        for row in tx.execute(
            "SELECT id,payload FROM manuscript_outline_versions WHERE branch_id=? ORDER BY rowid",
            (branch_id,),
        )
    }
    for row in _outline_rows(tx, branch_id):
        current = _source_id(row)
        if row["id"] == current:
            versions[current] = _native_version(tx, branch_id, row, parent)
        parent = current
    values = [version_object(branch_id, version) for version in _parents_first(versions)]
    if parent:
        values.append(head_object(branch_id, parent))
    return values


def observe_outline_version(tx, branch_id, version, receipt):
    validate_version(version)
    existing = read_outline_version(tx, branch_id, version.id)
    if existing is not None and existing != version:
        raise DomainError("sync_outline", "同一大纲源修订已存在不同内容，需人工复核。", 409)
    if version.parent_id and not read_outline_version(tx, branch_id, version.parent_id):
        raise DomainError("sync_outline", "缺少大纲父版本，待云端历史到齐。", 409)
    validate_version_sources(
        version,
        lambda identifier: read_project_version(tx, branch_id, identifier),
        lambda identifier: read_settings_version(tx, branch_id, identifier),
    )
    tx.execute(
        "INSERT INTO synced_outline_versions VALUES(?,?,?,?,?,?) ON CONFLICT(branch_id,id) DO UPDATE SET "
        "device_id=excluded.device_id,change_id=excluded.change_id,sequence=excluded.sequence "
        "WHERE excluded.sequence<synced_outline_versions.sequence",
        (
            branch_id,
            version.id,
            version.model_dump_json(),
            receipt.device_id,
            receipt.change_id,
            receipt.sequence,
        ),
    )


def _ensure_promises(tx, branch_id, version):
    additions = []
    for window in version.plan.promises:
        identifier = window.promise_id
        seed = version.promise_sources.get(identifier)
        current = tx.execute("SELECT * FROM story_records WHERE id=?", (identifier,)).fetchone()
        if current:
            if current["branch_id"] != branch_id or current["archived"] or current["kind"] != "promise":
                raise DomainError(
                    "sync_outline_promise", "本机伏笔编号的归属或归档状态不同，待作者处理。", 409
                )
            if seed and _native_seed(tx, branch_id, identifier) != seed:
                raise DomainError("sync_outline_promise", "同一伏笔的初始内容不一致，待作者核对。", 409)
        elif seed:
            additions.append((identifier, seed))
        else:
            raise DomainError("sync_outline_promise", "大纲引用的伏笔需等待对应故事记录，现有阶段保留。", 409)
    if additions:
        revision = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
        for offset, (identifier, seed) in enumerate(additions):
            save_record_tx(
                tx,
                branch_id,
                SaveRecord(base_story_revision=revision + offset, record=seed),
                new_identifier=identifier,
            )


def apply_outline_head(tx, branch_id, value, receipt):
    cached = _cached_version(tx, branch_id, value.revision_id)
    if not cached:
        raise DomainError("sync_outline", "缺少云端采用的大纲源版本，待来源记录到齐。", 409)
    version = OutlineVersion.model_validate_json(cached["payload"])
    if read_outline_head(tx, branch_id) == version.id:
        return
    latest = _latest(tx, branch_id)
    if latest and not latest["confirmed"]:
        raise DomainError("sync_outline_draft", "本机有未确认的大纲草稿，保留草稿并等待作者处理。", 409)
    if read_project_head(tx, branch_id) != version.project_version_id:
        raise DomainError("sync_outline_project", "本机采用的立项与大纲依据不同，待采用对应立项。", 409)
    confirmation_id = None
    if version.settings_version_id:
        if read_settings_head(tx, branch_id) != version.settings_version_id:
            raise DomainError("sync_outline_settings", "本机采用的设定与大纲依据不同，待采用对应设定。", 409)
        from .setting_review import confirmed_world_basis

        confirmation_id = confirmed_world_basis(tx, branch_id)["confirmation_id"]
        if not confirmation_id:
            raise DomainError("sync_outline_settings", "本机设定依据尚未生效，待确认后采用大纲。", 409)
    chapter_ids = {
        row["id"]
        for row in tx.execute("SELECT id FROM chapters WHERE branch_id=? AND archived=0", (branch_id,))
    }
    if any(
        chapter["chapter_id"] not in chapter_ids
        for _, _, chapter in _chapter_plans(version.plan.model_dump())
    ):
        raise DomainError("sync_outline_chapters", "本机章节目录尚未到齐，待同步后采用大纲。", 409)
    _ensure_promises(tx, branch_id, version)
    saved = save_outline_tx(
        tx,
        branch_id,
        SaveOutline(
            base_revision=latest["revision"] if latest else 0,
            confirmation_id=confirmation_id,
            plan=version.plan,
        ),
        frozen_cards=[card.model_dump() for card in version.reference_cards],
        sync_source={
            "revision_id": version.id,
            "parent_id": version.parent_id,
            "project_version_id": version.project_version_id,
            "settings_version_id": version.settings_version_id,
            "device_id": cached["device_id"],
            "source_change_id": cached["change_id"],
            "head_change_id": receipt.change_id,
        },
    )
    confirm_outline_tx(tx, branch_id, ConfirmOutline(base_revision=saved["draft"]["revision"]))
