"""Opt-in, two-round repair of unadopted AI character prose, never author records."""

from copy import deepcopy
import json
import re
from time import time
from typing import Literal

from pydantic import Field, ValidationError

from .character_review import (
    CharacterReview,
    current_input,
    ensure_character_review,
    response_issues as review_issues,
    sha,
    valid_report,
)
from .db import DomainError, dumps, uid
from .story_graph import Strict

VERSION = "character-field-repair/1"
ACTION = "修正未采纳人物自由文本字段"
MAX_ROUNDS = 2


class FieldReplacement(Strict):
    record_id: str
    field: Literal["notes", "profile"]
    old_sha256: str = Field(min_length=64, max_length=64)
    decision: Literal["replace", "cannot_repair"]
    replacement: str | None = Field(default=None, max_length=8000)
    source_ids: list[str] = Field(max_length=30)
    reason: str = Field(min_length=1, max_length=2000)


class CharacterRepair(Strict):
    version: Literal["character-field-repair/1"]
    input_sha256: str = Field(min_length=64, max_length=64)
    fields: list[FieldReplacement] = Field(min_length=1, max_length=120)


def content_sha(payload):
    # Semantic reports are independently added after staging; all other content
    # remains immutable for this operation, including scenes and source history.
    return sha(
        {
            k: v
            for k, v in payload.items()
            if k not in {"character_semantic_review", "character_semantic_review_history"}
        }
    )


def origin(payload, task_id, attempt):
    """Service-stamped at AI extraction staging, never inferred from revision 0."""
    return {
        "version": VERSION,
        "task_id": task_id,
        "attempt": attempt,
        "content_sha256": content_sha(payload),
    }


def lineage(tx, task):
    ids, current = set(), task
    while current["id"] not in ids:
        if current["branch_id"] != task["branch_id"] or current["kind"] != "workflow":
            raise DomainError("workflow_progress", "人物修订来源不属于同一工作版本工作流。", 409)
        ids.add(current["id"])
        previous = current["result"].get("continued_from")
        if not previous:
            return ids
        row = tx.execute("SELECT * FROM tasks WHERE id=?", (previous,)).fetchone()
        if not row:
            raise DomainError("workflow_progress", "人物修订续跑来源不存在。", 409)
        current = {**dict(row), "result": json.loads(row["result"])}
    raise DomainError("workflow_progress", "人物修订续跑来源循环。", 409)


def eligible(tx, artifact, task):
    marker = artifact["basis"].get("character_field_origin", {})
    if (
        task["kind"] != "workflow"
        or task["payload"].get("adoption") != "automatic"
        or task["payload"].get("character_field_repair") is not True
        or artifact["candidate_revision"] != 0
        or not artifact["basis"].get("maintenance_chapter")
        or marker.get("version") != VERSION
        or marker.get("content_sha256") != content_sha(artifact["payload"])
    ):
        return False
    ids = lineage(tx, task)
    if artifact["task_id"] not in ids or marker.get("task_id") not in ids:
        return False
    owner = tx.execute("SELECT payload FROM tasks WHERE id=?", (marker["task_id"],)).fetchone()
    return bool(owner and json.loads(owner[0]).get("adoption") == "automatic")


def failed_keys(report):
    return {(f["record_id"], f["field"]) for f in report["response"]["fields"] if f["result"] != "supported"}


def response_issues(response, inputs):
    expected = {(t["record_id"], t["field"]): t for t in inputs["targets"]}
    issues = []
    if response["input_sha256"] != inputs["input_sha256"]:
        issues.append("input_sha256须对应本轮冻结输入。")
    keys = [(f["record_id"], f["field"]) for f in response["fields"]]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        issues.append("fields须唯一完整覆盖本轮白名单，不得修改其他字段或增加人物。")
    for index, field in enumerate(response["fields"]):
        target = expected.get((field["record_id"], field["field"]))
        if not target:
            continue
        if field["old_sha256"] != target["old_sha256"]:
            issues.append(f"fields[{index}].old_sha256须对应被修订字段。")
        ids = field["source_ids"]
        if len(ids) != len(set(ids)) or not set(ids).issubset(target["source_ids"]):
            issues.append(f"fields[{index}].source_ids只可用本人物原有来源，不得借别项或拼造证据。")
        text = field["replacement"]
        if field["decision"] == "cannot_repair":
            if text is not None:
                issues.append(f"fields[{index}]不能修订时replacement须为null，保留原候选。")
            continue
        if (
            not text
            or not text.strip()
            or text == target["text"]
            or not ids
            or len(text) > (4000 if field["field"] == "notes" else 8000)
        ):
            issues.append(f"fields[{index}]替换须非空、有本项来源且实际改变；否则明确cannot_repair。")
        elif re.search(r"原文未提及|原文没有提及|暂无记载|未提供.{0,12}信息|信息不足", text):
            issues.append(
                f"fields[{index}]不能用泛化的未提及/信息不足替代有支持的信息，不能合理修订须cannot_repair。"
            )
    return issues


def saved_response(worker, slot, inputs, schema=CharacterRepair, validator=response_issues):
    """Only this chain's paid completed step may cross an account continuation."""
    from .worker import parse_json

    steps, response = [], None
    for row in worker.db.all(
        "SELECT fingerprint,result FROM task_steps WHERE task_id=? ORDER BY created", (slot["owner_task_id"],)
    ):
        if row["fingerprint"] in slot["before_steps"]:
            continue
        saved = json.loads(row["result"])
        steps.append(
            {
                "task_id": slot["owner_task_id"],
                "fingerprint": row["fingerprint"],
                "call_id": saved.get("call_id"),
                "model": saved.get("model"),
                "usage": saved.get("usage"),
            }
        )
        try:
            value = schema.model_validate(parse_json(saved.get("text", ""))).model_dump()
        except ValidationError, TypeError, DomainError:
            continue
        if value["input_sha256"] == inputs["input_sha256"] and not validator(value, inputs):
            response = value
    return response, steps


async def ensure_candidate_review(worker, artifact, result):
    """Persist a child's review dispatch, including the crash-before-report window."""
    if not artifact["basis"].get("character_field_repair"):
        artifact, token = await ensure_character_review(worker, artifact)
        return artifact, token, ""
    with worker.db.connect(True) as tx:
        artifact, task, inputs = current_input(tx, worker, artifact["id"])
        if valid_report(artifact["payload"].get("character_semantic_review"), inputs):
            return artifact, inputs["input_sha256"], ""
        if task["pause"]:
            return artifact, None, "批次已暂停，人物新候选尚未独立复核。"
        if not eligible(tx, artifact, task):
            return artifact, None, "人物新候选来源或内容已改变，未继续复核。"
        basis = artifact["basis"]["character_field_repair"]
        chain = task["result"].get("character_field_repairs", {}).get(basis["root_artifact_id"])
        if not chain or chain["current_artifact_id"] != artifact["id"]:
            raise DomainError("workflow_progress", "人物复检检查点缺失，未重新派发。", 409)
        slot = chain["rounds"][basis["round"] - 1]
        dispatch = slot.get("review_dispatch")
        recovering = bool(dispatch)
        if dispatch and (
            dispatch["input_sha256"] != inputs["input_sha256"]
            or dispatch["owner_task_id"] not in lineage(tx, task)
        ):
            raise DomainError("candidate_changed", "人物复检冻结输入已改变。", 409)
        if not dispatch:
            dispatch = slot["review_dispatch"] = {
                "input_sha256": inputs["input_sha256"],
                "owner_task_id": task["id"],
                "dispatched_by_attempt": worker.attempt,
                "created": time(),
                "before_steps": [
                    r["fingerprint"]
                    for r in tx.execute("SELECT fingerprint FROM task_steps WHERE task_id=?", (task["id"],))
                ],
            }
            persist(
                worker,
                tx,
                task,
                result,
                "character_field_repair_review_started",
                {
                    "artifact_id": artifact["id"],
                    "round": basis["round"],
                    "input_sha256": inputs["input_sha256"],
                },
            )
    sync(worker, result, task["result"])
    completed = None
    if recovering:
        response, steps = saved_response(worker, dispatch, inputs, CharacterReview, review_issues)
        if response is None:
            return artifact, None, "人物独立复检已派发但完整结果未知或不合格，未自动重发；请核对费用和候选。"
        owner = worker.domain.task(dispatch["owner_task_id"])
        provider = worker.settings.providers[owner["payload"]["account"]]
        completed = {
            "response": response,
            "completed_steps": steps,
            "reviewer": {
                "task_id": owner["id"],
                "account": provider.id,
                "provider": provider.kind,
                "model": steps[-1]["model"] or provider.model,
            },
        }
    artifact, token = await ensure_character_review(worker, artifact, completed=completed)
    return artifact, token, ""


def sync(worker, result, progress):
    result.clear()
    result.update(progress)
    worker.task["result"] = result


def persist(worker, tx, task, result, event, details):
    tx.execute(
        "UPDATE tasks SET result=?,updated=? WHERE id=? AND attempt=?",
        (dumps(task["result"]), time(), task["id"], worker.attempt),
    )
    worker.db.event(tx, task["id"], event, details)
    # Caller syncs after commit, so a rollback never leaks an uncommitted checkpoint.


def prepare(worker, artifact_id, result):
    with worker.db.connect(True) as tx:
        artifact, task, review = current_input(tx, worker, artifact_id)
        if task["pause"]:
            return None, "批次已暂停，未派发人物修订。"
        if not eligible(tx, artifact, task):
            return None, "人物候选缺少当前自动生成来源或已被编辑，不自动修订。"
        report = artifact["payload"].get("character_semantic_review")
        if not valid_report(report, review) or report["status"] != "needs_review":
            return None, "人物复核绑定已改变，未自动修订。"
        failures = failed_keys(report)
        records = {r["id"]: r for r in artifact["payload"]["records"]}
        if any(records[r].get("target_id") is not None for r, _ in failures):
            return None, "已有正式人物档案需作者处理，不自动改写。"
        root_id = artifact["basis"].get("character_field_repair", {}).get("root_artifact_id", artifact_id)
        chains = task["result"].setdefault("character_field_repairs", {})
        chain = chains.get(root_id)
        if not chain:
            if root_id != artifact_id:
                raise DomainError("workflow_progress", "人物修订链检查点缺失，未重置轮数。", 409)
            chain = chains[root_id] = {
                "version": VERSION,
                "root_artifact_id": root_id,
                "max_rounds": MAX_ROUNDS,
                "whitelist": sorted([list(key) for key in failures]),
                "rounds": [],
                "sources_sha256": review["binding"]["sources_sha256"],
                "constraints_sha256": review["binding"]["constraints_sha256"],
                "current_artifact_id": artifact_id,
            }
        if (
            chain.get("version") != VERSION
            or chain.get("current_artifact_id") != artifact_id
            or review["binding"]["sources_sha256"] != chain["sources_sha256"]
            or review["binding"]["constraints_sha256"] != chain["constraints_sha256"]
        ):
            raise DomainError("candidate_changed", "人物修订链的候选、来源或约束已改变，未重置轮数。", 409)
        if not failures.issubset({tuple(key) for key in chain["whitelist"]}):
            return None, "复检出现初始白名单以外问题，停止自动修订。"
        active = next((s for s in chain["rounds"] if s["parent_artifact_id"] == artifact_id), None)
        if active and active["status"] not in {"dispatched"}:
            return None, active.get("stop_reason", "本轮已停止，原候选与复核报告保留。")
        if not active and len(chain["rounds"]) >= MAX_ROUNDS:
            return None, "人物字段两轮修订后仍未获来源支持，保留全部候选和报告。"
        targets = []
        for target in review["targets"]:
            if (target["record_id"], target["field"]) in failures:
                targets.append({**target, "old_sha256": sha(target["text"])})
        allowed_sources = {identifier for target in targets for identifier in target["source_ids"]}
        data = {
            "version": VERSION,
            "root_artifact_id": root_id,
            "round": active["round"] if active else len(chain["rounds"]) + 1,
            "parent_input_sha256": review["input_sha256"],
            "report_sha256": sha(report),
            "targets": targets,
            "sources": [u for u in review["sources"] if u["source_id"] in allowed_sources],
            "constraints": review["constraints"],
        }
        inputs = {**data, "input_sha256": sha(data)}
        if active and active["input_sha256"] != inputs["input_sha256"]:
            raise DomainError("candidate_changed", "人物修订输入已改变，旧结果未套用。", 409)
        if active and active["owner_task_id"] not in lineage(tx, task):
            raise DomainError("workflow_progress", "人物修订步骤不属于当前续跑来源，未复用。", 409)
        recovering = bool(active)
        if not active:
            active = {
                "round": inputs["round"],
                "parent_artifact_id": artifact_id,
                "input_sha256": inputs["input_sha256"],
                "owner_task_id": task["id"],
                "status": "dispatched",
                "dispatched_by_attempt": worker.attempt,
                "created": time(),
                "before_steps": [
                    r["fingerprint"]
                    for r in tx.execute("SELECT fingerprint FROM task_steps WHERE task_id=?", (task["id"],))
                ],
            }
            chain["rounds"].append(active)
            persist(
                worker,
                tx,
                task,
                result,
                "character_field_repair_started",
                {
                    "root_artifact_id": root_id,
                    "parent_artifact_id": artifact_id,
                    "round": active["round"],
                    "input_sha256": active["input_sha256"],
                },
            )
    sync(worker, result, task["result"])
    return (artifact, inputs, deepcopy(active), recovering), ""


def finish(worker, result, artifact, inputs, response, steps, stop=""):
    """Candidate creation and pending pointer transfer share one CAS transaction."""
    child_id = None
    with worker.db.connect(True) as tx:
        fresh, task, review = current_input(tx, worker, artifact["id"])
        chain = task["result"]["character_field_repairs"][inputs["root_artifact_id"]]
        slot = chain["rounds"][inputs["round"] - 1]
        if (
            content_sha(fresh["payload"]) != content_sha(artifact["payload"])
            or review["input_sha256"] != inputs["parent_input_sha256"]
            or sha(fresh["payload"].get("character_semantic_review")) != inputs["report_sha256"]
            or slot["input_sha256"] != inputs["input_sha256"]
            or slot["status"] != "dispatched"
            or chain["current_artifact_id"] != artifact["id"]
            or not eligible(tx, fresh, task)
        ):
            raise DomainError(
                "candidate_changed", "人物候选、来源、报告或修订检查点已改变，旧修订未套用。", 409
            )
        slot.update(response=response, completed_steps=steps, completed=time())
        if response and any(f["decision"] == "cannot_repair" for f in response["fields"]):
            stop = "模型明确无法按本项原文合理修订；原字段与报告保留，等待作者。"
        if stop:
            slot.update(status="stopped", stop_reason=stop)
        else:
            payload = deepcopy(fresh["payload"])
            payload.pop("character_semantic_review", None)
            payload.pop("character_semantic_review_history", None)
            indexed = {r["id"]: r["record"] for r in payload["records"]}
            diffs = []
            for field in response["fields"]:
                record = indexed[field["record_id"]]
                diffs.append(
                    {
                        "record_id": field["record_id"],
                        "field": field["field"],
                        "before": record[field["field"]],
                        "after": field["replacement"],
                        "source_ids": field["source_ids"],
                        "reason": field["reason"],
                    }
                )
                record[field["field"]] = field["replacement"]
            child_id = uid()
            basis = {
                **deepcopy(fresh["basis"]),
                "character_field_origin": origin(payload, task["id"], worker.attempt),
                "character_field_repair": {
                    "version": VERSION,
                    "root_artifact_id": inputs["root_artifact_id"],
                    "parent_artifact_id": fresh["id"],
                    "round": inputs["round"],
                    "input_sha256": inputs["input_sha256"],
                    "diff": diffs,
                },
            }
            tx.execute(
                "INSERT INTO artifacts(id,novel_id,branch_id,chapter_id,kind,base_revision,payload,basis,task_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    child_id,
                    fresh["novel_id"],
                    fresh["branch_id"],
                    fresh["chapter_id"],
                    fresh["kind"],
                    fresh["base_revision"],
                    dumps(payload),
                    dumps(basis),
                    task["id"],
                    time(),
                ),
            )
            task["result"]["pending_structure"]["artifact_id"] = child_id
            task["result"].pop("waiting_structure", None)
            chain["current_artifact_id"] = child_id
            slot.update(status="candidate_created", child_artifact_id=child_id, diff=diffs)
        persist(
            worker,
            tx,
            task,
            result,
            "character_field_repair_completed",
            {
                "root_artifact_id": inputs["root_artifact_id"],
                "round": inputs["round"],
                "parent_artifact_id": artifact["id"],
                "child_artifact_id": child_id,
                "status": slot["status"],
                "input_sha256": inputs["input_sha256"],
            },
        )
    sync(worker, result, task["result"])
    return worker.domain.artifact(child_id) if child_id else artifact, stop


async def repair_fields(worker, artifact, result):
    """Semantic non-support is valid; repair a new candidate, never re-score it away."""
    while artifact["payload"]["character_semantic_review"]["status"] == "needs_review":
        prepared, stop = prepare(worker, artifact["id"], result)
        if not prepared:
            return artifact, None, stop
        artifact, inputs, slot, recovering = prepared
        response, steps = saved_response(worker, slot, inputs)
        if response is None and recovering:
            artifact, stop = finish(
                worker,
                result,
                artifact,
                inputs,
                None,
                steps,
                "人物修订没有可安全复用的完整结果（可能未派发或结果未知），未自动重发；请核对步骤、错误及费用后人工处理。",
            )
            return artifact, None, stop
        if response is None:
            response = await worker.grounded(
                {
                    "动作": ACTION,
                    "人物修订契约版本": VERSION,
                    "input_sha256": inputs["input_sha256"],
                    "轮次": inputs["round"],
                    "人物字段": inputs["targets"],
                    "本项原文来源": inputs["sources"],
                    "冻结工作流边界": inputs["constraints"],
                    "原复核": [
                        f
                        for f in artifact["payload"]["character_semantic_review"]["response"]["fields"]
                        if (f["record_id"], f["field"])
                        in {(t["record_id"], t["field"]) for t in inputs["targets"]}
                    ],
                    "要求": "只对所列未采纳AI生成的新人物notes/profile作最小、有本人物原文支持的修订。保留原文确实支持的实质信息和未知/猜测边界，不编造、拼接事实或删成空字段，不用泛化的未提及/信息不足掩盖可支持信息。身份、别名、来源、事件及其它字段均不可改。每项填old_sha256、replace/cannot_repair、replacement、本项source_ids及理由；不能合理修订则明确cannot_repair且replacement=null，系统保留原稿并停止，不为通过改判原报告。新候选将重新独立核对，旧报告始终保留。",
                },
                CharacterRepair,
                {
                    "branch": {
                        "id": artifact["branch_id"],
                        "story_revision": artifact["basis"]["story_revision"],
                    },
                    "coverage": "仅白名单人物字段、本项冻结原文与brief/fixed；没有当前档案或外部事实。",
                },
                lambda value: response_issues(value, inputs),
                max_output=8192,
            )
            _, steps = saved_response(worker, slot, inputs)
        artifact, stop = finish(worker, result, artifact, inputs, response, steps)
        if stop:
            return artifact, None, stop
        if worker.domain.task(worker.task["id"])["pause"]:
            return artifact, None, "批次已暂停，新候选保留，尚未派发独立复核。"
        artifact, token, stop = await ensure_candidate_review(worker, artifact, result)
        if stop:
            return artifact, token, stop
        if artifact["payload"]["character_semantic_review"]["status"] == "supported":
            return artifact, token, ""
    return artifact, None, "人物复核未提供可自动采用的绑定。"
