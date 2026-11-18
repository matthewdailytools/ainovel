"""Versioned planning workspace and explicit author handoff."""

import hashlib
import json
from time import time

from pydantic import Field, StrictInt, StringConstraints
from typing import Annotated

from .db import DomainError, dumps, uid
from .models import Input
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


class ConceptionItem(Input):
    id: Identifier
    title: Title
    detail: str = Field(default="", max_length=4000)
    links: list[Identifier] = Field(default_factory=list, max_length=30)


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


class ConfirmConception(ConceptionDecision):
    note: str = Field(default="", max_length=4000)


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


class ConceptionWorkspace(Input):
    revision: int
    story_revision: int
    categories: list[ConceptionCategory]
    issues: list[ConceptionIssue]
    confirmation: ConceptionConfirmation | None
    observed: list[ConceptionObserved]
    sources: list[ConceptionSource]


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


def _current(tx, branch, revision, confirmation, observed=None, source_records=None):
    return confirmation["revision"] == revision and (
        confirmation["story_revision"] == branch["story_revision"]
        or (
            confirmation["definitions"] == _definitions(tx, branch["id"])
            and confirmation["observed"]
            == (observed if observed is not None else _observed(tx, branch["id"]))
            and confirmation.get("sources", [])
            == (
                source_records
                if source_records is not None
                else _source_records(tx, branch["id"], confirmation)
            )
        )
    )


def _validate(categories):
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
    for key, (_, item) in items.items():
        links = item["links"]
        if len(set(links)) != len(links) or key in links or any(ref not in items for ref in links):
            raise DomainError("conception_links", f"「{item['title']}」含无效关联，请重新选择关联项目。")
    if len(dumps(categories).encode("utf-8")) > 100_000:
        raise DomainError("conception_size", "构思内容超过 100 KB，请缩减本书工作台内容后再保存。")


def _snapshot_tx(tx, branch):
    workspace = _workspace(tx, branch["id"])
    observed = _observed(tx, branch["id"])
    evidence = _source_records(tx, branch["id"], workspace)
    row = _latest(tx, branch["id"], CONFIRMATION)
    confirmation = None
    if row:
        payload = _read_payload(row)
        confirmation = {
            "id": row["id"],
            "revision": payload["revision"],
            "current": _current(tx, branch, workspace["revision"], payload, observed, evidence),
            "note": payload["note"],
            "planning_brief": payload["planning_brief"],
        }
    categories = workspace["categories"]
    return {
        "revision": workspace["revision"],
        "story_revision": branch["story_revision"],
        "categories": categories,
        "issues": completeness_issues(categories, observed, evidence),
        "confirmation": confirmation,
        "observed": observed,
        "sources": evidence,
    }


def snapshot(domain, branch_id):
    with domain.db.connect() as tx:
        tx.execute("BEGIN")
        return _snapshot_tx(tx, _branch(tx, branch_id))


def _check_basis(branch, workspace, data):
    if workspace["revision"] != data.base_revision or branch["story_revision"] != data.base_story_revision:
        raise DomainError("conception_changed", "构思或故事记录已更新。请保留当前编辑并重新读取后核对。", 409)


def _insert(tx, branch, kind, payload, basis):
    identifier = uid()
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
        "VALUES(?,?,?,?,?,?,'accepted',?)",
        (identifier, branch["novel_id"], branch["id"], kind, dumps(payload), dumps(basis), time()),
    )
    return identifier


def _persist(tx, branch, before, categories, generated=None):
    _validate(categories)
    previous = _items(before["categories"])
    origins = {}
    for key, entry in _items(categories).items():
        if generated and key in generated:
            origins[key] = generated[key]
        elif previous.get(key) == entry:
            origins[key] = before["origins"].get(key, {"origin": "author"})
        elif key in before["origins"]:
            origins[key] = {**before["origins"][key], "edited": True}
        else:
            origins[key] = {"origin": "author"}
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
        {"story_revision": branch["story_revision"], "conception_revision": before["revision"]},
    )


def save(domain, branch_id, data):
    categories = [category.model_dump() for category in data.categories]
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


def adopt(domain, branch_id, data):
    with domain.db.connect(True) as tx:
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
        if (
            basis.get("conception_revision") != before["revision"]
            or basis.get("story_revision") != branch["story_revision"]
        ):
            raise DomainError("conception_changed", "本候选依据已变，请重新生成或手动核对后保存。", 409)
        candidate = json.loads(row["payload"])
        source = {
            category["id"]: {
                item["id"]: ConceptionItem.model_validate(item).model_dump() for item in category["items"]
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
                value = item.model_dump()
                categories[section.id]["items"].append(value)
                categories[section.id]["skipped"] = False
                origins[item.id] = {
                    "origin": "ai",
                    "artifact_id": data.artifact_id,
                    "edited": value != source[section.id][item.id],
                }
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


def confirm(domain, branch_id, data):
    with domain.db.connect(True) as tx:
        branch = _branch(tx, branch_id)
        fingerprint, replay = _decision(tx, branch, data, "confirm")
        if replay:
            return _snapshot_tx(tx, branch)
        workspace = _workspace(tx, branch_id)
        _check_basis(branch, workspace, data)
        categories = workspace["categories"]
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
        identifier = _insert(tx, branch, CONFIRMATION, payload, {"story_revision": branch["story_revision"]})
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
    path = "/api/branches/{branch_id}/conception"

    @app.get(path)
    def get_workspace(branch_id: str) -> ConceptionWorkspace:
        return snapshot(domain, branch_id)

    @app.put(path)
    def save_workspace(branch_id: str, data: SaveConception) -> ConceptionWorkspace:
        return save(domain, branch_id, data)

    @app.post(path + "/adopt")
    def adopt_candidate(branch_id: str, data: AdoptConception) -> ConceptionWorkspace:
        return adopt(domain, branch_id, data)

    @app.post(path + "/confirm")
    def confirm_workspace(branch_id: str, data: ConfirmConception) -> ConceptionWorkspace:
        return confirm(domain, branch_id, data)
