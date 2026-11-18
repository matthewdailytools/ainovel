"""Durable parent run for the existing seven-stage author and generation operations."""

from decimal import Decimal
import json
from time import time
from typing import Literal

from pydantic import Field, model_validator

from .budget import micro
from .db import DomainError, dumps, uid
from .models import Input, ModuleInput
from .project_brief import Identifier, IdeaText

KIND = "seven_stage_run"
BUSY_CHILD = {"queued", "running"}


class StartRun(Input):
    start_from: Literal["idea", "settings", "outline"]
    source_id: Identifier | None = None
    idea: IdeaText | None = None
    chapter_count: int | None = Field(default=None, ge=1, le=100, strict=True)
    account: Identifier
    budget_limit: float = Field(gt=0, allow_inf_nan=False)
    client_request_id: str = Field(min_length=8, max_length=128)
    auto_confirm_planning: bool = Field(default=False, strict=True)
    auto_adopt_body: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def compatible_source(self):
        if self.start_from == "idea":
            if not self.idea or not self.chapter_count or self.source_id:
                raise ValueError("从创意启动需提供创作思路和章数。")
        elif not self.source_id or self.idea:
            raise ValueError("接力时请选择已确认设定或大纲。")
        return self


def _story(tx, branch_id):
    row = tx.execute("SELECT story_revision,novel_id FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not row:
        raise DomainError("not_found", "工作版本不存在。", 404)
    return row


def _story_revision(domain, branch_id):
    return domain.branch(branch_id)["story_revision"]


def _unchanged(now, expected):
    if now != expected:
        raise DomainError(
            "seven_stage_changed", "运行依据已改变，已完成结果保留。请核对后从有效版本另行接力。", 409
        )


def _key(worker, step):
    return f"seven-stage-{worker.task['id']}-{step}"


def start_run(domain, branch_id, data):
    from .conception import revision_tx
    from .outline_workspace import _view
    from .project_brief import confirmed_project_tx
    from .setting_review import confirmed_world_basis

    payload = {**data.model_dump(), "branch_id": branch_id}
    with domain.db.connect(True) as tx:
        prior = tx.execute(
            "SELECT tasks.id,tasks.kind,tasks.payload FROM task_requests JOIN tasks "
            "ON tasks.id=task_requests.task_id WHERE task_requests.key=?",
            (data.client_request_id,),
        ).fetchone()
        if prior:
            if prior["kind"] != KIND or json.loads(prior["payload"]) != payload:
                raise DomainError("idempotency", "该提交标识已用于不同的自动接力。", 409)
            identifier = prior["id"]
        else:
            story = _story(tx, branch_id)
            if tx.execute(
                "SELECT 1 FROM tasks WHERE branch_id=? AND kind=? AND status IN "
                "('queued','running','waiting_child','paused','needs_review') LIMIT 1",
                (branch_id, KIND),
            ).fetchone():
                raise DomainError("seven_stage_active", "当前工作版本已有未完成的自动接力。", 409)
            outline = _view(tx, branch_id)
            active_chapters = outline["chapters"]
            project = confirmed_project_tx(tx, branch_id)
            setting_id = None
            outline_id = None
            if data.start_from == "outline":
                confirmed = outline["confirmed"]
                if not confirmed or confirmed["id"] != data.source_id or outline["stale"]:
                    raise DomainError("outline_changed", "请选择仍有效的已确认大纲。", 409)
                outline_id = confirmed["id"]
                setting_id = confirmed["setting_basis"]["confirmation_id"]
            elif data.start_from == "settings":
                setting_id = confirmed_world_basis(tx, branch_id, data.source_id)["confirmation_id"]
            elif active_chapters or project or outline["draft"] or revision_tx(tx, branch_id):
                raise DomainError("seven_stage_idea_scope", "请从新建空白作品启动创意自动流程。", 409)
            if not active_chapters and not data.chapter_count:
                raise DomainError("seven_stage_chapters", "请先建立章节目录或指定生成章数。")
            if active_chapters and data.chapter_count and len(active_chapters) != data.chapter_count:
                raise DomainError("seven_stage_chapters", "指定的章数与已存在的目录范围不一致。")
            state = {
                "stage": {"idea": "project", "settings": "outline", "outline": "writing"}[data.start_from],
                "story_revision": story["story_revision"],
                "conception_revision": revision_tx(tx, branch_id),
                "project_id": project["id"] if project else None,
                "setting_id": setting_id,
                "outline_id": outline_id,
                "outline_revision": outline["draft"]["revision"] if outline["draft"] else 0,
                "children": [],
                "completed_chapters": [],
            }
            identifier = uid()
            timestamp = time()
            tx.execute(
                "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) "
                "VALUES(?,?,?,?,'queued',?,?,?,?)",
                (
                    identifier,
                    KIND,
                    story["novel_id"],
                    branch_id,
                    dumps(payload),
                    dumps({"seven_stage_run": state}),
                    timestamp,
                    timestamp,
                ),
            )
            tx.execute("INSERT INTO task_requests VALUES(?,?)", (data.client_request_id, identifier))
            domain.db.event(tx, identifier, "queued", {"kind": KIND, "stage": state["stage"]})
    return domain.task(identifier)


def wake_waiting(tx):
    tx.execute(
        "UPDATE tasks SET status='queued',updated=? WHERE kind=? AND status='waiting_child' AND cancel=0 "
        "AND EXISTS(SELECT 1 FROM tasks child "
        "WHERE child.id=json_extract(tasks.result,'$.seven_stage_run.child_id') "
        "AND child.status NOT IN ('queued','running'))",
        (time(), KIND),
    )


def _transition(worker, state, stage):
    state["stage"] = stage
    state.pop("child_id", None)
    state.pop("artifact_id", None)
    worker.checkpoint(worker.task["result"], "queued")


def _checkpoint(worker, status, message=""):
    worker.checkpoint(worker.task["result"], status, message)


def _remaining(worker, state):
    charged = 0
    if children := state["children"]:
        with worker.budget.db() as tx:
            charged = tx.execute(
                "SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls "
                f"WHERE task_id IN ({','.join('?' for _ in children)})",
                children,
            ).fetchone()[0]
    left = micro(worker.task["payload"]["budget_limit"]) - charged
    if left <= 0:
        raise DomainError("budget", "自动接力预算已用尽，已完成阶段可查。", 409)
    return float(Decimal(left) / 1_000_000)


def _drive_child(worker, state, step, kind, payload):
    key = _key(worker, step)
    if identifier := state.get("child_id"):
        child = worker.domain.task(identifier)
        if child["status"] in BUSY_CHILD:
            _checkpoint(worker, "waiting_child")
            return None
        if child["status"] != "succeeded":
            _checkpoint(worker, "needs_review", child["error"] or "子任务未完成，请先核对其记录。")
            return None
        artifact = worker.domain.artifact(child["result"]["artifact_id"])
        state["artifact_id"] = artifact["id"]
        return artifact
    rows = worker.db.all("SELECT task_id FROM task_requests WHERE key=?", (key,))
    if rows:
        child = worker.domain.task(rows[0]["task_id"])
        if child["kind"] != kind or child["branch_id"] != worker.task["branch_id"]:
            raise DomainError("idempotency", "子任务标识已被其他任务使用。", 409)
    else:
        charged = _remaining(worker, state)
        child = worker.domain.queue(
            kind,
            {
                **payload,
                "account": worker.task["payload"]["account"],
                "budget_limit": charged,
                "client_request_id": key,
                "novel_id": worker.task["novel_id"],
                "branch_id": worker.task["branch_id"],
                "seven_stage_story_revision": state["story_revision"],
                "seven_stage_run_id": worker.task["id"],
            },
        )
    if child["id"] not in state["children"]:
        state["children"].append(child["id"])
    state["child_id"] = child["id"]
    _checkpoint(worker, "waiting_child")
    return None


def _conception_child(worker, state, operation):
    from .conception_generation import KIND as CONCEPTION, VERSION

    if not state.get("child_id"):
        _unchanged(_story_revision(worker.domain, worker.task["branch_id"]), state["story_revision"])
    input_fields = (
        {"operation": "project_brief", "idea": worker.task["payload"]["idea"]}
        if operation == "project"
        else {"category_id": "world", "instruction": "", "project_confirmation_id": state["project_id"]}
    )
    return _drive_child(
        worker,
        state,
        operation,
        CONCEPTION,
        {
            **input_fields,
            "base_revision": state["conception_revision"],
            "base_story_revision": state["story_revision"],
            "conception_generation_version": VERSION,
        },
    )


def _project(worker, state):
    from .project_brief import ConfirmProject, confirm, confirmed_project_tx

    if not (draft := _conception_child(worker, state, "project")):
        return
    branch = worker.task["branch_id"]
    with worker.db.connect() as tx:
        current = confirmed_project_tx(tx, branch)
    if not current or current["candidate_id"] != draft["id"]:
        _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
        if not worker.task["payload"]["auto_confirm_planning"]:
            _checkpoint(worker, "needs_review", "请在立项台确认这份立项，再继续。")
            return
        current = confirm(
            worker.domain,
            branch,
            ConfirmProject(
                base_revision=draft["basis"]["project_revision"],
                idempotency_key=_key(worker, "confirm-project"),
                candidate_id=draft["id"],
                **draft["payload"],
            ),
        )
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"] + 1)
    state["story_revision"] += 1
    state["project_id"] = current["id"]
    _transition(worker, state, "settings")


def _settings(worker, state):
    from .conception import AdoptConception, ConfirmConception, _workspace, adopt, confirm, snapshot

    if not (draft := _conception_child(worker, state, "settings")):
        return
    branch = worker.task["branch_id"]
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
    current = snapshot(worker.domain, branch)
    with worker.db.connect() as tx:
        adopted = any(
            origin.get("artifact_id") == draft["id"] for origin in _workspace(tx, branch)["origins"].values()
        )
    allow = worker.task["payload"]["auto_confirm_planning"]
    if not adopted and allow:
        _unchanged(current["revision"], state["conception_revision"])
        current = adopt(
            worker.domain,
            branch,
            AdoptConception(
                base_revision=current["revision"],
                base_story_revision=current["story_revision"],
                artifact_id=draft["id"],
                idempotency_key=_key(worker, "adopt-settings"),
                categories=draft["payload"]["categories"],
            ),
        )
        adopted = True
    if adopted and allow and not (current["confirmation"] or {}).get("current"):
        current = confirm(
            worker.domain,
            branch,
            ConfirmConception(
                base_revision=current["revision"],
                base_story_revision=current["story_revision"],
                note="本次自动接力已获授权，采用 AI 设定草案。",
                idempotency_key=_key(worker, "confirm-settings"),
            ),
        )
    if not adopted or not (current["confirmation"] or {}).get("current"):
        _checkpoint(worker, "needs_review", "请采用本次设定草案并确认设定。")
        return
    state["conception_revision"] = current["revision"]
    state["setting_id"] = current["confirmation"]["id"]
    _transition(worker, state, "outline")


def _ensure_chapters(worker, state):
    branch = worker.task["branch_id"]
    with worker.db.connect(True) as tx:
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        _unchanged(_story(tx, branch)["story_revision"], state["story_revision"])
        if tx.execute("SELECT 1 FROM chapters WHERE branch_id=? AND archived=0", (branch,)).fetchone():
            return
        count = worker.task["payload"]["chapter_count"]
        if not count:
            raise DomainError("seven_stage_chapters", "本次接力缺少章节目录。", 409)
        for position in range(1, count + 1):
            identifier = uid()
            title = f"第 {position} 章"
            tx.execute(
                "INSERT INTO chapters(id,branch_id,position,title) VALUES(?,?,?,?)",
                (identifier, branch, position, title),
            )
            tx.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?)", (uid(), identifier, 0, "", "新建空稿", time())
            )
        next_revision = state["story_revision"] + 1
        tx.execute("UPDATE branches SET story_revision=? WHERE id=?", (next_revision, branch))
        state["story_revision"] = next_revision
        tx.execute(
            "UPDATE tasks SET result=?,updated=? WHERE id=? AND attempt=?",
            (dumps(worker.task["result"]), time(), worker.task["id"], worker.attempt),
        )


def _module_payload(worker, action, instruction, **fields):
    return ModuleInput(
        action=action,
        instruction=instruction,
        novel_id=worker.task["novel_id"],
        branch_id=worker.task["branch_id"],
        account=worker.task["payload"]["account"],
        **fields,
    ).model_dump(exclude_none=True)


def _outline(worker, state):
    from .outline_workspace import ConfirmOutline, SaveOutline, confirm_outline, read_outline, save_outline

    branch = worker.task["branch_id"]
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
    _ensure_chapters(worker, state)
    request = _module_payload(worker, "outline.plan", "按已确认立项和设定编排四级大纲。")
    request["outline_generation"] = {
        "base_revision": state["outline_revision"],
        "confirmation_id": state["setting_id"],
    }
    if not (candidate := _drive_child(worker, state, "outline", "module", request)):
        return
    view = read_outline(worker.domain, branch)
    latest = view["draft"]
    from_candidate = latest and latest["setting_basis"].get("generation_id") == candidate["id"]
    if not from_candidate or not latest["confirmed"]:
        if not worker.task["payload"]["auto_confirm_planning"]:
            _checkpoint(worker, "needs_review", "请载入本次大纲候选、核对并确认后继续。")
            return
        if not from_candidate:
            view = save_outline(
                worker.domain,
                branch,
                SaveOutline(
                    base_revision=state["outline_revision"],
                    confirmation_id=state["setting_id"],
                    source_generation_id=candidate["id"],
                    plan=candidate["payload"]["plan"],
                ),
            )
        view = confirm_outline(worker.domain, branch, ConfirmOutline(base_revision=view["draft"]["revision"]))
    if view["stale"]:
        raise DomainError("outline_changed", "本次确认大纲已失效。", 409)
    state["outline_id"] = view["confirmed"]["id"]
    _transition(worker, state, "writing")


def _writing(worker, state):
    from .outline_workspace import _chapter_plans, read_outline

    branch = worker.task["branch_id"]
    view = read_outline(worker.domain, branch)
    confirmed = view["confirmed"]
    if not confirmed or confirmed["id"] != state["outline_id"] or view["stale"]:
        raise DomainError("outline_changed", "采用的大纲已变化，请重新选择接力起点。", 409)
    plans = [chapter for _, _, chapter in _chapter_plans(confirmed["plan"])]
    completed = state["completed_chapters"]
    if len(completed) == len(plans):
        _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
        _transition(worker, state, "inspection")
        return
    plan = plans[len(completed)]
    chapter = worker.domain.chapter(plan["chapter_id"], branch)
    step = f"chapter-{chapter['id']}"
    if not state.get("child_id"):
        _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
        if chapter["content"].strip():
            completed.append({"id": chapter["id"], "revision": chapter["revision"], "existing": True})
            _transition(worker, state, "writing")
            return
    procedure = "\n".join(
        f"{label}：{plan[field]}"
        for field, label in (("goal", "目标"), ("obstacle", "阻碍"), ("turn", "转折"), ("result", "结果"))
    )
    if not (
        artifact := _drive_child(
            worker,
            state,
            step,
            "module",
            _module_payload(
                worker,
                "writing.expand_plot",
                procedure,
                chapter_id=chapter["id"],
                base_revision=chapter["revision"],
                outline_revision_id=state["outline_id"],
                fixed=[plan["result"]],
            ),
        )
    ):
        return
    if artifact["status"] != "accepted":
        _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
        if not worker.task["payload"]["auto_adopt_body"] or not artifact["payload"].get("review_passed"):
            _checkpoint(worker, "needs_review", "本章正文已生成，请核对候选后采纳。")
            return
        worker.domain.accept(
            artifact["id"],
            _key(worker, step + "-accept"),
            require_review=True,
            candidate_revision=artifact["candidate_revision"],
        )
    receipt = worker.domain.acceptance_receipt(artifact["id"])["receipt"]
    saved = worker.domain.chapter(chapter["id"], branch)
    if not receipt or receipt["revision"] != saved["revision"]:
        raise DomainError("seven_stage_changed", "采纳后的章稿已改变，已停止接力。", 409)
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"] + 1)
    state["story_revision"] += 1
    completed.append({"id": saved["id"], "revision": saved["revision"], "artifact_id": artifact["id"]})
    _transition(worker, state, "writing")


def _inspection(worker, state):
    from .workflow_inspection import InspectionInput, _evaluate, create_inspection, latest_book_inspection

    branch = worker.task["branch_id"]
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        report = latest_book_inspection(tx, branch)
        if not report or not report["current"]:
            preview, _ = _evaluate(tx, branch, "book", None)
    if not report or not report["current"]:
        report = create_inspection(
            worker.domain, branch, InspectionInput(basis_sha256=preview["basis_sha256"])
        )
    state["inspection_id"] = report["id"]
    if any(item["current_status"] not in {"green", "not_applicable"} for item in report["items"]):
        _checkpoint(worker, "needs_review", "体检单已生成，请核对待处理项再继续。")
        return
    _transition(worker, state, "completion")


def _completion(worker, state):
    from .book_completion import _evaluate, _latest

    branch = worker.task["branch_id"]
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        preview = _evaluate(tx, branch)
        record = _latest(tx, branch, preview["basis_sha256"])
    if not record or not record["current"]:
        _checkpoint(worker, "needs_review", "请核对完本六项并确认交付材料。")
        return
    state["completion_id"] = record["id"]
    state["stage"] = "done"
    _checkpoint(worker, "succeeded")


def run(worker):
    state = worker.task["result"]["seven_stage_run"]
    if worker.task["pause"]:
        _checkpoint(worker, "paused", "自动接力已在当前检查点暂停。")
        return
    handlers = {
        "project": _project,
        "settings": _settings,
        "outline": _outline,
        "writing": _writing,
        "inspection": _inspection,
        "completion": _completion,
    }
    handlers[state["stage"]](worker, state)


def register_routes(app, domain, provider_ready):
    endpoint = "/api/branches/{branch_id}/seven-stage-runs"

    @app.post(endpoint, status_code=202)
    def create_run(branch_id: str, data: StartRun):
        provider_ready(data.account)
        return start_run(domain, branch_id, data)

    @app.get(endpoint)
    def runs(branch_id: str):
        domain.branch(branch_id)
        rows = domain.db.all(
            "SELECT id FROM tasks WHERE branch_id=? AND kind=? ORDER BY created DESC,id DESC LIMIT 20",
            (branch_id, KIND),
        )
        return [domain.task(row["id"]) for row in rows]

    @app.post(endpoint + "/{task_id}/pause")
    def pause_run(branch_id: str, task_id: str):
        _control(domain, branch_id, task_id, "pause")
        return domain.task(task_id)

    @app.post(endpoint + "/{task_id}/resume")
    def resume_run(branch_id: str, task_id: str):
        _control(domain, branch_id, task_id, "resume")
        return domain.task(task_id)

    @app.post(endpoint + "/{task_id}/cancel")
    def cancel_run(branch_id: str, task_id: str):
        _control(domain, branch_id, task_id, "cancel")
        return domain.task(task_id)


def _control(domain, branch_id, task_id, action):
    with domain.db.connect(True) as tx:
        row = tx.execute(
            "SELECT status,result FROM tasks WHERE id=? AND branch_id=? AND kind=?",
            (task_id, branch_id, KIND),
        ).fetchone()
        if not row:
            raise DomainError("not_found", "找不到当前作品的自动接力。", 404)
        status = row["status"]
        if action == "resume" and status in {"paused", "needs_review", "blocked_runtime"}:
            result = json.loads(row["result"])
            state = result["seven_stage_run"]
            revision = _story(tx, branch_id)["story_revision"]
            if state["stage"] in {"inspection", "completion"} and revision != state["story_revision"]:
                from .outline_workspace import _view

                outline = _view(tx, branch_id)
                if (
                    not outline["confirmed"]
                    or outline["confirmed"]["id"] != state["outline_id"]
                    or outline["stale"]
                ):
                    raise DomainError("seven_stage_changed", "上游规划已改变，请从有效版本重新接力。", 409)
                # The author explicitly resumes against newer prose. Preserve the
                # generated receipts and re-run inspection before any completion.
                state["story_revision"] = revision
                state["stage"] = "inspection"
            tx.execute(
                "UPDATE tasks SET status='queued',result=?,pause=0,error='',updated=? WHERE id=?",
                (dumps(result), time(), task_id),
            )
        elif action == "pause" and status in {"queued", "running", "waiting_child"}:
            tx.execute(
                "UPDATE tasks SET status=CASE WHEN status='queued' THEN 'paused' ELSE status END,"
                "pause=1,updated=? WHERE id=?",
                (time(), task_id),
            )
        elif action == "cancel" and status not in {"succeeded", "cancelled"}:
            tx.execute("UPDATE tasks SET status='cancelled',cancel=1,updated=? WHERE id=?", (time(), task_id))
            child = json.loads(row["result"])["seven_stage_run"].get("child_id")
            if child:
                tx.execute(
                    "UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END "
                    "WHERE id=? AND status IN ('queued','running')",
                    (child,),
                )
        else:
            raise DomainError("seven_stage_state", "当前接力状态不支持此操作。", 409)
        domain.db.event(tx, task_id, f"seven_stage_{action}", {})
