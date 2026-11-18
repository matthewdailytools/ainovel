"""Reconcile a saved chapter with one explicitly selected confirmed rule."""

from typing import Literal

from pydantic import Field

from .conception import handoff_tx
from .db import DomainError
from .evidence import source_units
from .models import Input


class SettingDecision(Input):
    result: Literal["pass", "fail", "unknown"]
    evidence: str
    reason: str = Field(min_length=1, max_length=2000)


def freeze_rule(snapshot, item_id):
    for category in snapshot["categories"]:
        if category["id"] == "world":
            for item in category["items"]:
                if item["id"] == item_id and item["detail"].strip():
                    return {
                        "confirmation_id": snapshot["id"],
                        "revision": snapshot["revision"],
                        "id": item_id,
                        "title": item["title"],
                        "detail": item["detail"],
                    }
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
        handoff_tx(tx, artifact["branch_id"], rule["confirmation_id"])
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
