"""Immutable M1 versions and a CAS pointer to the adopted version."""

from hashlib import sha256
import json
from typing import Literal

from pydantic import ValidationError

from .db import DomainError
from .models import Input
from .project_brief import KIND, ConfirmProject, confirmed_project_tx
from .project_portability import Identifier, ProjectHistory, ProjectVersion

FORMAT = "ainovel-manuscript-sync/1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS synced_project_versions(
 branch_id TEXT NOT NULL REFERENCES branches(id), id TEXT NOT NULL,
 payload TEXT NOT NULL, device_id TEXT NOT NULL, change_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 PRIMARY KEY(branch_id,id));
"""


class ProjectVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["project-version"]
    branch_id: Identifier
    version: ProjectVersion


class ProjectHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["project-head"]
    branch_id: Identifier
    revision_id: Identifier


def version_object(branch_id, version):
    return ProjectVersionObject(format=FORMAT, kind="project-version", branch_id=branch_id, version=version)


def head_object(branch_id, version_id):
    return ProjectHeadObject(format=FORMAT, kind="project-head", branch_id=branch_id, revision_id=version_id)


def parse_project_object(branch_id, object_id, payload, tombstone):
    version = object_id.startswith("project-version:")
    try:
        schema = ProjectVersionObject if version else ProjectHeadObject
        value = schema.model_validate(payload)
    except ValidationError as error:
        raise DomainError("sync_project", "立项内容未通过工作台字段或题材校验。", 409) from error
    expected = f"project-version:{value.version.id}" if version else f"project:{branch_id}"
    if tombstone or value.branch_id != branch_id or object_id != expected:
        raise DomainError("sync_project", "立项不能删除，且须属于同一作品工作版本。", 409)
    return value


def _versions(tx, branch_id):
    return tx.execute(
        "SELECT id,payload,basis FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid",
        (branch_id, KIND),
    )


def _native_version_id(basis, fallback):
    source = basis.get("sync_project_source") or basis.get("manuscript_project_source")
    return source["revision_id"] if source else fallback


def _native_project_versions(tx, branch_id):
    ids = {}
    versions = {}
    current = None
    for row in _versions(tx, branch_id):
        basis = json.loads(row["basis"])
        payload = json.loads(row["payload"])
        current = ids[row["id"]] = _native_version_id(basis, row["id"])
        if "sync_project_source" not in basis and "manuscript_project_source" not in basis:
            versions[current] = ProjectVersion(
                id=current,
                parent_id=ids.get(basis.get("previous_project_id")),
                idea=payload["idea"],
                brief=payload["brief"],
            )
    return current, versions


def _archived_versions(tx, branch_id):
    return {
        row["id"]: ProjectVersion.model_validate_json(row["payload"])
        for row in tx.execute(
            "SELECT id,payload FROM manuscript_project_versions WHERE branch_id=?", (branch_id,)
        )
    }


def _cached_version(tx, branch_id, version_id):
    return tx.execute(
        "SELECT * FROM synced_project_versions WHERE branch_id=? AND id=?", (branch_id, version_id)
    ).fetchone()


def read_project_version(tx, branch_id, version_id):
    cached = _cached_version(tx, branch_id, version_id)
    if cached:
        return ProjectVersion.model_validate_json(cached["payload"])
    archived = tx.execute(
        "SELECT payload FROM manuscript_project_versions WHERE branch_id=? AND id=?",
        (branch_id, version_id),
    ).fetchone()
    return (
        ProjectVersion.model_validate_json(archived["payload"])
        if archived
        else _native_project_versions(tx, branch_id)[1].get(version_id)
    )


def read_project_head(tx, branch_id):
    row = tx.execute(
        "SELECT id,basis FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY rowid DESC LIMIT 1",
        (branch_id, KIND),
    ).fetchone()
    return _native_version_id(json.loads(row["basis"]), row["id"]) if row else None


def has_project_source(tx, branch_id, version_id):
    return _cached_version(tx, branch_id, version_id) is not None


def current_project_source(tx, branch_id):
    identifier = read_project_head(tx, branch_id)
    source = _cached_version(tx, branch_id, identifier) if identifier else None
    if not source:
        return None
    return {key: source[key] for key in ("id", "device_id", "change_id", "sequence")}


def draft_project_objects(tx, branch_id):
    current, native = _native_project_versions(tx, branch_id)
    ordered = _parents_first({**_archived_versions(tx, branch_id), **native})
    values = [version_object(branch_id, version) for version in ordered]
    if current:
        values.append(head_object(branch_id, current))
    return values


def _parents_first(versions):
    ordered = []
    seen = set()
    for row in versions.values():
        path = []
        while row and row.id not in seen:
            seen.add(row.id)
            path.append(row)
            row = versions.get(row.parent_id)
        ordered.extend(reversed(path))
    return ordered


def export_project_history(tx, branch_id):
    current, native = _native_project_versions(tx, branch_id)
    versions = {**_archived_versions(tx, branch_id), **native}
    if tx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='synced_project_versions'"
    ).fetchone():
        for row in tx.execute(
            "SELECT id,payload FROM synced_project_versions WHERE branch_id=?", (branch_id,)
        ):
            versions[row["id"]] = ProjectVersion.model_validate_json(row["payload"])
    if versions:
        return ProjectHistory(adopted_revision_id=current, versions=_parents_first(versions))
    return None


def restore_project_history(tx, branch_id, history):
    from .project_brief import confirm_tx

    tx.executemany(
        "INSERT INTO manuscript_project_versions VALUES(?,?,?)",
        [(branch_id, row.id, row.model_dump_json()) for row in history.versions],
    )
    if history.adopted_revision_id:
        source = next(row for row in history.versions if row.id == history.adopted_revision_id)
        key = sha256(f"{branch_id}/{source.id}".encode()).hexdigest()
        confirm_tx(
            tx,
            branch_id,
            ConfirmProject(
                base_revision=0,
                idempotency_key=f"project-file-{key}",
                idea=source.idea,
                brief=source.brief,
            ),
            archive_source_id=source.id,
        )


def observe_project_version(tx, branch_id, version, receipt):
    existing = read_project_version(tx, branch_id, version.id)
    if existing is not None and existing != version:
        raise DomainError("sync_project", "同一立项源修订已存在不同内容，需人工复核。", 409)
    if version.parent_id and not read_project_version(tx, branch_id, version.parent_id):
        raise DomainError("sync_project", "缺少立项父版本，待云端历史到齐。", 409)
    tx.execute(
        "INSERT INTO synced_project_versions VALUES(?,?,?,?,?,?) ON CONFLICT(branch_id,id) DO UPDATE SET "
        "device_id=excluded.device_id,change_id=excluded.change_id,sequence=excluded.sequence "
        "WHERE excluded.sequence<synced_project_versions.sequence",
        (
            branch_id,
            version.id,
            version.model_dump_json(),
            receipt.device_id,
            receipt.change_id,
            receipt.sequence,
        ),
    )


def apply_project_head(tx, branch_id, value, receipt):
    from .project_brief import confirm_tx

    cached = _cached_version(tx, branch_id, value.revision_id)
    if not cached:
        raise DomainError("sync_project", "缺少云端采用的立项源版本，待来源记录到齐。", 409)
    source = ProjectVersion.model_validate_json(cached["payload"])
    current = confirmed_project_tx(tx, branch_id)
    key = sha256(f"{branch_id}/{receipt.change_id}/{source.id}".encode()).hexdigest()
    confirm_tx(
        tx,
        branch_id,
        ConfirmProject(
            base_revision=current["revision"] if current else 0,
            idempotency_key=f"project-sync-{key}",
            idea=source.idea,
            brief=source.brief,
        ),
        sync_source={
            "revision_id": source.id,
            "parent_id": source.parent_id,
            "device_id": cached["device_id"],
            "source_change_id": cached["change_id"],
            "head_change_id": receipt.change_id,
        },
    )
