"""Reconcile a saved chapter with one explicitly selected confirmed rule."""

from typing import Literal

from pydantic import Field

from .conception import (
    CONFIRMATION,
    _latest,
    _read_payload,
    revision_tx,
    setting_findings_reviewed,
    setting_is_adopted,
)
from .db import DomainError
from .evidence import source_units
from .models import Input
from .project_brief import confirmed_project_tx


class SettingDecision(Input):
    result: Literal["pass", "fail", "unknown"]
    evidence: str
    reason: str = Field(min_length=1, max_length=2000)


def confirmed_world_basis(tx, branch_id, confirmation_id=None):
    """Read authored rules without treating new story observations as edits to those rules."""
    row = _latest(tx, branch_id, CONFIRMATION)
    absent = {"confirmation_id": None, "items": []}
    if not row or (confirmation_id and confirmation_id != row["id"]):
        if confirmation_id:
            raise DomainError("conception_snapshot", "所选世界设定确认版本已改变。", 409)
        return absent
    snapshot = _read_payload(row)
    project = confirmed_project_tx(tx, branch_id)
    if snapshot["revision"] != revision_tx(tx, branch_id) or snapshot.get("project_confirmation_id") != (
        project["id"] if project else None
    ):
        if confirmation_id:
            raise DomainError("conception_changed", "设定草稿已改变，请重新确认。", 409)
        return absent
    if not setting_findings_reviewed(snapshot):
        if confirmation_id:
            raise DomainError("conception_setting_review", "请到设定冲突体检逐项核对后再确认。", 409)
        return absent
    observed_ids = {source["item_id"] for source in snapshot.get("sources", [])}
    return {
        "confirmation_id": row["id"],
        "revision": snapshot["revision"],
        "items": [
            {
                "id": item["id"],
                "title": item["title"],
                "detail": item["detail"],
                **({"setting_policy": item["setting_policy"]} if item.get("setting_policy") else {}),
            }
            for category in snapshot["categories"]
            if category["id"] == "world" and not category["skipped"]
            for item in category["items"]
            if setting_is_adopted(item.get("adoption_state"))
            and item["id"] not in observed_ids
            and item["detail"].strip()
        ],
    }


def freeze_rule(snapshot, item_id):
    for item in snapshot["items"]:
        if item["id"] == item_id:
            return {"confirmation_id": snapshot["confirmation_id"], "revision": snapshot["revision"], **item}
    raise DomainError("setting_rule", "请选择已确认且有正文内容的世界设定条目。", 409)


def report_current(tx, artifact):
    chapter = tx.execute(
        "SELECT revision FROM chapters WHERE id=? AND branch_id=? AND archived=0",
        (artifact["chapter_id"], artifact["branch_id"]),
    ).fetchone()
    if not chapter or chapter["revision"] != artifact["base_revision"]:
        return False
    rule = artifact["basis"]["rule"]
    try:
        confirmed_world_basis(tx, artifact["branch_id"], rule["confirmation_id"])
    except DomainError as error:
        if error.code not in {"conception_snapshot", "conception_changed"}:
            raise
        return False
    return True


async def run(worker, request, rule):
    chapter = worker.domain.chapter(request["chapter_id"], request["branch_id"])
    if chapter["revision"] != request["base_revision"]:
        raise DomainError("revision_conflict", "排队后正文已改变，请重新提交对账。", 409)
    units = source_units(chapter["content"], "SETTING")
    if not units:
        raise DomainError("setting_review_empty", "请先保存非空正文。")
    sources = {unit["source_id"]: unit for unit in units}

    def validate(decision):
        evidence = decision["evidence"]
        if evidence in sources or (decision["result"] == "unknown" and not evidence):
            return []
        return ["通过或冲突须引用本次正文编号。待核对可留空或引用有效编号。"]

    decision = await worker.grounded(
        {
            "动作": "对照单条已确认设定与保存稿",
            "设定": rule["detail"],
            **({"适用规则": rule["setting_policy"]} if rule.get("setting_policy") else {}),
            "可引用原文": units,
            "要求": (
                "只判断给定设定与本次正文是否相符。明确违背填 fail，有证据支持相符填 pass，"
                "未触发、证据不足或仅是人物猜测填 unknown。evidence 填原文编号，unknown 可以留空。"
            ),
        },
        SettingDecision,
        {},
        validate,
    )
    if worker.cancelled():
        raise DomainError("cancelled", "任务已停止。", 409)
    report = worker.domain.stage(
        request,
        "setting_review",
        {**decision, "evidence": sources.get(decision["evidence"])},
        {"rule": rule},
        worker.task["id"],
        worker.attempt,
        on_stage=lambda tx, identifier: tx.execute(
            "UPDATE artifacts SET status='accepted' WHERE id=?", (identifier,)
        ),
    )
    worker.checkpoint({**worker.task["result"], "artifact_id": report["id"]}, "succeeded")
