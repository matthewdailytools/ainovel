"""Server-owned observation template epochs; source meaning and validators stay /2."""

from copy import deepcopy
import json
import re

from .db import DomainError

KEY = "authored_source_observation_execution_version"
SNAPSHOT = "authored_source_observation_execution_snapshot"
RECORD_HASH = "authored_source_observation_execution_sha256"
EVENT = "authored_source_observation_execution_frozen"
LEGACY_VERSION = "authored-source-observation-execution/1"
VERSION = "authored-source-observation-execution/2"
NEW_TASK_VERSION = VERSION
RETRY_KEY = "author_source_observation_retry_version"

GUIDANCE = (
    "本执行模板只改观察指导和定位诊断，不改作者原话或来源政策。"
    "covered是直接引用覆盖：该unit的source_id必须逐字等于所关联每个O的evidence或restriction_evidence。"
    "同一交易、前后句因果相关或reason里提到另一句，都不能代替这两个直接锚。"
    "清空或改动某个O的restriction_evidence时，必须同步重新核对全部unit_checks；"
    "不能把非范围事实塞回named_destination的限制字段来保住covered。"
    "交易失败、双方未交付、不动武、测量方法和不能推出角色全知等明确剧情，"
    "没有接收范围事项时标not_scope_related并且observation_ids=[]，但后续全部P解释与独立原意保持仍逐句保留。"
    "真实范围歧义仍unknown，真实限制仍直接定位，不准为通过检查改状态或删除剧情。"
    "已向受理人说出的申请是请求本身已发生；所求查看、许可或物品是否得到另行判断。"
    "没有写未获准，不能从申请不等于获准推造请求未获准；知识边界也不能扩大成新禁止传递。"
)


def invalid(message):
    raise DomainError("authored_execution_version", message, 409)


def reject_payload(payload):
    if any(key in payload for key in (KEY, SNAPSHOT, RECORD_HASH)):
        invalid("来源观察执行模板只由服务器冻结，不能由请求注入。")


def clone_source_witness(source_task, new_task_id, payload, initial):
    """Clone only identity metadata; never regenerate or reinterpret source entries."""
    from . import authored_source_scope as authored

    if (
        new_task_id == source_task["id"]
        or source_task["result"].get(authored.KEY) != authored.CURRENT_VERSION
        or payload["novel_id"] != source_task["novel_id"]
        or payload.get("branch_id", source_task["branch_id"]) != source_task["branch_id"]
        or {key: value for key, value in payload.items() if key not in {"client_request_id", "budget_limit"}}
        != {
            key: value
            for key, value in source_task["payload"].items()
            if key not in {"client_request_id", "budget_limit"}
        }
    ):
        invalid("明确恢复须建立同作品、同版本的新任务，不能升级原任务或换原话。")
    reject_payload(payload)
    for key in (KEY, SNAPSHOT, RECORD_HASH):
        initial.pop(key, None)
    witness = deepcopy(source_task["result"][authored.SNAPSHOT])
    witness.pop(KEY, None)
    witness.update(task_id=new_task_id, payload_sha256=authored.sha(payload))
    initial[authored.KEY] = authored.CURRENT_VERSION
    initial[authored.SNAPSHOT] = witness
    return witness


def freeze_execution(domain, task_id, kind, payload, initial, *, version=None):
    from . import authored_source_scope as authored

    reject_payload(payload)
    version = NEW_TASK_VERSION if version is None else version
    if version == LEGACY_VERSION:
        return
    if version != VERSION:
        invalid("未知来源观察执行模板，未回退或升级。")
    source = initial.get(authored.SNAPSHOT)
    if initial.get(authored.KEY) != authored.CURRENT_VERSION or not source or source.get("inherited"):
        # Reference inheritance keeps its original observation and policy. No new observe is run.
        return
    if domain.db.all("SELECT id FROM tasks WHERE id=?", (task_id,)):
        invalid("已存在任务不能静默补盖执行模板，请保留旧缓存和回执。")
    if any(key in initial or key in source for key in (KEY, SNAPSHOT, RECORD_HASH)):
        invalid("执行模板已冻结或来源见证已被改写，不能重复冻结。")
    if (
        source.get("task_id") != task_id
        or source.get("kind") != kind
        or source.get("version") != authored.CURRENT_VERSION
        or source.get("novel_id") != payload["novel_id"]
        or source.get("payload_sha256") != authored.sha(payload)
    ):
        invalid("执行模板须绑定本次新任务的原始来源见证。")
    source[KEY] = VERSION
    witness = {
        "version": VERSION,
        "task_id": task_id,
        "novel_id": payload["novel_id"],
        "branch_id": source["branch_id"],
        "kind": kind,
        "payload_sha256": authored.sha(payload),
        "source_policy_version": authored.CURRENT_VERSION,
        "source_snapshot_sha256": authored.sha(source),
        "legacy_receipts": {
            key: authored.sha(value) for key, value in initial.get(authored.RECEIPTS, {}).items()
        },
        "creation": "explicit_retry" if RETRY_KEY in initial else "new_source",
    }
    initial.update({KEY: VERSION, SNAPSHOT: witness})


def event(db, tx, task_id, payload, initial):
    from .authored_source_scope import sha

    if KEY in initial:
        db.event(
            tx,
            task_id,
            EVENT,
            {KEY: initial[KEY], "snapshot_sha256": sha(initial[SNAPSHOT]), "payload_sha256": sha(payload)},
        )


def record_metadata(task):
    from .authored_source_scope import sha

    result = task["result"]
    if result.get(KEY) is None:
        return {}
    return {KEY: result[KEY], SNAPSHOT: deepcopy(result[SNAPSHOT]), RECORD_HASH: sha(result[SNAPSHOT])}


def record_valid(record, witness=None):
    from .authored_source_scope import sha

    fields = (KEY, SNAPSHOT, RECORD_HASH)
    if not any(key in record for key in fields):
        return witness is None
    snapshot = record.get(SNAPSHOT)
    return (
        record.get(KEY) == VERSION
        and isinstance(snapshot, dict)
        and snapshot.get("version") == VERSION
        and snapshot.get("source_policy_version") == "authored-source-scope/2"
        and record.get(RECORD_HASH) == sha(snapshot)
        and re.fullmatch("[0-9a-f]{64}", snapshot.get("source_snapshot_sha256", "")) is not None
        and (witness is None or snapshot == witness)
    )


def same_record_metadata(left, right):
    return all(left.get(key) == right.get(key) for key in (KEY, SNAPSHOT, RECORD_HASH))


def validate_execution(task, domain, chain=None):
    from . import authored_source_scope as authored

    reject_payload(task["payload"])
    chain = list(chain or authored.ancestry(task, domain))
    rows = [
        r
        for item in chain
        for r in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (item["id"], EVENT)
        )
    ]
    result, source = task["result"], task["result"].get(authored.SNAPSHOT, {})
    marker, witness = result.get(KEY), result.get(SNAPSHOT)
    if marker is None and witness is None and source.get(KEY) is None and not rows:
        if any(
            any(key in record for key in (KEY, SNAPSHOT, RECORD_HASH))
            for record in result.get(authored.RECEIPTS, {}).values()
        ):
            invalid("旧任务不能借新回执静默升级观察模板。")
        return LEGACY_VERSION
    if (
        marker != VERSION
        or not isinstance(witness, dict)
        or witness.get("version") != VERSION
        or source.get(KEY) != VERSION
    ):
        invalid("来源观察模板、来源见证或独立事件缺失，不能降级读取旧请求。")
    origin = next((item for item in chain if item["id"] == witness.get("task_id")), None)
    if origin is None:
        invalid("来源观察执行模板不属于本次任务来源链。")
    original_source = origin["result"].get(authored.SNAPSHOT, {})
    if (
        origin["result"].get(KEY) != VERSION
        or origin["result"].get(SNAPSHOT) != witness
        or witness.get("payload_sha256") != authored.sha(origin["payload"])
        or witness.get("source_snapshot_sha256") != authored.sha(original_source)
        or witness.get("source_policy_version") != authored.CURRENT_VERSION
        or witness.get("novel_id") != task["novel_id"]
        or witness.get("branch_id") != task["branch_id"]
        or witness.get("kind") != task["kind"]
        or original_source != source
    ):
        invalid("来源观察模板与原话、身份或原创建见证不一致。")
    original_events = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (origin["id"], EVENT)
    )
    expected = {
        KEY: VERSION,
        "snapshot_sha256": authored.sha(witness),
        "payload_sha256": authored.sha(origin["payload"]),
    }
    if len(original_events) != 1 or json.loads(original_events[0]["payload"]) != expected:
        invalid("来源观察执行模板的独立创建事件无法核对。")
    legacy = witness.get("legacy_receipts")
    if not isinstance(legacy, dict):
        invalid("执行模板旧回执保留范围缺失。")
    records = result.get(authored.RECEIPTS, {})
    for key, checksum in legacy.items():
        if key not in records or authored.sha(records[key]) != checksum:
            invalid("明确恢复之前的其它旧回执已改变，不能按新模板重解释。")
    for key, record in records.items():
        if key not in legacy and not record_valid(record, witness):
            invalid("本章来源记录与冻结观察执行模板不一致。")
        observation = record.get("phases", {}).get("observe", {}).get("response")
        if (
            key not in legacy
            and observation is not None
            and (not same_record_metadata(record, observation) or not record_valid(observation, witness))
        ):
            invalid("已返回来源观察与本章冻结执行模板不一致。")
    if witness.get("creation") == "explicit_retry" or RETRY_KEY in result:
        from .author_source_observation_retry import validate_task

        validate_task(task, domain)
    return VERSION


def ensure_runtime(task, domain, settings):
    version = validate_execution(task, domain)
    if task["result"].get(SNAPSHOT, {}).get("creation") == "explicit_retry" or RETRY_KEY in task["result"]:
        from .author_source_observation_retry import ensure_runtime as ensure_retry

        ensure_retry(task, domain, settings)
    return version


def inventory_issues_detailed(value, token, choices):
    """Exactly the legacy invalid decision, with source-local ids rather than story text."""
    from . import workflow_plan_recipient_scope as core

    errors = core.inventory_issues(value, token, choices)
    if not errors:
        return errors
    indexed = {row["observation_id"]: row for row in value["observations"]}
    named = [
        f"O={row['observation_id']}, evidence={row['evidence']}, restriction_evidence={row['restriction_evidence'] or '空'}"
        for row in value["observations"]
        if row["kind"] == "named_destination" and (row["restriction_evidence"] or row["condition"])
    ]
    coverage = [
        f"unit={row['source_id']}→O={identifier}, evidence={indexed[identifier]['evidence']}, restriction_evidence={indexed[identifier]['restriction_evidence'] or '空'}"
        for row in value["unit_checks"]
        for identifier in row["observation_ids"]
        if identifier in indexed
        and row["source_id"]
        not in (indexed[identifier]["evidence"], indexed[identifier]["restriction_evidence"])
    ]
    return [
        message
        + (
            " 定位：" + "；".join(named)
            if "普通命名目的地不能补造" in message and named
            else " 定位：" + "；".join(coverage)
            if "单元覆盖必须对应" in message and coverage
            else ""
        )
        for message in errors
    ]
