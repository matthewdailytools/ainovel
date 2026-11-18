"""Preview a single historical setting draft against the actual downstream checks."""

from hashlib import sha256

from .conception import (
    CONFIRMATION,
    WORKSPACE,
    ConceptionBasis,
    Identifier,
    _branch,
    _check_basis,
    _current,
    _decision,
    _item_change,
    _item_history_state,
    _item_version,
    _latest,
    _persist,
    _read_payload,
    _receipt,
    _snapshot_tx,
    _workspace,
)
from .db import DomainError, dumps
from .models import Input
from .outline_workspace import _chapter_plans, _decode, _latest as latest_outline, _settings
from .restore_impact import AffectedReport, forecast, invalidated_reports, report_basis


class SettingRestoreChapter(Input):
    chapter_id: Identifier
    title: str
    chapter_revision: int
    volume_id: Identifier
    volume_title: str
    unit_id: Identifier
    unit_title: str
    references_item: bool


class SettingRestoreOutline(Input):
    id: Identifier
    revision: int
    confirmed: bool
    setting_confirmation_id: Identifier | None
    setting_basis_current: bool
    chapters: list[SettingRestoreChapter]


class SettingRestorePreview(ConceptionBasis):
    item_id: Identifier
    source_workspace_id: Identifier
    current_confirmation_id: Identifier | None
    confirmation_current: bool
    basis_sha256: str
    outlines: list[SettingRestoreOutline]
    affected_reports: list[AffectedReport]


def _replaced(tx, branch_id, workspace, item_id, source_id):
    row = tx.execute(
        "SELECT payload FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
        (source_id, branch_id, WORKSPACE),
    ).fetchone()
    source = _item_version(_read_payload(row), item_id) if row else None
    if source is None:
        raise DomainError("conception_item_version", "此历史修订中没有该设定条目。", 404)
    current = _item_version(workspace, item_id)
    if _item_change(current) == _item_change(source):
        raise DomainError("conception_item_unchanged", "选中的历史版本与当前条目一致。", 409)
    item, _, category_id, _, number = source
    if not any(category["id"] == category_id for category in workspace["categories"]):
        raise DomainError("conception_item_category", "该历史条目的原类别已被移除，无法原位恢复。", 409)
    position = current[4] - 1 if current is not None and current[2] == category_id else number - 1
    categories = []
    for category in workspace["categories"]:
        entries = [entry for entry in category["items"] if entry["id"] != item_id]
        if category["id"] == category_id:
            if len(entries) >= 40:
                raise DomainError("conception_items", "原类别已满，无法恢复该条目。", 409)
            entries.insert(position, item)
        categories.append({**category, "items": entries})
    return categories, current, source


def _outline_dependencies(tx, branch_id, item_id):
    outlines = {
        row["id"]: _decode(row)
        for row in (latest_outline(tx, branch_id, True), latest_outline(tx, branch_id))
        if row
    }
    chapters = {
        row["id"]: row
        for row in tx.execute(
            "SELECT id,title,revision FROM chapters WHERE branch_id=? AND archived=0", (branch_id,)
        )
    }
    for outline in outlines.values():
        confirmation_id = outline["setting_basis"]["confirmation_id"]
        references = []
        for volume, unit, plan in _chapter_plans(outline["plan"]):
            if not (chapter := chapters.get(plan["chapter_id"])):
                continue
            direct = item_id in plan["setting_ids"]
            if direct or confirmation_id:
                references.append(
                    {
                        "chapter_id": chapter["id"],
                        "title": chapter["title"],
                        "chapter_revision": chapter["revision"],
                        "volume_id": volume["id"],
                        "volume_title": volume["title"],
                        "unit_id": unit["id"],
                        "unit_title": unit["title"],
                        "references_item": direct,
                    }
                )
        if not confirmation_id and not references:
            continue
        try:
            basis_current = bool(_settings(tx, branch_id, confirmation_id)["confirmation_id"])
        except DomainError as error:
            if error.code not in {"conception_snapshot", "conception_changed", "conception_setting_review"}:
                raise
            basis_current = False
        yield {
            "id": outline["id"],
            "revision": outline["revision"],
            "confirmed": outline["confirmed"],
            "setting_confirmation_id": confirmation_id,
            "setting_basis_current": basis_current,
            "chapters": references,
        }


def _write(tx, branch, workspace, item_id, source_id, plan):
    categories, _, source = plan
    return _persist(
        tx,
        branch,
        workspace,
        categories,
        {item_id: source[1]},
        restored_item={"item_id": item_id, "workspace_id": source_id},
    )


def _preview(tx, branch, workspace, item_id, source_id, plan):
    branch_id = branch["id"]
    confirmation = _latest(tx, branch_id, CONFIRMATION)
    before = report_basis(tx, branch_id)
    outlines = list(_outline_dependencies(tx, branch_id, item_id))
    confirmation_current = bool(
        confirmation and _current(tx, branch, workspace["revision"], _read_payload(confirmation))
    )
    with forecast(tx):
        _write(tx, branch, workspace, item_id, source_id, plan)
        affected = invalidated_reports(before, report_basis(tx, branch_id)) if before else []
    preview = {
        "item_id": item_id,
        "source_workspace_id": source_id,
        "base_revision": workspace["revision"],
        "base_story_revision": branch["story_revision"],
        "current_confirmation_id": confirmation["id"] if confirmation else None,
        "confirmation_current": confirmation_current,
        "outlines": outlines,
        "affected_reports": affected,
    }
    preview["basis_sha256"] = sha256(
        dumps(
            [branch_id, *(_item_history_state(tx, branch_id, entry) for entry in plan[1:]), preview]
        ).encode()
    ).hexdigest()
    return preview


def preview_setting_restore(domain, branch_id, item_id, source_id):
    with domain.db.connect(True) as tx:
        branch = _branch(tx, branch_id)
        workspace = _workspace(tx, branch_id)
        plan = _replaced(tx, branch_id, workspace, item_id, source_id)
        return _preview(tx, branch, workspace, item_id, source_id, plan)


def restore_setting_item(domain, branch_id, item_id, data):
    with domain.db.connect(True) as tx:
        branch = _branch(tx, branch_id)
        fingerprint, replay = _decision(tx, branch, data, f"restore-item:{item_id}")
        if replay:
            return _snapshot_tx(tx, branch)
        workspace = _workspace(tx, branch_id)
        _check_basis(branch, workspace, data)
        source_id = data.source_workspace_id
        plan = _replaced(tx, branch_id, workspace, item_id, source_id)
        preview = _preview(tx, branch, workspace, item_id, source_id, plan)
        if data.basis_sha256 != preview["basis_sha256"]:
            raise DomainError(
                "conception_restore_changed",
                "设定依据、章纲或报告已有变化。请重新预览设定恢复影响。",
                409,
            )
        artifact_id = _write(tx, branch, workspace, item_id, source_id, plan)
        _receipt(tx, data, artifact_id, fingerprint)
        return _snapshot_tx(tx, branch)
