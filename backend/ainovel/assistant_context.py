"""Read bounded, versioned sources for the local workbench assistant."""

from hashlib import sha256
import json

from .db import DomainError, dumps


INSPECTION_CONTEXT = {
    "01": ("project", "intent"),
    "02": ("outline_id", "stale", "plans", "chapters"),
    "03": ("records", "state_conflicts"),
    "04": ("events", "scenes"),
    "05": ("confirmation_id", "rules", "reports", "tracked", "outline_id", "outline_stale"),
    "06": ("knowledge", "scenes", "knowledge_events", "viewpoint_names"),
    "07": ("records", "windows", "outline_id"),
    "08": (
        "impacted_chapters",
        "invalid_records",
        "review_blockers",
        "outline_id",
        "card_references",
        "planned_card_references",
        "author_setting_use_outline_id",
        "author_setting_uses",
    ),
    "09": ("method", "project_length", "length_target", "descendant_length_targets"),
    "10": ("outline", "pacing_plan", "scenes", "events"),
}
# Source quotations are provided separately, after exact current selection validation.
PRIVATE_EVIDENCE = {
    "quote",
    "excerpt",
    "content",
    "text",
    "evidence",
    "source",
    "sources",
    "source_quote",
    "source_text",
    "source_excerpt",
    "source_content",
}


def _bounded_evidence(value, depth=0):
    if isinstance(value, dict):
        return (
            {
                key: _bounded_evidence(item, depth + 1)
                for key, item in list(value.items())[:16]
                if key not in PRIVATE_EVIDENCE
            }
            if depth < 4
            else {}
        )
    if isinstance(value, list):
        return [_bounded_evidence(item, depth + 1) for item in value[:6]] if depth < 4 else []
    return value[:500] if isinstance(value, str) else value


def _digest(value):
    return sha256(dumps(value).encode()).hexdigest()


def _missing():
    return DomainError("assistant_scope", "找不到当前作品中该助手对象。", 404)


def _source(kind, identifier, label, revision=None, **content):
    return {"kind": kind, "id": identifier, "label": label, "revision": revision, **content}


def _project(tx, branch):
    from .project_brief import confirmed_project_tx

    if project := confirmed_project_tx(tx, branch):
        return _source(
            "project",
            project["id"],
            "已确认立项",
            project["revision"],
            idea=project["idea"],
            brief=project["brief"],
        )
    return None


def _reference_session(tx, branch, scope_id):
    row = tx.execute(
        "SELECT revision,payload FROM reference_sessions "
        "WHERE id=? AND json_extract(payload,'$.branch_id')=?",
        (scope_id, branch),
    ).fetchone()
    if not row:
        raise _missing()
    return row["revision"], json.loads(row["payload"])


def _reference(tx, branch, scope_id):
    revision, payload = _reference_session(tx, branch, scope_id)
    cards = [
        {key: value for key, value in card.items() if key != "evidence"} for card in payload.get("cards", [])
    ]
    return _source(
        "reference",
        scope_id,
        "当前拆解会话",
        revision,
        goal=payload["goal"],
        cards=cards,
        selected_source_sha256=_digest(payload.get("source")),
    )


def _reference_excerpt(tx, branch, scope_id):
    from .domain import utf16_slice

    revision, payload = _reference_session(tx, branch, scope_id)
    source = payload["source"]
    saved = tx.execute(
        "SELECT u.content,b.file_hash FROM source_units u JOIN books b ON b.id=u.book_id "
        "WHERE u.id=? AND u.book_id=?",
        (source["unit_id"], source["book_id"]),
    ).fetchone()
    if (
        not saved
        or saved["file_hash"] != source["file_hash"]
        or utf16_slice(saved["content"], source["start"], source["end"])[1] != source["quote"]
    ):
        raise DomainError("assistant_reference", "拆解选段已无法核对，请重新选择原文。", 409)
    excerpt = source["quote"][:1500]
    return _source(
        "reference_excerpt",
        scope_id,
        "已选拆解原文",
        revision,
        content=excerpt,
        unit_id=source["unit_id"],
        source_title=source["title"],
        file_hash=source["file_hash"],
        start=source["start"],
        end=source["start"] + len(excerpt.encode("utf-16-le")) // 2,
        selection_end=source["end"],
    )


def _settings(tx, branch, scope_id):
    from .conception import CONFIRMATION, _latest, _workspace

    current = _workspace(tx, branch)
    categories = current["categories"]
    if scope_id:
        matched = [
            (category, item)
            for category in categories
            for item in category["items"]
            if item["id"] == scope_id
        ]
        if len(matched) != 1:
            raise _missing()
        category, item = matched[0]
        values = [item]
        label = item["title"]
        identifier = scope_id
    else:
        category = None
        values = [
            {key: item[key] for key in ("id", "title")} for section in categories for item in section["items"]
        ]
        label = "当前设定台"
        identifier = branch
    adopted = _latest(tx, branch, CONFIRMATION)
    return _source(
        "settings",
        identifier,
        label,
        current["revision"],
        category=category["id"] if category else None,
        items=values,
        confirmation_id=adopted["id"] if adopted else None,
    )


def _outline(tx, branch, scope_id=None, *, chapter_context=False):
    from .outline_workspace import _card_key, _chapter_plans, _decode, _latest

    confirmed = _latest(tx, branch, True)
    draft = _decode(confirmed if chapter_context else _latest(tx, branch))
    if not draft:
        if scope_id and not chapter_context:
            raise _missing()
        return None
    plan = draft["plan"]
    if scope_id:
        matches = []
        for volume in plan["volumes"]:
            if not chapter_context and volume["id"] == scope_id:
                matches.append(
                    (
                        {key: value for key, value in volume.items() if key != "units"},
                        [chapter for unit in volume["units"] for chapter in unit["chapters"]],
                    )
                )
            for unit in volume["units"]:
                if not chapter_context and unit["id"] == scope_id:
                    matches.append(
                        (
                            {key: value for key, value in unit.items() if key != "chapters"},
                            unit["chapters"],
                        )
                    )
                matches.extend(
                    (chapter, [chapter]) for chapter in unit["chapters"] if chapter["chapter_id"] == scope_id
                )
        if len(matches) != 1:
            if chapter_context:
                return None
            raise _missing()
        selected, chapters = matches[0]
    else:
        selected = {"summary": plan["summary"], "volumes": [row["title"] for row in plan["volumes"]]}
        chapters = [chapter for _, _, chapter in _chapter_plans(plan)]
    setting_basis = draft["setting_basis"]
    chapter_ids = {chapter["chapter_id"] for chapter in chapters}
    setting_ids = {identifier for chapter in chapters for identifier in chapter["setting_ids"]}
    selected_cards = {_card_key(card) for chapter in chapters for card in chapter.get("reference_cards", [])}
    return _source(
        "outline",
        draft["id"],
        selected.get("title", "当前大纲"),
        draft["revision"],
        selected=selected,
        confirmed_id=confirmed["id"] if confirmed else None,
        setting_confirmation_id=setting_basis["confirmation_id"],
        setting_ids=sorted(setting_ids)[:40],
        settings=[
            {key: item[key] for key in ("id", "title")}
            for item in setting_basis["items"]
            if item["id"] in setting_ids
        ][:20],
        cards=[
            {
                key: card.get(key)
                for key in ("session_id", "card_id", "revision", "version_id", "source_title")
            }
            for card in setting_basis.get("reference_cards", [])
            if _card_key(card) in selected_cards
        ][:12],
        promise_windows=[
            window
            for window in plan["promises"]
            if chapter_ids.intersection(
                window[key] for key in ("plant_chapter_id", "opens_chapter_id", "closes_chapter_id")
            )
        ][:20],
    )


def _chapter(tx, branch, scope_id):
    row = tx.execute(
        "SELECT c.id,c.title,c.revision,c.content,r.id AS revision_id "
        "FROM chapters c JOIN revisions r ON r.chapter_id=c.id AND r.revision=c.revision "
        "WHERE c.branch_id=? AND c.id=? AND c.archived=0",
        (branch, scope_id),
    ).fetchone()
    if not row:
        raise _missing()
    return row


def _chapter_source(row):
    return _source(
        "chapter",
        row["id"],
        row["title"],
        row["revision"],
        revision_id=row["revision_id"],
        sha256=sha256(row["content"].encode()).hexdigest(),
    )


def _inspection_rows(tx, branch, scope_id):
    from .workflow_inspection import _effective, _evaluate, _latest

    if scope_id is None:
        scope = "book"
    elif tx.execute("SELECT 1 FROM chapters WHERE branch_id=? AND id=?", (branch, scope_id)).fetchone():
        scope = "chapter"
    else:
        scope = "volume"
    preview, chapters = _evaluate(tx, branch, scope, scope_id)
    saved = _latest(tx, branch, scope, scope_id)
    report = _effective(saved, preview) if saved else preview
    return preview, chapters, report, saved


def _inspection(tx, branch, scope_id):
    preview, _, report, saved = _inspection_rows(tx, branch, scope_id)
    checks = [
        {
            "id": row["check_id"],
            "title": row["title"],
            "status": row.get("current_status", row["status"]),
            "detail": row["summary"],
        }
        for row in report["items"]
    ]
    return (
        _source(
            "inspection" if saved else "inspection_preview",
            saved["id"] if saved else branch,
            "十处对账",
            scope=preview["scope"],
            scope_id=scope_id,
            basis_sha256=preview["basis_sha256"],
        ),
        checks,
    )


def _inspection_sources(tx, branch, scope_id, check_id):
    from .domain import utf16_slice

    preview, chapters, report, saved = _inspection_rows(tx, branch, scope_id)
    check = next(row for row in preview["items"] if row["check_id"] == check_id)
    reviewed = next(row for row in report["items"] if row["check_id"] == check_id)
    author = (
        reviewed.get("author_review", {})
        if saved and reviewed.get("current_status") not in {None, "outdated"}
        else {}
    )
    fields = (*INSPECTION_CONTEXT[check_id], "scoped_findings")
    sources = [
        _source(
            "inspection_check",
            f"{scope_id or branch}:{check_id}",
            check["title"],
            check_id=check_id,
            status=reviewed.get("current_status", reviewed["status"]),
            basis={key: _bounded_evidence(check["basis"][key]) for key in fields if key in check["basis"]},
            author_review={key: author[key] for key in ("basis_note", "reason") if key in author},
        )
    ]
    indexed = {row["id"]: row for row in chapters}
    for ref in author.get("evidence", [])[:4]:
        chapter = indexed.get(ref["chapter_id"])
        if not chapter or chapter["revision"] != ref["revision"]:
            continue
        try:
            quote = utf16_slice(chapter["content"], ref["start"], ref["end"])[1]
        except DomainError:
            continue
        if quote != ref["quote"]:
            continue
        excerpt = quote[:600]
        end = ref["start"] + len(excerpt.encode("utf-16-le")) // 2
        sources.append(
            _source(
                "inspection_excerpt",
                f"{ref['chapter_id']}:{ref['revision']}:{ref['start']}:{end}",
                f"{check_id} · {chapter['title']}",
                ref["revision"],
                chapter_id=ref["chapter_id"],
                content=excerpt,
                start=ref["start"],
                end=end,
            )
        )
    return sources


def _completion(tx, branch):
    from .book_completion import _evaluate, _latest

    report = _evaluate(tx, branch)
    latest = _latest(tx, branch, report["basis_sha256"])
    applied = latest if latest and latest["current"] else report
    checks = [{key: row[key] for key in ("id", "title", "status", "detail")} for row in applied["checks"]]
    return (
        _source(
            "completion",
            latest["id"] if latest else branch,
            "六项完本判定",
            basis_sha256=report["basis_sha256"],
        ),
        checks,
    )


def snapshot(tx, branch, stage, scope_id):
    book = tx.execute(
        "SELECT n.title FROM branches b JOIN novels n ON n.id=b.novel_id WHERE b.id=?", (branch,)
    ).fetchone()
    if not book:
        raise _missing()
    if scope_id is not None and stage in {"M1", "M7"}:
        raise _missing()
    sources = []
    checks = []
    if stage == "M1" and (project := _project(tx, branch)):
        sources.append(project)
    elif stage == "M2" and scope_id:
        sources.append(_reference(tx, branch, scope_id))
    elif stage == "M3":
        sources.append(_settings(tx, branch, scope_id))
        if project := _project(tx, branch):
            sources.append(project)
    elif stage == "M4" and (outline := _outline(tx, branch, scope_id)):
        sources.append(outline)
    elif stage == "M5" and scope_id:
        sources.append(_chapter_source(_chapter(tx, branch, scope_id)))
        if outline := _outline(tx, branch, scope_id, chapter_context=True):
            sources.append(outline)
    elif stage == "M6":
        source, checks = _inspection(tx, branch, scope_id)
        sources.append(source)
    elif stage == "M7":
        source, checks = _completion(tx, branch)
        sources.append(source)
    for kind, label in (("reader", "采用的题材配置"), ("intent", "作者约定")):
        row = tx.execute(
            "SELECT id,base_revision FROM artifacts WHERE branch_id=? AND kind=? AND chapter_id IS NULL "
            "AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
            (branch, kind),
        ).fetchone()
        if row:
            sources.append(_source(kind, row["id"], label, row["base_revision"]))
    return {
        "sha256": _digest([branch, stage, scope_id, book["title"], sources, checks]),
        "scope_label": sources[0]["label"] if sources else book["title"],
        "versions": [{key: source[key] for key in ("kind", "id", "label", "revision")} for source in sources],
        "checks": checks,
        "sources": sources,
    }


def turn_sources(tx, branch, stage, scope_id, request):
    from .domain import utf16_slice

    selected = request.get("selection")
    if selected:
        if stage != "M5" or request.get("target") != "selection" or not scope_id:
            raise DomainError("assistant_selection", "保存稿选区只能用于当前章的选区助手。")
        chapter = _chapter(tx, branch, scope_id)
        if chapter["revision"] != selected["revision"] or (
            utf16_slice(chapter["content"], selected["start"], selected["end"])[1] != request["draft_text"]
        ):
            raise DomainError("assistant_selection", "保存稿或选区已改变，请重新选择。", 409)
        return [
            _source(
                "selection",
                chapter["id"],
                "本轮保存稿选区",
                chapter["revision"],
                content=request["draft_text"],
                revision_id=chapter["revision_id"],
                start=selected["start"],
                end=selected["end"],
            )
        ]
    if stage == "M2" and scope_id:
        return [_reference_excerpt(tx, branch, scope_id)]
    if stage == "M6" and request.get("target") in INSPECTION_CONTEXT:
        return _inspection_sources(tx, branch, scope_id, request["target"])
    if stage == "M7" and request.get("target") == "intro":
        rows = tx.execute(
            "SELECT c.id,c.title,c.revision,c.content,r.id AS revision_id "
            "FROM chapters c JOIN revisions r ON r.chapter_id=c.id AND r.revision=c.revision "
            "WHERE c.branch_id=? AND c.archived=0 ORDER BY c.position,c.id",
            (branch,),
        ).fetchall()
        sample = rows[: request["sample_chapters"]]
        if len(sample) != request["sample_chapters"] or any(not row["content"].strip() for row in sample):
            raise DomainError("assistant_sample", "请选择有保存正文的连续开篇作为试读范围。", 409)
        allowed = rows if request["spoiler_scope"] == "whole" else sample
        return [{**_chapter_source(row), "content": row["content"]} for row in allowed]
    return []
