"""Work checkpoints with transactional, revision-aware restoration."""

from copy import deepcopy
import hashlib
import json
from time import time
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from .conception import (
    BUILTINS,
    CONFIRMATION,
    VERSION as CONCEPTION_VERSION,
    WORKSPACE,
    _definitions,
    _observed,
    _source_records,
    completeness_issues,
    revision_tx,
)
from .db import DomainError, dumps, uid
from .models import Input
from .outline_workspace import (
    WINDOW_CHAPTERS,
    _card_key,
    _chapter_plans,
    _decode,
    _latest as latest_outline,
    _validate,
)
from .project_brief import KIND as PROJECT, confirmed_project_tx
from .reference_fork import fork_reference_cards
from .restore_impact import AffectedReport, forecast, invalidated_reports, report_basis
from .setting_review import confirmed_outline_settings, confirmed_world_basis
from .story_extraction import rewrite_references
from .story_graph import graph_snapshot, sources
from .workflow_inspection import LENGTH_TARGET_KIND, _save_length_target, current_length_target

KIND = "work_snapshot"
RESTORE_KIND = "work_snapshot_restore"
VERSION = "work-snapshot/1"
Scope = Literal["chapter", "volume", "book"]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DEFINITIONS = ("intent", "reader", "world", "characters", "outline", PROJECT)


class SnapshotInput(Input):
    label: Label
    scope: Scope = "book"
    scope_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.scope == "book") != (self.scope_id is None):
            raise ValueError("章或卷须选择编号，全书不需填写编号。")
        return self


class RestoreInput(Input):
    basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["in_place", "new_branch"]
    branch_name: Label | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_mode(self):
        if (self.mode == "new_branch") != (self.branch_name is not None):
            raise ValueError("新分支须提供名称。")
        return self


class SnapshotSettingUseImpact(Input):
    chapter_id: str
    chapter_title: str
    item_id: str
    title: str
    version_id: str


class SnapshotRestorePreview(Input):
    snapshot: dict[str, JsonValue]
    basis_sha256: str
    changes: list[dict[str, JsonValue]]
    in_place_blockers: list[str]
    shared_changes: dict[str, JsonValue]
    reports: dict[str, str]
    affected_reports: list[AffectedReport]
    affected_setting_uses: list[SnapshotSettingUseImpact]
    notes: list[str]


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _artifact(tx, branch, kind, payload, basis):
    identifier = uid()
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,?,'accepted',?)",
        (identifier, branch["novel_id"], branch["id"], kind, dumps(payload), dumps(basis), time()),
    )
    return identifier


def _branch(tx, branch_id):
    row = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not row:
        raise DomainError("not_found", "工作版本不存在。", 404)
    return dict(row)


def _latest(tx, branch_id, kind):
    row = tx.execute(
        "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND chapter_id IS NULL "
        "AND status='accepted' ORDER BY rowid DESC LIMIT 1",
        (branch_id, kind),
    ).fetchone()
    return {"id": row["id"], "payload": json.loads(row["payload"])} if row else None


def _capture(tx, branch_id):
    branch = _branch(tx, branch_id)
    graph = graph_snapshot(tx, branch_id)
    old_outline = _decode(latest_outline(tx, branch_id, True))
    outline = _decode(latest_outline(tx, branch_id))
    workspace = _latest(tx, branch_id, WORKSPACE)
    confirmation = _latest(tx, branch_id, CONFIRMATION)
    chapters = []
    for row in tx.execute(
        "SELECT c.*,r.id AS revision_id,r.content AS revision_content FROM chapters c "
        "LEFT JOIN revisions r ON c.id=r.chapter_id AND c.revision=r.revision "
        "WHERE c.branch_id=? ORDER BY c.position,c.id",
        (branch_id,),
    ):
        if row["revision_id"] is None or row["revision_content"] != row["content"]:
            raise DomainError("snapshot_revision", "章节当前稿无法与历史修订核对。", 409)
        chapters.append(
            {
                **{
                    key: row[key]
                    for key in ("id", "position", "title", "revision", "revision_id", "archived", "stale")
                },
                "sha256": _hash(row["content"]),
            }
        )
    dependencies = [
        dict(row)
        for row in tx.execute(
            "SELECT d.* FROM story_record_dependencies d JOIN story_records r ON r.id=d.record_id "
            "WHERE r.branch_id=? ORDER BY d.record_id,d.parent_id",
            (branch_id,),
        )
    ]
    facts = [
        dict(row) for row in tx.execute("SELECT * FROM facts WHERE branch_id=? ORDER BY id", (branch_id,))
    ]
    scene_spans = [
        dict(row)
        for row in tx.execute(
            "SELECT id,chapter_id,revision FROM scene_times WHERE branch_id=? AND archived=0 ORDER BY id",
            (branch_id,),
        )
    ]
    reports = {
        kind: row["id"]
        for kind in ("workflow_inspection", "book_completion")
        if (row := _latest(tx, branch_id, kind))
    }
    finalization = tx.execute(
        "SELECT id FROM assessments WHERE branch_id=? AND category='author_finalization' "
        "ORDER BY created DESC,id DESC LIMIT 1",
        (branch_id,),
    ).fetchone()
    if finalization:
        reports["author_finalization"] = finalization["id"]
    length_targets = [
        {**json.loads(row["payload"]), "id": row["id"]}
        for row in tx.execute(
            "SELECT id,payload FROM (SELECT id,payload,ROW_NUMBER() OVER("
            "PARTITION BY json_extract(payload,'$.scope'),json_extract(payload,'$.scope_id') "
            "ORDER BY rowid DESC) AS rank FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted') "
            "WHERE rank=1 ORDER BY id",
            (branch_id, LENGTH_TARGET_KIND),
        )
    ]
    definitions = {kind: _latest(tx, branch_id, kind) for kind in DEFINITIONS}
    project_id = (definitions[PROJECT] or {}).get("id")
    setting = confirmed_world_basis(tx, branch_id)
    setting_id = setting["confirmation_id"]
    if confirmation and confirmation["payload"].get("project_confirmation_id") != project_id:
        setting_id = None
    outline_live = False
    if old_outline:
        basis = old_outline["setting_basis"]
        if basis.get("project_id") == project_id and basis.get("confirmation_id") in {None, setting_id}:
            try:
                _validate(graph, old_outline["plan"], basis, True)
                outline_live = True
            except DomainError:
                pass
    return {
        "story_revision": branch["story_revision"],
        "chapters": chapters,
        "definitions": definitions,
        "workspace": workspace,
        "confirmation": confirmation,
        "confirmed_setting_id": setting_id,
        "outline": outline,
        "confirmed_outline": old_outline,
        "outline_live": outline_live,
        "records": graph["records"],
        "dependencies": dependencies,
        "facts": facts,
        "scene_spans": scene_spans,
        "reports": reports,
        "length_targets": length_targets,
    }


def _volume(snapshot, identifier):
    outline = snapshot["confirmed_outline"]
    return (
        next((row for row in outline["plan"]["volumes"] if row["id"] == identifier), None)
        if outline
        else None
    )


def _volume_ids(volume):
    return [row["chapter_id"] for _, _, row in _chapter_plans({"volumes": [volume]})] if volume else []


def _scope_ids(snapshot, scope, scope_id):
    active = [chapter["id"] for chapter in snapshot["chapters"] if not chapter["archived"]]
    if scope == "book":
        return active
    selected = [scope_id] if scope == "chapter" else _volume_ids(_volume(snapshot, scope_id))
    if not selected or not set(selected).issubset(active):
        raise DomainError("snapshot_scope", "所选章或确认卷必须包含当前工作版本的有效章节。", 409)
    return selected


def _save_snapshot(tx, branch, label, scope, scope_id, state, purpose="manual"):
    scoped = _scope_ids(state, scope, scope_id)
    payload = {
        "version": VERSION,
        "label": label,
        "scope": scope,
        "scope_id": scope_id,
        "purpose": purpose,
        "scoped_chapter_ids": scoped,
        "manifest": state,
        "manifest_sha256": _hash(dumps(state)),
    }
    return _artifact(tx, branch, KIND, payload, {"story_revision": state["story_revision"]})


def checkpoint_tx(tx, branch_id, label, *, purpose="milestone", scope="book", scope_id=None):
    """Create a checkpoint in the caller's transaction."""
    branch = _branch(tx, branch_id)
    return _save_snapshot(tx, branch, label, scope, scope_id, _capture(tx, branch_id), purpose)


def _read_snapshot(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT id,payload,created FROM artifacts WHERE branch_id=? AND id=? AND kind=? AND status='accepted'",
        (branch_id, identifier, KIND),
    ).fetchone()
    if not row:
        raise DomainError("not_found", "找不到指定的作品快照。", 404)
    state = json.loads(row["payload"])
    if state["version"] != VERSION or state["manifest_sha256"] != _hash(dumps(state["manifest"])):
        raise DomainError("snapshot_integrity", "无法校验快照版本或内容完整性。", 409)
    return {**state, "id": row["id"], "created": row["created"]}


def _summary(snapshot):
    manifest = snapshot["manifest"]
    return {
        key: snapshot[key]
        for key in (
            "id",
            "version",
            "label",
            "scope",
            "scope_id",
            "purpose",
            "created",
            "manifest_sha256",
        )
    } | {
        "scoped_chapter_ids": snapshot["scoped_chapter_ids"],
        "chapters": manifest["chapters"],
        "story_revision": manifest["story_revision"],
        "reports": manifest["reports"],
        "project_id": (manifest["definitions"][PROJECT] or {}).get("id"),
        "setting_id": manifest["confirmed_setting_id"],
        "outline_id": (manifest["confirmed_outline"] or {}).get("id"),
    }


def _changed(then, now, key):
    return (then[key] or {}).get("id") != (now[key] or {}).get("id")


def _restore_plan(snapshot, current):
    historical = snapshot["manifest"]
    scope = snapshot["scope"]
    wanted = snapshot["scoped_chapter_ids"]
    before = [row["id"] for row in current["chapters"] if not row["archived"]]
    currently_scoped = before
    blockers = []
    if scope == "chapter":
        currently_scoped = [identifier for identifier in before if identifier in wanted]
    elif scope == "volume":
        currently_scoped = _volume_ids(_volume(current, snapshot["scope_id"]))
        if not currently_scoped:
            blockers.append("找不到版本一致的当前卷，请建立新分支检查该快照。")
        other_ids = set(before) - set(currently_scoped)
        if other_ids.intersection(wanted):
            blockers.append("快照卷内的章节已移到卷外，请改为从快照建立新分支。")
        if set(currently_scoped) - set(before):
            blockers.append("当前卷含已归档章节，请先核对卷纲。")
    outside = [identifier for identifier in before if identifier not in currently_scoped]
    if scope == "chapter" and not currently_scoped:
        blockers.append("章节已归档，请从全书快照恢复或建立新分支。")
    shared = scope == "book" or (scope == "volume" and not outside)
    if shared:
        missing = [
            kind
            for kind in DEFINITIONS
            if historical["definitions"][kind] is None and current["definitions"][kind] is not None
        ]
        if missing or (historical["confirmed_outline"] is None and current["confirmed_outline"] is not None):
            blockers.append("快照缺少现有已采用对象的早期版本，请建立新分支恢复空白起点。")
    if scope == "volume" and not shared:
        changed_setup = any(
            _changed(historical["definitions"], current["definitions"], kind) for kind in DEFINITIONS
        )
        if (
            changed_setup
            or _changed(historical, current, "workspace")
            or _changed(historical, current, "confirmation")
        ):
            blockers.append("卷内快照的共用立项或设定已变化，原位恢复会影响其他卷。请建立新分支。")
        old_windows = (historical["confirmed_outline"] or {}).get("plan", {}).get("promises", [])
        new_windows = (current["confirmed_outline"] or {}).get("plan", {}).get("promises", [])
        touched = set(currently_scoped) | set(wanted)
        if any(
            ids & touched and not ids <= touched
            for window in [*old_windows, *new_windows]
            if (ids := {window[key] for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")})
        ):
            blockers.append("伏笔窗口跨越本卷边界，请从快照建立新分支。")
    if scope != "chapter" and historical["confirmed_outline"] and not historical["outline_live"]:
        blockers.append("快照里的大纲确认已失效，请建立新分支后核对大纲草案。")
    return wanted, currently_scoped, outside, shared, blockers


def _live_setting_uses(tx, branch_id, state):
    from .outline_sync import read_outline_head
    from .setting_use import current_outline_uses
    from .settings_sync import settings_source_id

    if not state["outline_live"]:
        return []
    outline = state["confirmed_outline"]
    basis = outline["setting_basis"]
    settings = {
        item["id"]: item["title"] for item in basis["items"] if item.get("category_id") not in (None, "world")
    }
    if not settings:
        return []
    uses = current_outline_uses(
        tx,
        branch_id,
        read_outline_head(tx, branch_id),
        settings_source_id(tx, branch_id, basis["confirmation_id"]),
    )
    if not uses:
        return []
    titles = {row["id"]: row["title"] for row in state["chapters"]}
    live = []
    for _, _, chapter in _chapter_plans(outline["plan"]):
        chapter_id = chapter["chapter_id"]
        for item_id in chapter["setting_ids"]:
            if item_id in settings and (proof := uses.get((chapter_id, item_id))):
                live.append(
                    {
                        "chapter_id": chapter_id,
                        "chapter_title": titles[chapter_id],
                        "item_id": item_id,
                        "title": settings[item_id],
                        "version_id": proof["version"]["id"],
                    }
                )
    return live


def _restore_basis(snapshot, current, reports, setting_uses):
    return _hash(dumps([snapshot["id"], current, reports, setting_uses]))


def _preview(snapshot, current, reports, setting_uses):
    wanted, scoped, _, shared, blockers = _restore_plan(snapshot, current)
    historical = snapshot["manifest"]
    previous = {chapter["id"]: chapter for chapter in historical["chapters"]}
    latest = {chapter["id"]: chapter for chapter in current["chapters"]}
    changes = []
    for identifier in dict.fromkeys([*wanted, *scoped]):
        old, now = previous.get(identifier), latest.get(identifier)
        if identifier not in wanted:
            action = "archive"
        elif not now or now["archived"]:
            action = "restore"
        elif any(old[key] != now[key] for key in ("title", "sha256", "stale")) or (
            snapshot["scope"] == "book" and old["position"] != now["position"]
        ):
            action = "restore"
        else:
            action = "keep"
        changes.append(
            {
                "chapter_id": identifier,
                "title": (old or now)["title"],
                "action": action,
                "snapshot_revision": old["revision"] if identifier in wanted else None,
                "current_revision": now["revision"] if now else None,
            }
        )
    cautions = [
        "体检、定稿、任务回执和用量在原版本保留。恢复稿按新依据复检。",
        "新分支以放入快照时的整书内容建立独立身份。",
    ]
    if historical["scene_spans"] or current["scene_spans"]:
        cautions.append("场景时点与视角绑定保留在原分支，恢复后的范围需重新核对。")
    if historical["confirmed_outline"] and not historical["outline_live"]:
        cautions.append("本快照的大纲依据已失效，恢复时记录为待核对草案。")
    if historical["outline"] and _changed(
        historical, {"outline": historical["confirmed_outline"]}, "outline"
    ):
        cautions.append("整书恢复和新分支保留当时另行保存的大纲草案。")
    if snapshot["scope"] != "book" and not shared:
        cautions.append("原位恢复含选区章稿、卷纲和可确定归属的故事记录，共用或跨区记录需按新依据复检。")
    if "length_targets" in historical and historical["length_targets"] != current["length_targets"]:
        cautions.append("本次恢复范围内的篇幅目标将随快照恢复，旧修订仍可追溯。")
    return {
        "snapshot": _summary(snapshot),
        "basis_sha256": _restore_basis(snapshot, current, reports, setting_uses),
        "changes": changes,
        "in_place_blockers": blockers,
        "shared_changes": {
            "definitions": [
                kind
                for kind in DEFINITIONS
                if _changed(historical["definitions"], current["definitions"], kind)
            ],
            "conception": _changed(historical, current, "workspace")
            or _changed(historical, current, "confirmation"),
            "outline": _changed(historical, current, "confirmed_outline"),
        },
        "reports": current["reports"],
        "affected_reports": [],
        "affected_setting_uses": [],
        "notes": cautions,
    }


def _content(tx, chapter):
    row = tx.execute(
        "SELECT content FROM revisions WHERE id=? AND chapter_id=? AND revision=?",
        (chapter["revision_id"], chapter["id"], chapter["revision"]),
    ).fetchone()
    if not row or _hash(row["content"]) != chapter["sha256"]:
        raise DomainError("snapshot_revision", "快照的正文修订无法核验，恢复未提交。", 409)
    return row["content"]


def _new_chapters(tx, source, target):
    mapping = {}
    for chapter in source["chapters"]:
        identifier = uid()
        tx.execute(
            "INSERT INTO chapters VALUES(?,?,?,?,?,?,?,?)",
            (
                identifier,
                target["id"],
                chapter["position"],
                chapter["title"],
                chapter["revision"],
                _content(tx, chapter),
                chapter["archived"],
                chapter["stale"],
            ),
        )
        for rev in tx.execute(
            "SELECT revision,content,cause,created FROM revisions WHERE chapter_id=? AND revision<=? ORDER BY revision",
            (chapter["id"], chapter["revision"]),
        ):
            tx.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?)", (uid(), identifier, *tuple(rev)))
        mapping[chapter["id"]] = {
            "id": identifier,
            "old_revision": chapter["revision"],
            "revision": chapter["revision"],
        }
    return mapping


def _restore_chapters(tx, domain, branch, snapshot, current):
    wanted, scoped, outside, _, _ = _restore_plan(snapshot, current)
    historical = {row["id"]: row for row in snapshot["manifest"]["chapters"]}
    if not outside:
        order = wanted
    else:
        old_order = [row["id"] for row in current["chapters"] if not row["archived"]]
        first = min(old_order.index(identifier) for identifier in scoped)
        order = [identifier for identifier in old_order[:first] if identifier in outside] + wanted
        order += [identifier for identifier in old_order[first:] if identifier in outside]
    for identifier in set(scoped) - set(wanted):
        tx.execute("UPDATE chapters SET archived=1 WHERE branch_id=? AND id=?", (branch["id"], identifier))
        tx.execute("UPDATE facts SET stale=1 WHERE chapter_id=?", (identifier,))
    mapping = {}
    reorder = snapshot["scope"] == "book" or wanted != scoped
    for position, identifier in enumerate(order, 1):
        if identifier not in wanted and not reorder:
            continue
        row = dict(
            tx.execute(
                "SELECT * FROM chapters WHERE branch_id=? AND id=?", (branch["id"], identifier)
            ).fetchone()
        )
        if identifier in wanted:
            captured = historical[identifier]
            text = _content(tx, captured)
            revision = row["revision"]
            if row["content"] != text or row["title"] != captured["title"] or row["archived"]:
                revision = domain.revise_tx(tx, row, text, "恢复作品快照")
            tx.execute(
                "UPDATE chapters SET title=?,archived=0,stale=? WHERE id=?",
                (captured["title"], captured["stale"], identifier),
            )
            mapping[identifier] = {
                "id": identifier,
                "old_revision": captured["revision"],
                "revision": revision,
            }
        if reorder:
            if snapshot["scope"] == "book":
                position = historical[identifier]["position"]
            tx.execute("UPDATE chapters SET position=? WHERE id=?", (position, identifier))
    return mapping


def _remap_source(source, chapters):
    chapter = chapters.get(source["chapter_id"])
    if chapter:
        source["chapter_id"] = chapter["id"]
        if source["revision"] == chapter["old_revision"]:
            source["revision"] = chapter["revision"]


def _record_scope(source, current, chapter_ids):
    def owned(state):
        plan = (state["confirmed_outline"] or {}).get("plan", {})
        promises = {
            window["promise_id"]
            for window in plan.get("promises", [])
            if {window[key] for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")}
            <= chapter_ids
        }
        records = set()
        for row in state["records"]:
            proof_ids = {proof["chapter_id"] for proof in sources(row["payload"])}
            if proof_ids <= chapter_ids and (proof_ids or row["id"] in promises):
                records.add(row["id"])
        return records

    before, now = owned(source), owned(current)
    captured = {row["id"] for row in source["records"]}
    return (before & now) | (now - captured)


def _graph_restore(tx, source, target, chapters, new_branch, selected=None):
    records = {row["id"]: row for row in source["records"] if selected is None or row["id"] in selected}
    current = {
        row["id"]: dict(row)
        for row in tx.execute("SELECT * FROM story_records WHERE branch_id=?", (target["id"],))
    }
    mapping = {identifier: uid() if new_branch else identifier for identifier in records}
    revisions = {
        identifier: int(row["revision"]) if new_branch else current[identifier]["revision"] + 1
        for identifier, row in records.items()
    }
    now = time()
    for identifier, row in records.items():
        value = rewrite_references(row["payload"], mapping)
        for proof in sources(value):
            _remap_source(proof, chapters)
        payload = dumps(value)
        restored = mapping[identifier]
        revision = revisions[identifier]
        tx.execute(
            "INSERT INTO story_records VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "revision=excluded.revision,payload=excluded.payload,archived=excluded.archived,updated=excluded.updated",
            (restored, target["id"], row["kind"], revision, payload, row["archived"], now, now),
        )
        tx.execute(
            "INSERT INTO story_record_history VALUES(?,?,?,?,?)",
            (restored, revision, payload, row["archived"], now),
        )
        tx.execute("DELETE FROM story_record_dependencies WHERE record_id=?", (restored,))
    for dep in source["dependencies"]:
        identifier = dep["record_id"]
        if identifier not in records:
            continue
        parent = dep["parent_id"]
        parent_revision = dep["parent_revision"]
        if parent in records and records[parent]["revision"] == parent_revision:
            parent_revision = revisions[parent]
        tx.execute(
            "INSERT INTO story_record_dependencies VALUES(?,?,?)",
            (mapping[identifier], mapping.get(parent, parent), parent_revision),
        )
    for identifier, row in current.items():
        if identifier not in records and not row["archived"] and (selected is None or identifier in selected):
            revision = row["revision"] + 1
            tx.execute(
                "UPDATE story_records SET archived=1,revision=?,updated=? WHERE id=?",
                (revision, now, identifier),
            )
            tx.execute(
                "INSERT INTO story_record_history VALUES(?,?,?,?,?)",
                (identifier, revision, row["payload"], 1, now),
            )
    return mapping


def _restore_facts(tx, source, target, chapters):
    for chapter in chapters.values():
        tx.execute("UPDATE facts SET stale=1 WHERE chapter_id=?", (chapter["id"],))
    for row in source["facts"]:
        chapter = chapters.get(row["chapter_id"])
        if chapter and not row["stale"] and row["source_revision"] == chapter["old_revision"]:
            tx.execute(
                "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (
                    uid(),
                    target["id"],
                    chapter["id"],
                    chapter["revision"],
                    row["subject"],
                    row["kind"],
                    row["statement"],
                    row["truth"],
                    row["story_time"],
                    row["evidence"],
                    row["evidence_start"],
                    row["evidence_end"],
                ),
            )


def _restore_setup(tx, source, target, chapters, snapshot_id):
    definition_ids = {}
    origin = {"snapshot_id": snapshot_id, "story_revision": target["story_revision"]}
    adopted_project = confirmed_project_tx(tx, target["id"])
    for kind, saved in source["definitions"].items():
        if not saved:
            continue
        current = _latest(tx, target["id"], kind)
        if current and current["id"] == saved["id"]:
            definition_ids[kind] = current["id"]
            continue
        payload = deepcopy(saved["payload"])
        if kind == PROJECT:
            payload["revision"] = (adopted_project["revision"] if adopted_project else 0) + 1
            payload["candidate_id"] = None
        definition_ids[kind] = _artifact(
            tx, target, kind, payload, {**origin, "source_artifact_id": saved["id"]}
        )

    before = source["workspace"]
    workspace = (
        deepcopy(before["payload"])
        if before
        else {
            "version": CONCEPTION_VERSION,
            "origins": {},
            "categories": [
                {"id": key, "title": title, "items": [], "skipped": False} for key, title in BUILTINS.items()
            ],
        }
    )
    if before or revision_tx(tx, target["id"]):
        workspace["revision"] = revision_tx(tx, target["id"]) + 1
        captured_project_id = (source["definitions"][PROJECT] or {}).get("id")
        for value in workspace["origins"].values():
            if evidence := value.get("evidence"):
                _remap_source(evidence, chapters)
            if captured_project_id and captured_project_id in {
                value.get("project_confirmation_id"),
                value.get("equivalent_project_confirmation_id"),
            }:
                value["equivalent_project_confirmation_id"] = definition_ids[PROJECT]
        _artifact(
            tx,
            target,
            WORKSPACE,
            workspace,
            {**origin, "source_artifact_id": before["id"] if before else None},
        )
    setting = source["confirmation"]
    confirmed = None
    if setting and setting["id"] == source["confirmed_setting_id"]:
        observed = _observed(tx, target["id"])
        evidence = _source_records(tx, target["id"], workspace)
        confirmed = _artifact(
            tx,
            target,
            CONFIRMATION,
            {
                **workspace,
                "story_revision": target["story_revision"],
                "definitions": _definitions(tx, target["id"]),
                "observed": observed,
                "sources": evidence,
                "issues": completeness_issues(workspace["categories"], observed, evidence),
                "note": setting["payload"]["note"],
                "planning_brief": setting["payload"]["planning_brief"],
                "project_confirmation_id": definition_ids.get(PROJECT),
                "reconciled_project_items": setting["payload"].get("reconciled_project_items", []),
                "setting_findings": setting["payload"].get("setting_findings", []),
                "reviewed_setting_findings": setting["payload"].get("reviewed_setting_findings", []),
            },
            {**origin, "source_artifact_id": setting["id"]},
        )
    return confirmed, definition_ids.get(PROJECT)


def _rebase_outline(plan, chapters, records):
    for _, _, chapter in _chapter_plans(plan):
        if target_chapter := chapters.get(chapter["chapter_id"]):
            chapter["chapter_id"] = target_chapter["id"]
    for promise in plan["promises"]:
        promise["promise_id"] = records.get(promise["promise_id"], promise["promise_id"])
    for promise in [*plan["promises"], *plan.get("new_promises", [])]:
        for key in WINDOW_CHAPTERS:
            if chapter := chapters.get(promise[key]):
                promise[key] = chapter["id"]


def _append_outline(tx, target, plan, basis, confirmed):
    latest = latest_outline(tx, target["id"])
    identifier = uid()
    tx.execute(
        "INSERT INTO outline_revisions VALUES(?,?,?,?,?,?,?)",
        (
            identifier,
            target["id"],
            (latest["revision"] if latest else 0) + 1,
            int(confirmed),
            dumps(plan),
            dumps(basis),
            time(),
        ),
    )
    return identifier


def _restore_outline(
    tx, snapshot, current, target, chapters, records, setting_id, project_id, whole, reference_ids
):
    source = snapshot["manifest"]
    saved = source["confirmed_outline"]
    if not saved and not source["outline"]:
        return None
    basis = (
        confirmed_outline_settings(tx, target["id"], setting_id)
        if setting_id
        else {"confirmation_id": None, "items": []}
    )
    basis.update(project_id=project_id, source_snapshot_id=snapshot["id"])
    identifier = None
    if saved:
        plan = deepcopy(saved["plan"])
        sources = [saved["setting_basis"]]
        if not whole:
            sources.append(current["confirmed_outline"]["setting_basis"])
            active = deepcopy(current["confirmed_outline"]["plan"])
            volume_id = snapshot["scope_id"]
            volume = next(row for row in plan["volumes"] if row["id"] == volume_id)
            active["volumes"] = [volume if row["id"] == volume_id else row for row in active["volumes"]]
            selected = set(_volume_ids(volume)) | set(_volume_ids(_volume(current, volume_id)))
            active["promises"] = [
                row for row in active["promises"] if row["plant_chapter_id"] not in selected
            ]
            active["promises"] += [row for row in plan["promises"] if row["plant_chapter_id"] in selected]
            plan = active
        _rebase_outline(plan, chapters, records)
        adopted = (
            basis
            if saved["setting_basis"]["confirmation_id"]
            else {**basis, "confirmation_id": None, "items": []}
        )
        cards = _restored_cards(plan, sources, reference_ids)
        if cards:
            adopted = {**adopted, "reference_cards": cards}
        live = source["outline_live"]
        if live:
            _validate(graph_snapshot(tx, target["id"]), plan, adopted, True)
        identifier = _append_outline(tx, target, plan, {**adopted, "restored_outline_id": saved["id"]}, live)
    draft = source["outline"]
    if whole and draft and (not saved or draft["id"] != saved["id"]):
        plan = deepcopy(draft["plan"])
        _rebase_outline(plan, chapters, records)
        draft_basis = {**basis, "restored_outline_id": draft["id"]}
        if cards := _restored_cards(plan, [draft["setting_basis"]], reference_ids):
            draft_basis["reference_cards"] = cards
        draft_id = _append_outline(tx, target, plan, draft_basis, False)
        identifier = identifier or draft_id
    return identifier


def _outline_card_references(plan):
    for _, _, chapter in _chapter_plans(plan):
        yield from chapter.get("reference_cards", [])


def _restored_cards(plan, sources, reference_ids=None):
    references = list(_outline_card_references(plan))
    selected = {_card_key(ref) for ref in references}
    cards = {
        _card_key(card): card
        for basis in sources
        for card in basis.get("reference_cards", [])
        if _card_key(card) in selected
    }
    if reference_ids:
        for ref in references:
            if identifier := ref.get("version_id"):
                ref["version_id"] = reference_ids[identifier]
        return [
            {**card, "version_id": reference_ids[card["version_id"]]} if card.get("version_id") else card
            for card in cards.values()
        ]
    return list(cards.values())


def _fork_outline_cards(tx, origin, target, source):
    selected = []
    for outline in (source["confirmed_outline"], source["outline"]):
        if not outline:
            continue
        cards = _restored_cards(outline["plan"], [outline["setting_basis"]])
        frozen = {_card_key(card) for card in cards}
        if any(
            ref.get("version_id") and _card_key(ref) not in frozen
            for ref in _outline_card_references(outline["plan"])
        ):
            raise DomainError("snapshot_reference", "快照中的大纲缺少手法卡正式修订的冻结摘要。", 409)
        selected.extend(cards)
    return fork_reference_cards(tx, origin, target, selected)


def _restore_length_targets(tx, snapshot, current, target, chapters, chapter_ids, is_new):
    historical = snapshot["manifest"]
    if "length_targets" not in historical:
        return
    scope, scope_id = snapshot["scope"], snapshot["scope_id"]

    def in_scope(row):
        return (
            is_new
            or scope == "book"
            or (row["scope"], row["scope_id"]) == (scope, scope_id)
            or row["scope"] == "chapter"
            and row["scope_id"] in chapter_ids
        )

    desired = {}
    for row in historical["length_targets"]:
        if in_scope(row):
            identifier = row["scope_id"]
            if is_new and row["scope"] == "chapter":
                if identifier not in chapters:
                    continue
                identifier = chapters[identifier]["id"]
            desired[(row["scope"], identifier)] = row["targets"]
    if not is_new:
        for row in current["length_targets"]:
            if in_scope(row):
                desired.setdefault((row["scope"], row["scope_id"]), None)
    for (kind, identifier), targets in desired.items():
        active = current_length_target(tx, target["id"], kind, identifier, include_cleared=True)
        if (active["targets"] if active else None) != targets:
            _save_length_target(tx, target["id"], kind, identifier, active, targets)


def _execute_restore(tx, domain, origin, snapshot, current, data):
    is_new = data.mode == "new_branch"
    wanted, scoped, _, shared, blockers = _restore_plan(snapshot, current)
    if not is_new and blockers:
        raise DomainError("snapshot_restore_scope", " ".join(blockers), 409)
    source = snapshot["manifest"]
    if is_new:
        duplicate = tx.execute(
            "SELECT 1 FROM branches WHERE novel_id=? AND name=?", (origin["novel_id"], data.branch_name)
        ).fetchone()
        if duplicate:
            raise DomainError("snapshot_branch_name", "请给恢复分支输入不同的名称。", 409)
        target = {**origin, "id": uid(), "name": data.branch_name, "story_revision": 0}
        tx.execute(
            "INSERT INTO branches VALUES(?,?,?,?,?,?)",
            (target["id"], target["novel_id"], target["name"], target["mode"], 0, time()),
        )
        chapters = _new_chapters(tx, source, target)
    else:
        target = origin
        chapters = _restore_chapters(tx, domain, target, snapshot, current)
    tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (target["id"],))
    target = _branch(tx, target["id"])
    selected_records = None if shared or is_new else _record_scope(source, current, set(wanted) | set(scoped))
    records = _graph_restore(tx, source, target, chapters, is_new, selected_records)
    _restore_facts(tx, source, target, chapters)
    if is_new or shared:
        setting_id, project_id = _restore_setup(tx, source, target, chapters, snapshot["id"])
    else:
        project = confirmed_project_tx(tx, target["id"])
        setting_id = current["confirmed_setting_id"]
        project_id = project["id"] if project else None
    outline_id = None
    reference_ids = _fork_outline_cards(tx, origin["id"], target["id"], source) if is_new else {}
    if is_new or snapshot["scope"] != "chapter":
        outline_id = _restore_outline(
            tx,
            snapshot,
            current,
            target,
            chapters,
            records,
            setting_id,
            project_id,
            is_new or shared,
            reference_ids,
        )
    _restore_length_targets(tx, snapshot, current, target, chapters, set(wanted) | set(scoped), is_new)
    return {
        "branch_id": target["id"],
        "story_revision": target["story_revision"],
        "chapters": chapters,
        "record_ids": records,
        "setting_id": setting_id,
        "project_id": project_id,
        "outline_id": outline_id,
        **(
            {"reference_sources": {"source_branch_id": origin["id"], "version_ids": reference_ids}}
            if reference_ids
            else {}
        ),
    }


def restore(domain, branch_id, snapshot_id, data):
    fingerprint = _hash(dumps([branch_id, snapshot_id, data.model_dump(exclude={"idempotency_key"})]))
    with domain.db.connect(True) as tx:
        prior = tx.execute("SELECT result FROM receipts WHERE key=?", (data.idempotency_key,)).fetchone()
        if prior:
            result = json.loads(prior["result"])
            if result.get("work_restore_sha256") != fingerprint:
                raise DomainError("idempotency", "恢复提交标识已用于其他操作。", 409)
            return result
        branch = _branch(tx, branch_id)
        snapshot = _read_snapshot(tx, branch_id, snapshot_id)
        current = _capture(tx, branch_id)
        reports = report_basis(tx, branch_id)
        setting_uses = _live_setting_uses(tx, branch_id, current)
        if _restore_basis(snapshot, current, reports, setting_uses) != data.basis_sha256:
            raise DomainError("snapshot_changed", "工作版本已有变化，请重新预览影响后恢复。", 409)
        recovery = _save_snapshot(tx, branch, "恢复前检查点", "book", None, current, "pre_restore")
        result = _execute_restore(tx, domain, branch, snapshot, current, data)
        result.update(snapshot_id=snapshot_id, recovery_snapshot_id=recovery, work_restore_sha256=fingerprint)
        receipt = _artifact(tx, branch, RESTORE_KIND, result, {"snapshot_id": snapshot_id})
        result["id"] = receipt
        tx.execute("INSERT INTO receipts VALUES(?,?,?)", (data.idempotency_key, receipt, dumps(result)))
        return result


def register_routes(app, domain):
    endpoint = "/api/branches/{branch_id}/work-snapshots"

    @app.get(endpoint)
    def list_work_snapshots(branch_id: str):
        _ = domain.branch(branch_id)
        with domain.db.connect() as tx:
            rows = list(
                tx.execute(
                    "SELECT id FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid DESC LIMIT 50",
                    (branch_id, KIND),
                )
            )
            return [_summary(_read_snapshot(tx, branch_id, row["id"])) for row in rows]

    @app.post(endpoint, status_code=201)
    def save_work_snapshot(branch_id: str, data: SnapshotInput):
        with domain.db.connect(True) as tx:
            branch = _branch(tx, branch_id)
            identifier = _save_snapshot(
                tx, branch, data.label, data.scope, data.scope_id, _capture(tx, branch_id)
            )
            return _summary(_read_snapshot(tx, branch_id, identifier))

    @app.get(endpoint + "/{snapshot_id}/preview")
    def preview_work_snapshot(branch_id: str, snapshot_id: str) -> SnapshotRestorePreview:
        with domain.db.connect(True) as tx:
            branch = _branch(tx, branch_id)
            snapshot = _read_snapshot(tx, branch_id, snapshot_id)
            current = _capture(tx, branch_id)
            checked = report_basis(tx, branch_id)
            setting_uses = _live_setting_uses(tx, branch_id, current)
            preview = _preview(snapshot, current, checked, setting_uses)
            if (checked or setting_uses) and not preview["in_place_blockers"]:
                simulated = RestoreInput(
                    basis_sha256=preview["basis_sha256"], mode="in_place", idempotency_key="forecast-only"
                )
                try:
                    with forecast(tx):
                        _execute_restore(tx, domain, branch, snapshot, current, simulated)
                        if checked:
                            preview["affected_reports"] = invalidated_reports(
                                checked, report_basis(tx, branch_id)
                            )
                        if setting_uses:
                            live_ids = set()
                            head = latest_outline(tx, branch_id, True)
                            # A restored confirmation gets a new outline ID. The existing
                            # proofs stay bound to the confirmation they were made against.
                            if head and head["id"] == current["confirmed_outline"]["id"]:
                                live_ids = {
                                    proof["version_id"]
                                    for proof in _live_setting_uses(tx, branch_id, _capture(tx, branch_id))
                                }
                            preview["affected_setting_uses"] = [
                                proof for proof in setting_uses if proof["version_id"] not in live_ids
                            ]
                except DomainError as error:
                    preview["in_place_blockers"].append(error.message)
            return preview

    @app.post(endpoint + "/{snapshot_id}/restore", status_code=201)
    def restore_work_snapshot(branch_id: str, snapshot_id: str, data: RestoreInput):
        return restore(domain, branch_id, snapshot_id, data)

    @app.get(endpoint + "/{snapshot_id}/chapters/{chapter_id}")
    def diff_work_snapshot_chapter(branch_id: str, snapshot_id: str, chapter_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            snapshot = _read_snapshot(tx, branch_id, snapshot_id)
            if chapter_id not in snapshot["scoped_chapter_ids"]:
                raise DomainError("not_found", "该章节未包含在所选快照范围。", 404)
            captured = next(row for row in snapshot["manifest"]["chapters"] if row["id"] == chapter_id)
            current = tx.execute(
                "SELECT revision,content,archived FROM chapters WHERE branch_id=? AND id=?",
                (branch_id, chapter_id),
            ).fetchone()
            return {
                "title": captured["title"],
                "snapshot": {"revision": captured["revision"], "content": _content(tx, captured)},
                "current": dict(current) if current else None,
            }
