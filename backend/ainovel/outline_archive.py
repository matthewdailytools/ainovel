"""Independent archives of formal outlines and safely restorable promise plans."""

import json

from pydantic import ValidationError

from .db import DomainError, dumps
from .outline_portability import OutlineHistory, OutlineVersion
from .outline_sync import (
    _ensure_promises,
    _native_version,
    _outline_rows,
    _planned_seed,
    _source_id,
    read_outline_head,
    validate_version,
    validate_version_sources,
)
from .outline_workspace import (
    ConfirmOutline,
    SaveOutline,
    _chapter_plans,
    _latest,
    confirm_outline_tx,
    save_outline_tx,
)
from .project_sync import read_project_head
from .setting_review import confirmed_world_basis
from .settings_sync import _parents_first, read_settings_head
from .story_graph import RECORD


def _archives(tx, branch_id):
    return tx.execute(
        "SELECT id,payload,source_device FROM manuscript_outline_versions WHERE branch_id=? ORDER BY rowid",
        (branch_id,),
    )


def _restore_record(tx, branch_id):
    return tx.execute("SELECT * FROM manuscript_outline_restore WHERE branch_id=?", (branch_id,)).fetchone()


def _story_record(tx, branch_id, identifier):
    return tx.execute(
        "SELECT archived,revision,payload FROM story_records WHERE branch_id=? AND id=? AND kind='promise'",
        (branch_id, identifier),
    ).fetchone()


def _required_story_records(tx, branch_id, version):
    required = []
    for window in version.plan.promises:
        identifier = window.promise_id
        row = _story_record(tx, branch_id, identifier)
        seed = version.promise_sources.get(identifier)
        if (
            not row
            or row["archived"]
            or row["revision"] != 0
            or not seed
            or RECORD.validate_json(row["payload"]) != seed
        ):
            required.append(identifier)
    return required


def export_outline_history(tx, branch_id):
    versions = {}
    sources = {}
    native = None
    for row in _outline_rows(tx, branch_id):
        current = _source_id(row)
        if current == row["id"]:
            versions[current] = _native_version(tx, branch_id, row, native)
        native = current
    archived = list(_archives(tx, branch_id))
    if tx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='synced_outline_versions'"
    ).fetchone():
        archived.extend(
            tx.execute(
                "SELECT id,payload,device_id AS source_device FROM synced_outline_versions WHERE branch_id=? ORDER BY sequence",
                (branch_id,),
            )
        )
    for row in archived:
        version = OutlineVersion.model_validate_json(row["payload"])
        if versions.get(row["id"], version) != version:
            raise DomainError("manuscript_outline", "同一大纲源修订记录了不同内容，暂无法导出。", 409)
        versions[row["id"]] = version
        if row["source_device"]:
            sources[row["id"]] = row["source_device"]
    waiting = _restore_record(tx, branch_id)
    if not versions:
        if native or waiting:
            raise DomainError("manuscript_outline", "本机找不到采用大纲的原始修订，暂无法导出。", 409)
        return None
    adopted = native or (waiting["revision_id"] if waiting else None)
    required = []
    if adopted and adopted in versions:
        required = (
            _required_story_records(tx, branch_id, versions[adopted])
            if native
            else json.loads(waiting["required_records"])
        )
    try:
        return OutlineHistory(
            adopted_revision_id=adopted,
            versions=_parents_first(versions),
            source_devices=sources,
            required_story_records=required,
        )
    except ValidationError as error:
        raise DomainError("manuscript_outline", "本机大纲历史的父链或依赖档案不完整。", 409) from error


def validate_outline_history(history, projects, settings, chapters, references=None):
    if not history:
        return
    projects = {version.id: version for version in projects.versions} if projects else {}
    settings = {version.id: version for version in settings.versions} if settings else {}
    chapters = {chapter.id: chapter for chapter in chapters}
    references = {version.id: version for version in references.versions} if references else {}
    for version in history.versions:
        try:
            validate_version(version)
            validate_version_sources(
                version, projects.get, settings.get, chapters.get, reference_lookup=references.get
            )
            basis = settings.get(version.settings_version_id)
            actual = {
                item.id
                for category in (basis.categories if basis else [])
                if category.id == "world" and not category.skipped
                for item in category.items
                if item.detail.strip() and basis.origins[item.id].evidence is None
            }
            used = {
                item
                for _, _, chapter in _chapter_plans(version.plan.model_dump())
                for item in chapter["setting_ids"]
            }
            if not used <= actual:
                raise DomainError(
                    "outline_setting", "章纲引用的设定不在该正式世界设定版本的可用条目内。", 409
                )
        except DomainError as error:
            raise DomainError(
                "manuscript_outline", f"稿件包大纲修订 {version.id} 未通过校验。{error.message}", 409
            ) from error


def _prerequisite(tx, branch_id, version, required):
    if read_project_head(tx, branch_id) != version.project_version_id:
        return "稿件包采用的大纲仍基于另一立项版本，请核对立项后恢复。"
    if version.settings_version_id and (
        read_settings_head(tx, branch_id) != version.settings_version_id
        or not confirmed_world_basis(tx, branch_id)["confirmation_id"]
    ):
        return "稿件包采用的大纲仍基于另一设定版本，请核对设定后恢复。"
    for identifier in required:
        row = _story_record(tx, branch_id, identifier)
        if (
            not row
            or row["archived"]
            or (row["revision"] == 0 and _planned_seed(RECORD.validate_json(row["payload"])))
        ):
            return f"等待原伏笔记录 {identifier} 的实际进展或历史恢复。"
    return None


def restore_outline_archive(tx, branch_id):
    saved = _restore_record(tx, branch_id)
    if not saved:
        return None
    identifier = saved["revision_id"]
    last = _latest(tx, branch_id)
    if read_outline_head(tx, branch_id) == identifier:
        return {"status": "adopted", "revision_id": identifier}
    if last:
        raise DomainError("manuscript_outline_changed", "工作版本已有本机大纲，先比较稿件包历史。", 409)
    row = tx.execute(
        "SELECT payload,source_device FROM manuscript_outline_versions WHERE branch_id=? AND id=?",
        (branch_id, identifier),
    ).fetchone()
    if not row:
        raise DomainError("manuscript_outline", "稿件包的采用版记录不存在。", 409)
    version = OutlineVersion.model_validate_json(row["payload"])
    reason = _prerequisite(tx, branch_id, version, json.loads(saved["required_records"]))
    if not reason:
        tx.execute("SAVEPOINT restore_manuscript_outline")
        try:
            _ensure_promises(tx, branch_id, version)
            confirmation_id = (
                confirmed_world_basis(tx, branch_id)["confirmation_id"]
                if version.settings_version_id
                else None
            )
            pending = save_outline_tx(
                tx,
                branch_id,
                SaveOutline(base_revision=0, confirmation_id=confirmation_id, plan=version.plan),
                frozen_cards=[card.model_dump() for card in version.reference_cards],
                archive_source={
                    "revision_id": version.id,
                    "parent_id": version.parent_id,
                    "project_version_id": version.project_version_id,
                    "settings_version_id": version.settings_version_id,
                    "recorded_device_id": row["source_device"],
                },
            )
            confirm_outline_tx(tx, branch_id, ConfirmOutline(base_revision=pending["draft"]["revision"]))
        except DomainError as error:
            tx.execute("ROLLBACK TO restore_manuscript_outline")
            reason = error.message
        finally:
            tx.execute("RELEASE restore_manuscript_outline")
    tx.execute(
        "UPDATE manuscript_outline_restore SET waiting_reason=? WHERE branch_id=?", (reason, branch_id)
    )
    return (
        {"status": "deferred", "revision_id": identifier, "reason": reason}
        if reason
        else {
            "status": "adopted",
            "revision_id": identifier,
        }
    )


def import_outline_history(tx, branch_id, history):
    tx.executemany(
        "INSERT INTO manuscript_outline_versions VALUES(?,?,?,?)",
        [
            (branch_id, row.id, row.model_dump_json(), history.source_devices.get(row.id))
            for row in history.versions
        ],
    )
    if not history.adopted_revision_id:
        return None
    tx.execute(
        "INSERT INTO manuscript_outline_restore VALUES(?,?,?,NULL)",
        (branch_id, history.adopted_revision_id, dumps(history.required_story_records)),
    )
    return restore_outline_archive(tx, branch_id)
