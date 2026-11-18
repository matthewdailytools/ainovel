"""Immutable M3 confirmations and an adopted head bound to their M1 source."""

from hashlib import sha256
import json
from typing import Literal

from pydantic import Field, ValidationError

from .conception import (
    BUILTINS,
    CONFIRMATION,
    ConceptionCategory,
    ConfirmConception,
    _branch,
    _items,
    _persist,
    _read_payload,
    _review_covers,
    _validate,
    _workspace,
    confirm_tx,
    setting_findings,
)
from .db import DomainError
from .domain import utf16_slice
from .manuscript_exchange import Identifier, native_revision, source_revision
from .models import Input
from .project_brief import KIND as PROJECT, confirmed_project_tx
from .project_sync import FORMAT, read_project_head, read_project_version

SCHEMA = """
CREATE TABLE IF NOT EXISTS synced_settings_versions(
 branch_id TEXT NOT NULL REFERENCES branches(id), id TEXT NOT NULL,
 payload TEXT NOT NULL, device_id TEXT NOT NULL, change_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 PRIMARY KEY(branch_id,id));
"""


class SettingsEvidence(Input):
    chapter_id: Identifier
    revision_id: Identifier
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    quote_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SettingsOrigin(Input):
    origin: Literal["author", "ai"]
    edited: bool = Field(default=False, strict=True)
    artifact_id: Identifier | None = None
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    project_version_id: Identifier | None = None
    evidence: SettingsEvidence | None = None


class SettingsVersion(Input):
    id: Identifier
    parent_id: Identifier | None
    project_version_id: Identifier | None
    categories: list[ConceptionCategory] = Field(min_length=len(BUILTINS), max_length=24)
    origins: dict[Identifier, SettingsOrigin] = Field(max_length=240)
    note: str = Field(default="", max_length=4000)
    reconciled_project_items: list[Identifier] = Field(default_factory=list, max_length=240)
    reviewed_setting_findings: list[Identifier] = Field(default_factory=list, max_length=7440)


class SettingsVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["settings-version"]
    branch_id: Identifier
    version: SettingsVersion


class SettingsHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["settings-head"]
    branch_id: Identifier
    revision_id: Identifier


def version_object(branch_id, version):
    return SettingsVersionObject(format=FORMAT, kind="settings-version", branch_id=branch_id, version=version)


def head_object(branch_id, version_id):
    return SettingsHeadObject(
        format=FORMAT, kind="settings-head", branch_id=branch_id, revision_id=version_id
    )


def parse_settings_object(branch_id, object_id, payload, tombstone):
    is_version = object_id.startswith("settings-version:")
    try:
        schema = SettingsVersionObject if is_version else SettingsHeadObject
        value = schema.model_validate(payload)
    except ValidationError as error:
        raise DomainError("sync_settings", "设定内容未通过正式版本格式校验。", 409) from error
    expected = f"settings-version:{value.version.id}" if is_version else f"settings:{branch_id}"
    if tombstone or value.branch_id != branch_id or object_id != expected:
        raise DomainError("sync_settings", "正式设定不可删除，且须属于同一作品工作版本。", 409)
    if is_version:
        validate_version(value.version)
    return value


def _categories(version):
    return [category.model_dump(exclude_none=True) for category in version.categories]


def validate_version(version):
    categories = _categories(version)
    _validate(categories)
    if version.parent_id == version.id or set(version.origins) != _items(categories).keys():
        raise DomainError("sync_settings", "正式设定须有有效父版本与每条设定自己的来源。", 409)
    impacted = {
        item["id"]
        for category in categories
        if not category["skipped"] and version.project_version_id
        for item in category["items"]
        if version.origins[item["id"]].project_version_id not in {None, version.project_version_id}
    }
    checked = version.reconciled_project_items
    if len(checked) != len(impacted) or set(checked) != impacted:
        raise DomainError("sync_settings_review", "设定缺少对应的立项变更核对记录，待源端复核。", 409)
    if not _review_covers(setting_findings(categories), version.reviewed_setting_findings):
        raise DomainError("sync_settings_review", "设定缺少与内容一致的冲突核对记录，待源端复核。", 409)


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _evidence_quote(evidence, revision):
    if not revision or revision.chapter_id != evidence.chapter_id:
        raise DomainError("sync_settings_evidence", "缺少设定引用的准确正文修订，待正文历史到齐。", 409)
    try:
        _, quote, _ = utf16_slice(revision.content, evidence.start, evidence.end)
    except (ValueError, DomainError) as error:
        raise DomainError("sync_settings_evidence", "设定来源的选区无效，待源端核对。", 409) from error
    if not quote or len(quote) > 4000 or _digest(quote) != evidence.quote_sha256:
        raise DomainError("sync_settings_evidence", "设定引用与源正文不符，待源端核对。", 409)
    return quote


def validate_version_sources(version, project_lookup, revision_lookup):
    """Resolve canonical dependencies within the same work only."""
    projects = {
        version.project_version_id,
        *(origin.project_version_id for origin in version.origins.values()),
    }
    for identifier in projects - {None}:
        if not project_lookup(identifier):
            raise DomainError("sync_settings_project", "缺少设定引用的立项版本，待立项历史到齐。", 409)
    for origin in version.origins.values():
        if evidence := origin.evidence:
            _evidence_quote(evidence, revision_lookup(evidence.revision_id))


def _settings_rows(tx, branch_id):
    return tx.execute(
        "SELECT id,payload,basis FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY created,rowid",
        (branch_id, CONFIRMATION),
    )


def _source_version_id(basis, fallback):
    return basis.get("sync_settings_source", {}).get("revision_id", fallback)


def project_source_id(tx, branch_id, local_id):
    if not local_id:
        return None
    row = tx.execute(
        "SELECT basis FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
        (local_id, branch_id, PROJECT),
    ).fetchone()
    return (
        json.loads(row["basis"]).get("sync_project_source", {}).get("revision_id", local_id)
        if row
        else local_id
    )


def _portable_origin(tx, branch_id, origin):
    evidence = None
    if local := origin.get("evidence"):
        revision = None
        if source_id := origin.get("evidence_revision_id"):
            revision = source_revision(tx, branch_id, source_id)
        if not revision or revision.chapter_id != local["chapter_id"]:
            row = tx.execute(
                "SELECT r.id FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
                "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
                (branch_id, local["chapter_id"], local["revision"]),
            ).fetchone()
            if not row:
                raise DomainError("sync_settings_evidence", "缺少设定引用的本机正文修订。", 409)
            revision = native_revision(tx, branch_id, row["id"])
        evidence = SettingsEvidence(
            chapter_id=local["chapter_id"],
            revision_id=revision.id,
            start=local["start"],
            end=local["end"],
            quote_sha256=_digest(local["quote"]),
        )
        _evidence_quote(evidence, revision)
    return SettingsOrigin(
        origin=origin.get("source_origin", origin.get("origin", "author")),
        edited=origin.get("edited", False),
        artifact_id=origin.get("source_artifact_id", origin.get("artifact_id")),
        device_id=origin.get("source_device_id"),
        project_version_id=project_source_id(
            tx,
            branch_id,
            origin.get("equivalent_project_confirmation_id") or origin.get("project_confirmation_id"),
        ),
        evidence=evidence,
    )


def _native_version(tx, branch_id, row, parent_id):
    payload = _read_payload(row)
    return SettingsVersion(
        id=row["id"],
        parent_id=parent_id,
        project_version_id=project_source_id(tx, branch_id, payload.get("project_confirmation_id")),
        categories=payload["categories"],
        origins={
            item_id: _portable_origin(tx, branch_id, payload["origins"].get(item_id, {}))
            for item_id in _items(payload["categories"])
        },
        note=payload["note"],
        reconciled_project_items=payload.get("reconciled_project_items", []),
        reviewed_setting_findings=payload.get("reviewed_setting_findings", []),
    )


def _native_settings_versions(tx, branch_id):
    current = None
    native = {}
    for row in _settings_rows(tx, branch_id):
        basis = json.loads(row["basis"])
        if "sync_settings_source" not in basis:
            native[row["id"]] = _native_version(tx, branch_id, row, current)
        current = _source_version_id(basis, row["id"])
    return current, native


def _cached_version(tx, branch_id, version_id):
    return tx.execute(
        "SELECT * FROM synced_settings_versions WHERE branch_id=? AND id=?", (branch_id, version_id)
    ).fetchone()


def read_settings_version(tx, branch_id, version_id):
    cached = _cached_version(tx, branch_id, version_id)
    if cached:
        return SettingsVersion.model_validate_json(cached["payload"])
    current = None
    for row in _settings_rows(tx, branch_id):
        basis = json.loads(row["basis"])
        if row["id"] == version_id and "sync_settings_source" not in basis:
            return _native_version(tx, branch_id, row, current)
        current = _source_version_id(basis, row["id"])
    return None


def read_settings_head(tx, branch_id):
    row = tx.execute(
        "SELECT id,basis FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY created DESC,rowid DESC LIMIT 1",
        (branch_id, CONFIRMATION),
    ).fetchone()
    return _source_version_id(json.loads(row["basis"]), row["id"]) if row else None


def settings_source_id(tx, branch_id, confirmation_id):
    if not confirmation_id:
        return None
    row = tx.execute(
        "SELECT basis FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
        (confirmation_id, branch_id, CONFIRMATION),
    ).fetchone()
    return _source_version_id(json.loads(row["basis"]), confirmation_id) if row else confirmation_id


def has_settings_source(tx, branch_id, version_id):
    return _cached_version(tx, branch_id, version_id) is not None


def current_settings_source(tx, branch_id):
    identifier = read_settings_head(tx, branch_id)
    cached = _cached_version(tx, branch_id, identifier) if identifier else None
    return {key: cached[key] for key in ("id", "device_id", "change_id", "sequence")} if cached else None


def draft_settings_objects(tx, branch_id):
    current, native = _native_settings_versions(tx, branch_id)
    values = [version_object(branch_id, version) for version in native.values()]
    if current:
        values.append(head_object(branch_id, current))
    return values


def observe_settings_version(tx, branch_id, version, receipt):
    validate_version(version)
    existing = read_settings_version(tx, branch_id, version.id)
    if existing is not None and existing != version:
        raise DomainError("sync_settings", "同一设定源修订已存在不同内容，需人工复核。", 409)
    if version.parent_id and not read_settings_version(tx, branch_id, version.parent_id):
        raise DomainError("sync_settings", "缺少设定父版本，待云端历史到齐。", 409)
    validate_version_sources(
        version,
        lambda identifier: read_project_version(tx, branch_id, identifier),
        lambda identifier: source_revision(tx, branch_id, identifier),
    )
    tx.execute(
        "INSERT INTO synced_settings_versions VALUES(?,?,?,?,?,?) ON CONFLICT(branch_id,id) DO UPDATE SET "
        "device_id=excluded.device_id,change_id=excluded.change_id,sequence=excluded.sequence "
        "WHERE excluded.sequence<synced_settings_versions.sequence",
        (
            branch_id,
            version.id,
            version.model_dump_json(),
            receipt.device_id,
            receipt.change_id,
            receipt.sequence,
        ),
    )


def _local_origins(tx, branch_id, version, project, device_id):
    origins = {}
    for item_id, origin in version.origins.items():
        data = {
            "origin": "sync",
            "source_origin": origin.origin,
            "source_device_id": origin.device_id or device_id,
            "source_revision_id": version.id,
            "edited": origin.edited,
        }
        if origin.artifact_id:
            data["source_artifact_id"] = origin.artifact_id
        if origin.project_version_id:
            if project and origin.project_version_id == version.project_version_id:
                data.update(project_confirmation_id=project["id"], project_revision=project["revision"])
            else:
                data["project_confirmation_id"] = origin.project_version_id
        if evidence := origin.evidence:
            revision = native_revision(tx, branch_id, evidence.revision_id)
            quote = _evidence_quote(evidence, revision)
            data["evidence_revision_id"] = evidence.revision_id
            data["evidence"] = {
                "chapter_id": evidence.chapter_id,
                "revision": revision.revision,
                "start": evidence.start,
                "end": evidence.end,
                "quote": quote,
            }
        origins[item_id] = data
    return origins


def apply_settings_head(tx, branch_id, value, receipt):
    cached = _cached_version(tx, branch_id, value.revision_id)
    if not cached:
        raise DomainError("sync_settings", "缺少云端采用的设定源版本，待来源记录到齐。", 409)
    version = SettingsVersion.model_validate_json(cached["payload"])
    if read_settings_head(tx, branch_id) == version.id:
        return
    if read_project_head(tx, branch_id) != version.project_version_id:
        raise DomainError("sync_settings_project", "本机采用的立项与设定依据不同，待采用对应立项。", 409)
    branch = _branch(tx, branch_id)
    workspace = _workspace(tx, branch_id)
    current = tx.execute(
        "SELECT payload FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY created DESC,rowid DESC LIMIT 1",
        (branch_id, CONFIRMATION),
    ).fetchone()
    if workspace["revision"] != (_read_payload(current)["revision"] if current else 0):
        raise DomainError("sync_settings_draft", "本机有未确认的设定草稿，保留草稿并等待作者处理。", 409)
    project = confirmed_project_tx(tx, branch_id)
    origins = _local_origins(tx, branch_id, version, project, cached["device_id"])
    categories = _categories(version)
    _persist(tx, branch, workspace, categories, origins)
    key = _digest(f"{branch_id}/{receipt.change_id}/{version.id}")
    try:
        confirm_tx(
            tx,
            branch_id,
            ConfirmConception(
                base_revision=workspace["revision"] + 1,
                base_story_revision=branch["story_revision"],
                idempotency_key=f"settings-sync-{key}",
                note=version.note,
                reconciled_project_items=version.reconciled_project_items,
                reviewed_setting_findings=version.reviewed_setting_findings,
            ),
            sync_source={
                "revision_id": version.id,
                "parent_id": version.parent_id,
                "project_version_id": version.project_version_id,
                "device_id": cached["device_id"],
                "source_change_id": cached["change_id"],
                "head_change_id": receipt.change_id,
            },
        )
    except DomainError as error:
        if error.code not in {"conception_project_review", "conception_setting_review"}:
            raise
        raise DomainError("sync_settings_review", error.message, 409) from error
