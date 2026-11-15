"""Versioned source-only interpretation guidance; never normalize a failed reply."""

from copy import deepcopy
import hashlib
import json
import re

from .db import DomainError, dumps

KEY = "workflow_plan_source_roles_version"
VERSION = "workflow-plan-source-roles/1"
CREATION_KEY = "workflow_plan_source_roles_creation"
CREATION_VERSION = "workflow-plan-source-roles-creation/1"
CREATION_EVENT = "workflow_plan_source_roles_created"


def sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def source_payload(payload):
    return {key: value for key, value in payload.items() if key not in (KEY, CREATION_KEY)}


def replay_payload(domain, payload, *, previous=None):
    """Restore only an existing nonce's own server fields, never the new default."""
    if previous is None and payload.get("client_request_id"):
        rows = domain.db.all("SELECT task_id FROM task_requests WHERE key=?", (payload["client_request_id"],))
        if rows:
            previous = domain.task(rows[0]["task_id"])
    if previous is None or previous["kind"] != "workflow":
        if KEY in payload or CREATION_KEY in payload:
            raise DomainError("plan_source_roles_version", "新建请求不能自行携带服务器写前政策见证。", 409)
        return dict(payload)
    saved = previous["payload"]
    for key in (KEY, CREATION_KEY):
        if key in payload and (key not in saved or payload[key] != saved[key]):
            raise DomainError("idempotency", "该提交标识的冻结写前政策不同。", 409)
    validate_task(previous, domain)
    return {
        **source_payload(payload),
        **{key: deepcopy(saved[key]) for key in (KEY, CREATION_KEY) if key in saved},
    }


def inherit_payload(current, payload):
    """Continuations retain server metadata; absence never activates a policy."""
    saved = current["payload"]
    for key in (KEY, CREATION_KEY):
        if key in payload and (key not in saved or payload[key] != saved[key]):
            raise DomainError("plan_source_roles_version", "续跑不能更换或注入写前政策来源。", 409)
    return {
        **source_payload(payload),
        **{key: deepcopy(saved[key]) for key in (KEY, CREATION_KEY) if key in saved},
    }


def freeze_creation(task_id, novel_id, branch_id, payload, initial):
    witness = {
        "version": CREATION_VERSION,
        "task_id": task_id,
        "novel_id": novel_id,
        "branch_id": branch_id,
        "guidance_version": VERSION,
        "source_payload_sha256": sha(source_payload(payload)),
    }
    return (
        {**payload, KEY: VERSION, CREATION_KEY: deepcopy(witness)},
        {**initial, KEY: VERSION, CREATION_KEY: deepcopy(witness)},
    )


def creation_event(db, tx, task_id, payload, initial):
    db.event(
        tx,
        task_id,
        CREATION_EVENT,
        {KEY: VERSION, CREATION_KEY: deepcopy(initial[CREATION_KEY]), "payload_sha256": sha(payload)},
    )


def ancestors(domain, task):
    """Read ancestry only; old missing history without policy stays legacy."""
    seen = set()
    current = task
    while current["id"] not in seen:
        seen.add(current["id"])
        yield current
        parent = current["result"].get("continued_from")
        if not isinstance(parent, str) or not parent:
            return
        try:
            current = domain.task(parent)
        except DomainError:
            return
        if current["kind"] != "workflow" or current["branch_id"] != task["branch_id"]:
            return


def validate_creation(task, domain, witness, restart):
    if (
        domain is None
        or not isinstance(witness, dict)
        or set(witness)
        != {"version", "task_id", "novel_id", "branch_id", "guidance_version", "source_payload_sha256"}
    ):
        raise DomainError("plan_source_roles_version", "全新写前政策创建见证无法核实。", 409)
    if (
        witness["version"] != CREATION_VERSION
        or witness["guidance_version"] != VERSION
        or witness["novel_id"] != task["novel_id"]
        or witness["branch_id"] != task["branch_id"]
        or not isinstance(witness["task_id"], str)
        or not isinstance(witness["source_payload_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", witness["source_payload_sha256"]) is None
    ):
        raise DomainError("plan_source_roles_version", "创建见证的目标或版本不一致。", 409)
    origin = next(
        (ancestor for ancestor in ancestors(domain, task) if ancestor["id"] == witness["task_id"]), None
    )
    if (
        origin is None
        or origin["kind"] != "workflow"
        or origin["payload"].get(KEY) != VERSION
        or origin["result"].get(KEY) != VERSION
        or origin["payload"].get(CREATION_KEY) != witness
        or origin["result"].get(CREATION_KEY) != witness
        or sha(source_payload(origin["payload"])) != witness["source_payload_sha256"]
    ):
        raise DomainError(
            "plan_source_roles_version", "创建来源不属于本分支续跑链，或原冻结输入已改变。", 409
        )
    rows = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind=?", (origin["id"], CREATION_EVENT)
    )
    expected = {KEY: VERSION, CREATION_KEY: witness, "payload_sha256": sha(origin["payload"])}
    try:
        matching = len(rows) == 1 and json.loads(rows[0]["payload"]) == expected
    except ValueError, TypeError:
        matching = False
    if not matching:
        raise DomainError("plan_source_roles_version", "原创建事件缺失或与冻结见证不一致。", 409)
    if task["id"] != origin["id"] and domain.db.all(
        "SELECT id FROM task_events WHERE task_id=? AND kind=?", (task["id"], CREATION_EVENT)
    ):
        raise DomainError("plan_source_roles_version", "本任务自身创建事件与继承来源混用，未派发。", 409)
    if task["id"] == origin["id"] and (restart or task["result"].get("continued_from")):
        raise DomainError("plan_source_roles_version", "全新创建不能同时伪称续跑或显式重做。", 409)
    if restart:
        receipt = task["result"]["plan_preparation_restart"]
        source = receipt.get("source_task_id")
        if (
            not isinstance(source, str)
            or source == task["id"]
            or not any(
                ancestor["result"].get("continued_from") == source
                and ancestor["result"].get("plan_preparation_restart") == receipt
                for ancestor in ancestors(domain, task)
            )
        ):
            raise DomainError("plan_source_roles_version", "创建来源与显式重做来源混用，未派发。", 409)


GUIDANCE = (
    "先辨认本项原动作，再解释传递字段。许可、禁止和仅可采取的动作／物品范围，不等于实际资料接收范围。"
    "‘只允许乙另抄副本带在身上’保留许可对象、另抄与携带、原件／副本区别及原‘只’；"
    "不能因此编造许可者向乙交付副本。该许可事项无交换时transfer_scope=none，"
    "supplier、recipient、transfer_object、direction留空，recipient_scope=unspecified；许可对象仍在object／result中保留。"
    "若原话另有明确的信息、许可言语或实物传递，须单独按该传递本身的内容、不同双方及阶段解释，"
    "不能把被许可行动的执行者冒充所涉实物的接收者。只有来源确实限制某次传递的接收范围，才填recipient_scope=only；"
    "真正仅向某方交付／尝试／计划、否定及禁止外传仍须保留，不准见‘只’就清空真实限制。"
    "transfer_scope=none不表示所有带‘只’的限制都不存在；原许可／禁止动作、对象、结果、阶段与否定须原样保留。"
    "修本次定位错误时不要无据改动其他事项的阶段；已寄出不因回执未到改成仅尝试寄出。"
    "首次AI解释未报错不等于原话真值；若此前解释确实误读，允许依据同一原话纠正，保留差异供独立逐项核对。"
    "不读取候选、旧正文核对、作者提交原因或未来规划；真未明保持歧义，不靠补造交付方或改原动作通过。"
)

REPAIR_GUIDANCE = (
    "本阶段没有正文。仅依据给定章纲逐项修正定位到的字段一致性，引用有效AUTHOR编号。"
    "许可／禁止中的‘只’不自动表示资料only接收范围；无传递时不能为凑齐only而臆造supplier、transfer_object或交付阶段。"
    "原许可对象、动作、结果和‘只／仅’、否定仍保留；真实接收范围限制继续按严格守卫核对，不能清空来绕过。"
    "不要为修另一项而无据改变其余事项的阶段及事件。附带的先前字段不表示原话真值或独立语义通过；"
    "此前解释若误读，允许据同一原话纠正，不硬锁第一份AI输出。修正差异交后续独立全P核对，有真实来源疑问保留未知。"
)


def validate_task(task, domain=None):
    payload, result = task["payload"], task["result"]
    receipt = result.get("plan_preparation_restart", {})
    request = receipt.get("request", {}) if isinstance(receipt, dict) else {}
    restart = isinstance(receipt, dict) and (KEY in receipt or (isinstance(request, dict) and KEY in request))
    creation = CREATION_KEY in payload or CREATION_KEY in result
    # Independent creation events still expose deleted metadata, including on a
    # child whose continued_from chain retains its origin. Nothing is upgraded.
    if not creation and domain is not None and task["kind"] == "workflow":
        creation = any(
            domain.db.all(
                "SELECT id FROM task_events WHERE task_id=? AND kind=?", (ancestor["id"], CREATION_EVENT)
            )
            for ancestor in ancestors(domain, task)
        )
    if KEY not in payload and KEY not in result and not restart and not creation:
        return False
    if (
        payload.get(KEY) != VERSION
        or result.get(KEY) != VERSION
        or task["kind"] != "workflow"
        or payload.get("mode") != "automatic"
        or payload.get("plot_contract_check") is not True
        or result.get("workflow_plan_open_details_version") != "workflow-plan-open-details/1"
        or result.get("workflow_plan_preparation_version") != "workflow-plan-preparation/1"
        or result.get("plot_contract_version") != "author-plot-audit/2"
    ):
        raise DomainError(
            "plan_source_roles_version", "写前来源角色解释版本不完整或无法识别，未派发或降级。", 409
        )
    if restart and (
        not isinstance(receipt, dict)
        or receipt.get("version") != "workflow-plan-restart/1"
        or receipt.get(KEY) != VERSION
        or not isinstance(request, dict)
        or request.get(KEY) != VERSION
    ):
        raise DomainError("plan_source_roles_version", "显式重做来源的冻结写前政策不一致。", 409)
    if creation:
        witness = payload.get(CREATION_KEY)
        if CREATION_KEY not in payload or CREATION_KEY not in result or result[CREATION_KEY] != witness:
            raise DomainError("plan_source_roles_version", "创建政策见证不完整，未降级。", 409)
        validate_creation(task, domain, witness, restart)
    elif not restart:
        raise DomainError("plan_source_roles_version", "缺少独立创建或显式重做政策来源，未派发。", 409)
    return True


def check_row(task, row, domain=None):
    enabled = validate_task(task, domain)
    if (KEY in row) != enabled or (enabled and row[KEY] != VERSION):
        raise DomainError("plan_source_roles_version", "本章写前来源角色解释与冻结任务版本不一致。", 409)
    return enabled


class RepairState:
    """Record repairs for independent source review; first proposals are not truth."""

    def __init__(self, source, validator):
        self.source, self.validator = source, validator
        self.previous = None
        self.proposal_count = 0
        self.changes = []
        self.unflagged = []

    def validate(self, value):
        self.proposal_count += 1
        items = value["items"]
        if self.previous is not None:
            for index in range(max(len(self.previous), len(items))):
                before = self.previous[index] if index < len(self.previous) else {}
                after = items[index] if index < len(items) else {}
                fields = [
                    key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
                ]
                if fields:
                    self.changes.append(
                        {
                            "proposal": self.proposal_count,
                            "item_id": f"P{index + 1:02}",
                            "previous": {key: deepcopy(before.get(key)) for key in fields},
                            "current": {key: deepcopy(after.get(key)) for key in fields},
                        }
                    )
        issues, unflagged = [], []
        for index, item in enumerate(items, 1):
            item_id = f"P{index:02}"
            problems = self.validator({"items": [item], "note": value["note"]}, self.source)
            issues.extend(
                f"{item_id}（动作={item['action']}；transfer_scope={item['transfer_scope']}；recipient_scope={item['recipient_scope']}）：{problem}"
                for problem in problems
            )
            if not problems:
                unflagged.append({"item_id": item_id, **deepcopy(item)})
        self.previous = deepcopy(items)
        self.unflagged = unflagged
        return issues

    def feedback(self):
        return {
            "写前来源字段修正要求": REPAIR_GUIDANCE,
            "先前字段核对未报错的AI提案（不是原话真值）": deepcopy(self.unflagged),
            "解释修正差异（待按同一原话独立核对）": deepcopy(self.changes),
        }

    def receipt(self):
        return {"version": VERSION, "proposal_count": self.proposal_count, "changes": deepcopy(self.changes)}
