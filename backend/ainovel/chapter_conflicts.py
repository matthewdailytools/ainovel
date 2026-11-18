"""Shared chapter conflict validation and isolated snapshot forks."""

from hashlib import sha256
import json
from typing import Literal

from pydantic import ValidationError

from .db import DomainError, dumps
from .manuscript_exchange import PortableChapter, PortableRevision
from .models import Input
from .project_sync import FORMAT


class ChapterObject(Input):
    format: Literal["ainovel-manuscript-sync/1"]
    kind: Literal["chapter"]
    chapter: PortableChapter
    head: PortableRevision


def verified_chapter(work, object_id, payload):
    try:
        value = ChapterObject.model_validate(payload)
    except ValidationError as error:
        raise DomainError("sync_chapter", "该比较版本没有可用的章节正文。", 409) from error
    info, revision = value.chapter, value.head
    if (
        value.format != FORMAT
        or value.kind != "chapter"
        or object_id != f"chapter:{info.id}"
        or info.branch_id != work
        or info.archived
        or revision.chapter_id != info.id
        or revision.revision != info.revision
        or sha256(revision.content.encode()).hexdigest() != revision.sha256
    ):
        raise DomainError("sync_chapter", "比较章稿的工作版本、修订或校验值不一致。", 409)
    return value


def fork_fingerprint(branch_id, conflict_id, data):
    return sha256(
        dumps(
            [
                branch_id,
                conflict_id,
                data.parent_revision,
                data.local_sha256,
                data.choice,
                data.branch_name,
            ]
        ).encode()
    ).hexdigest()


def fork_receipt(tx, key, fingerprint):
    prior = tx.execute("SELECT result FROM receipts WHERE key=?", (key,)).fetchone()
    if not prior:
        return None
    result = json.loads(prior["result"])
    if result.get("sync_chapter_fork_sha256") != fingerprint:
        raise DomainError("idempotency", "另存分支的提交编号已用于其他内容。", 409)
    return result


def fork_chapter_tx(tx, domain, branch_id, conflict_id, data, selected, fingerprint):
    from .work_snapshot import (
        RestoreInput,
        _artifact,
        _branch,
        _capture,
        _execute_restore,
        _hash,
        _read_snapshot,
        _save_snapshot,
    )

    origin = _branch(tx, branch_id)
    current = _capture(tx, branch_id)
    snapshot_id = _save_snapshot(
        tx, origin, "同步冲突另存前的本机工作版本", "book", None, current, "milestone"
    )
    snapshot = _read_snapshot(tx, branch_id, snapshot_id)
    created = _execute_restore(
        tx,
        domain,
        origin,
        snapshot,
        current,
        RestoreInput(
            basis_sha256=_hash(dumps(current)),
            mode="new_branch",
            branch_name=data.branch_name,
            idempotency_key=data.idempotency_key,
        ),
    )
    source_chapter_id = selected.chapter.id
    target_id = created["branch_id"]
    mapped = created["chapters"].get(source_chapter_id)
    if not mapped:
        raise DomainError("sync_chapter", "本机快照中没有可另存的比较章节。", 409)
    chapter = dict(
        tx.execute("SELECT * FROM chapters WHERE id=? AND branch_id=?", (mapped["id"], target_id)).fetchone()
    )
    revision = domain.revise_tx(tx, chapter, selected.head.content, "另存同步冲突的作者选定章稿")
    position = chapter["position"]
    if (
        chapter["archived"]
        and tx.execute(
            "SELECT 1 FROM chapters WHERE branch_id=? AND position=? AND archived=0",
            (target_id, position),
        ).fetchone()
    ):
        position = tx.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM chapters WHERE branch_id=? AND archived=0",
            (target_id,),
        ).fetchone()[0]
    tx.execute(
        "UPDATE chapters SET title=?,position=?,archived=0 WHERE id=?",
        (selected.chapter.title, position, chapter["id"]),
    )
    result = {
        "branch_id": target_id,
        "branch_name": data.branch_name,
        "chapter_id": chapter["id"],
        "revision": revision,
        "source_branch_id": branch_id,
        "source_chapter_id": source_chapter_id,
        "source_revision_id": selected.head.id,
        "source_sha256": selected.head.sha256,
        "choice": data.choice,
        "snapshot_id": snapshot_id,
        "conflict_id": conflict_id,
        "sync_chapter_fork_sha256": fingerprint,
    }
    if "reference_sources" in created:
        result["reference_sources"] = created["reference_sources"]
    record_id = _artifact(
        tx, _branch(tx, target_id), "desktop_sync_chapter_fork", result, {"snapshot_id": snapshot_id}
    )
    result["id"] = record_id
    tx.execute("INSERT INTO receipts VALUES(?,?,?)", (data.idempotency_key, record_id, dumps(result)))
    return result
