"""Freeze the length sources used by a complete chapter draft."""

from .db import DomainError
from .project_brief import confirmed_project_tx
from .workflow_inspection import COUNT_VERSION, body_chars, current_length_target

KEY = "chapter_writing_length"
VERSION = "chapter-writing-length/1"


def freeze(tx, branch_id, chapter_id, volume_id):
    project = confirmed_project_tx(tx, branch_id)
    return {
        "version": VERSION,
        "method": COUNT_VERSION,
        "project": (
            {"id": project["id"], "revision": project["revision"], "length": project["brief"]["length"]}
            if project
            else None
        ),
        "targets": [
            {
                "scope": scope,
                "scope_id": scope_id,
                "target": current_length_target(tx, branch_id, scope, scope_id, include_cleared=True),
            }
            for scope, scope_id in (("chapter", chapter_id), ("volume", volume_id), ("book", None))
        ],
    }


def prompt_basis(frozen):
    active = [row for row in frozen["targets"] if row["target"] and row["target"]["targets"] is not None]
    if frozen["project"] or active:
        return {**frozen, "targets": active}
    return None


def chapter_check(frozen, text):
    source = next(row for row in frozen["targets"] if row["scope"] == "chapter")["target"]
    targets = (source or {}).get("targets") or {}
    minimum = targets.get("min_chars")
    maximum = targets.get("max_chars")
    actual = body_chars(text)
    reason = ""
    if not actual:
        reason = "本章候选没有有效正文。候选已保留，请补写后手动采纳。"
    elif minimum is None and maximum is None:
        return None
    elif minimum is not None and actual < minimum:
        reason = f"本章候选 {actual} 个有效字符，低于采用的下限 {minimum}。候选已保留，请核对后手动采纳。"
    elif maximum is not None and actual > maximum:
        reason = f"本章候选 {actual} 个有效字符，高于采用的上限 {maximum}。候选已保留，请核对后手动采纳。"
    return {"actual_chars": actual, "min_chars": minimum, "max_chars": maximum, "reason": reason}


def check_automatic_acceptance(tx, branch_id, frozen, text):
    for row in frozen["targets"]:
        if (
            current_length_target(tx, branch_id, row["scope"], row["scope_id"], include_cleared=True)
            != row["target"]
        ):
            raise DomainError(
                "chapter_length_changed", "本章适用的篇幅目标已变化。候选已保留，请核对后手动采纳。", 409
            )
    if (checked := chapter_check(frozen, text)) and checked["reason"]:
        raise DomainError("chapter_length_bounds", checked["reason"], 409)
