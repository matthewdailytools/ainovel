"""Read-only conversation bound to a saved chapter or an adopted setting."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .db import DomainError, dumps
from .models import Input

KIND = "workspace_chat"
ACTION = "workspace.chat"
VERSION = "workspace-chat/1"
SOURCE_LIMIT = 12_000
HISTORY_LIMIT = 4
SETTING_LABELS = {
    "world": "世界设定",
    "characters": "人物设定",
    "outline": "故事大纲",
    "intent": "创作约定",
    "reader": "读者与题材",
}
SettingKind = Literal["world", "characters", "outline", "intent", "reader"]


class WorkspaceChatInput(Input):
    chapter_id: str | None = Field(default=None, min_length=1, max_length=128)
    setting_kind: SettingKind | None = None
    base_revision: int | None = Field(default=None, ge=0)
    base_artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    account: str = Field(min_length=1, max_length=128)
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    reply_length: Literal["concise", "standard"] = "standard"
    client_request_id: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.chapter_id:
            if self.setting_kind or self.base_revision is None or self.base_artifact_id is not None:
                raise ValueError("章稿对话须指定当前保存修订。")
        elif self.setting_kind is None or self.base_revision is not None:
            raise ValueError("请选择一个设定类别。")
        return self


class WorkspaceChatSource(Input):
    title: str
    chapter_revision: int | None
    artifact_id: str | None
    chars: int
    sent_chars: int
    sha256: str


class WorkspaceChatTurn(Input):
    id: str
    status: str
    error: str
    question: str
    reply: str | None
    source: WorkspaceChatSource


class WorkspaceChatConversation(Input):
    source: WorkspaceChatSource
    turns: list[WorkspaceChatTurn]


def source(tx, branch_id, chapter_id, setting_kind):
    artifact_id, revision = None, None
    if chapter_id:
        row = tx.execute(
            "SELECT title,revision,content FROM chapters WHERE id=? AND branch_id=? AND archived=0",
            (chapter_id, branch_id),
        ).fetchone()
        if not row:
            raise DomainError("not_found", "当前工作版本没有此章。", 404)
        title, revision, text = row["title"], row["revision"], row["content"]
    else:
        title = SETTING_LABELS[setting_kind]
        row = tx.execute(
            "SELECT id,payload FROM artifacts WHERE branch_id=? AND kind=? AND chapter_id IS NULL "
            "AND status='accepted' ORDER BY created DESC,id DESC LIMIT 1",
            (branch_id, setting_kind),
        ).fetchone()
        text = ""
        if row:
            artifact_id = row["id"]
            payload = json.loads(row["payload"])
            fields = {
                "intent": ("text", "author_text", "intent_contract"),
                "reader": ("text", "audience", "genres", "mechanisms", "author_exceptions"),
            }.get(setting_kind, ("text",))
            values = {key: payload[key] for key in fields if payload.get(key)}
            text = dumps(values) if fields != ("text",) and values else values.get("text", "")
    cropped = (
        {"开头": text[: SOURCE_LIMIT // 2], "末尾": text[-SOURCE_LIMIT // 2 :]}
        if len(text) > SOURCE_LIMIT
        else text
    )
    basis = {
        "title": title,
        "chapter_revision": revision,
        "artifact_id": artifact_id,
        "chars": len(text),
        "sent_chars": min(len(text), SOURCE_LIMIT),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    return basis, cropped


def scoped_turns(tx, branch_id, chapter_id, setting_kind, statuses, limit):
    key, value = ("chapter_id", chapter_id) if chapter_id else ("setting_kind", setting_kind)
    return tx.execute(
        "SELECT id,status,error,payload,result FROM tasks WHERE branch_id=? AND kind=? "
        "AND json_extract(payload,?)=? AND status IN (SELECT value FROM json_each(?)) "
        "ORDER BY created DESC,id DESC LIMIT ?",
        (branch_id, KIND, f"$.{key}", value, dumps(statuses), limit),
    ).fetchall()


def turn_view(row):
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
    return {
        "id": row["id"],
        "status": row["status"],
        "error": row["error"],
        "question": payload["question"],
        "reply": result.get("workspace_chat_reply") if row["status"] == "succeeded" else None,
        "source": result["workspace_chat_snapshot"]["source"],
    }


def freeze(tx, payload):
    branch_id, chapter_id, setting_kind = (
        payload["branch_id"],
        payload["chapter_id"],
        payload["setting_kind"],
    )
    basis, _ = source(tx, branch_id, chapter_id, setting_kind)
    if (
        basis["chapter_revision"] != payload["base_revision"]
        or basis["artifact_id"] != payload["base_artifact_id"]
    ):
        raise DomainError("chat_source_changed", "对话依据已更新，请重新读取当前保存版本。", 409)
    if scoped_turns(tx, branch_id, chapter_id, setting_kind, ["queued", "running"], 1):
        raise DomainError("chat_pending", "当前条目的对话还在进行，请等回复或取消后再发送。", 409)
    recent = scoped_turns(tx, branch_id, chapter_id, setting_kind, ["succeeded"], 20)
    same_version = [turn for turn in map(turn_view, recent) if turn["source"] == basis][:HISTORY_LIMIT]
    return {
        "version": VERSION,
        "source": basis,
        "prior_turn_ids": [turn["id"] for turn in reversed(same_version)],
    }


async def run(worker):
    task = worker.task
    payload = task["payload"]
    snapshot = task["result"].get("workspace_chat_snapshot")
    if not snapshot or snapshot["version"] != VERSION:
        raise DomainError("chat_version", "无法核对对话依据版本。", 409)
    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        basis, text = source(tx, task["branch_id"], payload["chapter_id"], payload["setting_kind"])
        if basis != snapshot["source"]:
            raise DomainError("chat_source_changed", "排队期间正文或设定已更新，未发送旧版本。", 409)
        history = []
        for turn_id in snapshot["prior_turn_ids"]:
            previous = tx.execute(
                "SELECT id,status,error,payload,result FROM tasks WHERE id=? AND branch_id=? "
                "AND kind=? AND status='succeeded'",
                (turn_id, task["branch_id"], KIND),
            ).fetchone()
            if not previous:
                continue
            prior_payload = json.loads(previous["payload"])
            turn = turn_view(previous)
            if (
                turn["source"] == basis
                and prior_payload["chapter_id"] == payload["chapter_id"]
                and prior_payload["setting_kind"] == payload["setting_kind"]
                and turn["reply"]
            ):
                history.append({"作者": turn["question"][:1600], "AI": turn["reply"][:1600]})
    answer = await worker.call(
        {
            "action": ACTION,
            "对话模板版本": VERSION,
            "问题": payload["question"],
            "回复篇幅": "简短，只给直接相关的要点"
            if payload["reply_length"] == "concise"
            else "按问题需要展开",
            "要求": (
                "用简体中文回答本次作者问题。当前保存内容和历史消息仅供参考。"
                "指出依据不足之处，建议与已采用设定或已发生剧情分开。"
                "不要宣称已改动正文、设定或其他内容。"
            ),
        },
        context={
            "当前对象": basis["title"],
            "当前版本": basis,
            "当前保存内容": text,
            "节录范围": "开头和末尾，中间已省略" if basis["sent_chars"] < basis["chars"] else "完整",
            "同版本此前对话": history,
        },
        max_output=700 if payload["reply_length"] == "concise" else 1500,
    )
    answer = answer.strip()
    if not 0 < len(answer) <= 12_000:
        raise DomainError("chat_output", "模型回复为空或超出可显示范围，请查看任务记录。")
    worker.checkpoint({**task["result"], "workspace_chat_reply": answer}, "succeeded")


def register_routes(app, domain, provider_ready):
    path = "/api/branches/{branch_id}/workspace-chat"

    @app.get(path, response_model=WorkspaceChatConversation)
    def conversation(branch_id: str, chapter_id: str | None = None, setting_kind: SettingKind | None = None):
        if bool(chapter_id) == bool(setting_kind):
            raise DomainError("chat_scope", "请选择一个章节或设定类别。")
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            current, _ = source(tx, branch_id, chapter_id, setting_kind)
            rows = scoped_turns(
                tx,
                branch_id,
                chapter_id,
                setting_kind,
                ["queued", "running", "succeeded", "cancelled", "blocked_runtime"],
                24,
            )
            return {"source": current, "turns": [turn_view(row) for row in reversed(rows)]}

    @app.post(path, response_model=WorkspaceChatTurn, status_code=202)
    def send(branch_id: str, data: WorkspaceChatInput):
        branch = domain.branch(branch_id)
        payload = {**data.model_dump(), "branch_id": branch_id, "novel_id": branch["novel_id"]}
        if previous := domain.find_request(KIND, payload):
            return turn_view(previous)
        provider_ready(data.account)
        return turn_view(domain.queue(KIND, payload))
