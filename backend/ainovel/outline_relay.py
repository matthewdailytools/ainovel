"""Continue writing from explicitly selected, version-bound author outlines."""

from copy import deepcopy
import json
from time import time

from pydantic import Field

from .db import DomainError, dumps, uid
from .models import Input
from .workflow_plan_preparation import sha
from .writing_context import chapter_outline

KIND = "outline_relay"
VERSION = "outline-relay/1"
KEY = "outline_relay"
WAITING = "outline_relay_waiting"


class Clarification(Input):
    checkpoint_id: str = Field(min_length=1, max_length=128)
    clarification: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=8, max_length=128)


def outlines(tx, branch_id):
    rows = tx.execute(
        "SELECT id,branch_id,title,position,revision FROM chapters "
        "WHERE branch_id=? AND archived=0 ORDER BY position",
        (branch_id,),
    )
    result = []
    for chapter in rows:
        outline = chapter_outline(tx, chapter)
        result.append(
            {
                **dict(chapter),
                "outline": outline,
                "confirmed": bool(outline and outline["source"] == "author" and outline["task_id"] is None),
            }
        )
    return result


def freeze(tx, source, data):
    if not source or data.mode != "assisted" or not data.plot_contract_check:
        raise DomainError("outline_relay", "章纲接力需要明确来源工作版本及情节逐项核对。", 409)
    selected = [part.model_dump() for part in data.outline_sources]
    chapters = {row["id"]: row for row in outlines(tx, source["id"])}
    if len(selected) != data.chapters or len(data.plots) != data.chapters:
        raise DomainError("outline_relay", "接力章数须与明确选择的章纲数相同。", 409)
    receipts = []
    for number, (part, plot) in enumerate(zip(selected, data.plots), start=1):
        chapter = chapters.get(part["chapter_id"])
        outline = chapter and chapter["outline"]
        if not outline or outline["version"] != part["version"]:
            raise DomainError("outline_changed", "选择的章纲已变化或未经作者确认，请刷新后再启动。", 409)
        if not chapter["confirmed"]:
            raise DomainError(
                "outline_reconfirm", "请在写作页核对旧批次约定，将仍适用的固定条件写入并保存为作者章纲。", 409
            )
        expected = {"number": number, "title": chapter["title"], "plot": outline["text"], "fixed": []}
        if plot.model_dump() != expected:
            raise DomainError("outline_changed", "接力请求与已确认的章纲有差异，请先保存或核对章纲。", 409)
        if receipts and chapter["position"] != receipts[-1]["source_position"] + 1:
            raise DomainError("outline_scope", "请按目录顺序选择连续的章纲范围。", 409)
        receipts.append(
            {
                "number": number,
                "source_chapter_id": chapter["id"],
                "source_position": chapter["position"],
                "source_chapter_revision": chapter["revision"],
                "outline_version": outline["version"],
                "outline_sha256": sha(outline["text"]),
            }
        )
    return {
        "version": VERSION,
        "source_branch_id": source["id"],
        "source_story_revision": source["story_revision"],
        "sources": receipts,
    }


def enabled(task):
    expected = bool(task["payload"].get("outline_sources"))
    snapshot = task["result"].get(KEY)
    if expected != bool(snapshot) or (
        expected and (task["payload"]["mode"] != "assisted" or snapshot["version"] != VERSION)
    ):
        raise DomainError("outline_relay", "无法确认此批次的章纲接力来源。", 409)
    return expected


def author_plot(task, number):
    original = next((p for p in task["payload"]["plots"] if p["number"] == number), None)
    if not enabled(task):
        return original
    witness = next((row for row in task["result"][KEY]["sources"] if row["number"] == number), None)
    if not original or not witness or witness["outline_sha256"] != sha(original["plot"]):
        raise DomainError("outline_relay", "冻结章纲与来源版本无法核对，已停止。", 409)
    value = original
    for amendment in task["result"].get("outline_relay_clarifications", []):
        if amendment["number"] != number:
            continue
        if amendment["prior_outline_sha256"] != sha(value["plot"]):
            raise DomainError("outline_relay", "作者补充记录与上次章纲无法核对。", 409)
        value = {**value, "plot": f"{value['plot']}\n\n作者明确补充：{amendment['request']['clarification']}"}
    return value


async def prepare(worker, plot, chapter):
    from .plot_audit import interpret_plot

    task = worker.task
    progress = task["result"]
    if plot != author_plot(task, plot["number"]):
        raise DomainError("outline_relay", "当前章安排与冻结章纲或作者补充不符，未派发。", 409)
    receipts = progress.setdefault("outline_relay_preparations", {})
    identifier = str(plot["number"])
    if receipt := receipts.get(identifier):
        if receipt["outline_sha256"] != sha(plot["plot"]) or receipt["chapter_id"] != chapter["id"]:
            raise DomainError("outline_relay", "写前解释与当时的章纲或目标章无法核对。", 409)
    else:
        report = await interpret_plot(worker, plot["plot"], source_kind="author_plot")
        receipt = {
            "id": uid(),
            "number": plot["number"],
            "chapter_id": chapter["id"],
            "chapter_revision": chapter["revision"],
            "outline_sha256": sha(plot["plot"]),
            "contract": report,
        }
        receipts[identifier] = receipt
    contract = receipt["contract"]
    if not contract["items"] or any(item["certainty"] != "explicit" for item in contract["items"]):
        progress[WAITING] = receipt
        worker.checkpoint(progress, "needs_review", "本章安排仍有未明确的事项，已停在正文生成之前。")
        return None
    worker.checkpoint(progress)
    return contract


def frozen_contract(worker, text, number, contract):
    progress = worker.task["result"]
    receipt = progress.get("outline_relay_preparations", {}).get(str(number))
    if (
        not enabled(worker.task)
        or not receipt
        or receipt["outline_sha256"] != sha(text)
        or receipt["contract"] != contract
        or progress.get(WAITING)
    ):
        raise DomainError("outline_relay", "本章写前解释或正文依据的版本无法核对。", 409)
    return contract


def clarify(domain, task_id, data):
    request = data.model_dump()
    request["clarification"] = request["clarification"].strip()
    if not request["clarification"]:
        raise DomainError("outline_relay", "请填写本次明确的作者决定。")
    with domain.db.connect(True) as tx:
        row = tx.execute("SELECT * FROM tasks WHERE id=? AND kind='workflow'", (task_id,)).fetchone()
        if not row:
            raise DomainError("not_found", "接力任务不存在。", 404)
        progress = json.loads(row["result"])
        task = {**dict(row), "result": progress, "payload": json.loads(row["payload"])}
        if not enabled(task):
            raise DomainError("outline_relay", "该任务没有章纲接力检查点。", 409)
        amendments = progress.get("outline_relay_clarifications", [])
        prior = next(
            (r for r in amendments if r["request"]["client_request_id"] == request["client_request_id"]), None
        )
        if prior:
            if prior["request"] != request:
                raise DomainError("idempotency", "这个提交标识已用于另一项决定。", 409)
            return task
        waiting = progress.get(WAITING)
        if (
            row["status"] != "needs_review"
            or not waiting
            or waiting["id"] != request["checkpoint_id"]
            or waiting["number"] != progress["next_chapter"]
            or progress.get("waiting_artifact")
        ):
            raise DomainError("outline_relay_changed", "章纲接力检查点已变化，请重新读取。", 409)
        branch = tx.execute("SELECT story_revision FROM branches WHERE id=?", (row["branch_id"],)).fetchone()
        chapter = tx.execute(
            "SELECT revision,content,position FROM chapters WHERE branch_id=? AND id=? AND archived=0",
            (row["branch_id"], waiting["chapter_id"]),
        ).fetchone()
        if (
            branch[0] != progress["expected_story_revision"]
            or not chapter
            or chapter["position"] != waiting["number"]
            or chapter["revision"] != waiting["chapter_revision"]
            or chapter["content"].strip()
        ):
            raise DomainError("story_changed", "接力工作版本或目标稿已改动，作者补充尚未采用。", 409)
        plots = progress["plan"]["chapters"]
        index = waiting["number"] - 1
        before = author_plot(task, waiting["number"])
        if before != plots[index] or sha(before["plot"]) != waiting["outline_sha256"]:
            raise DomainError("outline_relay_changed", "原章纲已经改变，本次补充尚未采用。", 409)
        amendment = {
            "number": waiting["number"],
            "previous_preparation": deepcopy(waiting),
            "prior_outline_sha256": waiting["outline_sha256"],
            "request": request,
        }
        progress["outline_relay_clarifications"] = [*amendments, amendment]
        plots[index] = author_plot(task, waiting["number"])
        if len(plots[index]["plot"]) > 5000:
            raise DomainError("outline_relay", "原章纲与补充合计需在5000字符内。")
        del progress[WAITING]
        del progress["outline_relay_preparations"][str(waiting["number"])]
        tx.execute(
            "UPDATE tasks SET status='queued',result=?,attempt=NULL,cancel=0,pause=0,error='',updated=? WHERE id=?",
            (dumps(progress), time(), task_id),
        )
        domain.db.event(
            tx,
            task_id,
            "outline_relay_clarified",
            {
                "number": waiting["number"],
                "checkpoint": waiting["id"],
                "outline_sha256": sha(plots[index]["plot"]),
            },
        )
    return domain.task(task_id)


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/outline-relay")
    def list_relay_outlines(branch_id: str):
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return outlines(tx, branch_id)

    @app.post("/api/tasks/{task_id}/outline-relay/clarify", status_code=202)
    def clarify_relay(task_id: str, data: Clarification):
        return clarify(domain, task_id, data)
