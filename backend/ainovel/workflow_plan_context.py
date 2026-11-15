"""Freeze adopted creation context without teaching the source interpreter facts."""

from copy import deepcopy
import hashlib

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
