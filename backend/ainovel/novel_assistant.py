"""One local conversation per work version, with explicitly bound turn sources."""

from hashlib import sha256
import json

from fastapi import Query
from pydantic import Field, field_validator

from .assistant import RequestId, Stage, TARGETS, _utf8, _within_limit
from .assistant_context import _bounded_evidence, snapshot, turn_sources
from .db import DomainError, dumps, uid
from .models import Input
from .project_portability import Identifier

KIND = "novel_assistant_turn"
VERSION = "novel-assistant/1"
ACTION = "novel_assistant.turn"
KEY = "novel_assistant"
RESULT = "novel_assistant_result"
SCHEMA = """
CREATE TABLE IF NOT EXISTS novel_assistant_workspaces(
 branch_id TEXT PRIMARY KEY REFERENCES branches(id),revision INTEGER NOT NULL DEFAULT 0,
 draft_text TEXT NOT NULL DEFAULT '',draft_revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS novel_assistant_turns(
 id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES novel_assistant_workspaces(branch_id),
 sequence INTEGER NOT NULL,task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
 UNIQUE(branch_id,sequence));
"""


class NovelAssistantScope(Input):
    stage: Stage
    scope_id: Identifier | None = None


class NovelAssistantReference(NovelAssistantScope):
    scope_label: str


class NovelAssistantEditDraft(Input):
    text: str = Field(max_length=6000)
    base_revision: int = Field(ge=0, strict=True)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        return _utf8(value)


class NovelAssistantSendTurn(NovelAssistantScope):
    base_revision: int = Field(ge=0, strict=True)
    question: str = Field(min_length=1, max_length=6000)
    account: Identifier
    client_request_id: RequestId
    include_current_scope: bool = Field(default=True, strict=True)
    references: list[NovelAssistantScope] = Field(default_factory=list, max_length=5)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value):
        if not value.strip():
            raise ValueError("请先填写想讨论的问题。")
        return _utf8(value)


class NovelAssistantDraft(Input):
    text: str
    revision: int


class ProposedCard(Input):
    destination: str = Field(min_length=2, max_length=3)
    field: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(min_length=1, max_length=2000)


class ProposedReply(Input):
    answer: str = Field(min_length=1, max_length=12_000)
    cards: list[ProposedCard] = Field(max_length=6)


class NovelAssistantCard(NovelAssistantScope):
    id: Identifier
    field: str
    title: str
    value: str
    reason: str


class NovelAssistantReply(Input):
    answer: str
    cards: list[NovelAssistantCard]


class NovelAssistantTurn(NovelAssistantReference):
    id: Identifier
    sequence: int
    question: str
    status: str
    error: str
    task_id: Identifier
    result: NovelAssistantReply | None
    include_current_scope: bool
    references: list[NovelAssistantReference]


class NovelAssistantWorkspace(Input):
    revision: int
    draft: NovelAssistantDraft
    turns: list[NovelAssistantTurn]
    next_before: int | None


class NovelAssistantQueuedTurn(Input):
    revision: int
    turn: NovelAssistantTurn


_SELECT_TURN = (
    "SELECT a.id,a.sequence,a.task_id,t.status,t.error,t.payload,t.result "
    "FROM novel_assistant_turns a JOIN tasks t ON t.id=a.task_id "
)


def _workspace(tx, branch_id):
    row = tx.execute(
        "SELECT revision,draft_text,draft_revision FROM novel_assistant_workspaces WHERE branch_id=?",
        (branch_id,),
    ).fetchone()
    if row:
        return row["revision"], {"text": row["draft_text"], "revision": row["draft_revision"]}
    if not tx.execute("SELECT 1 FROM branches WHERE id=?", (branch_id,)).fetchone():
        raise DomainError("branch_missing", "找不到工作版本。", 404)
    return 0, {"text": "", "revision": 0}


def _ensure_workspace(tx, branch_id):
    tx.execute("INSERT OR IGNORE INTO novel_assistant_workspaces(branch_id) VALUES(?)", (branch_id,))


def _turn(row):
    payload = json.loads(row["payload"])
    result = json.loads(row["result"])
    frozen = result[KEY]
    reply = result.get(RESULT) if row["status"] == "succeeded" else None
    if reply:
        reply = {
            "answer": reply["answer"],
            "cards": [{key: card[key] for key in NovelAssistantCard.model_fields} for card in reply["cards"]],
        }
    return {
        **{key: row[key] for key in ("id", "sequence", "task_id", "status", "error")},
        **{key: payload[key] for key in ("stage", "scope_id", "question", "include_current_scope")},
        "scope_label": frozen["destinations"]["D1"]["scope_label"],
        "references": [
            {field: frozen["destinations"][key][field] for field in NovelAssistantReference.model_fields}
            for key in frozen["references"]
        ],
        "result": reply,
    }


def destination_basis(tx, branch_id, stage, scope_id):
    """The exact tracked object basis, shared with delivery preview."""
    return snapshot(tx, branch_id, stage, scope_id)["sha256"]


def read_card(tx, branch_id, turn_id, card_id):
    """Look up a locally issued card and the scope basis that authorized it."""
    row = tx.execute(_SELECT_TURN + "WHERE a.branch_id=? AND a.id=?", (branch_id, turn_id)).fetchone()
    if row and row["status"] == "succeeded":
        for card in json.loads(row["result"]).get(RESULT, {}).get("cards", []):
            if card["id"] == card_id:
                return card
    raise DomainError("novel_assistant_card", "该工作版本找不到这张待用卡。", 404)


def _source_content(tx, branch_id, stage, scope_id, basis):
    sources = [
        _bounded_evidence(source)
        for source in basis["sources"]
        if source["kind"] not in {"reader", "intent"} and (stage != "M3" or source["kind"] == "settings")
    ]
    if stage == "M2" and scope_id:
        sources.extend(turn_sources(tx, branch_id, stage, scope_id, {}))
    checks = basis["checks"]
    if stage == "M7":
        checks = [{key: row[key] for key in ("id", "title", "status")} for row in checks]
    return {"资料": sources, "检查": checks}


def _digest(value):
    return sha256(dumps(value).encode()).hexdigest()


def _collect(tx, branch_id, payload):
    """Read the current scope plus only explicitly included material in one transaction."""
    from .novel_assistant_delivery import delivery_destinations

    current = (payload["stage"], payload["scope_id"])
    requested = list(dict.fromkeys((ref["stage"], ref["scope_id"]) for ref in payload["references"]))
    destinations, reference_content, keys = {}, {}, {}
    fields_by_stage = {}
    included = set(requested)
    if payload["include_current_scope"]:
        included.add(current)
    for index, (stage, scope_id) in enumerate(dict.fromkeys([current, *requested]), start=1):
        key = f"D{index}"
        keys[(stage, scope_id)] = key
        basis = snapshot(tx, branch_id, stage, scope_id)
        if stage not in fields_by_stage:
            fields_by_stage[stage] = {
                option["scope_id"]: [field["id"] for field in option["fields"]]
                for option in delivery_destinations(tx, branch_id, stage)
            }
        destinations[key] = {
            "stage": stage,
            "scope_id": scope_id,
            "scope_label": basis["scope_label"],
            "basis_sha256": basis["sha256"],
            "fields": {name: TARGETS[stage][name] for name in fields_by_stage[stage].get(scope_id, [])},
        }
        if (stage, scope_id) in included:
            reference_content[key] = _source_content(tx, branch_id, stage, scope_id, basis)
    return destinations, [keys[ref] for ref in requested], reference_content


def _history(tx, branch_id, revision):
    rows = tx.execute(
        "SELECT t.result,t.payload FROM novel_assistant_turns a JOIN tasks t ON t.id=a.task_id "
        "WHERE a.branch_id=? AND a.sequence<=? AND t.status='succeeded' ORDER BY a.sequence DESC LIMIT 6",
        (branch_id, revision),
    )
    recent = []
    for row in rows:
        prior = json.loads(row["payload"])
        if reply := json.loads(row["result"]).get(RESULT):
            recent.append(
                {"工作台": prior["stage"], "问题": prior["question"][:1200], "回答": reply["answer"][:1800]}
            )
    return list(reversed(_within_limit(recent, 18_000)))


def prepare_turn_tx(tx, branch_id, payload):
    revision, _ = _workspace(tx, branch_id)
    if revision != payload["base_revision"]:
        raise DomainError("novel_assistant_changed", "对话已在其他窗口更新，请刷新后发送。", 409)
    if tx.execute(
        "SELECT 1 FROM tasks WHERE branch_id=? AND kind=? AND status IN ('queued','running')",
        (branch_id, KIND),
    ).fetchone():
        raise DomainError("novel_assistant_pending", "上一轮仍在运行，请等待或取消。", 409)
    destinations, references, reference_content = _collect(tx, branch_id, payload)
    if len(dumps(reference_content).encode()) > 48_000:
        raise DomainError("novel_assistant_context_size", "本轮引用超过容量，请减少引用。", 422)
    return {
        "version": VERSION,
        "destinations": destinations,
        "references": references,
        "reference_sha256": _digest(reference_content),
        "history": _history(tx, branch_id, revision),
    }


def record_turn_tx(tx, branch_id, task_id, payload):
    _ensure_workspace(tx, branch_id)
    sequence = payload["base_revision"] + 1
    tx.execute(
        "INSERT INTO novel_assistant_turns(id,branch_id,sequence,task_id) VALUES(?,?,?,?)",
        (uid(), branch_id, sequence, task_id),
    )
    tx.execute(
        "UPDATE novel_assistant_workspaces SET revision=?,"
        "draft_revision=draft_revision+CASE WHEN draft_text=? THEN 1 ELSE 0 END,"
        "draft_text=CASE WHEN draft_text=? THEN '' ELSE draft_text END WHERE branch_id=?",
        (sequence, payload["question"], payload["question"], branch_id),
    )


def _ready_sources(worker, frozen):
    try:
        with worker.db.connect() as tx:
            tx.execute("BEGIN")
            destinations, references, content = _collect(tx, worker.task["branch_id"], worker.task["payload"])
    except DomainError as error:
        raise DomainError("novel_assistant_basis", "本轮对象或引用已改变，请核对后另发一轮。", 409) from error
    if (
        destinations != frozen["destinations"]
        or references != frozen["references"]
        or _digest(content) != frozen["reference_sha256"]
    ):
        raise DomainError("novel_assistant_basis", "本轮对象或引用已改变，请核对后另发一轮。", 409)
    return content


def _validated_reply(data, destinations):
    reply = ProposedReply.model_validate(data)
    _utf8(reply.answer)
    result, seen = [], set()
    if not reply.answer.strip():
        raise DomainError("novel_assistant_result", "助手回答缺少有效文字。", 422)
    for card in reply.cards:
        anchor = (card.destination, card.field)
        target = destinations.get(card.destination)
        limit = target["fields"].get(card.field) if target else None
        if anchor in seen:
            raise DomainError("novel_assistant_result", "同一去向和字段只能有一张完整的建议卡。", 422)
        if not limit or not card.value.strip() or len(card.value) > limit:
            raise DomainError(
                "novel_assistant_result", "建议字段不在本轮范围内，或建议文字超出字段容量。", 422
            )
        if not card.title.strip() or not card.reason.strip():
            raise DomainError("novel_assistant_result", "建议卡缺少标题或理由。", 422)
        values = card.model_dump(exclude={"destination"})
        for value in values.values():
            _utf8(value)
        seen.add(anchor)
        result.append(
            {
                "id": uid(),
                **{key: target[key] for key in ("stage", "scope_id", "basis_sha256")},
                **values,
            }
        )
    return {"answer": reply.answer, "cards": result}


async def run(worker):
    frozen = worker.task["result"][KEY]
    if frozen["version"] != VERSION:
        raise DomainError("novel_assistant_version", "请在当前版本的对话另发一轮。", 409)
    content = _ready_sources(worker, frozen)
    destinations = frozen["destinations"]
    allowed = {
        key: {"工作台": row["stage"], "允许字段及字符上限": row["fields"]}
        for key, row in destinations.items()
        if row["fields"]
    }
    contract = ProposedReply.model_json_schema()
    if allowed:
        properties = contract["$defs"]["ProposedCard"]["properties"]
        properties["destination"]["enum"] = list(allowed)
        properties["field"]["enum"] = sorted(
            {field for row in destinations.values() for field in row["fields"]}
        )
    else:
        contract["properties"]["cards"]["maxItems"] = 0
    instruction = {
        "action": ACTION,
        "契约": VERSION,
        "目的": "与作者继续同一工作版本的创作讨论。只有可明确交付的建议才生成去向卡，普通讨论可只返回 answer。",
        "输出": (
            "cards 每张使用本轮给出的 destination 和对应的 field 编号，逐字填写编号。"
            "同一去向及字段最多一张完整的建议，value 是可编辑的文字。"
            "章正文卡是待审稿件。校验卡只可提议复核理由，完本卡只可提议文字。"
            "不宣称已经保存、采用、签认或完结。向读者展示的简介不得泄露终局、真相等关键转折。"
        ),
        "可用去向": allowed,
        "问题": worker.task["payload"]["question"],
    }
    context = {"本轮引用": content, "同一工作版本近期对话": frozen["history"]}
    try:
        data = await worker.call(
            instruction, ProposedReply, context, 4096, structure_repairs=0, contract=contract
        )
        result = _validated_reply(data, destinations)
    except ValueError as error:
        raise DomainError("novel_assistant_result", "建议含有无法保留的文字，请另发一轮。", 422) from error
    worker.checkpoint({**worker.task["result"], RESULT: result}, "succeeded")


def register_routes(app, domain, provider_ready):
    endpoint = "/api/branches/{branch_id}/novel-assistant"

    @app.get(endpoint)
    def read_novel_assistant(
        branch_id: Identifier,
        before: int | None = Query(default=None, ge=1, le=2**63 - 1),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> NovelAssistantWorkspace:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            revision, draft = _workspace(tx, branch_id)
            rows = tx.execute(
                _SELECT_TURN + "WHERE a.branch_id=? AND a.sequence<? ORDER BY a.sequence DESC LIMIT ?",
                (branch_id, before if before is not None else revision + 1, limit + 1),
            ).fetchall()
        return {
            "revision": revision,
            "draft": draft,
            "turns": [_turn(row) for row in reversed(rows[:limit])],
            "next_before": rows[limit - 1]["sequence"] if len(rows) > limit else None,
        }

    @app.put(endpoint + "/draft")
    def save_novel_assistant_draft(
        branch_id: Identifier, data: NovelAssistantEditDraft
    ) -> NovelAssistantDraft:
        with domain.db.connect(True) as tx:
            _, draft = _workspace(tx, branch_id)
            if draft["revision"] != data.base_revision:
                raise DomainError(
                    "novel_assistant_draft_changed", "输入草稿已在其他窗口更新，请重新读取。", 409
                )
            if draft["text"] == data.text:
                return draft
            _ensure_workspace(tx, branch_id)
            tx.execute(
                "UPDATE novel_assistant_workspaces SET draft_text=?,draft_revision=draft_revision+1 "
                "WHERE branch_id=?",
                (data.text, branch_id),
            )
        return {"text": data.text, "revision": data.base_revision + 1}

    @app.post(endpoint + "/turns", status_code=202)
    def send_novel_assistant_turn(
        branch_id: Identifier, data: NovelAssistantSendTurn
    ) -> NovelAssistantQueuedTurn:
        branch = domain.branch(branch_id)
        payload = {**data.model_dump(), "novel_id": branch["novel_id"], "branch_id": branch_id}
        task = domain.find_request(KIND, payload)
        if task is None:
            provider_ready(data.account)
            task = domain.queue(KIND, payload)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            revision, _ = _workspace(tx, branch_id)
            row = tx.execute(_SELECT_TURN + "WHERE a.task_id=?", (task["id"],)).fetchone()
        return {"revision": revision, "turn": _turn(row)}
