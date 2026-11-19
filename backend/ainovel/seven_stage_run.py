"""Durable parent run for the existing seven-stage author and generation operations."""

from decimal import Decimal
from hashlib import sha256
import json
from time import time
from typing import Literal

from pydantic import Field, model_validator

from .budget import micro
from .db import DomainError, dumps, uid
from .models import Input, ModuleInput
from .outline_directory import add_empty_chapters_tx
from .outline_workspace import SelectedOutlineCardReference, freeze_selected_outline_cards
from .project_brief import Identifier, IdeaText

KIND = "seven_stage_run"
BUSY_CHILD = {"queued", "running"}
STALE_BASIS = {
    "completion_changed",
    "conception_changed",
    "conception_snapshot",
    "inspection_changed",
    "outline_candidate_stale",
    "outline_changed",
    "outline_conflict",
    "project_brief_changed",
    "reference_changed",
    "revision_conflict",
    "seven_stage_changed",
    "story_changed",
}


class StartRun(Input):
    start_from: Literal["idea", "project", "settings", "outline", "writing", "inspection"]
    source_id: Identifier | None = None
    idea: IdeaText | None = None
    chapter_count: int | None = Field(default=None, ge=1, le=100, strict=True)
    account: Identifier
    budget_limit: float = Field(gt=0, allow_inf_nan=False)
    client_request_id: str = Field(min_length=8, max_length=128)
    auto_confirm_planning: bool = Field(default=False, strict=True)
    auto_adopt_body: bool = Field(default=False, strict=True)
    reference_session_id: Identifier | None = None
    reference_revision: int | None = Field(default=None, ge=0, strict=True)
    auto_adopt_reference: bool = Field(default=False, strict=True)
    reference_cards: list[SelectedOutlineCardReference] = Field(
        default_factory=list, max_length=8, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def compatible_source(self):
        if self.start_from == "idea":
            if not self.idea or not self.chapter_count or self.source_id:
                raise ValueError("从创意启动需提供创作思路和章数。")
        elif not self.source_id or self.idea:
            raise ValueError("接力时请选择有效的保存版本。")
        if self.reference_session_id:
            if self.reference_revision is None or self.start_from not in {"idea", "project", "settings"}:
                raise ValueError("生成大纲前可选择绑定修订的拆解会话。")
        elif self.reference_revision is not None or self.auto_adopt_reference:
            raise ValueError("请先选择本工作版本的参考会话。")
        if self.reference_cards and (
            self.reference_session_id or self.start_from not in {"idea", "project", "settings"}
        ):
            raise ValueError("生成大纲前可选择正式手法卡或重新拆解原文，请选用一种方式。")
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


def _guard(worker, state):
    def before_write(tx):
        worker.db.check_attempt(tx, worker.task["id"], worker.attempt)
        _unchanged(_story(tx, worker.task["branch_id"])["story_revision"], state["story_revision"])

    return before_write


def _ensure_exclusive(tx, branch_id, except_id=""):
    if tx.execute(
        "SELECT 1 FROM tasks WHERE branch_id=? AND kind=? AND id<>? AND status IN "
        "('queued','running','waiting_child','paused','needs_review') LIMIT 1",
        (branch_id, KIND, except_id),
    ).fetchone():
        raise DomainError("seven_stage_active", "当前工作版本已有未完成的自动接力。", 409)


def _reference_basis(tx, branch_id, identifier, revision):
    row = tx.execute("SELECT revision,payload FROM reference_sessions WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise DomainError("not_found", "找不到所选拆解会话。", 404)
    if row["revision"] != revision:
        raise DomainError("reference_changed", "已保存的拆解会话修订改变，请重新选择。", 409)
    stored = json.loads(row["payload"])
    if stored.get("branch_id") != branch_id:
        raise DomainError("ownership", "请使用当前工作版本的拆解会话。", 403)
    goal, source = stored["goal"], stored["source"]
    if not goal.strip() or not source["quote"].strip():
        raise DomainError("reference_scope", "自动拆解需要已保存的学习目标和原文选段。", 409)
    return {
        "id": identifier,
        "revision": revision,
        "goal": goal,
        "source": _reference_source(source),
    }


def _reference_source(source):
    return {
        **{key: source[key] for key in ("title", "unit_id", "file_hash", "start", "end")},
        "quote_sha256": sha256(source["quote"].encode()).hexdigest(),
    }


class ReviewSources(Input):
    writing_id: Identifier | None
    inspection_id: Identifier | None


def _review_sources(tx, branch_id, outline):
    from .workflow_inspection import _scope, body_chars, latest_book_inspection

    chapters = _scope(tx, branch_id, outline, "book", None)
    if (
        not chapters
        or outline["stale"]
        or any(row["stale"] or not body_chars(row["content"]) for row in chapters)
    ):
        return ReviewSources(writing_id=None, inspection_id=None), []
    manifest = [
        [
            row["id"],
            row["title"],
            row["position"],
            row["revision"],
            sha256(row["content"].encode()).hexdigest(),
        ]
        for row in chapters
    ]
    outline_id = (outline["confirmed"] or {}).get("id")
    writing_id = sha256(dumps([outline_id, manifest]).encode()).hexdigest()
    report = latest_book_inspection(tx, branch_id)
    return (
        ReviewSources(
            writing_id=writing_id, inspection_id=report["id"] if _inspection_ready(report) else None
        ),
        [{"id": row["id"], "revision": row["revision"], "existing": True} for row in chapters],
    )


def start_run(domain, branch_id, data):
    from .conception import revision_tx
    from .outline_workspace import _view
    from .project_brief import confirmed_project_tx
    from .setting_review import confirmed_world_basis
    from .work_snapshot import checkpoint_tx

    excluded = (
        set()
        if data.reference_session_id
        else {"reference_session_id", "reference_revision", "auto_adopt_reference"}
    )
    payload = {**data.model_dump(exclude=excluded), "branch_id": branch_id}
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
            _ensure_exclusive(tx, branch_id)
            outline = _view(tx, branch_id)
            active_chapters = outline["chapters"]
            project = confirmed_project_tx(tx, branch_id)
            setting_id = None
            outline_id = None
            completed_chapters = []
            report_id = None
            if data.start_from == "outline":
                confirmed = outline["confirmed"]
                if not confirmed or confirmed["id"] != data.source_id or outline["stale"]:
                    raise DomainError("outline_changed", "请选择仍有效的已确认大纲。", 409)
                outline_id = confirmed["id"]
                setting_id = confirmed["setting_basis"]["confirmation_id"]
            elif data.start_from == "settings":
                setting_id = confirmed_world_basis(tx, branch_id, data.source_id)["confirmation_id"]
            elif data.start_from == "project":
                if not project or project["id"] != data.source_id:
                    raise DomainError("project_brief_changed", "请选择当前已确认的立项。", 409)
            elif data.start_from in {"writing", "inspection"}:
                sources, completed_chapters = _review_sources(tx, branch_id, outline)
                selected = sources.writing_id if data.start_from == "writing" else sources.inspection_id
                if not selected or data.source_id != selected:
                    raise DomainError("inspection_changed", "请重新核对当前已保存正文和全书体检。", 409)
                confirmed = outline["confirmed"]
                if confirmed:
                    outline_id = confirmed["id"]
                    setting_id = confirmed["setting_basis"]["confirmation_id"]
                if data.start_from == "inspection":
                    report_id = sources.inspection_id
            elif active_chapters or project or outline["draft"] or revision_tx(tx, branch_id):
                raise DomainError("seven_stage_idea_scope", "请从新建空白作品启动创意自动流程。", 409)
            if not active_chapters and not data.chapter_count:
                raise DomainError("seven_stage_chapters", "请先建立章节目录或指定生成章数。")
            if active_chapters and data.chapter_count and len(active_chapters) != data.chapter_count:
                raise DomainError("seven_stage_chapters", "指定的章数与已存在的目录范围不一致。")
            state = {
                "stage": {
                    "idea": "project",
                    "project": "settings",
                    "settings": "outline",
                    "outline": "writing",
                    "writing": "inspection",
                    "inspection": "completion",
                }[data.start_from],
                "story_revision": story["story_revision"],
                "conception_revision": revision_tx(tx, branch_id),
                "project_id": project["id"] if project else None,
                "setting_id": setting_id,
                "outline_id": outline_id,
                "outline_revision": outline["draft"]["revision"] if outline["draft"] else 0,
                "children": [],
                "completed_chapters": completed_chapters,
            }
            if report_id:
                state.update(inspection_id=report_id, selected_inspection_id=report_id)
            if data.reference_session_id:
                state["reference_basis"] = _reference_basis(
                    tx, branch_id, data.reference_session_id, data.reference_revision
                )
                if data.start_from != "idea":
                    state["stage"] = "reference"
            if references := payload.get("reference_cards"):
                freeze_selected_outline_cards(tx, branch_id, references)
                state["reference_references"] = references
            state["start_snapshot_id"] = checkpoint_tx(tx, branch_id, "启动七台接力")
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
    state.pop("child_recovery", None)
    state.pop("interruption", None)
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


def _child_recovery(tx, parent_id, child_id):
    child = tx.execute("SELECT status,payload FROM tasks WHERE id=?", (child_id,)).fetchone()
    if not child or json.loads(child["payload"]).get("seven_stage_run_id") != parent_id:
        raise DomainError("seven_stage_child", "无法确认这项子任务属于当前接力。", 409)
    recovery = {"child_id": child_id, "state": "blocked", "dispatch_event_ids": []}
    if child["status"] in BUSY_CHILD or child["status"] == "succeeded":
        return {**recovery, "state": "ready"}
    if child["status"] != "blocked_runtime":
        return recovery
    unresolved = tx.execute(
        "SELECT sent.id FROM task_events sent WHERE sent.task_id=? AND sent.kind='step_dispatched' "
        "AND NOT EXISTS (SELECT 1 FROM task_events proof WHERE proof.task_id=sent.task_id "
        "AND proof.id>sent.id AND proof.kind='step_not_sent' "
        "AND json_extract(proof.payload,'$.dispatch_event_id')=sent.id "
        "AND json_extract(proof.payload,'$.fingerprint')=json_extract(sent.payload,'$.fingerprint')) "
        "AND NOT EXISTS (SELECT 1 FROM task_events completed JOIN task_steps cached "
        "ON cached.task_id=completed.task_id AND cached.fingerprint=json_extract(completed.payload,'$.fingerprint') "
        "WHERE completed.task_id=sent.task_id AND completed.id>sent.id AND completed.kind='step_completed' "
        "AND cached.fingerprint=json_extract(sent.payload,'$.fingerprint') AND cached.created>=sent.created "
        "AND NOT EXISTS (SELECT 1 FROM task_events other WHERE other.task_id=sent.task_id "
        "AND other.kind='step_dispatched' AND sent.id<other.id AND other.id<completed.id "
        "AND json_extract(other.payload,'$.fingerprint')=cached.fingerprint)) "
        "ORDER BY sent.id LIMIT 20",
        (child_id,),
    ).fetchall()
    if unresolved:
        recovery.update(state="unknown", dispatch_event_ids=[row["id"] for row in unresolved])
    else:
        recovery["state"] = "retryable"
    return recovery


def _drive_child(worker, state, step, kind, payload):
    key = _key(worker, step)
    if identifier := state.get("child_id"):
        child = worker.domain.task(identifier)
        if child["status"] in BUSY_CHILD:
            _checkpoint(worker, "waiting_child")
            return None
        if child["status"] != "succeeded":
            with worker.db.connect() as tx:
                tx.execute("BEGIN")
                recovery = _child_recovery(tx, worker.task["id"], identifier)
            state["child_recovery"] = recovery
            if recovery["state"] == "unknown":
                _checkpoint(worker, "needs_review", "子任务存在已派发但结果未知的步骤，请核对回执和费用。")
            else:
                _checkpoint(worker, "needs_review", child["error"] or "子任务未完成，请先核对其记录。")
            return None
        state.pop("child_recovery", None)
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
    request = worker.task["payload"]
    input_fields = (
        {
            "operation": "project_brief",
            "idea": request["idea"],
            "project_chapter_count": request.get("chapter_count"),
        }
        if operation == "project"
        else {
            "category_id": None,
            "target_category_ids": ["core", "characters", "world", "relationships"],
            "instruction": "",
            "project_confirmation_id": state["project_id"],
        }
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
            before_write=_guard(worker, state),
        )
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"] + 1)
    state["story_revision"] += 1
    state["project_id"] = current["id"]
    _transition(worker, state, "reference" if state.get("reference_basis") else "settings")


def _analysis_quote(artifact, basis, branch_id, revision):
    from .domain import utf16_slice

    snapshot = artifact["basis"].get("reference_session") or {}
    payload = snapshot.get("payload") or {}
    source = payload.get("source")
    if (
        artifact["kind"] != "reference_analysis"
        or artifact["branch_id"] != branch_id
        or artifact["status"] == "rejected"
        or (snapshot.get("id"), snapshot.get("revision")) != (basis["id"], revision)
        or (payload.get("branch_id"), payload.get("goal")) != (branch_id, basis["goal"])
        or not source
        or _reference_source(source) != basis["source"]
    ):
        return None
    scope = snapshot.get("analysis_scope")
    if scope != artifact["payload"].get("analysis_scope"):
        return None
    if not scope:
        return source["quote"]
    try:
        _, quote, _ = utf16_slice(source["quote"], scope["start"], scope["end"])
    except DomainError:
        return None
    return quote if quote.strip() and quote == scope.get("quote") else None


def _reference_reviews(domain, history, draft, basis, branch_id):
    for version in history:
        identifier = version["results"].get("analyze")
        if not identifier or identifier == draft["id"]:
            continue
        try:
            review = domain.artifact(identifier)
        except DomainError as error:
            if error.code != "not_found":
                raise
            continue
        if quote := _analysis_quote(review, basis, branch_id, version["revision"]):
            if not (cards := review["payload"]["cards"]):
                continue
            target = (review["payload"].get("analysis_scope") or {}).get("card_id")
            yield {
                "id": identifier,
                "revision": version["revision"],
                "quote": quote,
                "cards": {card["id"] for card in cards},
                "rewritten": next((card for card in version["cards"] if card["id"] == target), None),
            }


def _reference_revisions(domain, session, draft, basis, branch_id, original_quote):
    from .reference import card_version

    metadata = session["payload"]
    if (metadata["branch_id"], metadata["goal"]) != (branch_id, basis["goal"]) or _reference_source(
        metadata["source"]
    ) != basis["source"]:
        return [], []
    if adoption := metadata.get("analysis_adoption"):
        if adoption["artifact_id"] == draft["id"]:
            return [
                {"session_id": session["id"], "card_id": key, "revision": adoption["revision"]}
                for key in adoption["cards"]
            ], []

    history = [row for row in metadata.get("history", []) if row["revision"] >= basis["revision"]]
    if (
        not history
        or [row["revision"] for row in history] != list(range(basis["revision"], session["revision"]))
        or any((row.get("branch_id"), row.get("goal")) != (branch_id, basis["goal"]) for row in history)
    ):
        return [], []
    reviews = list(_reference_reviews(domain, history, draft, basis, branch_id))
    original_cards = {card["id"] for card in draft["payload"]["cards"]}

    refs = []
    reviewed = []
    for key in metadata["selected_ids"]:
        revision = metadata.get("pinned_revisions", {}).get(key, session["revision"])
        if revision <= basis["revision"]:
            continue
        version = card_version(session, key, revision)
        if version["goal"] != basis["goal"]:
            continue
        card = version["card"]
        review = next(
            (
                item
                for item in reversed(reviews)
                if item["revision"] < revision
                and (
                    key in item["cards"]
                    or ((before := item["rewritten"]) and key == before["id"] and card != before)
                )
            ),
            None,
        )
        if review is not None:
            if card["evidence"] not in review["quote"]:
                continue
            if review["id"] not in reviewed:
                reviewed.append(review["id"])
        elif key not in original_cards or card["evidence"] not in original_quote:
            continue
        refs.append({"session_id": session["id"], "card_id": key, "revision": revision})
    return refs, reviewed


def _save_reference_cards(worker, state, session, draft):
    from .reference import EditReference, edit_reference

    metadata = session["payload"]
    proposed = draft["payload"]["cards"]
    keys = [card["id"] for card in proposed]
    if len(metadata["cards"]) + len(proposed) > 8 or len(metadata["selected_ids"]) + len(keys) > 8:
        return []
    required = ("observation", "interpretation", "technique", "conditions", "exceptions", "evidence")
    if any(not card[field].strip() for card in proposed for field in required):
        return []
    saved = edit_reference(
        worker.domain,
        session["id"],
        EditReference(
            base_revision=session["revision"],
            **{
                key: metadata[key]
                for key in ("goal", "plot", "fixed", "application_plan", "branch_id", "chapter_id")
            },
            cards=[*metadata["cards"], *proposed],
            selected_ids=[*metadata["selected_ids"], *keys],
            pinned_revisions=metadata.get("pinned_revisions", {}),
        ),
        before_write=_guard(worker, state),
        analysis_adoption={"artifact_id": draft["id"], "cards": keys},
    )
    return [{"session_id": session["id"], "card_id": key, "revision": saved["revision"]} for key in keys]


def _reference(worker, state):
    from .reference import session

    branch = worker.task["branch_id"]
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
    basis = state["reference_basis"]
    child = worker.domain.task(state["child_id"]) if state.get("child_id") else None
    if not child or child["status"] != "succeeded":
        with worker.db.connect() as tx:
            live = _reference_basis(tx, branch, basis["id"], basis["revision"])
        if live != basis:
            raise DomainError("reference_changed", "拆解依据发生变化，请重新启动接力。", 409)
    request = _module_payload(
        worker,
        "reference.analyze",
        basis["goal"],
        reference_session_id=basis["id"],
        reference_revision=basis["revision"],
    )
    if not (draft := _drive_child(worker, state, "reference", "module", request)):
        return
    quote = _analysis_quote(draft, basis, branch, basis["revision"])
    if quote is None:
        raise DomainError("reference_changed", "拆解产物未对应显式选择的会话修订。", 409)
    state["reference_analysis_id"] = draft["id"]
    if not draft["payload"]["cards"]:
        state["reference_references"] = []
    else:
        current = session(worker.domain, basis["id"])
        refs, reviewed = _reference_revisions(worker.domain, current, draft, basis, branch, quote)
        if (
            not refs
            and worker.task["payload"].get("auto_adopt_reference")
            and current["revision"] == basis["revision"]
        ):
            refs = _save_reference_cards(worker, state, current, draft)
        if not refs:
            _checkpoint(
                worker, "needs_review", "请在拆解台核对本次分析，保存并选择含适用条件和须替换内容的手法卡。"
            )
            return
        state["reference_references"] = refs
        if reviewed:
            state["reference_review_analysis_ids"] = reviewed
    _transition(worker, state, "settings" if state["setting_id"] is None else "outline")


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
            before_write=_guard(worker, state),
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
            before_write=_guard(worker, state),
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
        if references := worker.task["payload"].get("reference_cards"):
            freeze_selected_outline_cards(tx, branch, references)
        if tx.execute("SELECT 1 FROM chapters WHERE branch_id=? AND archived=0", (branch,)).fetchone():
            return
        count = worker.task["payload"]["chapter_count"]
        if not count:
            raise DomainError("seven_stage_chapters", "本次接力缺少章节目录。", 409)
        add_empty_chapters_tx(tx, branch, count)
        state["story_revision"] += 1
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
    if not state.get("child_id"):
        _ensure_chapters(worker, state)
    request = _module_payload(worker, "outline.plan", "按已确认立项和设定编排四级大纲。")
    request["outline_generation"] = {
        "base_revision": state["outline_revision"],
        "confirmation_id": state["setting_id"],
    }
    if selected := state.get("reference_references"):
        request["outline_generation"]["reference_cards"] = selected
    if not (candidate := _drive_child(worker, state, "outline", "module", request)):
        return
    view = read_outline(worker.domain, branch)
    latest = view["draft"]
    from_candidate = latest and latest["setting_basis"].get("generation_id") == candidate["id"]
    _check_outline_story(latest, state, candidate["id"], view["story_revision"])
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
                before_write=_guard(worker, state),
            )
        if view.get("requires_promise_confirmation"):
            _checkpoint(worker, "needs_review", "请在大纲台核对必达承诺变更并确认后再继续。")
            return
        view = confirm_outline(
            worker.domain,
            branch,
            ConfirmOutline(base_revision=view["draft"]["revision"]),
            before_write=_guard(worker, state),
        )
        _check_outline_story(view["draft"], state, candidate["id"], view["story_revision"])
    if view["stale"]:
        raise DomainError("outline_changed", "本次确认大纲已失效。", 409)
    state["story_revision"] = view["story_revision"]
    state["outline_id"] = view["confirmed"]["id"]
    _transition(worker, state, "writing")


def _check_outline_story(latest, state, candidate_id, revision):
    expected = state["story_revision"]
    if revision == expected:
        return
    if latest and latest["confirmed"] and candidate_id:
        basis = latest["setting_basis"]
        receipt = basis.get("outline_story_receipt", {})
        if (
            basis.get("generation_id") == candidate_id
            and receipt.get("before") == expected
            and receipt.get("after") == revision
            and receipt.get("draft_revision") == latest["revision"] - 1
            and receipt["draft_revision"] > state["outline_revision"]
            and len(receipt.get("created_ids", [])) == revision - expected
        ):
            return
    _unchanged(revision, expected)


def _volume_checkpoint(worker, state, volume):
    from .work_snapshot import checkpoint_tx

    saved = state.setdefault("volume_snapshot_ids", {})
    if volume["id"] in saved:
        return
    branch = worker.task["branch_id"]
    with worker.db.connect(True) as tx:
        _guard(worker, state)(tx)
        saved[volume["id"]] = checkpoint_tx(
            tx,
            branch,
            f"接力到卷末 · {volume['title']}"[:200],
            scope="volume",
            scope_id=volume["id"],
        )
        tx.execute(
            "UPDATE tasks SET result=?,updated=? WHERE id=? AND attempt=?",
            (dumps(worker.task["result"]), time(), worker.task["id"], worker.attempt),
        )


def _writing(worker, state):
    from .outline_workspace import _chapter_plans, read_outline
    from .workflow_inspection import body_chars

    branch = worker.task["branch_id"]
    view = read_outline(worker.domain, branch)
    confirmed = view["confirmed"]
    if not confirmed or confirmed["id"] != state["outline_id"] or view["stale"]:
        raise DomainError("outline_changed", "采用的大纲已变化，请重新选择接力起点。", 409)
    plans = [(volume, chapter) for volume, _, chapter in _chapter_plans(confirmed["plan"])]
    completed = state["completed_chapters"]
    if completed:
        prior_volume = plans[len(completed) - 1][0]
        if len(completed) == len(plans) or prior_volume["id"] != plans[len(completed)][0]["id"]:
            _volume_checkpoint(worker, state, prior_volume)
    if len(completed) == len(plans):
        _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
        _transition(worker, state, "inspection")
        return
    plan = plans[len(completed)][1]
    chapter = worker.domain.chapter(plan["chapter_id"], branch)
    step = f"chapter-{chapter['id']}"
    if not state.get("child_id"):
        _unchanged(_story_revision(worker.domain, branch), state["story_revision"])
        if body_chars(chapter["content"]):
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
        length = artifact["payload"].get("review", {}).get("chapter_length") or {}
        if chapter["content"].strip():
            prompt = length.get("reason") or "请核对候选后手动采纳。"
            _checkpoint(
                worker, "needs_review", "本章保存稿只有标题、排版或不可计入的字符，已保留原稿。" + prompt
            )
            return
        if not worker.task["payload"]["auto_adopt_body"] or not artifact["payload"].get("review_passed"):
            _checkpoint(worker, "needs_review", length.get("reason") or "本章正文已生成，请核对候选后采纳。")
            return
        worker.domain.accept(
            artifact["id"],
            _key(worker, step + "-accept"),
            require_review=True,
            candidate_revision=artifact["candidate_revision"],
            before_write=_guard(worker, state),
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
    from .inspection_semantic import ACTION, KEYS, KIND as SEMANTIC
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
            worker.domain,
            branch,
            InspectionInput(basis_sha256=preview["basis_sha256"]),
            before_write=_guard(worker, state),
        )
    state["inspection_id"] = report["id"]
    if _inspection_ready(report):
        _transition(worker, state, "completion")
        return
    semantic = any(
        item["check_id"] in KEYS and item["current_status"] in {"pending", "yellow", "outdated"}
        for item in report["items"]
    )
    if semantic:
        basis = report["basis_sha256"]
        if state.get("inspection_basis_sha256") not in {None, basis}:
            state["inspection_basis_changed"] = True
            _checkpoint(worker, "needs_review", "体检依据已变化，请核对后续跑，旧候选仍可查。")
            return
        state["inspection_basis_sha256"] = basis
        request = _module_payload(worker, "review.chapter", ACTION)
        request["inspection_semantic"] = {"scope": "book", "scope_id": None, "basis_sha256": basis}
        candidate = _drive_child(worker, state, f"inspection-{basis}", "module", request)
        if not candidate:
            return
        if candidate["kind"] != SEMANTIC or candidate["basis"].get("inspection_basis_sha256") != basis:
            raise DomainError("inspection_changed", "无法核对本次体检候选的依据。", 409)
        state["inspection_candidate_id"] = candidate["id"]
        _checkpoint(worker, "needs_review", "体检单和语义候选已生成，请核对待处理项再继续。")
        return
    _checkpoint(worker, "needs_review", "体检单已生成，请核对待处理项再继续。")


def _inspection_ready(report):
    return bool(
        report
        and report["current"]
        and not report.get("current_red_checks")
        and all(item["current_status"] in {"green", "not_applicable"} for item in report["items"])
    )


def _completion(worker, state):
    from .book_completion import _basis, _latest
    from .completion_preparation import KEY, KIND as PREPARATION

    branch = worker.task["branch_id"]
    _unchanged(_story_revision(worker.domain, branch), state["story_revision"])

    def read_current(tx):
        basis, _, _, _, _, report, _ = _basis(tx, branch)
        return basis, report, _latest(tx, branch, basis)

    with worker.db.connect() as tx:
        tx.execute("BEGIN")
        basis, report, record = read_current(tx)
    if not _inspection_ready(report) or report["id"] != state.get("inspection_id"):
        state["completion_basis_changed"] = True
        adopted = "选定的" if state.get("selected_inspection_id") else "本次采用的"
        _checkpoint(worker, "needs_review", f"{adopted}全书体检已有变化，请核对后继续。")
        return
    if record and record["current"]:

        def finish(tx):
            current_basis, current_report, current_record = read_current(tx)
            if (
                current_basis != basis
                or not _inspection_ready(current_report)
                or not current_record
                or not current_record["current"]
                or current_record["id"] != record["id"]
            ):
                raise DomainError("completion_changed", "写入接力结果前完本依据已变化，请核对后继续。", 409)
            state["completion_id"] = current_record["id"]
            state["stage"] = "done"
            state.pop("child_id", None)

        worker.checkpoint(worker.task["result"], "succeeded", before_write=finish)
        return
    if state.get("completion_basis_sha256") not in {None, basis}:
        state["completion_basis_changed"] = True
        _checkpoint(
            worker, "needs_review", "完本建议的依据已变化，旧结果可查。请续跑核对最新体检和完本材料。"
        )
        return
    state["completion_basis_sha256"] = basis
    request = _module_payload(worker, "outline.plan", "准备完本核对建议与开篇范围的交付简介。")
    request[KEY] = {"basis_sha256": basis, "spoiler_scope": "opening", "sample_chapters": 1}
    prepared = _drive_child(worker, state, f"completion-{basis}", "module", request)
    if not prepared:
        return
    if prepared["kind"] != PREPARATION or prepared["basis"].get("completion_basis_sha256") != basis:
        raise DomainError("completion_changed", "无法核对本次完本建议的依据。", 409)
    state["completion_preparation_id"] = prepared["id"]
    _checkpoint(worker, "needs_review", "完本建议与简介已准备，请核对六项并确认交付材料。")


def run(worker):
    state = worker.task["result"]["seven_stage_run"]
    if worker.task["pause"]:
        _checkpoint(worker, "paused", "自动接力已在当前检查点暂停。")
        return
    handlers = {
        "project": _project,
        "reference": _reference,
        "settings": _settings,
        "outline": _outline,
        "writing": _writing,
        "inspection": _inspection,
        "completion": _completion,
    }
    try:
        handlers[state["stage"]](worker, state)
    except DomainError as error:
        if error.code == "conception_setting_review":
            _checkpoint(worker, "needs_review", "设定体检有待核对项，请在设定台处理并确认后继续。")
            return
        if error.code in {"chapter_length_changed", "chapter_length_bounds"}:
            _checkpoint(worker, "needs_review", error.message)
            return
        if error.code not in STALE_BASIS:
            raise
        if error.code == "completion_changed" and state["stage"] == "completion":
            state["completion_basis_changed"] = True
        state["interruption"] = {"code": error.code, "message": error.message}
        _checkpoint(worker, "needs_review", error.message)


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

    @app.get(endpoint + "/sources")
    def review_sources(branch_id: str) -> ReviewSources:
        from .outline_workspace import _view

        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            _story(tx, branch_id)
            return _review_sources(tx, branch_id, _view(tx, branch_id))[0]

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
            _ensure_exclusive(tx, branch_id, task_id)
            result = json.loads(row["result"])
            state = result["seven_stage_run"]
            revision = _story(tx, branch_id)["story_revision"]
            child_id = state.get("child_id")
            recovery = _child_recovery(tx, task_id, child_id) if child_id else None
            if recovery and _author_result_exists(tx, branch_id, state):
                recovery = None
            if recovery and recovery["state"] == "unknown":
                refs = "、".join(str(identifier) for identifier in recovery["dispatch_event_ids"])
                raise DomainError(
                    "unknown_result", f"存在未结子任务请求（派发记录 {refs}），请先核对回执。", 409
                )
            if state["stage"] in {"inspection", "completion"} and (
                revision != state["story_revision"]
                or state.get("inspection_basis_changed")
                or state.get("completion_basis_changed")
            ):
                from .outline_workspace import _view

                outline = _view(tx, branch_id)
                if (outline["confirmed"] or {}).get("id") != state["outline_id"] or outline["stale"]:
                    raise DomainError("seven_stage_changed", "上游规划已改变，请从有效版本重新接力。", 409)
                # The author explicitly resumes against newer prose. Preserve the
                # generated receipts and re-run inspection before any completion.
                state["story_revision"] = revision
                state["stage"] = "inspection"
                for key in (
                    "interruption",
                    "inspection_basis_changed",
                    "inspection_basis_sha256",
                    "inspection_candidate_id",
                    "completion_basis_changed",
                    "completion_basis_sha256",
                    "child_id",
                    "artifact_id",
                    "child_recovery",
                    "completion_preparation_id",
                    "selected_inspection_id",
                ):
                    state.pop(key, None)
                recovery = None
            if state.get("interruption"):
                raise DomainError(
                    "seven_stage_changed", "当前检查点的依据已失效，请从有效版本另行接力。", 409
                )
            if state["stage"] == "outline" and revision != state["story_revision"]:
                from .outline_workspace import _decode, _latest

                child = tx.execute(
                    "SELECT result FROM tasks WHERE id=? AND status='succeeded'", (child_id,)
                ).fetchone()
                candidate_id = json.loads(child["result"]).get("artifact_id") if child else None
                _check_outline_story(_decode(_latest(tx, branch_id)), state, candidate_id, revision)
            next_status = "queued"
            if recovery:
                if recovery["state"] == "blocked":
                    raise DomainError(
                        "seven_stage_child", "当前子任务已停止，请先核对原记录后重新启动接力。", 409
                    )
                if recovery["state"] == "retryable":
                    _unchanged(revision, state["story_revision"])
                    tx.execute(
                        "UPDATE tasks SET status='queued',cancel=0,pause=0,attempt=NULL,error='',updated=? "
                        "WHERE id=? AND status='blocked_runtime'",
                        (time(), child_id),
                    )
                    domain.db.event(tx, child_id, "resume_requested", {"seven_stage_parent": task_id})
                    next_status = "waiting_child"
                    state.pop("child_recovery", None)
            tx.execute(
                "UPDATE tasks SET status=?,result=?,pause=0,error='',updated=? WHERE id=?",
                (next_status, dumps(result), time(), task_id),
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


def _author_result_exists(tx, branch_id, state):
    if state["stage"] == "inspection":
        from .workflow_inspection import latest_book_inspection

        return _inspection_ready(latest_book_inspection(tx, branch_id))
    if state["stage"] == "completion":
        from .book_completion import _evaluate, _latest

        basis = _evaluate(tx, branch_id)["basis_sha256"]
        official = _latest(tx, branch_id, basis)
        return bool(official and official["current"])
    return False
