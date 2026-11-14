"""Loopback-only application API. Writes require a same-origin session and CSRF token."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import secrets
from time import time
from typing import Annotated
from typing import Literal

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .budget import Budget
from .author_contract import assemble_contract, authored_payload
from .db import Database, DomainError, dumps, uid
from .domain import Domain, utf16_slice
from .genre_catalog import assemble_profile, get_catalog
from .library import Library, MAX_FILE
from .models import (
    AcceptInput,
    AssessmentInput,
    CandidateEdit,
    CandidateVersionInput,
    ChapterInput,
    FactInput,
    DefinitionInput,
    FeedbackInput,
    Input,
    ModuleInput,
    NovelInput,
    RestoreInput,
    SaveInput,
    WorkflowInput,
)
from .settings import ROOT, Settings
from .modules import MODULES, public_modules
from .manual_finalization import register_routes as register_finalization_routes
from .targeted_revision import validate_targeted_revision
from .candidate_recheck import CandidateRecheckInput, sha as candidate_sha


class ReadingInput(Input):
    chapter_id: str
    cursor: int = Field(ge=0)
    note: str = Field(default="", max_length=5000)
    base_revision: int = Field(ge=0)


class ExcerptInput(Input):
    unit_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=10_000)
    note: str = Field(default="", max_length=10_000)
    novel_id: str | None = None


class ResumeInput(Input):
    acknowledge_unknown: bool = False
    acknowledge_changes: bool = False
    regenerate_pending: bool = False
    reason: str = Field(default="", max_length=2000)
    candidate_revision: int = Field(default=0, ge=0)


class ContinuationInput(Input):
    client_request_id: str = Field(min_length=8, max_length=128)
    account: str
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)
    acknowledge_unknown: bool = False


class DownloadInput(Input):
    title: str = Field(min_length=1, max_length=200)
    format: Literal["epub", "page_text"] = "epub"


class RenameInput(Input):
    title: str = Field(min_length=1, max_length=200)


class ArchiveInput(Input):
    base_revision: int = Field(ge=0)
    archived: bool


class MoveInput(Input):
    direction: Literal["up", "down"]
    base_story_revision: int = Field(ge=0)


def create_app(settings=None, schema_only=False):
    settings = settings or Settings.load()
    db = Database(settings.data_dir / "manuscripts.sqlite") if not schema_only else None
    domain, budget = (Domain(db), Budget(settings)) if not schema_only else (None, None)
    library = Library(db, settings.data_dir) if not schema_only else None

    @asynccontextmanager
    async def lifespan(app):
        from .runtime import alive, register, unregister

        if db and not any(alive(row["pid"]) for row in db.all("SELECT pid FROM processes WHERE role='api'")):
            with db.connect(True) as tx:
                tx.execute(
                    "UPDATE library_jobs SET status='failed',error='上次本机服务中断，未确认取得原件；可明确重新获取。' WHERE status IN ('queued','downloading')"
                )
        token = register(db, "api") if db else None
        try:
            yield
        finally:
            for job in jobs:
                job.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
            if token:
                unregister(db, token)

    app = FastAPI(title="ainovel 本机工作台", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.db, app.state.domain = settings, db, domain
    app.state.budget, app.state.library = budget, library
    register_finalization_routes(app, domain)
    csrf, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    jobs = set()
    origins = {
        f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (settings.port, 5173)
    }
    hosts = {f"{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (settings.port, 5173)}
    if settings.test_mode:
        hosts.add("testserver")

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.headers.get("host") not in hosts or (
            request.headers.get("origin") and request.headers["origin"] not in origins
        ):
            return JSONResponse({"code": "origin", "message": "仅允许本机工作台来源。"}, status_code=403)
        try:
            if int(request.headers.get("content-length", "0")) > 24 * 1024 * 1024:
                return JSONResponse(
                    {"code": "file_limit", "message": "请求超过 24 MiB，未读取或保存。"}, status_code=413
                )
        except ValueError:
            return JSONResponse({"code": "length", "message": "请求长度无效。"}, status_code=400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not (
                secrets.compare_digest(request.cookies.get("ainovel_session", ""), cookie)
                and secrets.compare_digest(request.headers.get("x-ainovel-csrf", ""), csrf)
            ):
                return JSONResponse(
                    {"code": "csrf", "message": "写入凭据失效，请刷新工作台后重试。"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"code": error.code, "message": error.message}, status_code=error.status)

    @app.get("/api/session")
    def session(response: Response):
        response.set_cookie("ainovel_session", cookie, httponly=True, samesite="strict")
        return {"csrf": csrf, "version": "0.1.0", "data_dir": str(settings.data_dir)}

    @app.get("/api/config")
    def configuration():
        return {
            "accounts": [account.public() for account in settings.providers.values()],
            "budget": budget.report(),
            "sending_scope": "模型仅收到本次指令、目标章、已采用作者原话与情绪钩子方向契约、已采用设定、所选频道题材按动作展开的冻结配置、前文有效事实、最近两章和主动勾选的摘录；整书任务另含简报与章纲。作者要求优先于预设，密钥仅在后端。",
        }

    @app.get("/api/novels")
    def novels():
        return db.all(
            "SELECT * FROM novels WHERE id NOT IN (SELECT novel_id FROM reference_workspace) ORDER BY created DESC"
        )

    @app.post("/api/novels", status_code=201)
    def create_novel(data: NovelInput):
        return domain.create_novel(data.title, data.brief)

    @app.patch("/api/novels/{novel_id}")
    def rename_novel(novel_id: str, data: RenameInput):
        db.one("SELECT id FROM novels WHERE id=?", (novel_id,))
        with db.connect(True) as tx:
            tx.execute("UPDATE novels SET title=? WHERE id=?", (data.title, novel_id))
        return {"id": novel_id, "title": data.title}

    @app.get("/api/novels/{novel_id}/branches")
    def branches(novel_id: str):
        return db.all("SELECT * FROM branches WHERE novel_id=? ORDER BY created", (novel_id,))

    @app.get("/api/branches/{branch_id}/chapters")
    def chapters(branch_id: str):
        domain.branch(branch_id)
        return db.all(
            "SELECT id,branch_id,title,position,revision,stale,length(content) AS chars FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position",
            (branch_id,),
        )

    @app.post("/api/branches/{branch_id}/chapters", status_code=201)
    def create_chapter(branch_id: str, data: ChapterInput):
        return domain.create_chapter(branch_id, data.title)

    @app.get("/api/chapters/{chapter_id}")
    def chapter(chapter_id: str):
        return domain.chapter(chapter_id)

    @app.put("/api/chapters/{chapter_id}")
    def save(chapter_id: str, data: SaveInput):
        return domain.save(chapter_id, data.base_revision, data.content)

    @app.patch("/api/chapters/{chapter_id}")
    def rename_chapter(chapter_id: str, data: RenameInput):
        domain.chapter(chapter_id)
        with db.connect(True) as tx:
            tx.execute("UPDATE chapters SET title=? WHERE id=?", (data.title, chapter_id))
        return domain.chapter(chapter_id)

    @app.get("/api/chapters/{chapter_id}/history")
    def history(chapter_id: str):
        domain.chapter(chapter_id)
        return db.all("SELECT * FROM revisions WHERE chapter_id=? ORDER BY revision DESC", (chapter_id,))

    @app.get("/api/branches/{branch_id}/archived")
    def archived_chapters(branch_id: str):
        domain.branch(branch_id)
        return db.all(
            "SELECT id,title,revision,position FROM chapters WHERE branch_id=? AND archived=1 ORDER BY position",
            (branch_id,),
        )

    @app.post("/api/chapters/{chapter_id}/archive")
    def archive_chapter(chapter_id: str, data: ArchiveInput):
        with db.connect(True) as tx:
            chapter = tx.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
            if not chapter:
                raise DomainError("not_found", "章节不存在。", 404)
            if chapter["revision"] != data.base_revision:
                raise DomainError("revision_conflict", "章节已有新版本，未改变归档状态。", 409)
            position = (
                chapter["position"]
                if data.archived
                else tx.execute(
                    "SELECT COALESCE(MAX(position),0)+1 FROM chapters WHERE branch_id=?",
                    (chapter["branch_id"],),
                ).fetchone()[0]
            )
            tx.execute(
                "UPDATE chapters SET archived=?,position=? WHERE id=?",
                (int(data.archived), position, chapter_id),
            )
            tx.execute("UPDATE facts SET stale=1 WHERE branch_id=?", (chapter["branch_id"],))
            tx.execute(
                "UPDATE chapters SET stale=1 WHERE branch_id=? AND content!=''", (chapter["branch_id"],)
            )
            tx.execute(
                "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (chapter["branch_id"],)
            )
        return {
            "id": chapter_id,
            "archived": data.archived,
            "note": "正文与历史保留；恢复归档放到目录末尾。结构变化后的状态需复检。",
        }

    @app.post("/api/chapters/{chapter_id}/move")
    def move_chapter(chapter_id: str, data: MoveInput):
        chapter = domain.chapter(chapter_id)
        with db.connect(True) as tx:
            story_revision = tx.execute(
                "SELECT story_revision FROM branches WHERE id=?", (chapter["branch_id"],)
            ).fetchone()[0]
            if story_revision != data.base_story_revision:
                raise DomainError("revision_conflict", "章节目录或正文已改变，请刷新后重排。", 409)
            rows = tx.execute(
                "SELECT id FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position,id",
                (chapter["branch_id"],),
            ).fetchall()
            ordered = [row[0] for row in rows]
            index = ordered.index(chapter_id)
            target = index + (-1 if data.direction == "up" else 1)
            if not 0 <= target < len(ordered):
                raise DomainError("order", "该章节已在目录边界。")
            ordered[index], ordered[target] = ordered[target], ordered[index]
            for position, item in enumerate(ordered, 1):
                tx.execute(
                    "UPDATE chapters SET position=?,stale=CASE WHEN content!='' THEN 1 ELSE 0 END WHERE id=?",
                    (position, item),
                )
            tx.execute("UPDATE facts SET stale=1 WHERE branch_id=?", (chapter["branch_id"],))
            tx.execute(
                "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (chapter["branch_id"],)
            )
        return chapters(chapter["branch_id"])

    @app.post("/api/chapters/{chapter_id}/restore")
    def restore(chapter_id: str, data: RestoreInput):
        return domain.restore(chapter_id, data.base_revision, data.revision_id)

    @app.get("/api/branches/{branch_id}/reading-state")
    def reading_state(branch_id: str):
        domain.branch(branch_id)
        rows = db.all("SELECT * FROM reading_state WHERE branch_id=?", (branch_id,))
        return (
            rows[0]
            if rows
            else {"branch_id": branch_id, "chapter_id": None, "cursor": 0, "note": "", "revision": 0}
        )

    @app.put("/api/branches/{branch_id}/reading-state")
    def save_reading_state(branch_id: str, data: ReadingInput):
        chapter = domain.chapter(data.chapter_id, branch_id)
        utf16_slice(chapter["content"], 0, data.cursor)
        if data.cursor > len(chapter["content"].encode("utf-16-le")) // 2:
            raise DomainError("cursor", "阅读位置超出已保存正文。")
        with db.connect(True) as tx:
            row = tx.execute("SELECT revision FROM reading_state WHERE branch_id=?", (branch_id,)).fetchone()
            revision = row[0] if row else 0
            if revision != data.base_revision:
                raise DomainError("revision_conflict", "工作位置已在另一窗口改变。", 409)
            tx.execute(
                "INSERT OR REPLACE INTO reading_state VALUES(?,?,?,?,?)",
                (branch_id, data.chapter_id, data.cursor, data.note, revision + 1),
            )
        return reading_state(branch_id)

    @app.post("/api/modules", status_code=202)
    def run_module(data: ModuleInput):
        if data.action.startswith("reference."):
            raise DomainError("reference_route", "请从参考会话提交动作，以冻结选段、规则、方案和检查对象。")
        provider_ready(data.account)
        if (
            data.action
            in (
                "writing.rewrite_selection",
                "writing.expand_plot",
                "review.chapter",
                "memory.extract",
                "revision.targeted",
            )
            and not data.chapter_id
        ):
            raise DomainError("chapter_required", "该操作需要章节。")
        payload = data.model_dump()
        for key in (
            "genre_profile_basis",
            "author_contract_basis",
            "source_review_id",
            "reference_session_id",
            "reference_revision",
        ):
            if payload[key] is None:
                payload.pop(key)
        if not payload["finding_indices"]:
            payload.pop("finding_indices")
        if data.action == "revision.targeted":
            validate_targeted_revision(domain, payload, domain.context(data.branch_id, data.chapter_id))
        return domain.queue("module", {**payload, "module_version": MODULES[data.action].version})

    from .reference import register_routes as register_reference_routes
    from .story_graph import register_routes as register_story_routes

    register_story_routes(app, domain)
    from .story_inheritance import register_routes as register_inheritance_routes

    register_inheritance_routes(app, domain)
    from .scene_time import register_routes as register_scene_routes

    register_scene_routes(app, domain)
    from .manuscript_import import register_routes as register_import_routes

    register_import_routes(app, domain)
    from .story_extraction import register_routes as register_extraction_routes

    @app.get("/api/modules")
    def module_specs():
        return public_modules()

    @app.get("/api/genre-catalog")
    def genre_catalog():
        return get_catalog()

    @app.get("/api/branches/{branch_id}/genre-profile")
    def genre_profile(branch_id: str):
        profile = domain.genre_profile(branch_id)
        source_id = profile["source_artifact_id"]
        return {
            **profile,
            "branch_id": branch_id,
            "story_revision": profile["source_story_revision"],
            "reader_definition": domain.artifact(source_id)["payload"] if source_id else None,
            "source_revision_note": "本次装配时的故事版本；保存设定使用 story_revision 进行版本校验。",
        }

    @app.post("/api/genre-profile/preview")
    def preview_genre_profile(data: DefinitionInput):
        if data.kind != "reader":
            raise DomainError("genre_profile", "频道题材预览仅用于读者与题材配置。")
        return {
            **assemble_profile(data.model_dump(exclude={"kind", "base_story_revision"})),
            "source_artifact_id": None,
            "source_story_revision": None,
            "preview": True,
        }

    @app.get("/api/branches/{branch_id}/author-contract")
    def author_contract(branch_id: str):
        contract = domain.author_contract(branch_id)
        source_id = contract["source_artifact_id"]
        return {
            **contract,
            "story_revision": contract["source_story_revision"],
            "raw_definition": domain.artifact(source_id)["payload"] if source_id else None,
            "source_revision_note": "本次装配时的故事版本；保存使用 story_revision，生成使用来源标识和契约版本。",
        }

    @app.post("/api/author-contract/preview")
    def preview_author_contract(data: DefinitionInput):
        if data.kind != "intent":
            raise DomainError("author_contract", "作者契约预览仅用于创作意图。")
        return {
            **assemble_contract(authored_payload(data)),
            "branch_id": None,
            "source_artifact_id": None,
            "source_story_revision": None,
            "preview": True,
        }

    def provider_ready(account):
        provider = settings.providers.get(account)
        if not provider or not provider.key or provider.input_rate is None or provider.output_rate is None:
            raise DomainError("configuration", "模型账户或单价尚未配置，请查看 .env 与 ainovel.toml。")

    @app.post("/api/workflows", status_code=202)
    def start_workflow(data: WorkflowInput):
        from .story_inheritance import inherit

        if data.scene_maintenance and not data.structured_maintenance:
            raise DomainError("scene_policy", "自动场景维护需要同时启用章节结构维护。")

        payload = data.model_dump()
        for key in ("genre_profile_basis", "author_contract_basis"):
            if payload[key] is None:
                payload.pop(key)
        previous = domain.find_request("workflow", payload)
        if previous:
            return previous
        provider_ready(data.account)
        db.one("SELECT id FROM novels WHERE id=?", (data.novel_id,))
        if data.min_chars > data.max_chars:
            raise DomainError("length", "字数下限不能大于上限。")
        if data.mode == "assisted" and [p.number for p in data.plots] != list(range(1, data.chapters + 1)):
            raise DomainError("plots", "半自动需要连续且完整的章级情节，可批量提供。")
        if data.mode == "automatic" and data.plots:
            raise DomainError("plots", "全自动仅接收简报与粗纲，不复用半自动的章级输入。")
        source = domain.branch(data.source_branch_id, data.novel_id) if data.source_branch_id else None
        if data.inherit_story_record_ids and (not source or data.inherit_source_revision is None):
            raise DomainError("story_inheritance", "继承结构需要明确来源工作版本及已读取的故事修订。", 409)
        definitions = domain.context(source["id"])["definitions"] if source else []
        profile = (
            domain.genre_profile(source["id"], definitions)
            if source
            else {
                **assemble_profile(None),
                "source_artifact_id": None,
            }
        )
        domain.check_genre_basis(
            profile, data.genre_profile_basis.model_dump() if data.genre_profile_basis else None
        )
        contract = (
            domain.author_contract(source["id"], definitions)
            if source
            else {
                **assemble_contract(None),
                "source_artifact_id": None,
            }
        )
        domain.check_author_contract_basis(
            contract, data.author_contract_basis.model_dump() if data.author_contract_basis else None
        )
        branch_id = uid()
        with db.connect(True) as tx:
            if (
                source
                and tx.execute("SELECT story_revision FROM branches WHERE id=?", (source["id"],)).fetchone()[
                    0
                ]
                != source["story_revision"]
            ):
                raise DomainError("story_changed", "源作品设定在创建时改变，请刷新后重试。", 409)
            tx.execute(
                "INSERT INTO branches(id,novel_id,name,mode,created) VALUES(?,?,?,?,?)",
                (
                    branch_id,
                    data.novel_id,
                    ("半自动" if data.mode == "assisted" else "全自动") + "独立稿 " + branch_id[:6],
                    data.mode,
                    time(),
                ),
            )
            for definition in definitions:
                tx.execute(
                    "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) VALUES(?,?,?,?,?,?,'accepted',?)",
                    (
                        uid(),
                        data.novel_id,
                        branch_id,
                        definition["kind"],
                        dumps(definition["payload"]),
                        dumps(
                            {
                                "source_artifact": definition["id"],
                                "source_story_revision": source["story_revision"],
                            }
                        ),
                        time(),
                    ),
                )
            if source:
                inherit(
                    tx,
                    source["id"],
                    branch_id,
                    data.novel_id,
                    data.inherit_story_record_ids,
                    data.inherit_source_revision,
                )
        return domain.queue("workflow", payload, branch_id)

    @app.post("/api/branches/{branch_id}/definitions", status_code=201)
    def save_definition(branch_id: str, data: DefinitionInput):
        branch = domain.branch(branch_id)
        if data.kind != "intent" and (data.intent_contract is not None or data.intent_source_candidate_id):
            raise DomainError("author_contract", "创作契约字段只能保存到创作意图。")
        source_candidate = None
        if data.intent_source_candidate_id:
            source_candidate = domain.artifact(data.intent_source_candidate_id)
            if (
                source_candidate["branch_id"] != branch_id
                or source_candidate["kind"] != "intent"
                or not source_candidate["payload"].get("intent_contract")
                or source_candidate["payload"].get("provenance", {}).get("origin")
                != "ai_compilation_candidate"
            ):
                raise DomainError("author_contract", "提案来源不是本版本的作者契约候选。", 409)
            if source_candidate["basis"].get("story_revision") != branch["story_revision"]:
                raise DomainError("story_changed", "契约提案所依据的故事版本已改变，请重新整理后核对。", 409)
        payload = (
            authored_payload(data, source_candidate)
            if data.kind == "intent"
            else data.model_dump(
                exclude={"kind", "base_story_revision", "intent_contract", "intent_source_candidate_id"}
            )
        )
        artifact = domain.stage(
            {"novel_id": branch["novel_id"], "branch_id": branch_id},
            data.kind,
            payload,
            {"story_revision": data.base_story_revision},
        )
        domain.accept(artifact["id"], "author-definition-" + artifact["id"])
        return domain.context(branch_id)

    @app.get("/api/tasks")
    def tasks(branch_id: str | None = None):
        rows = db.all(
            "SELECT id FROM tasks"
            + (" WHERE branch_id=?" if branch_id else "")
            + " ORDER BY created DESC LIMIT 50",
            (branch_id,) if branch_id else (),
        )
        return [domain.task(row["id"]) for row in rows]

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return domain.task(task_id)

    @app.get("/api/tasks/{task_id}/transcript")
    def transcript(task_id: str):
        domain.task(task_id)
        steps = db.all(
            "SELECT fingerprint,result,created FROM task_steps WHERE task_id=? ORDER BY created DESC LIMIT 20",
            (task_id,),
        )
        events = db.all(
            "SELECT id,kind,payload,created FROM task_events WHERE task_id=? AND kind='partial_retained' ORDER BY id DESC LIMIT 5",
            (task_id,),
        )
        return {
            "steps": [{**row, "result": json.loads(row["result"])} for row in steps],
            "partials": [{**row, "payload": json.loads(row["payload"])} for row in events],
            "scope": "最近20项已知完成步骤及最近5项中断输出；仅为可恢复产物，不表示已采纳。",
        }

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: str, request: Request, after: int = 0):
        domain.task(task_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise DomainError("event_cursor", "事件位置无效。")

        async def feed():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = db.all(
                    "SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id LIMIT 200",
                    (task_id, cursor),
                )
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\ndata: {dumps({'kind': row['kind'], 'payload': json.loads(row['payload'])})}\n\n"
                if not rows:
                    state = domain.task(task_id)["status"]
                    if state not in ("running", "queued"):
                        yield f"event: end\ndata: {dumps({'status': state})}\n\n"
                        return
                    yield ": keepalive\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(feed(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        domain.task(task_id)
        with db.connect(True) as tx:
            tx.execute(
                "UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('queued','running')",
                (task_id,),
            )
            db.event(tx, task_id, "cancel_requested", {})
        return domain.task(task_id)

    @app.post("/api/tasks/{task_id}/pause")
    def pause(task_id: str):
        current = domain.task(task_id)
        if current["kind"] != "workflow":
            raise DomainError("state", "单次生成请使用取消；批次支持在章间暂停。")
        with db.connect(True) as tx:
            tx.execute(
                "UPDATE tasks SET pause=1,status=CASE WHEN status='queued' THEN 'paused' ELSE status END WHERE id=?",
                (task_id,),
            )
        return domain.task(task_id)

    @app.post("/api/tasks/{task_id}/resume")
    def resume(task_id: str, data: ResumeInput):
        current = domain.task(task_id)
        if current["status"] not in (
            "paused",
            "blocked_runtime",
            "cancelled",
            "needs_review",
            "blocked_creative",
        ):
            raise DomainError("state", "当前状态不能恢复。", 409)
        if current["status"] in ("blocked_runtime", "cancelled") and not data.acknowledge_unknown:
            raise DomainError("unknown_result", "上次调用可能已计费；确认账本后可明确重新派发。", 409)
        result = current["result"]
        domain.check_genre_snapshot(current["branch_id"], result.get("genre_profile_snapshot"))
        domain.check_author_contract_snapshot(current["branch_id"], result.get("author_contract_snapshot"))
        reject_pending = None
        branch_snapshot = None
        if current["kind"] == "workflow":
            structure = result.get("waiting_structure")
            if structure:
                candidate = domain.artifact(structure)
                if not data.regenerate_pending:
                    raise DomainError(
                        "structure_review_required",
                        "请先在结构整理中确认采用待审记录，或填写原因后重做。",
                        409,
                    )
                if candidate["status"] not in ("candidate", "rejected") or not data.reason.strip():
                    raise DomainError("retry_reason", "结构重做需要未采用或已拒绝候选及明确原因。", 409)
                if candidate["candidate_revision"] != data.candidate_revision:
                    raise DomainError("candidate_conflict", "结构候选已更新，请重新核对。", 409)
                reject_pending = structure if candidate["status"] == "candidate" else None
                result["pending_structure"] = {**result["pending_structure"], "epoch": uid()}
                result["pending_structure"].pop("artifact_id", None)
                result.pop("waiting_structure")
                result.setdefault("interventions", []).append(
                    {
                        "action": "regenerate_structure",
                        "artifact_id": structure,
                        "reason": data.reason,
                        "time": time(),
                    }
                )
            waiting = result.get("waiting_artifact")
            if data.regenerate_pending and not structure:
                if (
                    not waiting
                    or domain.artifact(waiting)["status"] != "candidate"
                    or not data.reason.strip()
                ):
                    raise DomainError(
                        "retry_reason",
                        "重做需要一份未采纳候选及明确原因；旧候选将拒绝，费用与干预保留。",
                        409,
                    )
                if domain.artifact(waiting)["candidate_revision"] != data.candidate_revision:
                    raise DomainError("candidate_conflict", "待审候选已更新，请重新比较后决定是否重做。", 409)
                reject_pending = waiting
                result["generation_epoch"] = uid()
                result.setdefault("interventions", []).append(
                    {
                        "action": "regenerate_pending",
                        "artifact_id": waiting,
                        "reason": data.reason,
                        "time": time(),
                    }
                )
                result.pop("waiting_artifact")
                waiting = None
            if waiting and domain.artifact(waiting)["status"] != "accepted":
                raise DomainError("review_required", "请先采纳待审候选；拒绝后可启动新的工作版本。", 409)
            branch = domain.branch(current["branch_id"])
            branch_snapshot = branch["story_revision"]
            if result.get("expected_story_revision", branch["story_revision"]) != branch["story_revision"]:
                stale = db.all(
                    "SELECT position FROM chapters WHERE branch_id=? AND stale=1 ORDER BY position LIMIT 1",
                    (current["branch_id"],),
                )
                if not data.acknowledge_changes and (not waiting or stale):
                    raise DomainError("story_changed", "前文已改变；确认后从最早失效章重新检查生成。", 409)
                if stale:
                    result.pop("waiting_artifact", None)
                    waiting = None
                    result["next_chapter"] = min(result.get("next_chapter", 1), stale[0]["position"])
                pending_structure = result.get("pending_structure")
                if pending_structure:
                    source = domain.chapter(pending_structure["chapter_id"], current["branch_id"])
                    if stale and stale[0]["position"] <= source["position"]:
                        result.pop("pending_structure")
                        result.pop("waiting_structure", None)
                    elif source["revision"] != pending_structure["revision"]:
                        result["pending_structure"] = {
                            "chapter_id": source["id"],
                            "revision": source["revision"],
                            "epoch": uid(),
                        }
                        result.pop("waiting_structure", None)
                    result.setdefault("interventions", []).append(
                        {
                            "action": "recheck_structure_after_author_changes",
                            "chapter_id": source["id"],
                            "revision": source["revision"],
                            "time": time(),
                        }
                    )
                result["expected_story_revision"] = branch["story_revision"]
        with db.connect(True) as tx:
            latest = tx.execute("SELECT status,updated FROM tasks WHERE id=?", (task_id,)).fetchone()
            if latest["status"] != current["status"] or latest["updated"] != current["updated"]:
                raise DomainError("task_changed", "任务状态已改变，请刷新后操作。", 409)
            if (
                branch_snapshot is not None
                and tx.execute(
                    "SELECT story_revision FROM branches WHERE id=?", (current["branch_id"],)
                ).fetchone()[0]
                != branch_snapshot
            ):
                raise DomainError("story_changed", "恢复检查期间故事已改变，请重新核对。", 409)
            if reject_pending:
                pending_row = tx.execute(
                    "SELECT payload FROM artifacts WHERE id=?", (reject_pending,)
                ).fetchone()
                if (
                    not pending_row
                    or json.loads(pending_row["payload"]).get("_candidate_revision", 0)
                    != data.candidate_revision
                ):
                    raise DomainError("candidate_conflict", "恢复检查期间候选已更新，请重新比较。", 409)
                changed = tx.execute(
                    "UPDATE artifacts SET status='rejected' WHERE id=? AND status='candidate'",
                    (reject_pending,),
                )
                if changed.rowcount != 1:
                    raise DomainError("candidate_changed", "候选状态已改变，请重新审阅。", 409)
            tx.execute(
                "UPDATE tasks SET status='queued',cancel=0,pause=0,attempt=NULL,error='',result=?,updated=? WHERE id=?",
                (dumps(result), time(), task_id),
            )
            db.event(tx, task_id, "resume_requested", data.model_dump())
        return domain.task(task_id)

    @app.post("/api/tasks/{task_id}/continue", status_code=202)
    def continue_with_account(task_id: str, data: ContinuationInput):
        current = domain.task(task_id)
        if current["kind"] != "workflow":
            raise DomainError("state", "仅整书任务支持从检查点换账户续跑。", 409)
        payload = WorkflowInput.model_validate(
            {
                **current["payload"],
                "account": data.account,
                "budget_limit": data.budget_limit,
                "client_request_id": data.client_request_id,
            }
        ).model_dump()
        previous = domain.find_request("workflow", payload)
        if previous:
            return domain.continue_workflow(current, payload, data.model_dump())
        provider_ready(data.account)
        if (
            data.budget_limit is not None
            and data.budget_limit > settings.limits[settings.providers[data.account].kind]
        ):
            raise DomainError("budget", "续跑子预算不能高于该服务商的共享上限。", 409)
        return domain.continue_workflow(current, payload, data.model_dump())

    @app.get("/api/artifacts")
    def artifacts(branch_id: str, chapter_id: str | None = None):
        rows = db.all(
            "SELECT id FROM artifacts WHERE branch_id=?"
            + (" AND chapter_id=?" if chapter_id else "")
            + " ORDER BY created DESC LIMIT 50",
            (branch_id, chapter_id) if chapter_id else (branch_id,),
        )
        return [domain.artifact(row["id"]) for row in rows]

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        return domain.artifact(artifact_id)

    @app.get("/api/artifacts/{artifact_id}/history")
    def candidate_history(artifact_id: str, offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0):
        """分页返回候选编辑与复检前的只读快照；旧事件缺失的内容不推造。"""
        source = domain.artifact(artifact_id)
        rows = db.all(
            "SELECT id,kind,payload,created FROM task_events WHERE task_id=? "
            "AND kind IN ('candidate_edited','candidate_rechecked') "
            "AND json_extract(payload,'$.artifact_id')=? ORDER BY id DESC LIMIT 21 OFFSET ?",
            (source["task_id"], artifact_id, offset),
        )
        return {
            "artifact_id": artifact_id,
            "items": [{**row, "payload": json.loads(row["payload"])} for row in rows[:20]],
            "next_offset": offset + 20 if len(rows) > 20 else None,
        }

    @app.patch("/api/artifacts/{artifact_id}")
    def edit_candidate(artifact_id: str, data: CandidateEdit):
        with db.connect(True) as tx:
            row = tx.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row or row["status"] != "candidate":
                raise DomainError("candidate_state", "只能编辑待采纳候选。", 409)
            payload = json.loads(row["payload"])
            if "text" not in payload:
                raise DomainError("candidate_kind", "结构化报告请用事实更正或重新审查。")
            current_revision = payload.get("_candidate_revision", 0)
            if current_revision != data.candidate_revision:
                raise DomainError("candidate_conflict", "候选已在其他窗口更新，本地编辑请保留并比较。", 409)
            previous_candidate = json.loads(dumps(payload))
            if "review" in payload:
                payload["previous_review"] = payload.pop("review")
            if "plot_audit" in payload:
                payload["previous_plot_audit"] = payload.pop("plot_audit")
            if "reference_comparison" in payload:
                payload["previous_reference_comparison"] = payload.pop("reference_comparison")
            if "revision_trace" in payload:
                payload["previous_revision_trace"] = payload.pop("revision_trace")
            payload.update(text=data.text, review_passed=False, memory={})
            payload["_candidate_revision"] = current_revision + 1
            tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact_id))
            if row["task_id"]:
                tx.execute("UPDATE tasks SET updated=? WHERE id=?", (time(), row["task_id"]))
                db.event(
                    tx,
                    row["task_id"],
                    "candidate_edited",
                    {
                        "artifact_id": artifact_id,
                        "candidate_revision": current_revision + 1,
                        "previous_revision": current_revision,
                        "previous_content_sha256": candidate_sha(previous_candidate["text"]),
                        "previous_candidate": previous_candidate,
                    },
                )
        return domain.artifact(artifact_id)

    @app.post("/api/artifacts/{artifact_id}/accept")
    def accept_candidate(artifact_id: str, data: AcceptInput):
        return domain.accept(artifact_id, data.idempotency_key, candidate_revision=data.candidate_revision)

    @app.post("/api/artifacts/{artifact_id}/recheck", status_code=202)
    def recheck_candidate(artifact_id: str, data: CandidateRecheckInput):
        """按原写前冻结契约复检待审候选，不重新解释情节、不采纳或恢复原流程。"""
        source = domain.artifact(artifact_id)
        payload = {
            **data.model_dump(),
            "source_candidate_id": artifact_id,
            "plot_contract_check": True,
            "novel_id": source["novel_id"],
            "branch_id": source["branch_id"],
            "chapter_id": source["chapter_id"],
            "base_revision": source["base_revision"],
        }
        previous = domain.find_request("candidate_recheck", payload)
        if previous:
            return previous
        provider_ready(data.account)
        return domain.queue("candidate_recheck", payload)

    @app.post("/api/artifacts/{artifact_id}/reject")
    def reject(artifact_id: str, data: CandidateVersionInput | None = None):
        if domain.artifact(artifact_id)["kind"] == "candidate_recheck":
            raise DomainError("candidate_recheck_readonly", "候选复检报告只读，原报告及关联保持。", 409)
        with db.connect(True) as tx:
            row = tx.execute("SELECT status,payload FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise DomainError("not_found", "候选不存在。", 404)
            if row["status"] == "accepted":
                raise DomainError("candidate_state", "该候选已经采纳，请在正文历史中恢复。", 409)
            if row["status"] == "candidate" and json.loads(row["payload"]).get("_candidate_revision", 0) != (
                data.candidate_revision if data else 0
            ):
                raise DomainError("candidate_conflict", "候选已有新修订，请比较后再拒绝。", 409)
            tx.execute(
                "UPDATE artifacts SET status='rejected' WHERE id=? AND status='candidate'", (artifact_id,)
            )
        return domain.artifact(artifact_id)

    @app.get("/api/branches/{branch_id}/context")
    def context(branch_id: str, chapter_id: str | None = None, text_position: int = Query(0, ge=0)):
        return domain.context(branch_id, chapter_id, text_position)

    @app.get("/api/branches/{branch_id}/facts")
    def facts(branch_id: str):
        return db.all(
            "SELECT f.*,c.position,c.title FROM facts f JOIN chapters c ON c.id=f.chapter_id WHERE f.branch_id=? ORDER BY c.position,f.id",
            (branch_id,),
        )

    @app.get("/api/branches/{branch_id}/search")
    def search_manuscript(branch_id: str, q: str, before: int = 101):
        domain.branch(branch_id)
        if not 1 <= len(q) <= 200 or not 1 <= before <= 101:
            raise DomainError("query", "请输入1—200字符的原文查询，并选择有效章节范围。")
        rows = db.all(
            "SELECT * FROM chapters WHERE branch_id=? AND position<? AND archived=0 AND instr(content,?)>0 ORDER BY position LIMIT 100",
            (branch_id, before, q),
        )
        for row in rows:
            index = row["content"].find(q)
            row["start"] = len(row["content"][:index].encode("utf-16-le")) // 2
            row["end"] = row["start"] + len(q.encode("utf-16-le")) // 2
            row["snippet"] = row["content"][max(0, index - 80) : index + len(q) + 120]
            row.pop("content")
        return {
            "hits": rows,
            "scope": "当前工作版本指定章节之前的正文，每章首个连续子串命中；版本号与UTF-16定位随结果返回。",
        }

    @app.post("/api/facts", status_code=201)
    def add_fact(data: FactInput):
        chapter = domain.chapter(data.chapter_id)
        with db.connect(True) as tx:
            row = tx.execute("SELECT * FROM chapters WHERE id=?", (data.chapter_id,)).fetchone()
            if row["revision"] != data.source_revision:
                raise DomainError("revision_conflict", "事实来源版本已改变。", 409)
            domain.add_fact_tx(
                tx,
                chapter["branch_id"],
                chapter["id"],
                data.source_revision,
                data.model_dump(exclude={"chapter_id", "source_revision"}),
                row["content"],
            )
            tx.execute(
                "UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (chapter["branch_id"],)
            )
        return {"saved": True}

    @app.delete("/api/facts/{fact_id}")
    def invalidate_fact(fact_id: str):
        with db.connect(True) as tx:
            row = tx.execute("SELECT branch_id FROM facts WHERE id=?", (fact_id,)).fetchone()
            if not row:
                raise DomainError("not_found", "事实不存在。", 404)
            tx.execute("UPDATE facts SET stale=1 WHERE id=?", (fact_id,))
            tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (row[0],))
        return {"invalidated": True}

    @app.get("/api/branches/{branch_id}/feedback")
    def feedback(branch_id: str):
        return db.all(
            "SELECT f.*,c.title,c.position FROM feedback f JOIN chapters c ON c.id=f.chapter_id WHERE c.branch_id=? ORDER BY created DESC",
            (branch_id,),
        )

    @app.post("/api/feedback", status_code=201)
    def add_feedback(data: FeedbackInput):
        chapter = domain.chapter(data.chapter_id)
        if data.source_quote and data.source_quote not in chapter["content"]:
            raise DomainError("evidence", "反馈引用不在本章当前正文中。")
        with db.connect(True) as tx:
            tx.execute(
                "INSERT INTO feedback VALUES(?,?,?,?,?,?,?)",
                (
                    uid(),
                    data.chapter_id,
                    chapter["revision"],
                    data.category,
                    data.text,
                    data.source_quote,
                    time(),
                ),
            )
        return {"saved": True, "note": "反馈不会自动改动作者方向，可转为定向修订指令。"}

    @app.get("/api/branches/{branch_id}/completion")
    def completion(branch_id: str):
        return domain.completion(branch_id)

    @app.post("/api/assessments", status_code=201)
    def assess(data: AssessmentInput):
        branch = domain.branch(data.branch_id)
        allowed = {"准备", "查找", "提示调整", "等待", "核查", "返修", "整理保存", "基线总计"}
        if any(
            key not in allowed or value < 0 or value > 100_000 or value != value
            for key, value in data.minutes.items()
        ):
            raise DomainError("minutes", "投入记录需为有效的非负分钟数。")
        with db.connect(True) as tx:
            tx.execute(
                "INSERT INTO assessments VALUES(?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    data.branch_id,
                    branch["story_revision"],
                    data.category,
                    data.result,
                    data.note,
                    dumps(data.minutes),
                    time(),
                ),
            )
        return domain.completion(data.branch_id)

    @app.get("/api/branches/{branch_id}/export")
    def export(branch_id: str, format: str = "md"):
        if format not in ("md", "txt"):
            raise DomainError("format", "支持 Markdown 或 TXT。")
        return Response(
            domain.export(branch_id, format),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="ainovel-{branch_id[:8]}.{format}"'},
        )

    @app.post("/api/branches/{branch_id}/import", status_code=201)
    async def import_chapter(branch_id: str, file: Annotated[UploadFile, File()]):
        raw = await file.read(1_000_001)
        if len(raw) > 1_000_000 or Path(file.filename or "").suffix.lower() not in (".txt", ".md"):
            raise DomainError("format", "正文导入支持 1 MB 内 UTF-8 TXT／Markdown，导入为新章。")
        try:
            content = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            raise DomainError("encoding", "请先将正文转为 UTF-8。")
        if len(content) > 250_000:
            raise DomainError("length", "单章超过 25 万字符。")
        chapter = domain.create_chapter(branch_id, Path(file.filename).stem[:200] or "导入章节")
        return domain.save(chapter["id"], 0, content, "导入为新章")

    @app.get("/api/library/books")
    def books():
        """返回导入时资料记录；file_hash是导入文件SHA，原件变化不会自动重读。同名再导入建立独立资料。"""
        return db.all("SELECT * FROM books ORDER BY created DESC")

    @app.post("/api/library/import", status_code=201)
    async def import_book(
        file: Annotated[UploadFile, File()], encoding: Annotated[str, Form()] = "utf-8-sig"
    ):
        raw = await file.read(MAX_FILE + 1)
        return library.import_file(file.filename or "资料.txt", raw, "本地导入", encoding)

    @app.get("/api/library/search")
    def search(q: str, book_id: str | None = None, novel_id: str | None = None, offset: int = 0):
        """只检索导入时正文快照；命中file_hash为导入文件身份，不表示已核对当前磁盘原件。"""
        return library.search(q, book_id, novel_id, offset)

    @app.get("/api/library/books/{book_id}/units")
    def units(book_id: str):
        """返回导入时正文快照的单元目录，不因外部原件变化自动重解析。"""
        return db.all(
            "SELECT id,book_id,position,title,length(content) AS chars FROM source_units WHERE book_id=? ORDER BY position",
            (book_id,),
        )

    @app.get("/api/library/units/{unit_id}")
    def unit(unit_id: str):
        """content是导入时正文快照，file_hash是导入文件SHA；不保证当前原件仍存在或未改变。"""
        return db.one(
            "SELECT u.*,b.title AS book_title,b.source,b.file_hash FROM source_units u JOIN books b ON b.id=u.book_id WHERE u.id=?",
            (unit_id,),
        )

    @app.post("/api/library/excerpts", status_code=201)
    def excerpt(data: ExcerptInput):
        return library.excerpt(data.unit_id, data.start, data.end, data.quote, data.note, data.novel_id)

    @app.get("/api/library/excerpts")
    def excerpts(novel_id: str | None = None):
        return db.all(
            "SELECT e.*,b.title,b.source,u.book_id FROM excerpts e JOIN source_units u ON u.id=e.unit_id JOIN books b ON b.id=u.book_id"
            + (" WHERE e.novel_id=? OR e.novel_id IS NULL" if novel_id else "")
            + " ORDER BY e.created DESC LIMIT 200",
            (novel_id,) if novel_id else (),
        )

    @app.get("/api/library/catalog")
    async def catalog(q: str):
        return await library.find_books(q)

    @app.get("/api/library/jobs")
    def download_jobs():
        return db.all("SELECT * FROM library_jobs ORDER BY created DESC LIMIT 30")

    @app.post("/api/library/download", status_code=202)
    async def download(data: DownloadInput):
        job_id = uid()
        with db.connect(True) as tx:
            tx.execute(
                "INSERT INTO library_jobs(id,title,status,created,format) VALUES(?,?,'queued',?,?)",
                (job_id, data.title, time(), data.format),
            )
        job = asyncio.create_task(library.acquire(job_id))
        jobs.add(job)
        job.add_done_callback(jobs.discard)
        return {"id": job_id, "status": "queued"}

    @app.post("/api/library/jobs/{job_id}/cancel")
    def cancel_download(job_id: str):
        with db.connect(True) as tx:
            tx.execute(
                "UPDATE library_jobs SET status='cancelled' WHERE id=? AND status IN ('queued','downloading')",
                (job_id,),
            )
        return db.one("SELECT * FROM library_jobs WHERE id=?", (job_id,))

    @app.get("/api/library/books/{book_id}/progress")
    def book_progress(book_id: str):
        rows = db.all("SELECT * FROM book_progress WHERE book_id=?", (book_id,))
        return rows[0] if rows else {"book_id": book_id, "unit_id": None, "offset": 0, "revision": 0}

    @app.put("/api/library/books/{book_id}/progress")
    def save_book_progress(
        book_id: str, unit_id: str, offset: Annotated[int, Query(ge=0)], base_revision: int
    ):
        row = db.one("SELECT * FROM source_units WHERE id=? AND book_id=?", (unit_id, book_id))
        utf16_slice(row["content"], 0, offset)
        with db.connect(True) as tx:
            old = tx.execute("SELECT revision FROM book_progress WHERE book_id=?", (book_id,)).fetchone()
            if (old[0] if old else 0) != base_revision:
                raise DomainError("revision_conflict", "阅读位置已有新版本。", 409)
            tx.execute(
                "INSERT OR REPLACE INTO book_progress VALUES(?,?,?,?)",
                (book_id, unit_id, offset, base_revision + 1),
            )
        return book_progress(book_id)

    @app.post("/api/backups")
    def backup():
        from .backups import create_backup

        return create_backup(settings, db)

    @app.get("/api/backups")
    def backups():
        folder = settings.data_dir / "backups"
        return [
            {"id": path.stem, "bytes": path.stat().st_size}
            for path in sorted(folder.glob("*.zip"), reverse=True)
        ]

    @app.post("/api/backups/{backup_id}/verify")
    def verify_backup(backup_id: str):
        from .backups import verify_backup

        if len(backup_id) != 32 or any(c not in "0123456789abcdef" for c in backup_id):
            raise DomainError("backup_id", "备份标识无效。")
        return verify_backup(settings.data_dir / "backups" / (backup_id + ".zip"))

    register_reference_routes(app, domain, provider_ready)
    register_extraction_routes(app, domain, provider_ready)

    dist = ROOT / "dist"
    if dist.is_dir():
        if not settings.test_mode and not schema_only:
            from .ui_snapshot import freeze_ui

            dist = freeze_ui(dist, settings.data_dir)
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(dist / "index.html")

    return app
