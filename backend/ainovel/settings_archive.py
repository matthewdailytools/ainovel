"""Restore M3 graph and adopted source through the formal setting confirmation service."""

from pydantic import ValidationError

from .conception import ConfirmConception, _branch, _persist, _workspace, confirm_tx
from .db import DomainError
from .project_brief import confirmed_project_tx
from .project_sync import read_project_head
from .settings_portability import SettingsHistory, SettingsVersion
from .settings_sync import (
    _archive_versions,
    _categories,
    _digest,
    _local_origins,
    _native_settings_versions,
    _parents_first,
    validate_version,
    validate_version_sources,
)


def export_settings_history(tx, branch_id):
    try:
        adopted, native = _native_settings_versions(tx, branch_id)
        archived = list(_archive_versions(tx, branch_id))
        if tx.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='synced_settings_versions'"
        ).fetchone():
            archived.extend(
                tx.execute(
                    "SELECT id,payload,device_id AS source_device FROM synced_settings_versions WHERE branch_id=? ORDER BY sequence",
                    (branch_id,),
                )
            )
        versions = dict(native)
        sources = {}
        for row in archived:
            versions[row["id"]] = SettingsVersion.model_validate_json(row["payload"])
            if row["source_device"]:
                sources[row["id"]] = row["source_device"]
        if not versions:
            return None
        return SettingsHistory(
            adopted_revision_id=adopted, versions=_parents_first(versions), source_devices=sources
        )
    except ValidationError as error:
        raise DomainError("manuscript_settings", "本机设定历史的父链或来源档案不完整。", 409) from error


def validate_settings_history(history, projects, revisions):
    if not history:
        return
    projects = {version.id: version for version in projects.versions} if projects else {}
    for version in history.versions:
        try:
            validate_version(version)
            validate_version_sources(version, projects.get, revisions.get)
        except DomainError as error:
            summary = error.message.split("，待", 1)[0].rstrip("。")
            raise DomainError(
                "manuscript_settings",
                f"稿件包设定修订 {version.id} 未通过校验。{summary}。",
                409,
            ) from error


def import_settings_history(tx, branch_id, history):
    tx.executemany(
        "INSERT INTO manuscript_settings_versions VALUES(?,?,?,?)",
        [
            (branch_id, version.id, version.model_dump_json(), history.source_devices.get(version.id))
            for version in history.versions
        ],
    )
    if not history.adopted_revision_id:
        return
    chosen = next(row for row in history.versions if row.id == history.adopted_revision_id)
    project = confirmed_project_tx(tx, branch_id)
    if read_project_head(tx, branch_id) != chosen.project_version_id:
        # A previously adopted setting can be older than M1. Keep the selected archival
        # project basis so the normal workspace reports it as requiring reconfirmation.
        project = {"id": chosen.project_version_id, "revision": None} if chosen.project_version_id else None
    branch = _branch(tx, branch_id)
    workspace = _workspace(tx, branch_id)
    source_device = history.source_devices.get(chosen.id)
    origins = _local_origins(tx, branch_id, chosen, project, source_device, from_package=True)
    _persist(tx, branch, workspace, _categories(chosen), origins)
    key = _digest(f"{branch_id}/{chosen.id}")
    confirm_tx(
        tx,
        branch_id,
        ConfirmConception(
            base_revision=workspace["revision"] + 1,
            base_story_revision=branch["story_revision"],
            idempotency_key=f"settings-file-{key}",
            note=chosen.note,
            reconciled_project_items=chosen.reconciled_project_items,
            reviewed_setting_findings=chosen.reviewed_setting_findings,
        ),
        archive_source={
            "revision_id": chosen.id,
            "parent_id": chosen.parent_id,
            "project_version_id": chosen.project_version_id,
            "project_confirmation_id": project["id"] if project else None,
            "recorded_device_id": source_device,
        },
    )
