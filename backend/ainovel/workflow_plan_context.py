"""Freeze adopted creation context without teaching the source interpreter facts."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from .db import DomainError, dumps
from .genre_catalog import profile_for_action
from .story_graph import graph

VERSION = "workflow-plan-creative-context/1"
KEY = "creative_context"


def sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def freeze(worker, row, chapter):
    """Called only while constructing a clean new preparation row, before dispatch."""
    expected = worker.task["result"]["expected_story_revision"]
    branch_id = worker.task["branch_id"]
    worker.domain.check_genre_snapshot(branch_id, worker.genre_snapshot())
    worker.domain.check_author_contract_snapshot(branch_id, worker.author_snapshot())
    context = worker.context(branch_id, chapter["id"], text_position=0)
    snapshot = graph(worker.domain, branch_id)
    if snapshot["story_revision"] != expected:
        raise DomainError("story_changed", "冻结开放细节上下文期间故事已改变，未派发。", 409)
    chapters = {
        c["id"]: c
        for c in worker.db.all(
            "SELECT id,branch_id,position,revision,archived,stale FROM chapters WHERE branch_id=?",
            (branch_id,),
        )
    }
    position = chapter["position"]

    def before(source):
        c = chapters.get(source.get("chapter_id"))
        return bool(
            c
            and not c["archived"]
            and not c["stale"]
            and c["position"] < position
            and c["revision"] == source.get("revision")
        )

    excluded = []
    originals = {r["id"]: r for r in snapshot["records"]}

    def record_allowed(row):
        original = originals.get(row["id"])
        if not original or original["archived"] or original["invalid_reasons"]:
            return False
        payload = original["payload"]
        sources = [*payload["sources"], *(s for stage in payload.get("stages", []) for s in stage["sources"])]
        # query_context can trim later quotations while a free-text field still
        # describes them. Require the WHOLE maintained record to be earlier.
        if any(not before(s) for s in sources):
            excluded.append(
                {
                    "id": row["id"],
                    "status": "unknown",
                    "reason": "记录含当前／后章或失效来源，未把部分可见引文扩展成整条当前事实。",
                }
            )
            return False
        return True

    longform = deepcopy(context["longform"])
    for name in ("states", "uncertain", "plans", "characters", "events", "promises", "relationships"):
        if name in longform:
            longform[name] = [r for r in longform[name] if record_allowed(r)]
    longform["conflicts"] = [
        [r for r in group if record_allowed(r)] for group in longform.get("conflicts", [])
    ]
    longform["conflicts"] = [group for group in longform["conflicts"] if group]
    facts = []
    for fact in context["facts"]:
        original = worker.db.one("SELECT * FROM facts WHERE id=?", (fact["id"],))
        if (
            original["branch_id"] == branch_id
            and not original["stale"]
            and before({"chapter_id": original["chapter_id"], "revision": original["source_revision"]})
        ):
            facts.append({**fact, "source_chapter_id": original["chapter_id"]})
        else:
            excluded.append(
                {"id": fact["id"], "status": "unknown", "reason": "旧事实缺少有效前章来源，未用于选择。"}
            )
    recent = []
    for previous in context["recent"]:
        if before({"chapter_id": previous["id"], "revision": previous["revision"]}):
            recent.append(deepcopy(previous))
        else:
            excluded.append(
                {"id": previous["id"], "status": "unknown", "reason": "正文不在目标之前的有效范围，未装配。"}
            )
    data = {
        "scope": "已采用创作边界和前章资料；设定／规划不是已发生事实，前叙述不自动等于当前故事时点或人物知识。",
        "novel": {k: context["novel"][k] for k in ("id", "title", "brief")},
        "branch": {"id": branch_id, "novel_id": worker.task["novel_id"], "story_revision": expected},
        "chapter": {k: chapter[k] for k in ("id", "title", "position", "revision")},
        "definitions": deepcopy(context["definitions"]),
        "definitions_policy": "只使用本书最新已采用的作者设定／人物档案／规划；不作已发生事实或人物已知，未来规划不得提前兑现。",
        "facts": facts,
        "recent": recent,
        "longform": longform,
        "genre_profile": deepcopy(worker.genre_snapshot()),
        "author_contract": deepcopy(worker.author_snapshot()),
        "excluded_by_boundary": excluded,
        "coverage": context["coverage"]
        + " 当前章节正文、候选、后章正文和其他作品未提供。故事时点未知时沿用longform的uncertain，不推算当前状态；只在选择确实依赖未明状态时保留待核对，不因无关未知一律阻断。",
    }
    basis = {
        "novel_id": worker.task["novel_id"],
        "branch_id": branch_id,
        "chapter_id": chapter["id"],
        "chapter_position": position,
        "chapter_revision": chapter["revision"],
        "story_revision": expected,
        "original_plot_sha256": row["original_plot_sha256"],
        "constraints_sha256": row["constraints_sha256"],
        "genre_sha256": sha(worker.genre_snapshot()),
        "author_contract_sha256": sha(worker.author_snapshot()),
    }
    value = {"version": VERSION, "basis": basis, "data": data}
    value["sha256"] = sha(value)
    validate(worker, row, value, current=True)
    return value


def validate(worker, row, value=None, *, current=False):
    value = row.get(KEY) if value is None else value
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise DomainError(
            "plan_preparation_context", "本章缺少原先冻结的创作上下文，未在已有阶段后补取或更换。", 409
        )
    if value.get("sha256") != sha({k: v for k, v in value.items() if k != "sha256"}):
        raise DomainError("plan_preparation_context", "冻结创作上下文摘要无法确认，未重新读取或派发。", 409)
    basis = value["basis"]
    if (
        basis["novel_id"] != worker.task["novel_id"]
        or basis["branch_id"] != worker.task["branch_id"]
        or basis["chapter_id"] != row["chapter_id"]
        or basis["chapter_position"] != row["chapter"]
        or basis["original_plot_sha256"] != row["original_plot_sha256"]
        or basis["constraints_sha256"] != row["constraints_sha256"]
        or basis["genre_sha256"] != sha(worker.genre_snapshot())
        or basis["author_contract_sha256"] != sha(worker.author_snapshot())
    ):
        raise DomainError("plan_preparation_context", "创作上下文不属于原章纲、目标或采用配置，未复用。", 409)
    if current:
        chapter = worker.domain.chapter(row["chapter_id"], worker.task["branch_id"])
        if (
            worker.domain.branch(worker.task["branch_id"])["story_revision"] != basis["story_revision"]
            or chapter["revision"] != basis["chapter_revision"]
            or chapter["content"] != ""
            or chapter["archived"]
            or chapter["position"] != basis["chapter_position"]
        ):
            raise DomainError(
                "plan_preparation_context_changed",
                "已冻结创作上下文的来源或目标改变，原快照保留；未补取新资料或重发。",
                409,
            )
    return value


def for_action(worker, row, action):
    value = validate(worker, row, current=True)
    context = deepcopy(value["data"])
    # Reuse the catalog's existing scene/review clipping without changing v1's
    # source-isolated action mapping or adding automatic prompt enrichment.
    stage = "writing.expand_plot" if action == "choice" else "review.chapter"
    context["genre_profile"] = profile_for_action(context["genre_profile"], stage)
    context["creative_context_version"] = VERSION
    context["creative_context_sha256"] = value["sha256"]
    context["creative_context_purpose"] = (
        "仅用于开放选择和保持兼容核对，不能据此声称原章纲已明确，也不能凭作者全知让人物提前知道。"
    )
    return context


def refresh_after_revision(domain, current, result, usage, reason=""):
    """Refresh only unstarted creative work after an acknowledged prior rewrite.

    The caller holds the existing budget/manuscript resume locks. Source-only
    replies, their unknowns, and every previous preparation stay unchanged.
    """
    from . import workflow_plan_recipient_scope as scope
    from .flow_policy_continuation import is_body_action
    from .scene_time import chapter_context
    from .worker import Worker, parse_json
    from .workflow_plan_preparation import check_receipt

    def require(value, message):
        if not value:
            raise DomainError("plan_context_refresh", message, 409)

    number = result.get("next_chapter")
    rows = result.get("plot_preparations", {})
    row = rows.get(str(number), {})
    require(
        current["kind"] == "workflow"
        and current["payload"].get("mode") == "automatic"
        and current["status"] in {"needs_review", "paused"}
        and type(number) is int
        and number >= 2
        and result.get("repair_round", 0) == 0
        and row.get("round") == 0
        and row.get("status") in {"interpreting", "needs_review"}
        and not any(row.get(k) for k in ("interpretations", "choices", "preservation_checks"))
        and set(row.get("phases", {})) == {"recipient_scope:0"}
        and not any(not key.isdigit() or int(key) > number for key in rows)
        and not any(result.get(k) for k in ("waiting_artifact", "waiting_structure", "pending_structure")),
        "仅前章改稿后、当前章尚未解释或选择的自动准备可刷新创作资料。",
    )
    require(
        not domain.db.all(
            "SELECT id FROM tasks WHERE branch_id=? AND id!=? AND status IN ('queued','running') AND cancel=0 LIMIT 1",
            (current["branch_id"], current["id"]),
        ),
        "同工作版本仍有活动任务，请先完成前章维护。",
    )
    worker = SimpleNamespace(task={**current, "result": result}, domain=domain, db=domain.db)
    worker.genre_snapshot = lambda: result["genre_profile_snapshot"]
    worker.author_snapshot = lambda: result["author_contract_snapshot"]
    worker.context = lambda branch, chapter_id, text_position=0: Worker.context(
        worker, branch, chapter_id, text_position=text_position
    )
    chapter = domain.chapter(row["chapter_id"], current["branch_id"])
    plot = next((p for p in result["plan"]["chapters"] if p["number"] == number), None)
    require(plot is not None, "原规划缺少当前章，不能刷新准备。")
    check_receipt(worker, row, plot, chapter)
    previous = validate(worker, row)
    require(
        chapter["revision"] == 0 and chapter["content"] == "" and not chapter["archived"],
        "当前章已有正文修订，不能刷新写前资料。",
    )
    slot = row["phases"]["recipient_scope:0"]
    require(
        slot.get("owner_task_id") == current["id"]
        and slot.get("status") == "completed"
        and slot.get("response_sha256") == sha(slot.get("response"))
        and scope.inventory_valid(slot.get("response"), row["original_plot"]),
        "原来源观察须完整有效；未知调用或缺失回执不能借改稿重发。",
    )
    provider = current["payload"]["account"]
    chosen = usage.execute("SELECT provider FROM calls WHERE task_id=? LIMIT 1", (current["id"],)).fetchone()
    require(chosen is not None, "缺少原来源调用，不能核对写前资料刷新。")
    require(
        not usage.execute(
            "SELECT id FROM calls WHERE provider=? AND (status!='settled' OR charged IS NULL) LIMIT 1",
            (chosen["provider"],),
        ).fetchone(),
        "原服务商仍有未决调用，不能借改稿刷新准备。",
    )
    steps = domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (current["id"],))
    calls = [dict(c) for c in usage.execute("SELECT * FROM calls WHERE task_id=?", (current["id"],))]
    require(
        set(slot["before_steps"]) <= {s["fingerprint"] for s in steps}
        and set(slot["before_calls"]) <= {c["id"] for c in calls},
        "原阶段前像缺失，不能刷新准备。",
    )
    steps = [s for s in steps if s["fingerprint"] not in slot["before_steps"]]
    calls = [c for c in calls if c["id"] not in slot["before_calls"]]
    try:
        returned = [json.loads(s["result"]) for s in steps]
    except TypeError, json.JSONDecodeError:
        raise DomainError("plan_context_refresh", "原观察返回缺失或格式不完整，未刷新准备。", 409) from None
    require(
        bool(returned)
        and len(returned) == len(calls)
        and all(isinstance(s, dict) and isinstance(s.get("text"), str) and s.get("call_id") for s in returned)
        and {s["call_id"] for s in returned} == {c["id"] for c in calls}
        and all(c["account"] == provider and c["status"] == "settled" for c in calls)
        and any(parse_json(s["text"]) == slot["response"]["inventory"] for s in returned)
        and all(
            usage.execute("SELECT call_id FROM call_metadata WHERE call_id=?", (c["id"],)).fetchone()
            for c in calls
        ),
        "原观察完整返回与结算不能一一核对，未刷新或重发。",
    )
    chapters = domain.db.all(
        "SELECT * FROM chapters WHERE branch_id=? ORDER BY position,id", (current["branch_id"],)
    )
    future = {c["id"] for c in chapters if c["position"] >= number}
    require(
        not any(c["revision"] or c["content"] for c in chapters if c["id"] in future)
        and not any(
            domain.db.all(
                "SELECT id FROM artifacts WHERE chapter_id=? AND kind IN ('chapter_draft','text_patch')",
                (identifier,),
            )
            for identifier in future
        ),
        "当前章或后章已有正文或候选，不能刷新其写前资料。",
    )
    for event in domain.db.all(
        "SELECT e.kind,e.payload FROM task_events e JOIN tasks t ON t.id=e.task_id "
        "WHERE t.branch_id=? AND e.kind IN ('chapter_started','step_dispatched')",
        (current["branch_id"],),
    ):
        data = json.loads(event["payload"])
        require(
            not (
                event["kind"] == "chapter_started"
                and data.get("number", 0) >= number
                or event["kind"] == "step_dispatched"
                and is_body_action(data.get("action"))
                and data.get("request", {}).get("target_version", {}).get("id") in future
            ),
            "当前章或后章已派发正文，不能借刷新写前资料重发。",
        )
    snapshot = graph(domain, current["branch_id"])
    require(
        snapshot["story_revision"] == result["expected_story_revision"]
        and previous["basis"]["story_revision"] != snapshot["story_revision"]
        and not any(r["invalid_reasons"] for r in snapshot["records"] if not r["archived"]),
        "当前故事结构仍失效或版本未改变，请先核对前章维护。",
    )
    prior = [c for c in chapters if c["position"] < number and not c["archived"]]
    require(
        [c["position"] for c in prior] == list(range(1, number))
        and all(c["content"].strip() and not c["stale"] for c in prior),
        "前章缺稿或失效，不能刷新创作资料。",
    )
    old_recent = {c["id"]: c for c in previous["data"]["recent"]}
    changed = []
    for c in prior:
        old = old_recent.get(c["id"])
        if old and old["revision"] < c["revision"] and old["content"] != c["content"]:
            historical = domain.db.all(
                "SELECT content FROM revisions WHERE chapter_id=? AND revision=?",
                (c["id"], old["revision"]),
            )
            require(
                len(historical) == 1 and historical[0]["content"] == old["content"],
                "旧快照的前章原稿无法回源，未刷新准备。",
            )
            changed.append(
                {"chapter_id": c["id"], "from_revision": old["revision"], "to_revision": c["revision"]}
            )
        if current["payload"].get("scene_maintenance"):
            context = chapter_context(domain, current["branch_id"], c["id"])
            require(context and not context["unbound_ranges"], "前章新稿场景未完整维护，未刷新准备。")
    require(changed, "没有旧快照可核实的前章改稿，不能把其它故事变化当作重做准备的依据。")
    require(
        any(
            json.loads(e["payload"]).get("plot_preparations", {}).get(str(number), {}).get(KEY) == previous
            for e in domain.db.all(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='checkpoint'", (current["id"],)
            )
        ),
        "旧创作快照缺少原检查点见证，未刷新准备。",
    )
    fresh = freeze(worker, row, chapter)
    audit = {
        "version": "workflow-plan-context-refresh/1",
        "chapter": number,
        "previous_preparation": deepcopy(row),
        "context": deepcopy(fresh),
        "changed_prior_chapters": changed,
        "source_phase_sha256": sha(slot),
        "reason": reason,
        "source_observation_replayed": False,
    }
    result["plot_preparations"][str(number)] = {**row, KEY: fresh}
    result.setdefault("preparation_context_refreshes", []).append(audit)
    return audit
