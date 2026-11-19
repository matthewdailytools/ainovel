"""Restore an accepted story graph and its precise historical chapter bindings."""

from collections import defaultdict

from pydantic import ValidationError

from .db import DomainError, dumps
from .manuscript_exchange import native_revision, source_revision
from .story_graph import RECORD, references
from .story_portability import NativeStoryRevision, PortableStoryArchive, StoryVersion
from .story_sync import (
    _history_dependencies,
    _native_catalog,
    _validate,
    _verified_source,
    order_story_versions,
    validate_version_sources,
)


def _archive_error(message):
    return DomainError("manuscript_story", message, 409)


def _wire_sources(version):
    return [version.evidence.record, *version.evidence.stages]


def _binding_sources(row):
    return [row.evidence.record, *row.evidence.stages]


def _bind_native(version, history, original_lookup, native_lookup):
    payload = version.record.model_dump()
    proof = _wire_sources(version)
    bindings = _binding_sources(history)
    if [len(group) for group in proof] != [len(group) for group in bindings]:
        raise _archive_error("故事本机原文引用与正式修订的选区数不一致。")
    aliases = set()
    for item, group, native_ids in zip([payload, *payload.get("stages", [])], proof, bindings, strict=True):
        item["sources"] = []
        for evidence, identifier in zip(group, native_ids, strict=True):
            original = original_lookup(evidence.revision_id)
            actual = native_lookup(identifier)
            if (
                not original
                or not actual
                or (original.chapter_id != actual.chapter_id or original.sha256 != actual.sha256)
            ):
                raise _archive_error("故事记录缺少已采用的同一章、同全文的原文修订。")
            item["sources"].append(_verified_source(evidence, actual))
            if evidence.revision_id != identifier:
                aliases.add((evidence.revision_id, identifier))
    return payload, aliases


def export_story_archive(tx, branch_id):
    local, native, failures = _native_catalog(tx, branch_id)
    if failures:
        raise _archive_error("本机故事历史缺少已保存依赖或原文，核对后方可完整导出。")
    devices = {}
    versions = dict(native)
    # Keep the file source device ahead of the relay device recorded during cloud upload.
    for table, column in (
        ("synced_story_versions", "device_id"),
        ("manuscript_story_versions", "source_device"),
    ):
        for row in tx.execute(f"SELECT id,payload,{column} FROM {table} WHERE branch_id=?", (branch_id,)):
            version = StoryVersion.model_validate_json(row["payload"])
            if version.id in versions and versions[version.id] != version:
                raise _archive_error("故事记录编号指向两个不同的版本。")
            versions[version.id] = version
            if row[column]:
                devices[version.id] = row[column]
            elif table == "manuscript_story_versions":
                devices.pop(version.id, None)
    if not versions:
        return None
    history = defaultdict(list)
    chapter_sources = {
        (row["chapter_id"], row["revision"]): row["id"]
        for row in tx.execute(
            "SELECT r.id,r.chapter_id,r.revision FROM revisions r JOIN chapters c ON c.id=r.chapter_id "
            "WHERE c.branch_id=?",
            (branch_id,),
        )
    }
    dependencies = _history_dependencies(tx, branch_id)

    def source_ids(sources):
        try:
            return [chapter_sources[proof["chapter_id"], proof["revision"]] for proof in sources]
        except KeyError as error:
            raise _archive_error("故事本机历史缺少引用的正文修订。") from error

    for row in tx.execute(
        "SELECT h.* FROM story_record_history h JOIN story_records r ON r.id=h.record_id "
        "WHERE r.branch_id=? ORDER BY h.record_id,h.revision",
        (branch_id,),
    ):
        key = (row["record_id"], row["revision"])
        payload = RECORD.validate_json(row["payload"]).model_dump()
        if key not in local or not references(payload) <= dependencies[key].keys():
            raise _archive_error("本机故事修订缺少对应历史或关联证明。")
        history[row["record_id"]].append(
            NativeStoryRevision(
                revision=row["revision"],
                version_id=local[key].id,
                created=row["created"],
                dependencies={
                    identifier: dependencies[key][identifier] for identifier in references(payload)
                },
                evidence={
                    "record": source_ids(payload["sources"]),
                    "stages": [source_ids(stage["sources"]) for stage in payload.get("stages", [])],
                },
            )
        )
    heads = {}
    for row in tx.execute(
        "SELECT id,revision FROM story_records WHERE branch_id=? ORDER BY id", (branch_id,)
    ):
        if not (chosen := local.get((row["id"], row["revision"]))):
            raise _archive_error("本机故事采用头缺少正式修订。")
        heads[row["id"]] = chosen.id
    try:
        return PortableStoryArchive(
            adopted_heads=heads,
            versions=order_story_versions(
                {identifier: versions[identifier] for identifier in sorted(versions)}
            ),
            source_devices=devices,
            native_history=dict(history),
        )
    except (DomainError, ValidationError) as error:
        raise _archive_error("本机故事档案的历史或采用头不完整。") from error


def validate_effective_cycles(adopted_heads, versions):
    refs = {
        identifier: set(versions[version_id].dependencies) for identifier, version_id in adopted_heads.items()
    }
    ready = {identifier for identifier, parents in refs.items() if not parents}
    for _ in refs:
        if all(identifier in ready for identifier in refs):
            return
        added = {identifier for identifier, parents in refs.items() if parents <= ready} - ready
        if not added:
            break
        ready.update(added)
    if set(refs) != ready:
        raise _archive_error("故事采用头的关联构成循环或缺失。")


def validate_story_archive(history, branch_id, revisions, native_revisions):
    if not history:
        return
    versions = {row.id: row for row in history.versions}
    try:
        for version in order_story_versions(versions):
            _validate(branch_id, version)
            validate_version_sources(version, versions.get, revisions.get)
        local = {
            (record_id, row.revision): row
            for record_id, entries in history.native_history.items()
            for row in entries
        }
        for row in local.values():
            version = versions[row.version_id]
            if row.dependencies.keys() != version.dependencies.keys() or any(
                (record_id, revision) not in local
                or local[record_id, revision].version_id != version.dependencies[record_id]
                for record_id, revision in row.dependencies.items()
            ):
                raise _archive_error("故事本机修订的依赖与冻结版本不一致。")
            _bind_native(version, row, revisions.get, native_revisions.get)
        validate_effective_cycles(history.adopted_heads, versions)
    except DomainError as error:
        raise _archive_error(f"稿件包故事档案未通过校验。{error.message}") from error


def import_story_archive(tx, branch_id, history):
    if not history:
        return
    versions = {row.id: row for row in history.versions}
    native_rows = []
    dependencies = []
    head_dependencies = []
    aliases = set()
    mapping = []
    heads = []
    for record_id, entries in history.native_history.items():
        for entry in entries:
            version = versions[entry.version_id]
            payload, sources = _bind_native(
                version,
                entry,
                lambda identifier: source_revision(tx, branch_id, identifier),
                lambda identifier: native_revision(tx, branch_id, identifier),
            )
            native_rows.append(
                (record_id, entry.revision, dumps(payload), int(version.archived), entry.created)
            )
            dependencies.extend(
                (record_id, entry.revision, parent, revision)
                for parent, revision in entry.dependencies.items()
            )
            aliases.update(sources)
            mapping.append((branch_id, record_id, entry.revision, version.id))
        latest = entries[-1]
        heads.append(
            (
                record_id,
                branch_id,
                version.record.kind,
                latest.revision,
                dumps(payload),
                int(version.archived),
                entries[0].created,
                latest.created,
            )
        )
        head_dependencies.extend(
            (record_id, parent, revision) for parent, revision in latest.dependencies.items()
        )
    tx.executemany(
        "INSERT INTO manuscript_story_versions VALUES(?,?,?,?)",
        [
            (branch_id, version.id, version.model_dump_json(), history.source_devices.get(version.id))
            for version in history.versions
        ],
    )
    tx.executemany("INSERT INTO story_records VALUES(?,?,?,?,?,?,?,?)", heads)
    tx.executemany("INSERT INTO story_record_history VALUES(?,?,?,?,?)", native_rows)
    tx.executemany("INSERT INTO story_record_dependency_history VALUES(?,?,?,?)", dependencies)
    tx.executemany("INSERT INTO story_record_dependencies VALUES(?,?,?)", head_dependencies)
    tx.executemany("INSERT INTO manuscript_story_applied VALUES(?,?,?,?)", mapping)
    tx.executemany(
        "INSERT INTO story_sync_chapter_sources VALUES(?,?,?)",
        [(branch_id, original, native) for original, native in aliases],
    )
