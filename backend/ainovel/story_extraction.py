"""Grounded reverse organization, followed by explicit atomic author adoption."""

from copy import deepcopy
import json
from time import time

from pydantic import Field

from .db import DomainError, dumps, uid
from .evidence import source_units
from .story_graph import RECORD, Record, Strict, graph, references, sources, validate_record


class ProposedRecord(Strict):
    local_id: str = Field(min_length=1, max_length=100)
    record: Record
    reasoning: str = Field(min_length=1, max_length=2000)
    target_id: str | None = None
    target_revision: int | None = Field(default=None, ge=0)
    update_fields: list[str] = Field(default_factory=list, max_length=30)


class Extraction(Strict):
    records: list[ProposedRecord] = Field(max_length=60)
    unknowns: list[str] = Field(max_length=30)
    coverage: str = Field(min_length=1, max_length=2000)


class StartExtraction(Strict):
    base_story_revision: int = Field(ge=0)
    chapter_ids: list[str] = Field(min_length=1, max_length=20)
    account: str
    client_request_id: str = Field(min_length=8, max_length=128)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class AdoptExtraction(Strict):
    base_story_revision: int = Field(ge=0)
    candidate_revision: int = Field(ge=0)
    selected_ids: list[str] = Field(min_length=1, max_length=60)
    idempotency_key: str = Field(min_length=8, max_length=128)


def freeze_sources(domain, request):
    branch = domain.branch(request["branch_id"])
    ids = request.get("story_chapter_ids", [])
    if not ids or len(set(ids)) != len(ids):
        raise DomainError("story_range", "请选择不重复的已保存章节。")
    if request.get("story_extraction_revision", branch["story_revision"]) != branch["story_revision"]:
        raise DomainError("story_changed", "选择范围后故事已改变，请重新核对。", 409)
    chapters = [domain.chapter(i, branch["id"]) for i in ids]
    if any(not c["content"].strip() or c["stale"] for c in chapters):
        raise DomainError("story_range", "范围含空稿或待复检章节，请先核对。")
    if sum(len(c["content"]) for c in chapters) > 50_000:
        raise DomainError("story_range", "本批原文超过5万字符，请缩小章节范围，分批整理。")
    return {
        "story_revision": branch["story_revision"],
        "chapters": [{k: c[k] for k in ("id", "title", "position", "revision", "content")} for c in chapters],
    }


def rewrite_references(record, mapping):
    p = deepcopy(record)
    p["depends_on"] = [mapping.get(i, i) for i in p["depends_on"]]
    if p["kind"] == "event":
        for field in ("participants", "after"):
            p[field] = [mapping.get(i, i) for i in p[field]]
    elif p["kind"] == "state":
        for field in ("character_id", "event_id", "until_event_id"):
            p[field] = mapping.get(p[field], p[field])
    elif p["kind"] == "relationship":
        for field in ("from_id", "to_id", "event_id"):
            p[field] = mapping.get(p[field], p[field])
    else:
        if p["kind"] == "promise":
            for stage in p["stages"]:
                stage["event_id"] = mapping.get(stage["event_id"], stage["event_id"])
    return p


async def run_extraction(worker, request, context):
    snapshot = worker.task["result"]["story_extraction_snapshot"]
    if worker.domain.branch(request["branch_id"])["story_revision"] != snapshot["story_revision"]:
        raise DomainError("story_changed", "源稿排队后改变，未发送整理请求。", 409)
    units = []
    for i, chapter in enumerate(snapshot["chapters"]):
        units += [
            {**unit, "chapter_id": chapter["id"], "revision": chapter["revision"]}
            for unit in source_units(chapter["content"], f"S{i + 1}P")
        ]
    indexed = {u["source_id"]: u for u in units}
    existing = [r for r in graph(worker.domain, request["branch_id"])["records"] if not r["archived"]]
    known = {r["id"] for r in existing}
    by_id = {r["id"]: r for r in existing}

    def validate(value):
        issues = []
        local = [r["local_id"] for r in value["records"]]
        targets = [r["target_id"] for r in value["records"] if r["target_id"]]
        if len(targets) != len(set(targets)):
            issues.append("同一正式记录在本批只能有一个更新提案。")
        if len(set(local)) != len(local) or set(local) & known:
            issues.append("候选local_id必须独立、唯一，不得与正式标识相同。")
        for proposal in value["records"]:
            record = proposal["record"]
            target = by_id.get(proposal["target_id"])
            if proposal["target_id"]:
                if (
                    not target
                    or target["revision"] != proposal["target_revision"]
                    or target["kind"] != record["kind"]
                ):
                    issues.append("更新目标必须使用当前索引中同类型记录的正式id和revision。")
                if not proposal["update_fields"] or any(
                    k not in record or k == "kind" for k in proposal["update_fields"]
                ):
                    issues.append("更新须明确update_fields，仅能修改该记录已定义字段，kind不可变。")
            elif proposal["update_fields"] or proposal["target_revision"] is not None:
                issues.append("新记录不能指定更新字段或目标修订。")
            quoted = sources(record)
            if not quoted:
                issues.append("每项候选都需本批原文依据；不能从作者计划推断发生。")
            for source in quoted:
                unit = indexed.get(source["quote"])
                if not unit:
                    # Earlier repair rounds already restored successful source IDs.
                    unit = next(
                        (
                            u
                            for u in units
                            if all(source[k] == u[k] for k in ("chapter_id", "revision", "start", "end"))
                            and source["quote"] == u["text"]
                        ),
                        None,
                    )
                if not unit:
                    issues.append("来源quote必须使用提供的source_id。")
                else:
                    source.update({k: unit[k] for k in ("chapter_id", "revision", "start", "end")})
                    source["quote"] = unit["text"]
            if not references(record).issubset(known | set(local)):
                issues.append("关联只能使用本批local_id或已提供的正式标识。")
        return issues

    narrow = {
        "branch": context["branch"],
        "coverage": "仅本批已选择保存稿和正式身份索引；不把外部资料、预设或未来计划当事实。",
    }
    result = await worker.grounded(
        {
            "动作": "按保存稿反向整理长篇结构候选",
            "可引用原文": units,
            "已有记录索引": [
                {
                    "id": r["id"],
                    "revision": r["revision"],
                    "kind": r["kind"],
                    "invalid_reasons": r["invalid_reasons"],
                    **{
                        k: v
                        for k, v in r["payload"].items()
                        if k
                        in {
                            "title",
                            "aliases",
                            "story_order",
                            "story_time",
                            "status",
                            "category",
                            "key",
                            "audience",
                        }
                    },
                }
                for r in existing
            ],
            "要求": "仅提取所给正文有依据的人物、事件、状态、关系及伏笔。已有身份用正式id关联，避免重建同一人物；新项使用本批local_id。更新已有项必须指定target_id、target_revision和非空update_fields，只列本批确有新依据的变化字段，未列字段保持原值，不猜测未提供的旧档案。sources默认追加证据；明确需替换时才列sources。状态在更晚事件中变化时新建事件与状态，不把新变化回写成旧事件当时的状态；修改旧状态只作为有明确依据的纠错提案。quote填source_id，其余来源位置由服务复原。档案、作者知识、角色知识及读者知识分开；计划不冒充实际，未知故事时间和冲突保持可见。不确定伏笔只列unknowns。每项解释推导边界，不能改稿或自动采用。",
        },
        Extraction,
        narrow,
        validate,
        max_output=request["max_output_tokens"],
    )
    allocated = {r["local_id"]: r["target_id"] or uid() for r in result["records"]}
    for item in result["records"]:
        item["id"] = allocated[item["local_id"]]
        if item["target_id"]:
            old = by_id[item["target_id"]]
            item["previous_record"] = {"revision": old["revision"], "record": deepcopy(old["payload"])}
            merged = deepcopy(old["payload"])
            for field in item["update_fields"]:
                merged[field] = deepcopy(item["record"][field])
            if "sources" not in item["update_fields"]:
                unique = {
                    (s["chapter_id"], s["revision"], s["start"], s["end"], s["quote"]): s
                    for s in [*merged["sources"], *item["record"]["sources"]]
                }
                merged["sources"] = list(unique.values())
                if len(merged["sources"]) > 30:
                    raise DomainError(
                        "story_sources_limit",
                        "合并后来源超过30条，未自动丢弃旧证据；请先手工核对来源或明确提出来源替换。",
                    )
            item["record"] = RECORD.validate_python(merged).model_dump()
        item["dependency_proposals"] = [allocated[k] for k in references(item["record"]) if k in allocated]
        item["record"] = rewrite_references(item["record"], allocated)
    result["source_manifest"] = [
        {k: c[k] for k in ("id", "revision", "position", "title")} for c in snapshot["chapters"]
    ]
    result["source_story_revision"] = snapshot["story_revision"]
    return "story_extraction", result


def adopt(domain, branch_id, artifact_id, data):
    request_signature = data.model_dump()
    with domain.db.connect(True) as tx:
        artifact = tx.execute(
            "SELECT * FROM artifacts WHERE id=? AND branch_id=? AND kind='story_extraction'",
            (artifact_id, branch_id),
        ).fetchone()
        if not artifact:
            raise DomainError("not_found", "结构候选不存在或不属于当前版本。", 404)
        prior = tx.execute("SELECT * FROM receipts WHERE key=?", (data.idempotency_key,)).fetchone()
        if prior:
            result = json.loads(prior["result"])
            if prior["artifact_id"] != artifact_id or result.get("request") != request_signature:
                raise DomainError("idempotency", "确认标识已用于不同选择。", 409)
            return result
        payload, basis = json.loads(artifact["payload"]), json.loads(artifact["basis"])
        revision = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
        if artifact["status"] != "candidate" or data.candidate_revision != payload.get(
            "_candidate_revision", 0
        ):
            raise DomainError("candidate_changed", "候选已改变或已处理。", 409)
        if revision != data.base_story_revision or revision != basis.get("story_revision"):
            raise DomainError("story_changed", "故事或源稿已改变，请重新整理。", 409)
        indexed = {item["id"]: item for item in payload["records"]}
        chosen = set(data.selected_ids)
        if len(chosen) != len(data.selected_ids) or not chosen.issubset(indexed):
            raise DomainError("story_selection", "候选选择重复或不存在。")
        for identifier in chosen:
            missing = set(
                indexed[identifier].get("dependency_proposals", references(indexed[identifier]["record"]))
            ) & (set(indexed) - chosen)
            if missing:
                raise DomainError(
                    "story_dependencies", "所选候选还依赖未选择的人物或事件，请明确一并选择。", 409
                )
        # All tentative rows stay inside this transaction; any validation failure rolls back ALL.
        record_revisions = {}
        for identifier in chosen:
            p = indexed[identifier]["record"]
            item = indexed[identifier]
            if item.get("target_id"):
                old = tx.execute(
                    "SELECT * FROM story_records WHERE id=? AND branch_id=? AND archived=0",
                    (identifier, branch_id),
                ).fetchone()
                if not old or old["revision"] != item["target_revision"] or old["kind"] != p["kind"]:
                    raise DomainError("record_changed", "待更新正式记录已改变，请重新整理。", 409)
                record_revisions[identifier] = old["revision"] + 1
                tx.execute(
                    "UPDATE story_records SET revision=revision+1,payload=?,updated=? WHERE id=?",
                    (dumps(p), time(), identifier),
                )
            else:
                record_revisions[identifier] = 0
                tx.execute(
                    "INSERT INTO story_records VALUES(?,?,?,?,?,?,?,?)",
                    (identifier, branch_id, p["kind"], 0, dumps(p), 0, time(), time()),
                )
        for identifier in chosen:
            p = indexed[identifier]["record"]
            validate_record(tx, branch_id, identifier, p)
            tx.execute(
                "INSERT INTO story_record_history VALUES(?,?,?,?,?)",
                (identifier, record_revisions[identifier], dumps(p), 0, time()),
            )
            tx.execute("DELETE FROM story_record_dependencies WHERE record_id=?", (identifier,))
            for parent in references(p):
                parent_revision = tx.execute(
                    "SELECT revision FROM story_records WHERE id=?", (parent,)
                ).fetchone()[0]
                tx.execute(
                    "INSERT INTO story_record_dependencies VALUES(?,?,?)",
                    (identifier, parent, parent_revision),
                )
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch_id,))
        tx.execute("UPDATE artifacts SET status='accepted' WHERE id=?", (artifact_id,))
        result = {
            "artifact_id": artifact_id,
            "record_ids": data.selected_ids,
            "record_revisions": record_revisions,
            "request": request_signature,
            "story_revision": revision + 1,
            "note": "仅所选结构候选已确认；未选项保留在原产物，不影响正文。",
        }
        tx.execute("INSERT INTO receipts VALUES(?,?,?)", (data.idempotency_key, artifact_id, dumps(result)))
        return result


def register_routes(app, domain, provider_ready):
    @app.get("/api/branches/{branch_id}/story-extractions")
    def history(branch_id: str):
        domain.branch(branch_id)
        return {
            "tasks": domain.db.all(
                "SELECT id,status,error,created,json_extract(result,'$.artifact_id') AS artifact_id FROM tasks WHERE branch_id=? AND kind='module' AND json_extract(payload,'$.action')='story.extract' ORDER BY created DESC LIMIT 100",
                (branch_id,),
            ),
            "artifacts": domain.db.all(
                "SELECT id,status,created FROM artifacts WHERE branch_id=? AND kind='story_extraction' ORDER BY created DESC LIMIT 100",
                (branch_id,),
            ),
        }

    @app.post("/api/branches/{branch_id}/story-extractions", status_code=202)
    def start(branch_id: str, data: StartExtraction):
        from .models import ModuleInput

        provider_ready(data.account)
        branch = domain.branch(branch_id)
        request = ModuleInput(
            action="story.extract",
            novel_id=branch["novel_id"],
            branch_id=branch_id,
            instruction="按所选保存稿整理长篇结构，供作者核对采用。",
            account=data.account,
            client_request_id=data.client_request_id,
            budget_limit=data.budget_limit,
            story_chapter_ids=data.chapter_ids,
        ).model_dump(exclude_none=True)
        request["story_extraction_revision"] = data.base_story_revision
        return domain.queue("module", request)

    @app.post("/api/branches/{branch_id}/story-extractions/{artifact_id}/adopt")
    def confirm(branch_id: str, artifact_id: str, data: AdoptExtraction):
        return adopt(domain, branch_id, artifact_id, data)
