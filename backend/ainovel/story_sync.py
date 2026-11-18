"""Formal story-record versions and per-record adopted heads."""

from collections import defaultdict
from hashlib import sha256
import json
from typing import Literal

from pydantic import ValidationError

from .db import DomainError
from .domain import utf16_slice
from .manuscript_exchange import Identifier, native_revision, source_revision
from .models import Input
from .project_sync import FORMAT
from .settings_portability import SettingsEvidence
from .story_graph import RECORD, SaveRecord, references, save_record_tx
from .story_portability import StoryEvidence, StoryRevisionId, StoryVersion

ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS manuscript_story_versions(
 branch_id TEXT NOT NULL REFERENCES branches(id),id TEXT NOT NULL,
 payload TEXT NOT NULL,source_device TEXT,PRIMARY KEY(branch_id,id));
CREATE TABLE IF NOT EXISTS manuscript_story_applied(
 branch_id TEXT NOT NULL REFERENCES branches(id),record_id TEXT NOT NULL,native_revision INTEGER NOT NULL,
 source_id TEXT NOT NULL,PRIMARY KEY(branch_id,record_id,native_revision),
 FOREIGN KEY(record_id,native_revision) REFERENCES story_record_history(record_id,revision) ON DELETE CASCADE,
 FOREIGN KEY(branch_id,source_id) REFERENCES manuscript_story_versions(branch_id,id));
"""
SCHEMA = (
    ARCHIVE_SCHEMA
    + """
CREATE TABLE IF NOT EXISTS synced_story_versions(
 branch_id TEXT NOT NULL REFERENCES branches(id), id TEXT NOT NULL,
 payload TEXT NOT NULL, device_id TEXT NOT NULL, change_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 PRIMARY KEY(branch_id,id));
CREATE TABLE IF NOT EXISTS story_sync_applied(
 branch_id TEXT NOT NULL REFERENCES branches(id),record_id TEXT NOT NULL,native_revision INTEGER NOT NULL,
 source_id TEXT NOT NULL,head_change_id TEXT NOT NULL,head_device_id TEXT NOT NULL,
 PRIMARY KEY(branch_id,record_id,native_revision),
 FOREIGN KEY(record_id,native_revision) REFERENCES story_record_history(record_id,revision) ON DELETE CASCADE,
 FOREIGN KEY(branch_id,source_id) REFERENCES synced_story_versions(branch_id,id));
CREATE TABLE IF NOT EXISTS story_sync_chapter_sources(
 branch_id TEXT NOT NULL REFERENCES branches(id),source_id TEXT NOT NULL,
 native_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
 PRIMARY KEY(branch_id,source_id,native_id));
"""
)


class StoryVersionObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["story-version"]
    branch_id: Identifier
    version: StoryVersion


class StoryHeadObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["story-head"]
    branch_id: Identifier
    record_id: Identifier
    revision_id: StoryRevisionId


def version_object(branch_id, version):
    return StoryVersionObject(format=FORMAT, kind="story-version", branch_id=branch_id, version=version)


def head_object(branch_id, record_id, revision_id):
    return StoryHeadObject(
        format=FORMAT, kind="story-head", branch_id=branch_id, record_id=record_id, revision_id=revision_id
    )


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def story_version_id(branch_id, version):
    return _digest(
        json.dumps(
            [branch_id, version.model_dump(exclude={"id"}, mode="json")],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _validate(branch_id, version):
    value = version.record.model_dump()
    expected_refs = set(references(value))
    stages = value.get("stages", [])
    if (
        story_version_id(branch_id, version) != version.id
        or not value["title"].strip()
        or version.parent_id == version.id
        or version.record_id in expected_refs
        or set(version.dependencies) != expected_refs
    ):
        raise DomainError("sync_story", "故事记录版本哈希或精确依赖不匹配。", 409)
    if (
        value["sources"]
        or any(stage["sources"] for stage in stages)
        or len(stages) != len(version.evidence.stages)
        or any(len(items) > 30 for items in version.evidence.stages)
    ):
        raise DomainError("sync_story_evidence", "故事引用须按实际阶段使用原文修订地址。", 409)
    if (value.get("status", value.get("truth")) == "actual" and not version.evidence.record) or any(
        stage["status"] in {"planted", "reinforced", "resolved"} and not proof
        for stage, proof in zip(stages, version.evidence.stages, strict=True)
    ):
        raise DomainError("sync_story_evidence", "实际状态、埋设、强化或回收缺少原文证据。", 409)
    if any(stage["status"] in {"retained", "abandoned"} and not stage["note"].strip() for stage in stages):
        raise DomainError("sync_story", "保留或放弃伏笔必须带有原确认理由。", 409)


def parse_story_object(branch_id, object_id, payload, tombstone):
    is_version = object_id.startswith("story-version:")
    try:
        schema = StoryVersionObject if is_version else StoryHeadObject
        value = schema.model_validate(payload)
    except ValidationError as error:
        raise DomainError("sync_story", "故事记录未通过正式版本格式校验。", 409) from error
    expected = f"story-version:{value.version.id}" if is_version else f"story:{value.record_id}"
    if tombstone or value.branch_id != branch_id or object_id != expected:
        raise DomainError("sync_story", "故事版本必须属于同一工作版本，归档须作为新修订提交。", 409)
    if is_version:
        _validate(branch_id, value.version)
    return value


def _verified_source(value, revision):
    if not revision or revision.chapter_id != value.chapter_id:
        raise DomainError("sync_story_evidence", "缺少故事记录引用的原始章稿，待原文证据到齐。", 409)
    try:
        _, quote, _ = utf16_slice(revision.content, value.start, value.end)
    except DomainError as error:
        raise DomainError("sync_story_evidence", "故事记录引用的原文范围无效。", 409) from error
    if not quote or len(quote) > 4000 or _digest(quote) != value.quote_sha256:
        raise DomainError("sync_story_evidence", "故事记录的原文选区校验值不匹配。", 409)
    return {
        "chapter_id": value.chapter_id,
        "revision": revision.revision,
        "start": value.start,
        "end": value.end,
        "quote": quote,
    }


def validate_version_sources(version, story_lookup, revision_lookup):
    if version.parent_id:
        parent = story_lookup(version.parent_id)
        if (
            not parent
            or parent.id != version.parent_id
            or parent.record_id != version.record_id
            or parent.record.kind != version.record.kind
        ):
            raise DomainError("sync_story_dependency", "缺少同一故事记录的上一个版本。", 409)
    dependencies = {}
    for record_id, version_id in version.dependencies.items():
        dependency = story_lookup(version_id)
        if not dependency or dependency.id != version_id or dependency.record_id != record_id:
            raise DomainError("sync_story_dependency", "缺少故事记录引用的准确依赖版本。", 409)
        dependencies[record_id] = dependency.record
    _validate_dependency_kinds(version.record, dependencies)
    for items in [version.evidence.record, *version.evidence.stages]:
        for source in items:
            _verified_source(source, revision_lookup(source.revision_id))


def _validate_dependency_kinds(record, dependencies):
    people, events = (), ()
    if record.kind == "event":
        people, events = record.participants, record.after
    elif record.kind == "state":
        people, events = (record.character_id,), (record.event_id, record.until_event_id)
    elif record.kind == "relationship":
        people, events = (record.from_id, record.to_id), (record.event_id,)
    elif record.kind == "promise":
        events = [stage.event_id for stage in record.stages]
    for ids, kind in ((people, "character"), (events, "event")):
        if any(identifier and dependencies[identifier].kind != kind for identifier in ids):
            raise DomainError("sync_story_dependency", "故事关系、事件或人物的关联类型不符。", 409)
    if (
        record.kind in {"state", "relationship"}
        and record.event_id
        and record.truth == "actual"
        and dependencies[record.event_id].status != "actual"
    ):
        raise DomainError("sync_story_dependency", "实际状态或人物关系需要已经发生的关联事件。", 409)


def _portable_source(tx, branch_id, source):
    row = tx.execute(
        "SELECT r.id FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
        "WHERE c.branch_id=? AND r.chapter_id=? AND r.revision=?",
        (branch_id, source["chapter_id"], source["revision"]),
    ).fetchone()
    if not row:
        raise DomainError("sync_story_evidence", "故事引用缺少本机原始正文修订。", 409)
    value = SettingsEvidence(
        chapter_id=source["chapter_id"],
        revision_id=row["id"],
        start=source["start"],
        end=source["end"],
        quote_sha256=_digest(source["quote"]),
    )
    _verified_source(value, native_revision(tx, branch_id, row["id"]))
    return value


def _native_version(tx, branch_id, row, record, parent, dependencies):
    evidence = StoryEvidence(
        record=[_portable_source(tx, branch_id, source) for source in record["sources"]],
        stages=[
            [_portable_source(tx, branch_id, source) for source in stage["sources"]]
            for stage in record.get("stages", [])
        ],
    )
    record["sources"] = []
    for stage in record.get("stages", []):
        stage["sources"] = []
    version = StoryVersion(
        id="0" * 64,
        parent_id=parent.id if parent else None,
        record_id=row["record_id"],
        archived=bool(row["archived"]),
        record=record,
        evidence=evidence,
        dependencies={key: value.id for key, value in dependencies.items()},
    )
    version = version.model_copy(update={"id": story_version_id(branch_id, version)})
    _validate(branch_id, version)
    return version


def _history_dependencies(tx, branch_id):
    by_revision = defaultdict(dict)
    for row in tx.execute(
        "SELECT h.* FROM story_record_dependency_history h JOIN story_records r ON r.id=h.record_id "
        "WHERE r.branch_id=?",
        (branch_id,),
    ):
        by_revision[row["record_id"], row["revision"]][row["parent_id"]] = row["parent_revision"]
    for row in tx.execute(
        "SELECT d.*,r.revision FROM story_record_dependencies d JOIN story_records r ON r.id=d.record_id "
        "WHERE r.branch_id=?",
        (branch_id,),
    ):
        by_revision[row["record_id"], row["revision"]].setdefault(row["parent_id"], row["parent_revision"])
    return by_revision


def _native_catalog(tx, branch_id):
    history = list(
        tx.execute(
            "SELECT h.*,COALESCE(v.payload,m.payload) AS adopted FROM story_record_history h JOIN story_records r ON r.id=h.record_id "
            "LEFT JOIN story_sync_applied a ON a.branch_id=r.branch_id AND a.record_id=h.record_id "
            "AND a.native_revision=h.revision LEFT JOIN synced_story_versions v ON v.branch_id=a.branch_id "
            "AND v.id=a.source_id LEFT JOIN manuscript_story_applied p ON p.branch_id=r.branch_id "
            "AND p.record_id=h.record_id AND p.native_revision=h.revision LEFT JOIN manuscript_story_versions m "
            "ON m.branch_id=p.branch_id AND m.id=p.source_id WHERE r.branch_id=? ORDER BY h.created,h.rowid",
            (branch_id,),
        )
    )
    dependencies = _history_dependencies(tx, branch_id)
    versions, native, failures = {}, {}, {}
    predecessors = {}
    last_revisions = {}
    for row in sorted(history, key=lambda item: (item["record_id"], item["revision"])):
        identifier, revision = row["record_id"], row["revision"]
        if identifier in last_revisions:
            predecessors[identifier, revision] = (identifier, last_revisions[identifier])
        last_revisions[identifier] = revision
    pending = {}
    for row in history:
        key = (row["record_id"], row["revision"])
        if row["adopted"]:
            versions[key] = StoryVersion.model_validate_json(row["adopted"])
        else:
            pending[key] = (row, RECORD.validate_json(row["payload"]).model_dump())
    while pending:
        count = len(pending)
        for key, (row, record) in list(pending.items()):
            parent_key = predecessors.get(key)
            required = {ref: (ref, dependencies[key].get(ref)) for ref in references(record)}
            if any(dependency[1] is None for dependency in required.values()):
                failures[key] = "故事记录旧修订的准确依赖未保留，暂不能同步该版本。"
            elif (parent_key and parent_key not in versions) or not all(
                ref in versions for ref in required.values()
            ):
                continue
            else:
                try:
                    value = _native_version(
                        tx,
                        branch_id,
                        row,
                        record,
                        versions.get(parent_key),
                        {identifier: versions[dep] for identifier, dep in required.items()},
                    )
                except DomainError as error:
                    failures[key] = error.message
                else:
                    versions[key] = native[value.id] = value
            del pending[key]
        if len(pending) == count:
            for key in pending:
                failures[key] = "故事记录的父版本或依赖尚未到齐，暂不能同步该版本。"
            break
    return versions, native, failures


def _cached_version(tx, branch_id, version_id):
    return tx.execute(
        "SELECT * FROM synced_story_versions WHERE branch_id=? AND id=?", (branch_id, version_id)
    ).fetchone()


def read_story_version(tx, branch_id, version_id):
    cached = _cached_version(tx, branch_id, version_id)
    if cached:
        return StoryVersion.model_validate_json(cached["payload"])
    row = tx.execute(
        "SELECT payload FROM manuscript_story_versions WHERE branch_id=? AND id=?", (branch_id, version_id)
    ).fetchone()
    if row:
        return StoryVersion.model_validate_json(row["payload"])
    return _native_catalog(tx, branch_id)[1].get(version_id)


def read_story_head(tx, branch_id, record_id):
    row = tx.execute(
        "SELECT r.revision,COALESCE(a.source_id,p.source_id) AS source_id FROM story_records r "
        "LEFT JOIN story_sync_applied a ON a.branch_id=r.branch_id AND a.record_id=r.id "
        "AND a.native_revision=r.revision LEFT JOIN manuscript_story_applied p ON p.branch_id=r.branch_id "
        "AND p.record_id=r.id AND p.native_revision=r.revision WHERE r.branch_id=? AND r.id=?",
        (branch_id, record_id),
    ).fetchone()
    if not row:
        return None
    if row["source_id"]:
        return row["source_id"]
    key = (record_id, row["revision"])
    versions, _, failures = _native_catalog(tx, branch_id)
    if key not in versions:
        raise DomainError("sync_story_dependency", failures.get(key, "故事记录缺少本机历史，待复核。"), 409)
    return versions[key].id


def has_story_source(tx, branch_id, version_id):
    return _cached_version(tx, branch_id, version_id) is not None


def current_story_source(tx, branch_id, record_id):
    identifier = read_story_head(tx, branch_id, record_id)
    cached = _cached_version(tx, branch_id, identifier) if identifier else None
    return {key: cached[key] for key in ("id", "device_id", "change_id", "sequence")} if cached else None


def draft_story_objects(tx, branch_id, *, on_pending=None):
    versions, native, failures = _native_catalog(tx, branch_id)
    archived = {
        row["id"]: StoryVersion.model_validate_json(row["payload"])
        for row in tx.execute(
            "SELECT id,payload FROM manuscript_story_versions WHERE branch_id=?", (branch_id,)
        )
    }
    known = {
        row["id"]
        for row in tx.execute("SELECT id FROM synced_story_versions WHERE branch_id=?", (branch_id,))
    }
    outgoing = order_story_versions({**archived, **native}, known)
    values = [version_object(branch_id, value) for value in outgoing]
    heads = []
    for row in tx.execute(
        "SELECT id,revision FROM story_records WHERE branch_id=? ORDER BY id", (branch_id,)
    ):
        key = (row["id"], row["revision"])
        if key not in versions:
            if on_pending:
                on_pending(row["id"], failures.get(key, "故事记录缺少本机历史，待复核。"))
        else:
            heads.append(head_object(branch_id, row["id"], versions[key].id))
    order = {version.id: index for index, version in enumerate(outgoing)}
    return [*values, *sorted(heads, key=lambda head: order.get(head.revision_id, -1))]


def order_story_versions(versions, external=()):
    ready = set(external) - versions.keys()
    remaining = dict(versions)
    ordered = []
    while remaining:
        progress = False
        for identifier, version in list(remaining.items()):
            required = set(version.dependencies.values())
            if version.parent_id:
                required.add(version.parent_id)
            if required <= ready:
                ready.add(identifier)
                ordered.append(remaining.pop(identifier))
                progress = True
        if not progress:
            raise DomainError("sync_story_dependency", "故事档案缺少版本依赖或版本构成循环。", 409)
    return ordered


def observe_story_version(tx, branch_id, version, receipt):
    _validate(branch_id, version)
    cached = _cached_version(tx, branch_id, version.id)
    if cached and StoryVersion.model_validate_json(cached["payload"]) != version:
        raise DomainError("sync_story", "同一故事源修订已存在不同内容，需人工核对。", 409)
    validate_version_sources(
        version,
        lambda identifier: read_story_version(tx, branch_id, identifier),
        lambda identifier: source_revision(tx, branch_id, identifier),
    )
    tx.execute(
        "INSERT INTO synced_story_versions VALUES(?,?,?,?,?,?) ON CONFLICT(branch_id,id) DO UPDATE SET "
        "device_id=excluded.device_id,change_id=excluded.change_id,sequence=excluded.sequence "
        "WHERE excluded.sequence<synced_story_versions.sequence",
        (
            branch_id,
            version.id,
            version.model_dump_json(),
            receipt.device_id,
            receipt.change_id,
            receipt.sequence,
        ),
    )


def remember_adopted_chapter_source(tx, branch_id, source):
    """A selected cloud chapter may be saved as a new native revision after a CAS conflict."""
    row = tx.execute(
        "SELECT r.id,r.content FROM chapters c JOIN revisions r ON r.chapter_id=c.id AND r.revision=c.revision "
        "WHERE c.branch_id=? AND c.id=? AND c.archived=0",
        (branch_id, source.chapter_id),
    ).fetchone()
    if (
        row
        and row["id"] != source.id
        and row["content"] == source.content
        and source_revision(tx, branch_id, source.id) == source
    ):
        tx.execute(
            "INSERT OR IGNORE INTO story_sync_chapter_sources VALUES(?,?,?)",
            (branch_id, source.id, row["id"]),
        )


def _adopted_chapter_revision(tx, branch_id, identifier):
    if native := native_revision(tx, branch_id, identifier):
        return native
    row = tx.execute(
        "SELECT a.native_id FROM story_sync_chapter_sources a JOIN revisions r ON r.id=a.native_id "
        "WHERE a.branch_id=? AND a.source_id=? ORDER BY r.revision DESC LIMIT 1",
        (branch_id, identifier),
    ).fetchone()
    if row and (original := source_revision(tx, branch_id, identifier)):
        local = native_revision(tx, branch_id, row["native_id"])
        if local and local.chapter_id == original.chapter_id and local.sha256 == original.sha256:
            return local
    return None


def _local_record(tx, branch_id, version):
    record = version.record.model_dump()
    for item, evidence in zip(
        [record, *record.get("stages", [])],
        [version.evidence.record, *version.evidence.stages],
        strict=True,
    ):
        item["sources"] = [
            _verified_source(source, _adopted_chapter_revision(tx, branch_id, source.revision_id))
            for source in evidence
        ]
    return record


def apply_story_head(tx, branch_id, value, receipt):
    cached = _cached_version(tx, branch_id, value.revision_id)
    version = StoryVersion.model_validate_json(cached["payload"]) if cached else None
    if not version or version.record_id != value.record_id:
        raise DomainError("sync_story", "缺少云端采用的对应故事版本，待来源记录到齐。", 409)
    if read_story_head(tx, branch_id, value.record_id) == version.id:
        return
    for key, expected in version.dependencies.items():
        if read_story_head(tx, branch_id, key) != expected:
            raise DomainError("sync_story_dependency", "本机尚未采用故事记录所需的关联版本。", 409)
    old = tx.execute("SELECT * FROM story_records WHERE id=?", (version.record_id,)).fetchone()
    if old and old["branch_id"] != branch_id:
        raise DomainError("sync_story", "该故事记录编号属于其他工作版本。", 409)
    story_revision = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
    payload = _local_record(tx, branch_id, version)
    identifier = save_record_tx(
        tx,
        branch_id,
        SaveRecord(
            base_story_revision=story_revision,
            base_revision=old["revision"] if old else None,
            record=payload,
            archived=version.archived,
        ),
        old["id"] if old else None,
        new_identifier=None if old else version.record_id,
        sync_import=True,
    )
    local_revision = old["revision"] + 1 if old else 0
    tx.execute(
        "INSERT INTO story_sync_applied VALUES(?,?,?,?,?,?)",
        (branch_id, identifier, local_revision, version.id, receipt.change_id, receipt.device_id),
    )
