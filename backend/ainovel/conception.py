"""Versioned planning workspace and explicit author handoff."""

import hashlib
import json
from time import time
from unicodedata import normalize

from fastapi import Query
from pydantic import Field, StrictInt, StringConstraints
from typing import Annotated, Literal

from .db import DomainError, dumps, uid
from .models import Input
from .project_brief import ConfirmedProject, confirmed_project_tx
from .story_graph import Source

WORKSPACE = "conception_workspace"
CONFIRMATION = "conception_completion"
BUNDLE = "conception_bundle"
HANDOFF = "conception_handoff"
VERSION = "conception/1"
BUILTINS = {
    "core": "故事核心",
    "characters": "人物",
    "world": "世界设定",
    "relationships": "人物关系",
    "events": "事件与多线情节",
    "timeline": "时间线",
    "foreshadowing": "伏笔与回收",
    "outline": "全书与分卷大纲",
}

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
INVALID_LINK_FINDINGS = {"missing_link", "self_link", "duplicate_link"}


class SettingPolicy(Input):
    strength: Literal["hard", "soft"]
    scope: str = Field(default="", max_length=800)
    exceptions: str = Field(default="", max_length=800)


class ConceptionItem(Input):
    id: Identifier
    title: Title
    detail: str = Field(default="", max_length=4000)
    links: list[Identifier] = Field(default_factory=list, max_length=30)
    setting_policy: SettingPolicy | None = Field(default=None, exclude_if=lambda value: value is None)


class ConceptionCategory(Input):
    id: Identifier
    title: Title
    items: list[ConceptionItem] = Field(default_factory=list, max_length=40)
    skipped: bool = Field(default=False, strict=True)


class ConceptionBasis(Input):
    base_revision: StrictInt = Field(ge=0)
    base_story_revision: StrictInt = Field(ge=0)


class SaveConception(ConceptionBasis):
    categories: list[ConceptionCategory] = Field(min_length=len(BUILTINS), max_length=24)


class ConceptionDecision(ConceptionBasis):
    idempotency_key: str = Field(min_length=8, max_length=128)


class RestoreSettingItem(ConceptionDecision):
    source_workspace_id: Identifier


class ConfirmConception(ConceptionDecision):
    note: str = Field(default="", max_length=4000)
    reconciled_project_items: list[Identifier] = Field(default_factory=list, max_length=240)
    reviewed_setting_findings: list[Identifier] = Field(default_factory=list, max_length=7440)


class CandidateSelection(Input):
    id: Identifier
    items: list[ConceptionItem] = Field(min_length=1, max_length=40)


class AdoptConception(ConceptionDecision):
    artifact_id: Identifier
    categories: list[CandidateSelection] = Field(min_length=1, max_length=24)


class ConceptionIssue(Input):
    category_id: Identifier
    message: str


class ConceptionConfirmation(Input):
    id: Identifier
    revision: int
    current: bool
    note: str
    planning_brief: str


class ConceptionObserved(Input):
    id: Identifier
    category_id: Identifier
    title: str
    revision: int
    description: str


class ConceptionSource(Input):
    item_id: Identifier
    evidence: Source
    current: bool
    author_edited: bool


class ConceptionProjectImpact(Input):
    item_id: Identifier
    category_id: Identifier
    title: str
    source_project_id: Identifier
    source_project_revision: int | None = None
    reconciled: bool


class SettingFindingLocation(Input):
    item_id: Identifier
    category_id: Identifier | None = None
    category_title: str | None = None
    item_number: int | None = None
    link_number: int | None = None


class SettingFinding(Input):
    id: Identifier
    kind: Literal[
        "same_name",
        "missing_link",
        "self_link",
        "duplicate_link",
        "skipped_link",
        "empty_link",
        "policy_link",
    ]
    locations: list[SettingFindingLocation]
    reviewed: bool = False


class SettingInspection(Input):
    findings: list[SettingFinding]


class SettingItemProvenance(Input):
    origin: Literal["author", "ai", "sync"]
    artifact_id: Identifier | None = None
    source_origin: Literal["author", "ai"] | None = None
    source_device_id: str | None = None
    source_artifact_id: Identifier | None = None
    source_revision_id: Identifier | None = None
    project_confirmation_id: Identifier | None = None
    project_revision: int | None = None
    author_edited: bool = False
    evidence: Source | None = None
    evidence_current: bool = False


class SettingItemState(Input):
    category_id: Identifier
    category_title: str
    item_number: int
    item: ConceptionItem
    provenance: SettingItemProvenance


class SettingItemVersion(Input):
    workspace_id: Identifier
    revision: int
    created: float
    state: SettingItemState | None
    restored_from: Identifier | None = None


class SettingItemHistory(ConceptionBasis):
    current: SettingItemState | None
    entries: list[SettingItemVersion]
    next_before_revision: int | None


class ConceptionWorkspace(Input):
    revision: int
    story_revision: int
    categories: list[ConceptionCategory]
    issues: list[ConceptionIssue]
    confirmation: ConceptionConfirmation | None
    observed: list[ConceptionObserved]
    sources: list[ConceptionSource]
    project: ConfirmedProject | None = None
    project_impacts: list[ConceptionProjectImpact] = Field(default_factory=list)
    setting_findings: list[SettingFinding] = Field(default_factory=list)


def _latest(tx, branch_id, kind):
    return tx.execute(
        "SELECT id,payload,basis FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY created DESC,rowid DESC LIMIT 1",
        (branch_id, kind),
    ).fetchone()


def _read_payload(row):
    payload = json.loads(row["payload"])
    if payload.get("version") != VERSION:
        raise DomainError("conception_version", "构思资料版本不兼容，请使用支持该版本的工作台。", 409)
    return payload


def revision_tx(tx, branch_id):
    current = _latest(tx, branch_id, WORKSPACE)
    return _read_payload(current)["revision"] if current else 0


def _branch(tx, branch_id):
    branch = tx.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    return branch


def _workspace(tx, branch_id):
    row = _latest(tx, branch_id, WORKSPACE)
    if row:
        return _read_payload(row)
    return {
        "revision": 0,
        "categories": [
            {"id": key, "title": title, "items": [], "skipped": False} for key, title in BUILTINS.items()
        ],
        "origins": {},
    }


def _items(categories):
    return {item["id"]: (category["id"], item) for category in categories for item in category["items"]}


def _comparison_text(value):
    return " ".join(normalize("NFC", value).split())


def _policy_terms(item):
    policy = item.get("setting_policy") or {}
    return tuple(_comparison_text(policy.get(field, "")) for field in ("scope", "exceptions"))


def _invalid_link_reason(source_id, target_id, items, preceding):
    if target_id == source_id:
        return "self_link"
    if target_id not in items:
        return "missing_link"
    if target_id in preceding:
        return "duplicate_link"
    return None


def setting_findings(categories):
    """Compare recorded names, link addresses and policy text without deciding rule semantics."""
    entries = {
        item["id"]: (
            item,
            category["skipped"],
            {
                "item_id": item["id"],
                "category_id": category["id"],
                "category_title": category["title"],
                "item_number": number,
                "link_number": None,
            },
        )
        for category in categories
        for number, item in enumerate(category["items"], 1)
    }
    findings = []

    def report(kind, *locations):
        evidence = [
            (location, entries[location["item_id"]][0] if location["item_id"] in entries else None)
            for location in locations
        ]
        identifier = hashlib.sha256(
            json.dumps([kind, evidence], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        findings.append({"id": identifier, "kind": kind, "locations": list(locations), "reviewed": False})

    names = {}
    for item, skipped, location in entries.values():
        policy = item.get("setting_policy")
        if skipped or not item["detail"].strip() or (location["category_id"] != "world" and not policy):
            continue
        meaning = (_comparison_text(item["detail"]), (policy or {}).get("strength"), _policy_terms(item))
        name = normalize("NFKC", _comparison_text(item["title"])).casefold()
        names.setdefault(name, []).append((meaning, location))
    for group in names.values():
        if len({meaning for meaning, _ in group}) > 1:
            report("same_name", *(location for _, location in group))

    for source, skipped, location in entries.values():
        preceding = set()
        for number, target_id in enumerate(source["links"], 1):
            target = entries.get(target_id)
            origin = {**location, "link_number": number}
            invalid = _invalid_link_reason(source["id"], target_id, entries, preceding)
            preceding.add(target_id)
            if invalid:
                destination = (
                    target[2]
                    if target
                    else {
                        "item_id": target_id,
                        "category_id": None,
                        "category_title": None,
                        "item_number": None,
                        "link_number": None,
                    }
                )
                report(invalid, origin, destination)
            elif not skipped and target:
                entry, target_skipped, destination = target
                if target_skipped:
                    report("skipped_link", origin, destination)
                elif not entry["detail"].strip():
                    report("empty_link", origin, destination)
                elif any(
                    left and right and left != right
                    for left, right in zip(_policy_terms(source), _policy_terms(entry), strict=True)
                ):
                    report("policy_link", origin, destination)
    return findings


def _review_covers(findings, reviewed):
    selected = set(reviewed)
    return (
        len(selected) == len(reviewed)
        and not any(finding["kind"] in INVALID_LINK_FINDINGS for finding in findings)
        and selected == {finding["id"] for finding in findings}
    )


def setting_findings_reviewed(payload, findings=None):
    if findings is None:
        findings = setting_findings(payload["categories"])
    return _review_covers(findings, payload.get("reviewed_setting_findings", []))


def completeness_issues(categories, observed=(), source_records=()):
    issues = []
    items = _items(categories)
    for category in categories:
        key = category["id"]
        if category["skipped"]:
            issues.append({"category_id": key, "message": "此类别已跳过。"})
            continue
        populated = [item for item in category["items"] if item["detail"].strip()]
        if not populated and not any(record["category_id"] == key for record in observed):
            issues.append({"category_id": key, "message": "尚未填写构思内容。"})
            continue
        expected = {
            "relationships": ("characters", 2),
            "timeline": ("events", 1),
            "foreshadowing": ("events", 2),
        }
        if key in expected:
            target, minimum = expected[key]
            for item in populated:
                count = sum(items[ref][0] == target for ref in item["links"] if ref in items)
                if count < minimum:
                    issues.append(
                        {
                            "category_id": key,
                            "message": f"「{item['title']}」的关联未齐，可核对{BUILTINS[target]}。",
                        }
                    )
    # Event-to-event links express dependencies. Other sections can refer to the same
    # event from several directions without implying chronological causality.
    edges = {
        key: [link for link in item["links"] if link in items and items[link][0] == "events"]
        for key, (category, item) in items.items()
        if category == "events"
    }
    visiting, checked = set(), set()

    def has_cycle(key):
        if key in visiting:
            return True
        if key in checked:
            return False
        visiting.add(key)
        if any(has_cycle(parent) for parent in edges[key]):
            return True
        visiting.remove(key)
        checked.add(key)
        return False

    if any(has_cycle(key) for key in edges):
        issues.append({"category_id": "events", "message": "事件依赖形成循环，请核对因果和先后顺序。"})
    for record in source_records:
        if not record["current"]:
            category_id, item = items[record["item_id"]]
            issues.append(
                {
                    "category_id": category_id,
                    "message": f"「{item['title']}」的正文依据已失效，请重新提取或另存作者设定。",
                }
            )
    return issues


def _observed(tx, branch_id):
    # Reuse the evidence and dependency checks applied by the story workbench.
    from .story_graph import graph_snapshot, sources

    mapping = {
        "character": "characters",
        "event": "events",
        "relationship": "relationships",
        "promise": "foreshadowing",
    }
    result = []
    for record in graph_snapshot(tx, branch_id)["records"]:
        payload = record["payload"]
        category = mapping.get(record["kind"])
        if (
            not category
            or record["archived"]
            or record["invalid_reasons"]
            or not sources(payload)
            or payload.get("status", payload.get("truth")) in {"planned", "unknown", "conflict"}
        ):
            continue
        description = {
            "characters": payload.get("profile"),
            "relationships": payload.get("relation"),
        }.get(category) or payload["notes"]
        entry = {
            "id": record["id"],
            "title": payload["title"],
            "revision": record["revision"],
            "description": description or "",
        }
        result.append({**entry, "category_id": category})
        if category == "events" and payload["story_order"] is not None:
            result.append({**entry, "category_id": "timeline", "description": payload["story_time"]})
    return result


def _definitions(tx, branch_id):
    return [
        row["id"]
        for row in tx.execute(
            "SELECT id FROM (SELECT id,ROW_NUMBER() OVER(PARTITION BY kind ORDER BY created DESC,id DESC) AS rank "
            "FROM artifacts WHERE branch_id=? AND chapter_id IS NULL AND status='accepted' "
            "AND kind IN ('intent','reader','world','characters','outline')) WHERE rank=1 ORDER BY id",
            (branch_id,),
        )
    ]


def _source_current(tx, branch_id, source):
    row = tx.execute(
        "SELECT revision,archived,stale FROM chapters WHERE id=? AND branch_id=?",
        (source["chapter_id"], branch_id),
    ).fetchone()
    return bool(row and row["revision"] == source["revision"] and not row["archived"] and not row["stale"])


def _source_records(tx, branch_id, workspace):
    result = []
    for identifier, origin in workspace["origins"].items():
        evidence = origin.get("evidence")
        if evidence:
            result.append(
                {
                    "item_id": identifier,
                    "evidence": evidence,
                    "current": _source_current(tx, branch_id, evidence),
                    "author_edited": origin.get("edited", False),
                }
            )
    return result


def _project_impacts(workspace, project_id):
    if not project_id:
        return []
    impacts = []
    for category in workspace["categories"]:
        if category["skipped"]:
            continue
        for item in category["items"]:
            origin = workspace["origins"].get(item["id"], {})
            previous = origin.get("project_confirmation_id")
            if previous and project_id not in {previous, origin.get("equivalent_project_confirmation_id")}:
                impacts.append(
                    {
                        "item_id": item["id"],
                        "category_id": category["id"],
                        "title": item["title"],
                        "source_project_id": previous,
                        "source_project_revision": origin.get("project_revision"),
                    }
                )
    return impacts


def _current(tx, branch, revision, confirmation, observed=None, source_records=None, findings=None):
    project = confirmed_project_tx(tx, branch["id"])
    project_id = project["id"] if project else None
    if confirmation.get("project_confirmation_id") != project_id:
        return False
    if {item["item_id"] for item in _project_impacts(confirmation, project_id)} != set(
        confirmation.get("reconciled_project_items", [])
    ):
        return False
    if confirmation["revision"] != revision or not setting_findings_reviewed(confirmation, findings):
        return False
    return confirmation["story_revision"] == branch["story_revision"] or (
        confirmation["definitions"] == _definitions(tx, branch["id"])
        and confirmation["observed"] == (observed if observed is not None else _observed(tx, branch["id"]))
        and confirmation.get("sources", [])
        == (source_records if source_records is not None else _source_records(tx, branch["id"], confirmation))
    )


def _validate(categories, *, allow_invalid_links=False):
    ids = [category["id"] for category in categories]
    if len(ids) != len(set(ids)) or not set(BUILTINS).issubset(ids):
        raise DomainError("conception_categories", "请保留内置类别，每个类别标识必须唯一。")
    titles = [category["title"].casefold() for category in categories]
    if len(titles) != len(set(titles)):
        raise DomainError("conception_categories", "构思类别名称不能重复。")
    for category in categories:
        if category["id"] in BUILTINS and category["title"] != BUILTINS[category["id"]]:
            raise DomainError("conception_categories", "内置类别名称固定，可以另建同级类别。")
    items = _items(categories)
    count = sum(len(category["items"]) for category in categories)
    if len(items) != count or count > 240:
        raise DomainError("conception_items", "每项构思须有唯一标识，每个工作版本最多保存 240 项。")
    if not allow_invalid_links:
        for key, (_, item) in items.items():
            preceding = set()
            for number, target in enumerate(item["links"], 1):
                if _invalid_link_reason(key, target, items, preceding):
                    raise DomainError(
                        "conception_links",
                        f"「{item['title']}」（{key}）的第 {number} 个关联（{target}）无效，请重新选择。",
                    )
                preceding.add(target)
    if len(dumps(categories).encode("utf-8")) > 100_000:
        raise DomainError("conception_size", "构思内容超过 100 KB，请缩减本书工作台内容后再保存。")


def _snapshot_tx(tx, branch):
    workspace = _workspace(tx, branch["id"])
    categories = workspace["categories"]
    findings = setting_findings(categories)
    observed = _observed(tx, branch["id"])
    evidence = _source_records(tx, branch["id"], workspace)
    project = confirmed_project_tx(tx, branch["id"])
    row = _latest(tx, branch["id"], CONFIRMATION)
    confirmation = None
    reconciled_items = set()
    reviewed_findings = set()
    if row:
        payload = _read_payload(row)
        confirmation = {
            "id": row["id"],
            "revision": payload["revision"],
            "current": _current(tx, branch, workspace["revision"], payload, observed, evidence, findings),
            "note": payload["note"],
            "planning_brief": payload["planning_brief"],
        }
        if confirmation["current"]:
            reconciled_items = set(payload.get("reconciled_project_items", []))
            reviewed_findings = set(payload.get("reviewed_setting_findings", []))
    return {
        "revision": workspace["revision"],
        "story_revision": branch["story_revision"],
        "categories": categories,
        "issues": completeness_issues(categories, observed, evidence),
        "confirmation": confirmation,
        "observed": observed,
        "sources": evidence,
        "project": project,
        "setting_findings": [
            {**finding, "reviewed": finding["id"] in reviewed_findings} for finding in findings
        ],
        "project_impacts": [
            {**item, "reconciled": item["item_id"] in reconciled_items}
            for item in _project_impacts(workspace, project["id"] if project else None)
        ],
    }


def snapshot(domain, branch_id):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        return _snapshot_tx(tx, _branch(tx, branch_id))


def _check_basis(branch, workspace, data):
    if workspace["revision"] != data.base_revision or branch["story_revision"] != data.base_story_revision:
        raise DomainError("conception_changed", "构思或故事记录已更新。请保留当前编辑并重新读取后核对。", 409)


def inspect_settings(domain, branch_id, data):
    categories = [category.model_dump(exclude_none=True) for category in data.categories]
    _validate(categories, allow_invalid_links=True)
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        branch = _branch(tx, branch_id)
        _check_basis(branch, _workspace(tx, branch_id), data)
    return {"findings": setting_findings(categories)}


def _item_version(workspace, item_id):
    for category in workspace["categories"]:
        for position, item in enumerate(category["items"], 1):
            if item["id"] == item_id:
                origin = workspace["origins"].get(item_id, {"origin": "author"})
                return (item, origin, category["id"], category["title"], position)
    return None


def _item_history_state(tx, branch_id, entry):
    if entry is None:
        return None
    item, origin, category_id, category_title, number = entry
    evidence = origin.get("evidence")
    return {
        "category_id": category_id,
        "category_title": category_title,
        "item_number": number,
        "item": item,
        "provenance": {
            "origin": origin["origin"],
            "artifact_id": origin.get("artifact_id"),
            "source_origin": origin.get("source_origin"),
            "source_device_id": origin.get("source_device_id"),
            "source_artifact_id": origin.get("source_artifact_id"),
            "source_revision_id": origin.get("source_revision_id"),
            "project_confirmation_id": origin.get("project_confirmation_id"),
            "project_revision": origin.get("project_revision"),
            "author_edited": origin.get("edited", False),
            "evidence": evidence,
            "evidence_current": bool(evidence and _source_current(tx, branch_id, evidence)),
        },
    }


def _item_change(entry):
    # A sibling moving or a category being renamed does not create an item revision.
    return entry[:3] if entry is not None else None


def setting_item_history(domain, branch_id, item_id, before_revision, limit):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        branch = _branch(tx, branch_id)
        workspace = _workspace(tx, branch_id)
        current = _item_version(workspace, item_id)
        cutoff = workspace["revision"] + 1 if before_revision is None else before_revision
        rows = tx.execute(
            "SELECT id,payload,basis,created FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
            "AND json_extract(payload,'$.revision')<? ORDER BY json_extract(payload,'$.revision') DESC",
            (branch_id, WORKSPACE, cutoff),
        )
        entries = []
        newer = None

        def record(version):
            row, revision, entry = version
            restore = json.loads(row["basis"]).get("restored_item") or {}
            entries.append(
                {
                    "workspace_id": row["id"],
                    "revision": revision,
                    "created": row["created"],
                    "state": _item_history_state(tx, branch_id, entry),
                    "restored_from": restore.get("workspace_id")
                    if restore.get("item_id") == item_id
                    else None,
                }
            )

        for row in rows:
            payload = _read_payload(row)
            entry = _item_version(payload, item_id)
            if newer is not None and _item_change(newer[2]) != _item_change(entry):
                record(newer)
                if len(entries) > limit:
                    break
            newer = (row, payload["revision"], entry)
        else:
            if newer is not None and newer[2] is not None:
                record(newer)
        if before_revision is None and not entries and current is None:
            raise DomainError("conception_item", "本工作版本中找不到该编号的设定条目。", 404)
        return {
            "base_revision": workspace["revision"],
            "base_story_revision": branch["story_revision"],
            "current": _item_history_state(tx, branch_id, current),
            "entries": entries[:limit],
            "next_before_revision": entries[limit - 1]["revision"] if len(entries) > limit else None,
        }


def _insert(tx, branch, kind, payload, basis):
    identifier = uid()
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,?,'accepted',?)",
        (identifier, branch["novel_id"], branch["id"], kind, dumps(payload), dumps(basis), time()),
    )
    return identifier


def _persist(tx, branch, before, categories, origin_overrides=None, *, restored_item=None):
    _validate(categories)
    previous = _items(before["categories"])
    origins = {}
    for key, entry in _items(categories).items():
        if origin_overrides and key in origin_overrides:
            origins[key] = origin_overrides[key]
        elif previous.get(key) == entry:
            origins[key] = before["origins"].get(key, {"origin": "author"})
        elif key in before["origins"]:
            origins[key] = {**before["origins"][key], "edited": True}
        else:
            origins[key] = {"origin": "author"}
    basis = {"story_revision": branch["story_revision"], "conception_revision": before["revision"]}
    if restored_item:
        basis["restored_item"] = restored_item
    return _insert(
        tx,
        branch,
        WORKSPACE,
        {
            "version": VERSION,
            "revision": before["revision"] + 1,
            "categories": categories,
            "origins": origins,
        },
        basis,
    )


def save(domain, branch_id, data):
    categories = [category.model_dump(exclude_none=True) for category in data.categories]
    with domain.db.connect(True) as tx:
        branch = _branch(tx, branch_id)
        before = _workspace(tx, branch_id)
        _check_basis(branch, before, data)
        if before["categories"] != categories:
            _persist(tx, branch, before, categories)
        return _snapshot_tx(tx, branch)


def _decision(tx, branch, data, operation):
    fingerprint = hashlib.sha256(dumps([branch["id"], operation, data.model_dump()]).encode()).hexdigest()
    prior = tx.execute("SELECT result FROM receipts WHERE key=?", (data.idempotency_key,)).fetchone()
    if prior and json.loads(prior["result"]).get("conception_decision") != fingerprint:
        raise DomainError("idempotency", "提交标识已用于其他操作，请重新提交。", 409)
    return fingerprint, prior is not None


def _receipt(tx, data, artifact_id, fingerprint):
    tx.execute(
        "INSERT INTO receipts VALUES(?,?,?)",
        (data.idempotency_key, artifact_id, dumps({"conception_decision": fingerprint})),
    )


def restore_setting_item(domain, branch_id, item_id, data):
    with domain.db.connect(True) as tx:
        branch = _branch(tx, branch_id)
        fingerprint, replay = _decision(tx, branch, data, f"restore-item:{item_id}")
        if replay:
            return _snapshot_tx(tx, branch)
        before = _workspace(tx, branch_id)
        _check_basis(branch, before, data)
        row = tx.execute(
            "SELECT payload FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
            (data.source_workspace_id, branch_id, WORKSPACE),
        ).fetchone()
        source = _item_version(_read_payload(row), item_id) if row else None
        if source is None:
            raise DomainError("conception_item_version", "此历史修订中没有该设定条目。", 404)
        current = _item_version(before, item_id)
        if _item_change(current) == _item_change(source):
            raise DomainError("conception_item_unchanged", "选中的历史版本与当前条目一致。", 409)
        item, origin, category_id, _, number = source
        target = next((category for category in before["categories"] if category["id"] == category_id), None)
        if target is None:
            raise DomainError("conception_item_category", "该历史条目的原类别已被移除，无法原位恢复。", 409)
        position = current[4] - 1 if current is not None and current[2] == category_id else number - 1
        # Keep the revision we read intact so baseline comparison and origins are preserved.
        categories = []
        for category in before["categories"]:
            entries = [entry for entry in category["items"] if entry["id"] != item_id]
            if category["id"] == category_id:
                if len(entries) >= 40:
                    raise DomainError("conception_items", "原类别已满，无法恢复该条目。", 409)
                entries.insert(position, item)
            categories.append({**category, "items": entries})
        artifact_id = _persist(
            tx,
            branch,
            before,
            categories,
            {item_id: origin},
            restored_item={"item_id": item_id, "workspace_id": data.source_workspace_id},
        )
        _receipt(tx, data, artifact_id, fingerprint)
        return _snapshot_tx(tx, branch)


def adopt(domain, branch_id, data, *, before_write=None):
    with domain.db.connect(True) as tx:
        if before_write is not None:
            before_write(tx)
        branch = _branch(tx, branch_id)
        fingerprint, replay = _decision(tx, branch, data, "adopt")
        if replay:
            return _snapshot_tx(tx, branch)
        before = _workspace(tx, branch_id)
        _check_basis(branch, before, data)
        row = tx.execute(
            "SELECT payload,basis FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='candidate'",
            (data.artifact_id, branch_id, BUNDLE),
        ).fetchone()
        if not row:
            raise DomainError("conception_source", "本工作版本中没有该构思候选。", 404)
        basis = json.loads(row["basis"])
        if project_id := basis.get("project_confirmation_id"):
            confirmed_project_tx(tx, branch_id, project_id)
        if (
            basis.get("conception_revision") != before["revision"]
            or basis.get("story_revision") != branch["story_revision"]
        ):
            raise DomainError("conception_changed", "本候选依据已变，请重新生成或手动核对后保存。", 409)
        candidate = json.loads(row["payload"])
        source = {
            category["id"]: {
                item["id"]: ConceptionItem.model_validate(item).model_dump(exclude_none=True)
                for item in category["items"]
            }
            for category in candidate["categories"]
        }
        categories = {
            category["id"]: {**category, "items": list(category["items"])}
            for category in before["categories"]
        }
        existing = _items(before["categories"])
        origins = {}
        selected = set()
        for section in data.categories:
            if section.id in selected or section.id not in categories or section.id not in source:
                raise DomainError("conception_source", "选择的类别不在本次候选中，或被重复选择。")
            selected.add(section.id)
            for item in section.items:
                if item.id in existing or item.id in origins or item.id not in source[section.id]:
                    raise DomainError("conception_source", "选择的条目不在本次候选中，或与现有条目标识冲突。")
                value = item.model_dump(exclude_none=True)
                categories[section.id]["items"].append(value)
                categories[section.id]["skipped"] = False
                origins[item.id] = {
                    "origin": "ai",
                    "artifact_id": data.artifact_id,
                    "edited": value != source[section.id][item.id],
                }
                if project_id:
                    origins[item.id]["project_confirmation_id"] = project_id
                    origins[item.id]["project_revision"] = basis.get("project_revision")
                if evidence := candidate.get("source_evidence", {}).get(item.id):
                    from .domain import utf16_slice

                    evidence = Source.model_validate(evidence).model_dump()
                    if not _source_current(tx, branch_id, evidence):
                        raise DomainError("conception_changed", "旧稿依据已变化，请重新提取。", 409)
                    saved = tx.execute(
                        "SELECT content FROM chapters WHERE id=?", (evidence["chapter_id"],)
                    ).fetchone()
                    _, quote, _ = utf16_slice(saved["content"], evidence["start"], evidence["end"])
                    if quote != evidence["quote"]:
                        raise DomainError("conception_source", "引用无法与当前原文匹配，请重新提取。", 409)
                    origins[item.id]["evidence"] = evidence
        artifact_id = _persist(tx, branch, before, list(categories.values()), origins)
        _receipt(tx, data, artifact_id, fingerprint)
        return _snapshot_tx(tx, branch)


def confirm(domain, branch_id, data, *, before_write=None):
    with domain.db.connect(True) as tx:
        return confirm_tx(tx, branch_id, data, before_write=before_write)


def confirm_tx(tx, branch_id, data, *, before_write=None, sync_source=None):
    if before_write is not None:
        before_write(tx)
    branch = _branch(tx, branch_id)
    fingerprint, replay = _decision(tx, branch, data, "confirm")
    if replay:
        return _snapshot_tx(tx, branch)
    workspace = _workspace(tx, branch_id)
    _check_basis(branch, workspace, data)
    project = confirmed_project_tx(tx, branch_id)
    project_id = project["id"] if project else None
    impacted = [item["item_id"] for item in _project_impacts(workspace, project_id)]
    accepted = set(data.reconciled_project_items)
    if accepted != set(impacted) or len(accepted) != len(data.reconciled_project_items):
        raise DomainError("conception_project_review", "请逐项核对受立项变更影响的设定，再确认。", 409)
    categories = workspace["categories"]
    findings = setting_findings(categories)
    reviewed = data.reviewed_setting_findings
    if not _review_covers(findings, reviewed):
        raise DomainError("conception_setting_review", "请到设定冲突体检逐项核对后再确认。", 409)
    observed = _observed(tx, branch_id)
    evidence = _source_records(tx, branch_id, workspace)
    planning_brief = "\n\n".join(
        f"{item['title']}\n{item['detail'].strip()}"
        for category in categories
        if category["id"] in {"core", "outline"} and not category["skipped"]
        for item in category["items"]
        if item["detail"].strip()
    )
    payload = {
        "version": VERSION,
        **workspace,
        "story_revision": branch["story_revision"],
        "issues": completeness_issues(categories, observed, evidence),
        "observed": observed,
        "sources": evidence,
        "definitions": _definitions(tx, branch_id),
        "note": data.note.strip(),
        "planning_brief": planning_brief if len(planning_brief) <= 30_000 else "",
    }
    if project:
        payload["project_confirmation_id"] = project_id
    if impacted:
        payload["reconciled_project_items"] = impacted
    if findings:
        payload["setting_findings"] = [{**finding, "reviewed": True} for finding in findings]
        payload["reviewed_setting_findings"] = reviewed
    basis = {"story_revision": branch["story_revision"]}
    if sync_source:
        basis["sync_settings_source"] = sync_source
    identifier = _insert(tx, branch, CONFIRMATION, payload, basis)
    _receipt(tx, data, identifier, fingerprint)
    return _snapshot_tx(tx, branch)


def handoff_tx(tx, branch_id, identifier):
    branch = _branch(tx, branch_id)
    row = _latest(tx, branch_id, CONFIRMATION)
    if not row or row["id"] != identifier:
        raise DomainError("conception_snapshot", "请重新选择本工作版本最新的构思确认。", 409)
    payload = _read_payload(row)
    if not _current(tx, branch, revision_tx(tx, branch_id), payload):
        raise DomainError("conception_changed", "构思或故事依据已有变化，请重新确认后交接。", 409)
    derived = {record["item_id"]: record for record in payload.get("sources", [])}
    eligible = [
        {**category, "items": [item for item in category["items"] if item["id"] not in derived]}
        for category in payload["categories"]
        if not category["skipped"]
    ]
    eligible_ids = _items(eligible)
    categories = [
        {
            "id": category["id"],
            "title": category["title"],
            "items": [
                {**item, "links": [link for link in item["links"] if link in eligible_ids]}
                for item in category["items"]
            ],
        }
        for category in eligible
        if category["items"]
    ]
    observed = [*payload["observed"]]
    for category in payload["categories"]:
        if category["skipped"]:
            continue
        for item in category["items"]:
            if item["id"] in derived and derived[item["id"]]["current"]:
                observed.append(
                    {
                        "id": item["id"],
                        "category_id": category["id"],
                        "title": item["title"],
                        "description": item["detail"],
                    }
                )
    return {
        "id": identifier,
        "version": VERSION,
        "revision": payload["revision"],
        "categories": categories,
        "observed_reference": observed,
    }


def copy_handoff_tx(tx, source_id, target_id, identifier):
    payload = handoff_tx(tx, source_id, identifier)
    _insert(tx, _branch(tx, target_id), HANDOFF, payload, {"source_branch_id": source_id})


def queued_handoff_tx(tx, branch_id, kind, identifier):
    if kind == "module":
        return handoff_tx(tx, branch_id, identifier)
    if kind == "workflow":
        row = _latest(tx, branch_id, HANDOFF)
        payload = _read_payload(row) if row else None
        if payload and payload["id"] == identifier:
            return payload
    raise DomainError("conception_snapshot", "任务找不到所选构思交接版本，请重新选择。", 409)


def register_routes(app, domain):
    from .project_brief import register_routes as register_project_routes

    register_project_routes(app, domain)
    path = "/api/branches/{branch_id}/conception"

    @app.get(path)
    def get_workspace(branch_id: str) -> ConceptionWorkspace:
        return snapshot(domain, branch_id)

    @app.put(path)
    def save_workspace(branch_id: str, data: SaveConception) -> ConceptionWorkspace:
        return save(domain, branch_id, data)

    @app.post(path + "/setting-inspection")
    def inspect_workspace_settings(branch_id: str, data: SaveConception) -> SettingInspection:
        return inspect_settings(domain, branch_id, data)

    @app.get(path + "/items/{item_id}/history")
    def get_setting_item_history(
        branch_id: str,
        item_id: Identifier,
        before_revision: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> SettingItemHistory:
        return setting_item_history(domain, branch_id, item_id, before_revision, limit)

    @app.post(path + "/items/{item_id}/restore")
    def restore_workspace_setting_item(
        branch_id: str, item_id: Identifier, data: RestoreSettingItem
    ) -> ConceptionWorkspace:
        return restore_setting_item(domain, branch_id, item_id, data)

    @app.post(path + "/adopt")
    def adopt_candidate(branch_id: str, data: AdoptConception) -> ConceptionWorkspace:
        return adopt(domain, branch_id, data)

    @app.post(path + "/confirm")
    def confirm_workspace(branch_id: str, data: ConfirmConception) -> ConceptionWorkspace:
        return confirm(domain, branch_id, data)
