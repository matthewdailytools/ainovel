"""Initialize an empty chapter directory without scheduling model work."""

from hashlib import sha256
import json
from time import time

from pydantic import Field

from .db import DomainError, dumps, uid
from .models import Input
from .outline_workspace import Identifier, _check_base, _latest


class InitializeOutlineDirectory(Input):
    base_revision: int = Field(ge=0, strict=True)
    base_story_revision: int = Field(ge=0, strict=True)
    chapter_count: int = Field(ge=1, le=100, strict=True)
    client_request_id: str = Field(min_length=8, max_length=128)


class OutlineDirectoryResult(Input):
    story_revision: int
    chapter_ids: list[Identifier]


def add_empty_chapters_tx(tx, branch_id, count):
    first = tx.execute(
        "SELECT COALESCE(MAX(position),0)+1 FROM chapters WHERE branch_id=?", (branch_id,)
    ).fetchone()[0]
    chapter_ids = []
    for position in range(first, first + count):
        identifier = uid()
        tx.execute(
            "INSERT INTO chapters(id,branch_id,position,title) VALUES(?,?,?,?)",
            (identifier, branch_id, position, f"第 {position} 章"),
        )
        tx.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?)", (uid(), identifier, 0, "", "新建空稿", time())
        )
        chapter_ids.append(identifier)
    tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
    return chapter_ids


def initialize_directory(domain, branch_id, data):
    fingerprint = sha256(
        dumps([branch_id, data.model_dump(exclude={"client_request_id"})]).encode()
    ).hexdigest()
    with domain.db.connect(True) as tx:
        branch = tx.execute(
            "SELECT novel_id,story_revision FROM branches WHERE id=?", (branch_id,)
        ).fetchone()
        if not branch:
            raise DomainError("not_found", "工作版本不存在。", 404)
        prior = tx.execute("SELECT result FROM receipts WHERE key=?", (data.client_request_id,)).fetchone()
        if prior:
            receipt = json.loads(prior["result"])
            if receipt.get("outline_directory_sha256") != fingerprint:
                raise DomainError("idempotency", "该提交标识已用于不同的目录操作。", 409)
            return receipt["directory"]
        _check_base(_latest(tx, branch_id), data.base_revision)
        if (
            branch["story_revision"] != data.base_story_revision
            or tx.execute(
                "SELECT 1 FROM chapters WHERE branch_id=? AND archived=0 LIMIT 1", (branch_id,)
            ).fetchone()
        ):
            raise DomainError("outline_directory_changed", "章节目录依据已变化，请重新核对。", 409)
        directory = {
            "chapter_ids": add_empty_chapters_tx(tx, branch_id, data.chapter_count),
            "story_revision": branch["story_revision"] + 1,
        }
        receipt_id = uid()
        tx.execute(
            "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
            "VALUES(?,?,?,'outline_directory',?,?,'accepted',?)",
            (
                receipt_id,
                branch["novel_id"],
                branch_id,
                dumps(directory),
                dumps({"base_story_revision": data.base_story_revision}),
                time(),
            ),
        )
        tx.execute(
            "INSERT INTO receipts VALUES(?,?,?)",
            (
                data.client_request_id,
                receipt_id,
                dumps({"outline_directory_sha256": fingerprint, "directory": directory}),
            ),
        )
        return directory
