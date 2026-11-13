"""Grounded reverse organization, followed by explicit atomic author adoption."""

from copy import deepcopy
import json
from time import time

from pydantic import Field

from .db import DomainError, dumps, uid
from .evidence import source_units
from .story_graph import RECORD, Record, Strict, graph, references, validate_record
from .extraction_scenes import SceneProposal, adopt_scenes, freeze_scenes, normalize_scenes, scene_issues

PROMISE_STAGE_GROUNDING_VERSION = "promise-stage-grounding/2"
SCENE_GROUNDING_VERSION = "scene-event-order/1"


class ProposedRecord(Strict):
    local_id: str = Field(min_length=1, max_length=100)
    record: Record
    reasoning: str = Field(min_length=1, max_length=2000)
    target_id: str | None = None
    target_revision: int | None = Field(default=None, ge=0)
    update_fields: list[str] = Field(default_factory=list, max_length=30)
    append_stages: bool = Field(default=False, strict=True)


class Extraction(Strict):
    records: list[ProposedRecord] = Field(max_length=60)
    unknowns: list[str] = Field(max_length=30)
    coverage: str = Field(min_length=1, max_length=2000)


class ExtractionWithScenes(Extraction):
    scenes: list[SceneProposal] = Field(min_length=1, max_length=200)


class StartExtraction(Strict):
    include_scene_ranges: bool = False
    base_story_revision: int = Field(ge=0)
    chapter_ids: list[str] = Field(min_length=1, max_length=20)
    account: str
    client_request_id: str = Field(min_length=8, max_length=128)
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class AdoptExtraction(Strict):
    selected_scene_ids: list[str] = Field(default_factory=list, max_length=200)
    confirm_scene_replacement: bool = False
    base_story_revision: int = Field(ge=0)
    candidate_revision: int = Field(ge=0)
    selected_ids: list[str] = Field(max_length=60)
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
        **(
            {"scene_replacements": freeze_scenes(domain, chapters)}
            if request.get("include_scene_ranges")
            else {}
        ),
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


def promise_stage_issues(record, path, records, source_hint):
    """Match the promise checks in validate_record before a candidate can stage."""
    issues = []
    event_ids = [identifier for identifier, item in records.items() if item["kind"] == "event"]
    for index, stage in enumerate(record["stages"]):
        location = f"{path}.stages[{index}]({stage['status']})"
        if stage["status"] in {"planted", "reinforced", "resolved"} and not stage["sources"]:
            issues.append(
                f"{location}.sources 缺少本阶段独立原文依据；{source_hint}"
                "主record.sources不能代替阶段证据，不自动挪用；同段确实支持时可明确选择同一source_id，"
                "确无依据应列unknowns。"
            )
        if stage["status"] in {"retained", "abandoned"} and not stage["note"].strip():
            issues.append(f"{location}.note 缺少保留或放弃的作者理由，不得编造决定。")
        if stage["event_id"] and (
            stage["event_id"] not in records or records[stage["event_id"]]["kind"] != "event"
        ):
            issues.append(
                f"{location}.event_id 只能关联事件，不能引用人物或其他类型；"
                f"可用事件id示例：{'、'.join(event_ids[:12]) or '本批无事件'}。"
            )
    return issues


async def _grounded_extraction(worker, instruction, schema, context, validator, **kwargs):
    """Keep final field diagnostics without changing the shared retry contract."""
    last_issues = []

    def validate(value):
        issues = validator(value)
        last_issues[:] = issues
        return issues

    try:
        return await worker.grounded(instruction, schema, context, validate, **kwargs)
    except DomainError as error:
        if error.code != "evidence" or not last_issues:
            raise
        details = "；".join(issue[:800] for issue in last_issues[:6])
        if len(last_issues) > 6:
            details += f"；另{len(last_issues) - 6}项详见任务引用修正记录"
        raise DomainError(
            error.code,
            f"结构候选在两轮引用修正后仍未通过校验（{len(last_issues)}项）：{details}",
            error.status,
        ) from error


async def run_extraction(worker, request, context, snapshot=None):
    snapshot = snapshot or worker.task["result"]["story_extraction_snapshot"]
    if worker.domain.branch(request["branch_id"])["story_revision"] != snapshot["story_revision"]:
        raise DomainError("story_changed", "源稿排队后改变，未发送整理请求。", 409)
    units = []
    for i, chapter in enumerate(snapshot["chapters"]):
        units += [
            {**unit, "chapter_id": chapter["id"], "revision": chapter["revision"]}
            for unit in source_units(chapter["content"], f"S{i + 1}P")
        ]
    indexed = {u["source_id"]: u for u in units}
    with_scenes = request.get("include_scene_ranges", False)
    if with_scenes and len(units) > 2000:
        raise DomainError("scene_range", "本批超过2000个原文片段，请缩小章节范围后整理场景。")
    existing = [r for r in graph(worker.domain, request["branch_id"])["records"] if not r["archived"]]
    known = {r["id"] for r in existing}
    by_id = {r["id"]: r for r in existing}
    source_hint = (
        f"quote须经原文核实后选择合法source_id，如{'、'.join(list(indexed)[:12])}"
        "（完整编号见可引用原文，示例不表示支持本阶段）。"
    )

    def validate(value):
        issues = []
        local = [r["local_id"] for r in value["records"]]
        targets = [r["target_id"] for r in value["records"] if r["target_id"]]
        if len(targets) != len(set(targets)):
            issues.append("同一正式记录在本批只能有一个更新提案。")
        if len(set(local)) != len(local) or set(local) & known:
            issues.append("候选local_id必须独立、唯一，不得与正式标识相同。")
        reference_index = {**by_id, **{r["local_id"]: r["record"] for r in value["records"]}}
        for proposal_index, proposal in enumerate(value["records"]):
            record = proposal["record"]
            path = f"records[{proposal_index}](local_id={proposal['local_id']}).record"
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
            if proposal["append_stages"]:
                if (
                    not target
                    or target["kind"] != "promise"
                    or record["kind"] != "promise"
                    or "stages" not in proposal["update_fields"]
                ):
                    issues.append(
                        f"{path} append_stages=true仅适用于已有promise且update_fields明确包含stages；"
                        "不得用于新记录、其他类型或未选择的字段。"
                    )
                else:
                    count = len(target["payload"]["stages"]) + len(record["stages"])
                    if count > 100:
                        issues.append(
                            f"{path}.stages 追加后为{count}个阶段，超过100上限；未截断历史。"
                            "保留旧阶段，当前无法追加时列unknowns或交作者处理，不得删除历史以通过。"
                        )
                    if any(
                        s["status"] not in {"planted", "reinforced", "resolved"} for s in record["stages"]
                    ):
                        issues.append(
                            f"{path}.stages 追加模式仅接受有本批原文依据的planted/reinforced/resolved；"
                            "计划、保留或放弃不能借追加模式自动决定。"
                        )
            source_fields = [(f"{path}.sources", record["sources"])]
            if record["kind"] == "promise":
                source_fields += [
                    (f"{path}.stages[{i}]({stage['status']}).sources", stage["sources"])
                    for i, stage in enumerate(record["stages"])
                ]
                # Unlisted update fields retain the existing formal stages.
                if not target or "stages" in proposal["update_fields"]:
                    issues.extend(promise_stage_issues(record, path, reference_index, source_hint))
            quoted = [(f"{field}[{i}]", s) for field, entries in source_fields for i, s in enumerate(entries)]
            if not quoted:
                issues.append(f"{path} 每项候选都需本批原文依据；不能从作者计划推断发生。{source_hint}")
            for source_path, source in quoted:
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
                    issues.append(f"{source_path}.quote 无法回到本批原文；{source_hint}")
                else:
                    source.update({k: unit[k] for k in ("chapter_id", "revision", "start", "end")})
                    source["quote"] = unit["text"]
            if not references(record).issubset(known | set(local)):
                issues.append("关联只能使用本批local_id或已提供的正式标识。")
        if with_scenes:
            issues.extend(scene_issues(value["scenes"], units, by_id, value["records"]))
        return issues

    narrow = {
        "branch": context["branch"],
        "coverage": "仅本批已选择保存稿和正式身份索引；不把外部资料、预设或未来计划当事实。",
    }
    result = await _grounded_extraction(
        worker,
        {
            "动作": "按保存稿反向整理长篇结构候选",
            "阶段证据契约版本": PROMISE_STAGE_GROUNDING_VERSION,
            **(
                {
                    "场景证据契约版本": SCENE_GROUNDING_VERSION,
                    "场景范围要求": "同时输出scenes，按每章原文source_id连续分组，完整覆盖且每段仅一次。不输出自己计算的位置，服务根据冻结原文计算UTF-16范围。人物视角明确character_id，可用本批人物local_id。story_order只采用所覆盖原文中实际事件的已知故事顺序；缺依据使用null和未知说明，不能按章号推断。不将未来计划当实际时点，插叙、倒叙及信息视角分别表达。",
                    "场景顺序语义": "story_order是覆盖范围内实际事件的唯一有效故事顺序，不是场景编号或章节编号。先核对关联actual事件和其原文来源；同一场景覆盖多个不同事件顺序时不得任取一项，可依据实际事件合理拆分，或保留null并在note说明未知边界。没有唯一有效事件顺序时同样保留null；不得为了通过校验删事件、改正文、编造时点或清空已有事实。",
                }
                if with_scenes
                else {}
            ),
            "可引用原文": units,
            "已有记录索引": [
                {
                    "id": r["id"],
                    "revision": r["revision"],
                    "kind": r["kind"],
                    "invalid_reasons": r["invalid_reasons"],
                    "source_free_plan": not r["payload"]["sources"]
                    and (r["payload"].get("status") == "planned" or r["payload"].get("truth") == "planned"),
                    **(
                        {
                            "promise_current_status": r["payload"]["stages"][-1]["status"],
                            "promise_stage_count": len(r["payload"]["stages"]),
                        }
                        if r["kind"] == "promise"
                        else {}
                    ),
                    **(
                        {"planned_value": r["payload"]["value"]}
                        if r["kind"] == "state" and r["payload"]["truth"] == "planned"
                        else {}
                    ),
                    **(
                        {"planned_relation": r["payload"]["relation"]}
                        if r["kind"] == "relationship" and r["payload"]["truth"] == "planned"
                        else {}
                    ),
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
                            "truth",
                            "character_id",
                            "event_id",
                            "until_event_id",
                            "from_id",
                            "to_id",
                            "participants",
                            "after",
                        }
                    },
                }
                for r in existing
            ],
            "计划兑现边界": "source_free_plan只表示尚无原文依据的计划，绝不表示实际。仅本批正文明确兑现同一事件、状态或关系且其他断言不变时，才可提案只更新status或truth为actual并提供本批原文。计划值不同或已有历史来源时，不做自动兑现；新建有依据的实际记录或保留为待作者核对的提案。实际状态须关联实际事件，必要时在同批明确提出事件兑现。不改写已发生历史，也不把计划引用当成实际证据。",
            "伏笔阶段要求": "planted、reinforced、resolved每个阶段都须在自身stages[i].sources独立选择有依据的source_id，主record.sources不能代替，不自动挪用主来源。同段确实支持多个阶段时可显式选择同一source_id，但各阶段仍须分别填写。retained、abandoned须在note记录明确作者理由，不编造决定；event_id仅能关联本批或已有的event，缺证据保留unknowns。",
            "伏笔显式追加": "已有promise的新增埋设、强化或回收使用append_stages=true，指定target_id、target_revision及update_fields包含stages；record.stages只输出本批新增且有独立来源的阶段，服务将完整保留旧阶段并追加，不能重建或重复未提供的旧历史。索引只提供当前状态与阶段数。追加不能改写历史或自动决定retained/abandoned；无证据列unknowns。append_stages默认false，仍表示原有的整字段替换提案，不能假装已掌握未提供历史，自动采用仍受既有安全政策约束。sources字段原有追加/明确替换语义不变。",
            "要求": "仅提取所给正文有依据的人物、事件、状态、关系及伏笔。已有身份用正式id关联，避免重建同一人物；新项使用本批local_id。更新已有项必须指定target_id、target_revision和非空update_fields，只列本批确有新依据的变化字段，未列字段保持原值，不猜测未提供的旧档案。sources默认追加证据；明确需替换时才列sources。状态在更晚事件中变化时新建事件与状态，不把新变化回写成旧事件当时的状态；修改旧状态只作为有明确依据的纠错提案。quote填source_id，其余来源位置由服务复原。档案、作者知识、角色知识及读者知识分开；计划不冒充实际，未知故事时间和冲突保持可见。不确定伏笔只列unknowns。每项解释推导边界，不能改稿或自动采用。",
        },
        ExtractionWithScenes if with_scenes else Extraction,
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
                merged[field] = (
                    deepcopy(old["payload"]["stages"]) + deepcopy(item["record"]["stages"])
                    if field == "stages" and item["append_stages"]
                    else deepcopy(item["record"][field])
                )
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
    if with_scenes:
        result["scenes"] = normalize_scenes(result["scenes"], units, snapshot["chapters"], allocated)
        result["scene_replacements"] = snapshot.get("scene_replacements", [])
        result["scene_required"] = True
    return "story_extraction", result


def adopt(domain, branch_id, artifact_id, data, attempt=None, character_review_token=None):
    request_signature = data.model_dump()
    if not data.selected_scene_ids:
        request_signature.pop("selected_scene_ids")
    if not data.confirm_scene_replacement:
        request_signature.pop("confirm_scene_replacement")
    with domain.db.connect(True) as tx:
        artifact = tx.execute(
            "SELECT * FROM artifacts WHERE id=? AND branch_id=? AND kind='story_extraction'",
            (artifact_id, branch_id),
        ).fetchone()
        if not artifact:
            raise DomainError("not_found", "结构候选不存在或不属于当前版本。", 404)
        owners = tx.execute(
            "SELECT id,kind,status,result FROM tasks WHERE branch_id=? AND kind='workflow' AND status!='continued' AND json_extract(result,'$.pending_structure.artifact_id')=?",
            (branch_id, artifact_id),
        ).fetchall()
        if len(owners) > 1:
            raise DomainError("workflow_progress", "多个批次指向同一待维护结果，请先核对检查点。", 409)
        maintenance_owner = owners[0] if owners else None
        if attempt:
            domain.db.check_attempt(
                tx, maintenance_owner["id"] if maintenance_owner else artifact["task_id"], attempt
            )
        prior = tx.execute("SELECT * FROM receipts WHERE key=?", (data.idempotency_key,)).fetchone()
        if prior:
            result = json.loads(prior["result"])
            if prior["artifact_id"] != artifact_id or result.get("request") != request_signature:
                raise DomainError("idempotency", "确认标识已用于不同选择。", 409)
            return result
        creator = (
            tx.execute("SELECT kind FROM tasks WHERE id=?", (artifact["task_id"],)).fetchone()
            if artifact["task_id"]
            else None
        )
        if creator and creator["kind"] == "workflow" and not maintenance_owner:
            raise DomainError(
                "workflow_progress",
                "结构产物的批次检查点尚未发布或已推进，请刷新后核对；未修改任何记录。",
                409,
            )
        payload, basis = json.loads(artifact["payload"]), json.loads(artifact["basis"])
        revision = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()[0]
        if artifact["status"] != "candidate" or data.candidate_revision != payload.get(
            "_candidate_revision", 0
        ):
            raise DomainError("candidate_changed", "候选已改变或已处理。", 409)
        if revision != data.base_story_revision or revision != basis.get("story_revision"):
            raise DomainError("story_changed", "故事或源稿已改变，请重新整理。", 409)
        if attempt and maintenance_owner:
            from .character_review import validate_adoption_review

            validate_adoption_review(
                tx,
                {**dict(artifact), "payload": payload, "basis": basis},
                maintenance_owner["id"],
                character_review_token,
            )
        indexed = {item["id"]: item for item in payload["records"]}
        if indexed and not data.selected_ids and not data.selected_scene_ids:
            raise DomainError("story_selection", "请选择至少一项，空选择不能确认非空候选。")
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
        required = bool(maintenance_owner and payload.get("scene_required"))
        scene_revisions, archived_scenes = adopt_scenes(
            tx, branch_id, payload, data.selected_scene_ids, data.confirm_scene_replacement, required
        )
        increment = 1 if chosen or scene_revisions else 0
        tx.execute("UPDATE branches SET story_revision=story_revision+? WHERE id=?", (increment, branch_id))
        tx.execute("UPDATE artifacts SET status='accepted' WHERE id=?", (artifact_id,))
        result = {
            "artifact_id": artifact_id,
            "record_ids": data.selected_ids,
            "record_revisions": record_revisions,
            "scene_revisions": scene_revisions,
            "archived_scene_ids": archived_scenes,
            "request": request_signature,
            "story_revision": revision + increment,
            "note": "仅所选结构候选已确认；未选项保留在原产物，不影响正文。",
        }
        tx.execute("INSERT INTO receipts VALUES(?,?,?)", (data.idempotency_key, artifact_id, dumps(result)))
        if maintenance_owner:
            owner = maintenance_owner
            progress = json.loads(owner["result"]) if owner else {}
            if (
                owner
                and owner["kind"] == "workflow"
                and progress.get("pending_structure", {}).get("artifact_id") == artifact_id
            ):
                source = progress.pop("pending_structure")
                progress.pop("waiting_structure", None)
                progress["expected_story_revision"] = result["story_revision"]
                progress.setdefault("structure_history", []).append(
                    {
                        **source,
                        "record_ids": data.selected_ids,
                        "record_revisions": record_revisions,
                        "scene_ids": list(scene_revisions),
                        "scene_revisions": scene_revisions,
                        "policy": "worker_automatic" if attempt else "author_confirmed",
                        "created": time(),
                    }
                )
                tx.execute(
                    "UPDATE tasks SET result=?,updated=? WHERE id=?",
                    (dumps(progress), time(), owner["id"]),
                )
                domain.db.event(tx, owner["id"], "structure_maintained", progress["structure_history"][-1])
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
            include_scene_ranges=data.include_scene_ranges,
        ).model_dump(exclude_none=True)
        request["story_extraction_revision"] = data.base_story_revision
        return domain.queue("module", request)

    @app.post("/api/branches/{branch_id}/story-extractions/{artifact_id}/adopt")
    def confirm(branch_id: str, artifact_id: str, data: AdoptExtraction):
        return adopt(domain, branch_id, artifact_id, data)
