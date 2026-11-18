"""Budgeted four-level outline proposals pinned to author-approved planning inputs."""

import json
from typing import Annotated

from pydantic import Field, StringConstraints

from .db import DomainError
from .models import Input, ModuleInput
from .outline_workspace import (
    Identifier,
    OutlineCardReference,
    OutlinePlan,
    WINDOW_CHAPTERS,
    _card_key,
    _chapter_plans,
    _check_base,
    _decode,
    _freeze_cards,
    _latest,
    _settings,
    _validate,
)
from .story_graph import graph_snapshot

CANDIDATE = "outline_generation_candidate"
ACTION = "生成四级大纲候选"


class GenerateOutline(Input):
    base_revision: int = Field(ge=0, strict=True)
    confirmation_id: Identifier | None = None
    account: Identifier
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000)] = ""
    client_request_id: str = Field(min_length=8, max_length=128)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reference_cards: list[OutlineCardReference] = Field(
        default_factory=list, max_length=8, exclude_if=lambda value: not value
    )


class OutlineGenerationTask(Input):
    id: str
    status: str
    error: str
    created: float
    artifact_id: str | None


class OutlineProposal(Input):
    id: str
    base_revision: int
    confirmation_id: str | None
    project_revision: int | None
    current: bool
    plan: OutlinePlan


def freeze_tx(tx, branch_id, request):
    latest = _latest(tx, branch_id)
    _check_base(latest, request["base_revision"])
    graph = graph_snapshot(tx, branch_id)
    chapters = [
        {key: chapter[key] for key in ("id", "title", "position")}
        for chapter in sorted(graph["chapters"], key=lambda row: row["position"])
        if not chapter["archived"]
    ]
    if not chapters:
        raise DomainError("outline_chapters", "请先在写作页建立章节目录，再生成章纲。")
    records = [
        {
            "id": row["id"],
            "kind": row["kind"],
            "revision": row["revision"],
            "title": row["payload"]["title"],
            "status": row["payload"]["stages"][-1]["status"],
        }
        for row in graph["records"]
        if row["kind"] == "promise" and not row["archived"]
    ]
    from .project_brief import confirmed_project_tx

    previous = _decode(latest)
    basis = _settings(tx, branch_id, request["confirmation_id"])
    if previous and (cards := previous["setting_basis"].get("reference_cards")):
        basis["reference_cards"] = cards
    references = request.get("reference_cards", [])
    if references:
        approved = _freeze_cards(tx, branch_id, references, basis)
        basis["reference_cards"] = list(
            {_card_key(card): card for card in [*basis.get("reference_cards", []), *approved]}.values()
        )
    return {
        "base_revision": request["base_revision"],
        "outline": previous["plan"] if previous else None,
        "setting_basis": basis,
        "project": confirmed_project_tx(tx, branch_id),
        "chapters": chapters,
        "promises": records,
        **({"reference_cards": references} if references else {}),
    }


def _current(tx, branch_id, frozen):
    try:
        latest = freeze_tx(
            tx,
            branch_id,
            {
                "base_revision": frozen["base_revision"],
                "confirmation_id": frozen["setting_basis"]["confirmation_id"],
                "reference_cards": frozen.get("reference_cards", []),
            },
        )
    except DomainError as error:
        if error.code not in {
            "outline_conflict",
            "outline_chapters",
            "conception_snapshot",
            "conception_changed",
            "outline_reference",
            "reference_card",
            "reference_card_incomplete",
        }:
            raise
        return False
    return latest == frozen


def _candidate_tx(tx, branch_id, identifier):
    row = tx.execute(
        "SELECT payload,basis FROM artifacts WHERE id=? AND branch_id=? AND kind=? AND status='accepted'",
        (identifier, branch_id, CANDIDATE),
    ).fetchone()
    if not row:
        raise DomainError("outline_candidate", "找不到此工作版本的大纲候选。", 404)
    return json.loads(row["payload"])["plan"], json.loads(row["basis"])["outline_generation"]


def check_candidate_tx(tx, branch_id, identifier, confirmation_id):
    _, frozen = _candidate_tx(tx, branch_id, identifier)
    if confirmation_id != frozen["setting_basis"]["confirmation_id"] or not _current(tx, branch_id, frozen):
        raise DomainError("outline_candidate_stale", "大纲候选的确认依据或目录已改变，请重新生成。", 409)
    return frozen["project"]["id"] if frozen["project"] else None


def _task_view(task):
    return {
        "id": task["id"],
        "status": task["status"],
        "error": task["error"],
        "created": task["created"],
        "artifact_id": task["result"].get("artifact_id"),
    }


def register_routes(app, domain, provider_ready):
    endpoint = "/api/branches/{branch_id}/outline-workspace/generation"

    @app.post(endpoint, status_code=202)
    def start(branch_id: str, data: GenerateOutline) -> OutlineGenerationTask:
        branch = domain.branch(branch_id)
        provider_ready(data.account)
        request = ModuleInput(
            action="outline.plan",
            novel_id=branch["novel_id"],
            branch_id=branch_id,
            account=data.account,
            instruction=data.instruction or "根据当前作品依据生成四级大纲候选。",
            client_request_id=data.client_request_id,
            budget_limit=data.budget_limit,
        ).model_dump(exclude_none=True)
        request["outline_generation"] = {
            "base_revision": data.base_revision,
            "confirmation_id": data.confirmation_id,
            **({"reference_cards": data.model_dump()["reference_cards"]} if data.reference_cards else {}),
        }
        return _task_view(domain.queue("module", request))

    @app.get(endpoint)
    def history(branch_id: str) -> list[OutlineGenerationTask]:
        domain.branch(branch_id)
        return domain.db.all(
            "SELECT id,status,error,created,json_extract(result,'$.artifact_id') AS artifact_id "
            "FROM tasks WHERE branch_id=? AND kind='module' AND json_type(payload,'$.outline_generation')='object' "
            "ORDER BY created DESC,id DESC LIMIT 25",
            (branch_id,),
        )

    @app.get(endpoint + "/{identifier}")
    def proposal(branch_id: str, identifier: str) -> OutlineProposal:
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            plan, frozen = _candidate_tx(tx, branch_id, identifier)
            return {
                "id": identifier,
                "base_revision": frozen["base_revision"],
                "confirmation_id": frozen["setting_basis"]["confirmation_id"],
                "project_revision": frozen["project"]["revision"] if frozen["project"] else None,
                "current": _current(tx, branch_id, frozen),
                "plan": plan,
            }


async def run(worker, request, frozen):
    with worker.domain.db.connect() as tx:
        tx.execute("BEGIN")
        if not _current(tx, request["branch_id"], frozen):
            raise DomainError("outline_candidate_stale", "排队后大纲依据或目录已改变，请重新提交。", 409)
    chapter_ids = [chapter["id"] for chapter in frozen["chapters"]]
    setting_ids = [item["id"] for item in frozen["setting_basis"]["items"]]
    promise_ids = [row["id"] for row in frozen["promises"]]
    graph = {
        "chapters": [{**chapter, "archived": False} for chapter in frozen["chapters"]],
        "records": [
            {**row, "archived": False, "payload": {"title": row["title"]}} for row in frozen["promises"]
        ],
    }
    schema = OutlinePlan.model_json_schema()
    definitions = schema["$defs"]
    chapter = definitions["ChapterPlan"]["properties"]
    chapter["chapter_id"]["enum"] = chapter_ids
    eligible = {_card_key(card) for card in frozen.get("reference_cards", [])}
    chapter["reference_cards"]["maxItems"] = len(eligible)
    if setting_ids:
        chapter["setting_ids"]["items"]["enum"] = setting_ids
    else:
        chapter["setting_ids"]["maxItems"] = 0
    if promise_ids:
        definitions["PromiseWindow"]["properties"]["promise_id"]["enum"] = promise_ids
    else:
        schema["properties"]["promises"]["maxItems"] = 0
    for window_kind in ("PromiseWindow", "NewPromisePlan"):
        for field in WINDOW_CHAPTERS:
            definitions[window_kind]["properties"][field]["enum"] = chapter_ids
    instruction = {
        "动作": ACTION,
        "作者本次要求": request["instruction"],
        "已确认立项": frozen["project"],
        "已确认世界设定": frozen["setting_basis"]["items"],
        "作者已保存大纲": frozen["outline"],
        "章节目录": frozen["chapters"],
        "可用伏笔": frozen["promises"],
        "要求": (
            "生成总纲、卷纲、单元纲和章纲，尊重作者原话、已确认终局、已采用设定和已保存大纲。"
            "逐章安排目标、阻碍、转折和结果，每格用一两句。章纲按目录顺序覆盖全部章节，各一次。"
            "卷与单元的 id 使用互不相同的简短字母数字编号。"
            "按需引用现有设定与伏笔编号。缺少合适记录时，在 new_promises 提议新伏笔，填写唯一标题和计划说明。"
            "新旧伏笔窗口均须使用目录章号，按埋设、进入回收窗口、最迟回收排列。"
            "这是规划候选，不能把计划写成已发生的事实。"
        ),
    }
    if eligible:
        instruction["获准采用的手法卡"] = [
            {key: value for key, value in card.items() if key != "source_title"}
            for card in frozen["setting_basis"]["reference_cards"]
            if _card_key(card) in eligible
        ]
        instruction["要求"] += "可按章纲需要选用获准手法卡，只照填其三个编号字段，替换原作人物、场景和道具。"
    preserved = {
        row["chapter_id"]: row["reference_cards"]
        for _, _, row in _chapter_plans(frozen["outline"] or {"volumes": []})
        if row.get("reference_cards")
    }
    for attempt in range(2):
        value = await worker.call(
            instruction, schema=OutlinePlan, context={}, contract=schema, max_output=8192
        )
        plan = OutlinePlan.model_validate(value).model_dump()
        try:
            for _, _, row in _chapter_plans(plan):
                chosen = row.get("reference_cards", [])
                if not {_card_key(ref) for ref in chosen} <= eligible:
                    raise DomainError("outline_reference", "只可选用获准的手法卡修订。")
                saved = preserved.get(row["chapter_id"], [])
                if saved:
                    selected = {(ref["session_id"], ref["card_id"]) for ref in saved}
                    row["reference_cards"] = [
                        *saved,
                        *(ref for ref in chosen if (ref["session_id"], ref["card_id"]) not in selected),
                    ]
            _validate(graph, plan, frozen["setting_basis"], True)
            if [row["chapter_id"] for _, _, row in _chapter_plans(plan)] != chapter_ids:
                raise DomainError("outline_coverage", "候选须按序覆盖现有目录的全部章节。")
            break
        except DomainError as error:
            if attempt:
                raise
            instruction = {**instruction, "前次产物": plan, "修正要求": error.message}
    if worker.cancelled():
        raise DomainError("cancelled", "任务已停止。", 409)
    artifact = worker.domain.stage(
        request,
        CANDIDATE,
        {"plan": plan},
        {"outline_generation": frozen},
        worker.task["id"],
        worker.attempt,
        on_stage=lambda tx, identifier: tx.execute(
            "UPDATE artifacts SET status='accepted' WHERE id=?", (identifier,)
        ),
    )
    worker.checkpoint({**worker.task["result"], "artifact_id": artifact["id"]}, "succeeded")
