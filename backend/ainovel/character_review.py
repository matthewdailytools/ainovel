"""Bounded, durable source-entailment review before automatic character adoption."""

from copy import deepcopy
import hashlib
import json
from time import time
from typing import Literal

from pydantic import Field, ValidationError

from .db import DomainError, dumps
from .story_graph import Strict

VERSION = "character-field-entailment/2"
ACTION = "独立核对人物自由文本的本项来源蕴含"


class FieldReview(Strict):
    record_id: str
    field: Literal["notes", "profile"]
    result: Literal["supported", "unknown", "contradicted"]
    reason: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(max_length=30)


class CharacterReview(Strict):
    version: Literal["character-field-entailment/2"]
    input_sha256: str = Field(min_length=64, max_length=64)
    fields: list[FieldReview] = Field(min_length=1, max_length=120)


def sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def review_input(tx, artifact, task):
    """Use only frozen candidate sources, never the current graph's prose."""
    from .domain import utf16_slice

    payload = artifact["payload"]
    targets, units, allowed = [], [], {}
    source_chapters = {}
    unit_ids = {}
    for item in payload["records"]:
        record = item["record"]
        if record["kind"] != "character":
            continue
        before = item.get("previous_record", {}).get("record", {})
        changed = [
            field
            for field in ("notes", "profile")
            if record.get(field, "").strip()
            and (not item.get("target_id") or record[field] != before.get(field, ""))
        ]
        if not changed:
            continue
        source_ids = []
        for source in record["sources"]:
            chapter = tx.execute(
                "SELECT * FROM chapters WHERE id=? AND branch_id=? AND archived=0",
                (source["chapter_id"], task["branch_id"]),
            ).fetchone()
            if not chapter or chapter["revision"] != source["revision"] or chapter["stale"]:
                raise DomainError("revision_conflict", "人物字段复核来源已改变或待复检，未继续采用。", 409)
            if utf16_slice(chapter["content"], source["start"], source["end"])[1] != source["quote"]:
                raise DomainError("evidence", "人物字段复核的来源须逐字匹配冻结保存稿。", 409)
            source_chapters[chapter["id"]] = {
                "chapter_id": chapter["id"],
                "revision": chapter["revision"],
                "content_sha256": hashlib.sha256(chapter["content"].encode()).hexdigest(),
            }
            key = sha(source)
            if key not in unit_ids:
                unit_ids[key] = f"CHR{len(units) + 1:04}"
                units.append({"source_id": unit_ids[key], **deepcopy(source)})
            source_ids.append(unit_ids[key])
        if not source_ids:
            raise DomainError("evidence", "人物自由文本没有本项原文依据，不能自动核准。", 409)
        for field in changed:
            target = {
                "record_id": item["id"],
                "field": field,
                "text": record[field],
                "identity": {"title": record["title"], "aliases": record.get("aliases", [])},
                "source_ids": source_ids,
            }
            targets.append(target)
            allowed[(item["id"], field)] = set(source_ids)
    constraints = {
        key: deepcopy(task["payload"].get(key, default)) for key, default in (("brief", ""), ("fixed", []))
    }
    binding = {
        "version": VERSION,
        "artifact_id": artifact["id"],
        "candidate_revision": payload.get("_candidate_revision", 0),
        "branch_id": task["branch_id"],
        "story_revision": artifact["basis"].get("story_revision"),
        "records_sha256": sha(payload["records"]),
        "sources_sha256": sha({"units": units, "chapters": list(source_chapters.values())}),
        "constraints_sha256": sha(constraints),
    }
    return {
        "binding": binding,
        "input_sha256": sha(binding),
        "targets": targets,
        "sources": units,
        "allowed": allowed,
        "constraints": constraints,
    }


def response_issues(response, inputs):
    issues = []
    if response["input_sha256"] != inputs["input_sha256"]:
        issues.append("input_sha256必须逐字对应本次冻结输入，不能复用其他候选的报告。")
    expected = set(inputs["allowed"])
    keys = [(field["record_id"], field["field"]) for field in response["fields"]]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        issues.append("fields须完整且唯一覆盖本次record_id与field，不增补、遗漏或重复。")
    for index, field in enumerate(response["fields"]):
        key = (field["record_id"], field["field"])
        ids = field["source_ids"]
        if len(ids) != len(set(ids)) or not set(ids).issubset(inputs["allowed"].get(key, set())):
            issues.append(f"fields[{index}].source_ids只能使用此人物本项来源，不得借别项原文。")
        if field["result"] in {"supported", "contradicted"} and not ids:
            issues.append(f"fields[{index}]肯定或矛盾判断须有本项原文依据；不足应保留unknown。")
    return issues


def resolved_results(response, inputs):
    units = {unit["source_id"]: unit for unit in inputs["sources"]}
    return [
        {
            **{key: field[key] for key in ("record_id", "field", "result", "reason")},
            "evidence": [
                {key: units[identifier][key] for key in ("chapter_id", "revision", "start", "end", "quote")}
                for identifier in field["source_ids"]
            ],
        }
        for field in response["fields"]
    ]


def valid_report(report, inputs):
    if not isinstance(report, dict):
        return False
    try:
        response = CharacterReview.model_validate(report.get("response")).model_dump()
    except ValidationError, TypeError:
        return False
    status = "supported" if all(f["result"] == "supported" for f in response["fields"]) else "needs_review"
    return (
        report.get("version") == VERSION
        and report.get("binding") == inputs["binding"]
        and report.get("input_sha256") == inputs["input_sha256"]
        and report.get("candidate_revision") == inputs["binding"]["candidate_revision"]
        and not response_issues(response, inputs)
        and report.get("results") == resolved_results(response, inputs)
        and report.get("status") == status
    )


def current_input(tx, worker, artifact_id):
    worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
    task_row = tx.execute("SELECT * FROM tasks WHERE id=?", (worker.task["id"],)).fetchone()
    task = dict(task_row)
    task["payload"], task["result"] = json.loads(task["payload"]), json.loads(task["result"])
    artifact_row = tx.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    if (
        not artifact_row
        or artifact_row["branch_id"] != task["branch_id"]
        or artifact_row["kind"] != "story_extraction"
        or artifact_row["status"] != "candidate"
        or task["result"].get("pending_structure", {}).get("artifact_id") != artifact_id
    ):
        raise DomainError("candidate_changed", "结构候选或批次检查点已改变，复核结果未套用。", 409)
    artifact = dict(artifact_row)
    artifact["payload"], artifact["basis"] = json.loads(artifact["payload"]), json.loads(artifact["basis"])
    artifact["candidate_revision"] = artifact["payload"].get("_candidate_revision", 0)
    revision = tx.execute("SELECT story_revision FROM branches WHERE id=?", (task["branch_id"],)).fetchone()[
        0
    ]
    if revision != artifact["basis"].get("story_revision") or revision != task["result"].get(
        "expected_story_revision"
    ):
        raise DomainError("story_changed", "人物复核依据的故事版本已改变。", 409)
    return artifact, task, review_input(tx, artifact, task)


async def ensure_character_review(worker, artifact):
    """Return a bound artifact/token. Unknown is final, not a repair request."""
    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        artifact, task, inputs = current_input(tx, worker, artifact["id"])
    token = inputs["input_sha256"]
    if not inputs["targets"]:
        return artifact, token
    old = artifact["payload"].get("character_semantic_review")
    if valid_report(old, inputs):
        return artifact, token
    contract = CharacterReview.model_json_schema()
    contract["properties"]["input_sha256"]["enum"] = [token]
    contract["properties"]["fields"].update(minItems=len(inputs["targets"]), maxItems=len(inputs["targets"]))
    contract["$defs"]["FieldReview"]["properties"]["record_id"]["enum"] = list(
        dict.fromkeys(target["record_id"] for target in inputs["targets"])
    )
    contract["$defs"]["FieldReview"]["properties"]["source_ids"]["items"]["enum"] = [
        source["source_id"] for source in inputs["sources"]
    ]
    response = await worker.grounded(
        {
            "动作": ACTION,
            "字段复核契约版本": VERSION,
            "input_sha256": token,
            "人物字段": inputs["targets"],
            "本项原文来源": inputs["sources"],
            "冻结工作流边界": inputs["constraints"],
            "结论判定": {
                "supported": "字段中的全部实质断言均得到本项原文支持。如字段如实记录某事尚未知、未证实或仅为人物猜测，且来源确实如此，这一保留未知的描述本身可以得到支持；不要将它误当确定事实断言。",
                "unknown": "缺少支持且没有相反证据时必须为unknown。未证实不等于证伪；没有证据证明成立，也不等于已经证明相反命题。人物猜测既不证明事情为真，也不证明其必假。冻结约束要求保持未知，不等于事实反命题已经成立。",
                "contradicted": "须有本项来源明示的相反事实，并引用该依据。仅以没有支持、尚未交代、角色怀疑或要求保持未知为理由，不能判contradicted；这类依据不足应为unknown，不得补出相反事实。",
            },
            "要求": "仅独立核对所列人物字段的全部实质断言能否由其本项原文支持。身份用于定位，字段本身不是证据，不引用当前记忆或外部知识循环佐证。brief/fixed用于约束冲突和时点边界，未来规划不能当已发生事实。逐项返回record_id、field、supported/unknown/contradicted、source_ids和理由；肯定须完整受支持，未知不能升级为事实。先区分来源支持、明确反证、依据不足，再给结论，不以阻止自动采用为由将unknown改判为contradicted。未知或矛盾是有效结果，不为通过删除断言、改写字段或勉强改判。只用本人物列出的来源编号，原句和位置由程序复原。",
        },
        CharacterReview,
        {
            "branch": {"id": task["branch_id"], "story_revision": inputs["binding"]["story_revision"]},
            "coverage": "仅本项冻结原文、人物身份及任务brief/fixed，不以既有自由文本档案为证据。",
        },
        lambda value: response_issues(value, inputs),
        max_output=8192,
        contract=contract,
    )
    provider = worker.settings.providers[task["payload"]["account"]]
    steps = []
    for step in worker.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task["id"],)):
        saved = json.loads(step["result"])
        try:
            content = json.loads(saved.get("text", ""))
        except ValueError, TypeError:
            continue
        if (
            isinstance(content, dict)
            and content.get("input_sha256") == token
            and content.get("version") == VERSION
        ):
            steps.append(
                {
                    "fingerprint": step["fingerprint"],
                    "call_id": saved.get("call_id"),
                    "model": saved.get("model") or provider.model,
                    "usage": saved.get("usage"),
                }
            )
    report = {
        "version": VERSION,
        "input_sha256": token,
        "binding": inputs["binding"],
        "candidate_revision": inputs["binding"]["candidate_revision"],
        "response": response,
        "results": resolved_results(response, inputs),
        "status": "supported"
        if all(field["result"] == "supported" for field in response["fields"])
        else "needs_review",
        "reviewer": {
            "task_id": task["id"],
            "account": provider.id,
            "provider": provider.kind,
            "model": provider.model,
        },
        "created": time(),
        "completed_steps": steps,
        "persisted_by_attempt": worker.attempt,
    }
    with worker.db.connect(True) as tx:
        fresh, _, fresh_inputs = current_input(tx, worker, artifact["id"])
        if fresh_inputs["input_sha256"] != token:
            raise DomainError("candidate_changed", "人物字段、来源或约束在复核期间改变，旧结果未套用。", 409)
        previous = fresh["payload"].get("character_semantic_review")
        if previous and previous != report:
            fresh["payload"].setdefault("character_semantic_review_history", []).append(previous)
        fresh["payload"]["character_semantic_review"] = report
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(fresh["payload"]), artifact["id"]))
        worker.db.event(
            tx,
            task["id"],
            "character_semantic_reviewed",
            {
                "artifact_id": artifact["id"],
                "version": VERSION,
                "input_sha256": token,
                "status": report["status"],
            },
        )
    return fresh, token


def validate_adoption_review(tx, artifact, task_id, expected_token):
    """Recheck content/source/constraint binding in the actual adoption transaction."""
    row = tx.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    task = {**dict(row), "payload": json.loads(row["payload"])}
    if task["payload"].get("adoption") != "automatic":
        return
    inputs = review_input(tx, artifact, task)
    if inputs["targets"] and expected_token is None:
        raise DomainError("character_review", "自动维护的人物自由文本缺少复核绑定，未采用。", 409)
    if not inputs["targets"] and expected_token is None:
        return
    if inputs["input_sha256"] != expected_token:
        raise DomainError("candidate_changed", "人物复核绑定已失效，未自动采用。", 409)
    if inputs["targets"]:
        report = artifact["payload"].get("character_semantic_review")
        if not valid_report(report, inputs) or report["status"] != "supported":
            raise DomainError("character_review", "人物自由文本缺少有效支持复核，请人工核对。", 409)
