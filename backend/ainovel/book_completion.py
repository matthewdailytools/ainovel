"""Six-gate book completion and versioned delivery packages."""

from hashlib import sha256
from io import BytesIO
import json
from time import time
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import Response
from pydantic import Field

from .db import DomainError, dumps, uid
from .domain import utf16_slice
from .models import Input
from .outline_workspace import _chapter_plans, _view
from .project_brief import confirmed_project_tx
from .setting_review import confirmed_world_basis
from .story_graph import Source, graph_snapshot
from .workflow_inspection import body_chars, current_length_target, latest_book_inspection, length_result

KIND = "book_completion"
VERSION = "book-completion/1"


class ClosureReview(Input):
    note: str = Field(max_length=4000)
    sources: list[Source] = Field(default_factory=list, max_length=30)


class CompletionRequest(Input):
    basis_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    read_note: str = Field(max_length=4000)
    ending: ClosureReview
    characters: ClosureReview
    promises: ClosureReview
    intro: str = Field(max_length=4000)
    spoiler_scope: Literal["opening", "whole"]
    sample_chapter_ids: list[str] = Field(max_length=20)
    materials_reviewed: bool = Field(strict=True)
    preparation_id: str | None = Field(default=None, min_length=1, max_length=128)


def _hash(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _basis(tx, branch_id):
    branch = tx.execute(
        "SELECT b.*,n.title AS novel_title FROM branches b JOIN novels n ON n.id=b.novel_id WHERE b.id=?",
        (branch_id,),
    ).fetchone()
    if not branch:
        raise DomainError("not_found", "工作版本不存在。", 404)
    chapters = [
        dict(row)
        for row in tx.execute(
            "SELECT id,title,position,revision,content,stale FROM chapters WHERE branch_id=? AND archived=0 "
            "ORDER BY position,id",
            (branch_id,),
        )
    ]
    outline = _view(tx, branch_id)
    graph = graph_snapshot(tx, branch_id)
    report = latest_book_inspection(tx, branch_id)
    project = confirmed_project_tx(tx, branch_id)
    rules = confirmed_world_basis(tx, branch_id)
    promises = [row for row in graph["records"] if row["kind"] == "promise" and not row["archived"]]
    manifest = {
        "format": VERSION,
        "branch_id": branch_id,
        "title": branch["novel_title"],
        "story_revision": branch["story_revision"],
        "chapters": [
            {
                **{key: row[key] for key in ("id", "title", "position", "revision")},
                "sha256": _hash(row["content"]),
            }
            for row in chapters
        ],
        "project_id": project["id"] if project else None,
        "setting_id": rules["confirmation_id"],
        "outline_id": outline["confirmed"]["id"] if outline["confirmed"] else None,
        "inspection_id": report["id"] if report else None,
        "length_target": current_length_target(tx, branch_id, "book", None, include_cleared=True),
        "promises": [{"id": row["id"], "revision": row["revision"]} for row in promises],
    }
    fingerprint = _hash(
        dumps(
            [
                manifest,
                [row["stale"] for row in chapters],
                outline["stale"],
                [item["current_status"] for item in report["items"]] if report else [],
            ]
        )
    )
    return fingerprint, manifest, chapters, rules, outline, report, promises


def _evidence(review, chapters):
    if review is None:
        return False
    for source in review.sources:
        row = chapters.get(source.chapter_id)
        if (
            not row
            or not source.quote.strip()
            or row["revision"] != source.revision
            or (utf16_slice(row["content"], source.start, source.end)[1] != source.quote)
        ):
            raise DomainError("completion_evidence", "完本判断的引文须对应当前正文修订和原文范围。", 409)
    return bool(review.note.strip())


def _check(identifier, title, detail, passed=False, blocked=False):
    return {
        "id": identifier,
        "title": title,
        "status": "green" if passed else "red" if blocked else "pending",
        "detail": detail,
    }


def _text(chapters):
    return "\n\n".join(f"## {row['title']}\n\n{row['content']}" for row in chapters)


def _materials(manifest, chapters, rules, data):
    if not data:
        return None
    requested = data.sample_chapter_ids
    if not requested or requested != [row["id"] for row in chapters[: len(requested)]]:
        return None
    sample = chapters[: len(requested)]
    if not data.intro.strip() or any(not row["content"].strip() for row in sample):
        return None
    title = manifest["title"]
    catalogue = "\n\n".join(f"## {item['title']}\n\n{item['detail']}" for item in rules["items"])
    if not catalogue:
        catalogue = "本作没有单独确认的世界设定条目。"
    limit = "仅开篇" if data.spoiler_scope == "opening" else "含全书内容"
    return {
        "正文.md": f"# {title}\n\n{_text(chapters)}\n",
        "设定册.md": f"# {title} · 设定册\n\n{catalogue}\n",
        "简介.md": f"# {title} · 简介\n\n剧透范围：{limit}\n\n{data.intro.strip()}\n",
        "试读.md": f"# {title} · 试读\n\n{_text(sample)}\n",
    }


def _evaluate(tx, branch_id, data=None):
    basis, manifest, chapters, rules, outline, report, promises = _basis(tx, branch_id)
    if data and basis != data.basis_sha256:
        raise DomainError("completion_changed", "完本依据已变化，请刷新并重新预览。", 409)
    if data and data.preparation_id:
        from .completion_preparation import KIND as PREPARATION

        candidate = tx.execute(
            "SELECT basis FROM artifacts WHERE id=? AND branch_id=? AND kind=?",
            (data.preparation_id, branch_id, PREPARATION),
        ).fetchone()
        if not candidate or json.loads(candidate["basis"])["completion_basis_sha256"] != basis:
            raise DomainError(
                "completion_preparation_stale", "这份 AI 完本建议的依据已变化，请重新核对。", 409
            )
        manifest["ai_preparation_id"] = data.preparation_id
    by_id = {row["id"]: row for row in chapters}
    expected = (
        [chapter["chapter_id"] for _, _, chapter in _chapter_plans(outline["confirmed"]["plan"])]
        if outline["confirmed"]
        else []
    )
    manuscript = bool(chapters) and all(row["content"].strip() for row in chapters)
    manuscript = manuscript and (not expected or expected == list(by_id)) and not outline["stale"]
    manuscript_detail = "核对目录、非空正文及大纲覆盖，并记录通读。"
    length_target = manifest["length_target"]
    if length_target and length_target["targets"] is not None:
        length_status, length_detail = length_result(
            [{"chars": body_chars(row["content"])} for row in chapters], length_target["targets"]
        )
        manuscript = manuscript and length_status == "green"
        manuscript_detail += length_detail
    open_promises = [
        row["payload"]["title"]
        for row in promises
        if row["invalid_reasons"]
        or row["payload"]["stages"][-1]["status"] not in {"resolved", "retained", "abandoned"}
    ]
    inspection_ok = bool(
        report
        and report["current"]
        and all(item["current_status"] in {"green", "not_applicable"} for item in report["items"])
    )
    blocked = (report and any(item["current_status"] == "red" for item in report["items"])) or any(
        row["stale"] for row in chapters
    )
    materials = _materials(manifest, chapters, rules, data)
    checks = [
        _check(
            "manuscript",
            "稿件齐全与通读",
            manuscript_detail,
            bool(manuscript and data and data.read_note.strip()),
            not manuscript,
        ),
        _check(
            "ending",
            "主冲突与终局",
            "记录正文中的处理或作者确认的安排。",
            _evidence(data.ending if data else None, by_id),
        ),
        _check(
            "characters",
            "关键人物与关系",
            "记录人物与关系的去向或作者确认的留白。",
            _evidence(data.characters if data else None, by_id),
        ),
        _check(
            "promises",
            "伏笔与读者承诺",
            "仍待处理：" + "、".join(open_promises) if open_promises else "核对已处理的承诺并记录依据。",
            _evidence(data.promises if data else None, by_id) and not open_promises,
            bool(open_promises),
        ),
        _check(
            "inspection",
            "一致性与复检",
            "需要当前全书体检通过，且所有受影响正文都已复检。",
            inspection_ok and not blocked,
            bool(blocked),
        ),
        _check(
            "delivery",
            "交付材料核验",
            "选择从首章连续的试读范围，填写简介并确认材料。",
            bool(materials and data.materials_reviewed),
        ),
    ]
    if materials:
        manifest["files"] = {name: _hash(text) for name, text in materials.items()}
        manifest["spoiler_scope"] = data.spoiler_scope
        manifest["sample_chapter_ids"] = data.sample_chapter_ids
    return {"basis_sha256": basis, "checks": checks, "manifest": manifest, "materials": materials}


def _latest(tx, branch_id, basis):
    row = tx.execute(
        "SELECT id,payload,basis FROM artifacts WHERE branch_id=? AND kind=? AND status='accepted' "
        "ORDER BY rowid DESC LIMIT 1",
        (branch_id, KIND),
    ).fetchone()
    if not row:
        return None
    value = json.loads(row["payload"])
    return {
        "id": row["id"],
        "current": json.loads(row["basis"])["basis_sha256"] == basis,
        "checks": value["checks"],
        "manifest": value["manifest"],
    }


def register_routes(app, domain):
    endpoint = "/api/branches/{branch_id}/book-completion"

    @app.get(endpoint)
    def book_completion(branch_id: str):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            value = _evaluate(tx, branch_id)
            return {
                "basis_sha256": value["basis_sha256"],
                "checks": value["checks"],
                "latest": _latest(tx, branch_id, value["basis_sha256"]),
                "history": [
                    {
                        "id": row["id"],
                        "created": row["created"],
                        "current": row["basis"] == value["basis_sha256"],
                    }
                    for row in tx.execute(
                        "SELECT id,created,json_extract(basis,'$.basis_sha256') AS basis FROM artifacts "
                        "WHERE branch_id=? AND kind=? AND status='accepted' ORDER BY rowid DESC LIMIT 25",
                        (branch_id, KIND),
                    )
                ],
            }

    @app.post(endpoint + "/preview")
    def preview_completion(branch_id: str, data: CompletionRequest):
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return _evaluate(tx, branch_id, data)

    @app.post(endpoint + "/confirm", status_code=201)
    def confirm_completion(branch_id: str, data: CompletionRequest):
        with domain.db.connect(True) as tx:
            value = _evaluate(tx, branch_id, data)
            if any(check["status"] != "green" for check in value["checks"]):
                raise DomainError("completion_blocked", "六项完本判定全部通过后才可确认完结。", 409)
            basis = {"basis_sha256": value["basis_sha256"], "review": data.model_dump()}
            previous = _latest(tx, branch_id, value["basis_sha256"])
            if previous and previous["current"]:
                stored = tx.execute("SELECT basis FROM artifacts WHERE id=?", (previous["id"],)).fetchone()
                if json.loads(stored["basis"]) == basis:
                    return previous
            identifier = uid()
            tx.execute(
                "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) "
                "SELECT ?,novel_id,id,?,?,?,'accepted',? FROM branches WHERE id=?",
                (identifier, KIND, dumps(value), dumps(basis), time(), branch_id),
            )
            from .work_snapshot import checkpoint_tx

            checkpoint_tx(tx, branch_id, "确认作品完结")
            return {
                "id": identifier,
                "current": True,
                "checks": value["checks"],
                "manifest": value["manifest"],
            }

    @app.get(endpoint + "/{identifier}/export")
    def export_completion(branch_id: str, identifier: str):
        row = domain.db.one(
            "SELECT payload FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
            (identifier, branch_id, KIND),
        )
        payload = json.loads(row["payload"])
        manifest, materials = payload["manifest"], payload["materials"]
        if manifest["files"] != {name: _hash(text) for name, text in materials.items()}:
            raise DomainError("completion_integrity", "交付材料校验失败，请从备份恢复并核对。", 409)
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name, text in materials.items():
                archive.writestr(name, text)
            archive.writestr("manifest.json", dumps(manifest))
        return Response(
            output.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ainovel-delivery.zip"'},
        )
