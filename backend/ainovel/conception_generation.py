"""Generate reviewable conception sections in a durable, version-bound task."""

import hashlib
import json
from time import time
from typing import Annotated
from uuid import UUID, uuid5

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from .conception import SettingPolicy
from .db import DomainError, dumps, uid
from .evidence import source_units
from .genre_catalog import assemble_profile, setting_framework
from .ideation_structure import Detail, substantive
from .models import Input
from .project_brief import DRAFT_KIND, IdeaText, confirmed_project_tx


KIND = "conception_generate"
VERSION = "conception-generate/3"
LEGACY_VERSION = "conception-generate/2"
CONSISTENCY_VERSION = "conception-consistency/1"
WORLD_EXTRACTION_VERSION = "conception-world-extract/1"
SETTING_FRAME_VERSION = "conception-setting-frame/1"
PROJECT_PLANNING_SCOPE_VERSION = "project-planning-scope/1"
WORLD_SOURCE_LIMIT = 20_000
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Reference = Annotated[str, StringConstraints(pattern=r"^[SN][1-9][0-9]{0,3}$")]
ProposalTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), AfterValidator(substantive)
]
GUIDES = {
    "core": "写出一句话故事、读者看点、主角目标与代价、核心矛盾和建议结局。保留明确的作者限制。",
    "characters": "设计主角及重要人物的目标、动机、行动局限与可能变化。用稳定的称谓，区别身份与别名。",
    "world": "写支撑主线的世界规则、资源或成长约束。交代适用范围、代价与例外，不补写无关百科。",
    "relationships": "说明关系双方、方向、起点、利益冲突与可能改变；能关联人物时用 links。",
    "events": "按主线及必要支线设计具体事件，写清起因、行动、结果和代价。用 links 关联前序事件和参与人物。",
    "timeline": "区分故事内先后与正文叙述安排（例如倒叙、并行）；没有明确日期时用相对时点。用 links 关联事件。",
    "foreshadowing": "写明铺设、强化与建议回收方式，或注明开放原因；用 links 连接相应的事件，保留计划性质。",
    "outline": "按卷或阶段整理大纲，写出起点、关键转折、阶段结果与收束，沿用已有事件，不强制固定章数。",
}

SETTING_GUIDES = {
    "core": "确定主线需求、读者承诺、代价和终局。对需要保留的秘密，写出作者底稿、叙述视角和最早披露时点。",
    "characters": "按题材为人物设计目标、资格、能做到的事及其代价。分别写明角色已知、误以为真、怎样得到新信息。",
    "relationships": "写明关系两端的权责、资源或证词依赖和选择。分别写双方所知与相信的事，并标明隐藏承诺或真相何时可披露。",
    "world": (
        "交代世界规则的触发、限制、代价、适用范围和例外。为规则提供 setting_policy，"
        "使用 hard 表示明确限制或 soft 表示可调整倾向，写明适用范围与例外。"
    ),
}

SETTING_GUIDANCE = (
    "使用主类型的专项主题，辅类型只取与主线交叉的设定，机制按适用场景挂载。"
    "已确认立项的题材、读者定位和约束优先；频道只调整读者预期和叙述重点。"
    "保留作者固定条件、禁项和明确例外。存在冲突时说明冲突双方与适用场景，将取舍留给作者。"
    "分别标明作者底稿中的真相、人物在相应时点可知或误判的信息、读者当时可见的证据。"
    "本次生成是待确认设定。工作台的选用状态供复核，待定条目和先前 AI 候选仅供参考。"
    "即使设定已确认，规划中的事件也不等同于正文已发生的事实。"
)


class ConceptionTaskInput(Input):
    base_revision: int = Field(strict=True, ge=0)
    base_story_revision: int = Field(strict=True, ge=0)
    account: Identifier
    client_request_id: Identifier
    budget_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class ConceptionGenerateInput(ConceptionTaskInput):
    category_id: Identifier | None = None
    instruction: str = Field(default="", max_length=6000)
    project_confirmation_id: Identifier | None = None


class ProjectGenerateInput(ConceptionTaskInput):
    idea: IdeaText


class ConceptionWorldExtractInput(ConceptionTaskInput):
    chapter_ids: list[Identifier] = Field(min_length=1, max_length=20)


class ConceptionProposalItem(Input):
    title: ProposalTitle
    detail: Detail
    links: list[Reference] = Field(max_length=12)
    setting_policy: SettingPolicy | None = None


class ConceptionCategoryProposal(Input):
    items: list[ConceptionProposalItem] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct_items(self):
        names = [item.title.casefold() for item in self.items]
        if len(set(names)) != len(names):
            raise ValueError("同一分类中的构思项须有不同标题。")
        for index, item in enumerate(self.items, 1):
            if len(set(item.links)) != len(item.links) or f"N{index}" in item.links:
                raise ValueError("关联不得重复或指向构思项自身。")
            if any(link.startswith("N") and int(link[1:]) > len(self.items) for link in item.links):
                raise ValueError("N 编号只可引用本次实际输出的构思项。")
        return self


class ConceptionWorldEvidence(Input):
    title: ProposalTitle
    detail: Detail
    source_id: Identifier
    quote: str = Field(min_length=1, max_length=600)


class ConceptionWorldProposal(Input):
    items: list[ConceptionWorldEvidence] = Field(max_length=12)


class ConceptionConsistencyIssue(Input):
    category_id: Identifier
    message: Detail


class ConceptionConsistencyReview(Input):
    issues: list[ConceptionConsistencyIssue] = Field(max_length=16)


def register_routes(app, domain, provider_ready):
    def queue_task(branch_id, data, fields):
        branch = domain.branch(branch_id)
        payload = {
            **data.model_dump(),
            **fields,
            "branch_id": branch_id,
            "novel_id": branch["novel_id"],
            "conception_generation_version": LEGACY_VERSION
            if fields.get("operation") == "world_extract"
            else VERSION,
        }
        if payload.get("project_confirmation_id") is None:
            payload.pop("project_confirmation_id", None)
        try:
            prior = domain.find_request(KIND, payload)
        except DomainError as error:
            if (
                error.code != "idempotency"
                or payload["conception_generation_version"] != VERSION
                or "operation" in payload
                or "project_confirmation_id" in payload
            ):
                raise
            prior = domain.find_request(
                KIND,
                {
                    **payload,
                    "instruction": payload["instruction"].strip(),
                    "conception_generation_version": LEGACY_VERSION,
                },
            )
            if not prior:
                raise error
        if prior:
            return prior
        provider_ready(data.account)
        return domain.queue(KIND, payload)

    @app.post("/api/branches/{branch_id}/conception/generate", status_code=202)
    def generate_conception(branch_id: str, data: ConceptionGenerateInput):
        return queue_task(branch_id, data, {"instruction": data.instruction})

    @app.post("/api/branches/{branch_id}/conception/project-brief/generate", status_code=202)
    def generate_project(branch_id: str, data: ProjectGenerateInput):
        return queue_task(branch_id, data, {"operation": "project_brief"})

    @app.post("/api/branches/{branch_id}/conception/extract-world", status_code=202)
    def extract_conception_world(branch_id: str, data: ConceptionWorldExtractInput):
        return queue_task(branch_id, data, {"operation": "world_extract"})


def validate_basis_tx(tx, branch_id, payload):
    from .conception import revision_tx

    story = tx.execute("SELECT story_revision FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not story or story[0] != payload["base_story_revision"]:
        raise DomainError("story_changed", "作品依据已更新，请刷新后重新生成。", 409)
    if revision_tx(tx, branch_id) != payload["base_revision"]:
        raise DomainError("conception_changed", "构思草稿已更新，请刷新核对后重新生成。", 409)
    if identifier := payload.get("project_confirmation_id"):
        confirmed_project_tx(tx, branch_id, identifier)


def _setting_profile(profile, project):
    if not project:
        return profile
    return assemble_profile(
        {
            "audience": profile["audience"],
            "genres": project["brief"]["genre_ids"],
            "mechanisms": profile.get("mechanism_requests", []),
            "author_exceptions": profile.get("author_exceptions", []),
            "text": profile.get("author_notes", ""),
        }
    )


def freeze(domain, payload, *, genre_profile):
    from .conception import snapshot

    current = snapshot(domain, payload["branch_id"])
    if current["revision"] != payload["base_revision"]:
        raise DomainError("conception_changed", "构思草稿已更新，请刷新核对后重新生成。", 409)
    if current["story_revision"] != payload["base_story_revision"]:
        raise DomainError("story_changed", "作品依据已更新，请刷新后重新生成。", 409)
    basis = {
        "version": payload["conception_generation_version"],
        "conception_revision": current["revision"],
        "story_revision": current["story_revision"],
    }
    if payload.get("operation") == "world_extract":
        return {**basis, "target_ids": ["world"], "world_sources": freeze_world_sources(domain, payload)}
    project = current["project"]
    if payload.get("operation") == "project_brief":
        if (count := payload.get("project_chapter_count")) is not None:
            basis["project_planning_scope"] = {
                "version": PROJECT_PLANNING_SCOPE_VERSION,
                "chapter_count": count,
            }
        return {**basis, "idea": payload["idea"], "project_revision": project["revision"] if project else 0}
    if identifier := payload.get("project_confirmation_id"):
        if not project or project["id"] != identifier:
            raise DomainError("project_brief_changed", "已确认立项已更新，请读取最新修订。", 409)
    categories = current["categories"]
    requested = payload.get("target_category_ids")
    if requested is not None and (
        payload["category_id"] is not None
        or not isinstance(requested, list)
        or not requested
        or any(not isinstance(identifier, str) for identifier in requested)
        or len(set(requested)) != len(requested)
        or not set(requested) <= {section["id"] for section in categories}
    ):
        raise DomainError("conception_scope", "接力分类必须是当前工作版本内互不重复的类别。", 409)
    if payload["category_id"] is None:
        targets = [
            section["id"]
            for section in categories
            if not section["skipped"] and (requested is None or section["id"] in requested)
        ]
    else:
        targets = [section["id"] for section in categories if section["id"] == payload["category_id"]]
    if not targets:
        raise DomainError("conception_scope", "请选择本作品存在的构思分类；一键生成只处理未跳过分类。")
    source_records = {source["item_id"]: source for source in current["sources"]}
    categories = [
        {
            **section,
            "items": [
                item
                for item in section["items"]
                if not section["skipped"] and source_records.get(item["id"], {}).get("current", True)
            ],
        }
        for section in categories
    ]
    definitions = domain.db.all(
        "SELECT id,kind,payload FROM artifacts WHERE branch_id=? AND chapter_id IS NULL "
        "AND status='accepted' AND kind IN ('world','characters','outline') ORDER BY created DESC,id DESC",
        (payload["branch_id"],),
    )
    existing_definitions = {}
    for definition in definitions:
        kind = definition["kind"]
        if kind not in existing_definitions:
            existing_definitions[kind] = {
                "id": definition["id"],
                "kind": kind,
                "text": json.loads(definition["payload"]).get("text", ""),
            }
    framed_targets = [target for target in targets if target in SETTING_GUIDES]
    if framed_targets:
        profile = _setting_profile(genre_profile, project)
        basis["setting_frame_version"] = SETTING_FRAME_VERSION
        basis["setting_frames"] = {
            target: {
                "guide": SETTING_GUIDANCE + SETTING_GUIDES[target],
                "context": setting_framework(profile, category=target),
            }
            for target in framed_targets
        }
    return {
        **basis,
        **({"confirmed_project": project} if project else {}),
        "categories": categories,
        "target_ids": targets,
        "definitions": list(existing_definitions.values()),
        "current_source_items": {
            key: source["author_edited"] for key, source in source_records.items() if source["current"]
        },
    }


def freeze_world_sources(domain, payload):
    branch_id = payload["branch_id"]
    ids = payload["chapter_ids"]
    if len(set(ids)) != len(ids):
        raise DomainError("conception_chapters", "请选择不重复的来源章节。")
    chapters = sorted(
        (domain.chapter(identifier, branch_id) for identifier in ids), key=lambda c: c["position"]
    )
    if any(not chapter["content"].strip() or chapter["stale"] for chapter in chapters):
        raise DomainError("conception_chapters", "来源章节须有已保存且仍有效的正文。")
    if sum(len(chapter["content"]) for chapter in chapters) > WORLD_SOURCE_LIMIT:
        raise DomainError("conception_source_size", "本次反向提取最多读取 20,000 字符，请分批选择章节。")
    return [
        {
            "chapter_id": chapter["id"],
            "title": chapter["title"],
            "revision": chapter["revision"],
            "content_sha256": hashlib.sha256(chapter["content"].encode()).hexdigest(),
            "content": chapter["content"],
        }
        for chapter in chapters
    ]


def world_proposal_evidence(items, sources):
    evidence, issues = [], []
    titles = set()
    for index, item in enumerate(items):
        try:
            unit = sources[item["source_id"]]
        except KeyError:
            issues.append(f"items[{index}].source_id 不在本次来源内")
            continue
        quote = item["quote"]
        start = unit["text"].find(quote)
        if not quote.strip() or start < 0 or unit["text"].find(quote, start + 1) >= 0:
            issues.append(f"items[{index}].quote 须为来源单元内唯一、连续、逐字的原文")
            continue
        title = item["title"].casefold()
        if title in titles:
            issues.append(f"items[{index}].title 与前项重复")
            continue
        titles.add(title)
        start16 = unit["start"] + len(unit["text"][:start].encode("utf-16-le")) // 2
        evidence.append(
            {
                "chapter_id": unit["chapter_id"],
                "revision": unit["revision"],
                "start": start16,
                "end": start16 + len(quote.encode("utf-16-le")) // 2,
                "quote": quote,
            }
        )
    return evidence, issues


async def extract_world(worker, frozen):
    chapters = frozen["world_sources"]
    units = [
        {**unit, "chapter_id": chapter["chapter_id"], "revision": chapter["revision"]}
        for index, chapter in enumerate(chapters, 1)
        for unit in source_units(chapter["content"], f"W{index}P", group_size=600)
    ]
    sources = {unit["source_id"]: unit for unit in units}
    instruction = {
        "action": "conception.extract.world",
        "世界设定提取版本": WORLD_EXTRACTION_VERSION,
        "可引用原文": units,
        "要求": (
            "只从所给保存稿中提取明确可回查、与故事有关的世界规则、地点、制度、能力代价和资源限制。"
            "每项的 detail 采用保守表述，保留限定范围和原文中的推测、传闻或角色观点。"
            "不根据类型期待或角色所说推断全世界都如此，不添加未来安排。没有明确依据时返回空数组。"
            "每项附唯一可定位的 source_id 和该单元中连续逐字的 quote，引用包含支持说明的必要范围。"
            "归并相同条目，最多列出十二项最明确、有区分度的候选。"
        ),
    }
    contract = ConceptionWorldProposal.model_json_schema()
    if len(sources) <= 250:
        contract["$defs"]["ConceptionWorldEvidence"]["properties"]["source_id"]["enum"] = list(sources)
    context = {
        "branch": {"id": worker.task["branch_id"], "story_revision": frozen["story_revision"]},
        "source_version_basis": [
            {key: chapter[key] for key in ("chapter_id", "revision", "content_sha256")}
            for chapter in chapters
        ],
    }
    for round_number in range(2):
        output = await worker.call(
            instruction,
            schema=ConceptionWorldProposal,
            context=context,
            max_output=4096,
            structure_repairs=1,
            contract=contract,
        )
        evidence, issues = world_proposal_evidence(output["items"], sources)
        if not issues:
            items = [
                {
                    "id": uuid5(UUID(worker.task["id"]), f"world-evidence:{index}").hex,
                    "title": item["title"],
                    "detail": item["detail"],
                    "links": [],
                }
                for index, item in enumerate(output["items"], 1)
            ]
            return {"id": "world", "items": items}, {
                item["id"]: source for item, source in zip(items, evidence, strict=True)
            }
        if round_number == 0:
            instruction = {**instruction, "上一轮候选": output, "定位错误": issues}
    raise DomainError("evidence", "世界设定候选仍无法回查到所选正文，本次没有发布候选。")


def reference_context(categories, completed, source_items, *, distinguish_adoption=False):
    entries = [
        (category["id"], entry, candidate)
        for group, candidate in ((categories, False), (completed, True))
        for category in group
        for entry in category["items"]
    ]
    reference_ids = {f"S{index}": entry["id"] for index, (_, entry, _) in enumerate(entries, 1)}
    tokens = {identifier: token for token, identifier in reference_ids.items()}
    references = [
        {
            "ref": tokens[entry["id"]],
            "category_id": category_id,
            "title": entry["title"],
            "detail": entry["detail"],
            "links": [tokens[linked] for linked in entry["links"] if linked in tokens],
            "origin": (
                "从当前保存稿提取，作者已编辑"
                if source_items[entry["id"]]
                else "从当前保存稿提取，作者已采用"
            )
            if entry["id"] in source_items
            else ("本次先前分类的 AI 候选" if candidate else "构思工作台已保存"),
            **(
                {"设定选用状态": "proposed" if candidate else entry.get("adoption_state") or "adopted"}
                if distinguish_adoption
                else {}
            ),
        }
        for category_id, entry, candidate in entries
    ]
    return reference_ids, references


async def generate_category(worker, request, frozen, category, completed):
    frame = frozen.get("setting_frames", {}).get(category["id"])
    reference_ids, references = reference_context(
        frozen["categories"],
        completed,
        frozen["current_source_items"],
        distinguish_adoption=frame is not None,
    )
    instruction = {
        "action": "conception.generate.category",
        "构思生成契约版本": frozen["version"],
        "category_id": category["id"],
        "category_title": category["title"],
        "作者本次要求": request["instruction"],
        "分类要求": GUIDES.get(
            category["id"], "按作者自定义分类的实际用途安排可编辑条目，与现有构思和明确边界一致。"
        ),
        "输出要求": (
            "只提出当前分类可编辑的构思条目，每项写清具体作用。"
            "作者原话、已保存构思和既有正式设定优先于本次 AI 候选。保留未确定空间，不替作者新增硬约束。"
            "不生成正文，不把计划当成已发生的事实。不要整项照抄已经存在的构思。"
            "links 只写来源表中真实存在的 S 编号，或同一输出数组内按 1 开始的 N 编号。"
            "不允许自引、重复或猜测编号。没有必要关联时返回空数组。"
        ),
    }
    context = {
        "branch": {"id": worker.task["branch_id"], "story_revision": frozen["story_revision"]},
        "source_version_basis": {
            "conception_revision": frozen["conception_revision"],
            "story_revision": frozen["story_revision"],
            "conception_generation_version": frozen["version"],
        },
        "definitions": frozen["definitions"],
        "构思分类": [
            {"id": row["id"], "title": row["title"]}
            for row in frozen["categories"]
            if not row["skipped"] or row["id"] == category["id"]
        ],
        "可关联构思与来源": references,
    }
    if project := frozen.get("confirmed_project"):
        context["已确认立项"] = project
        instruction["立项交接"] = (
            "以已确认立项的定位、限制和终局为规划依据。与原作者硬约定冲突时列出供作者核对。"
        )
    if frame:
        context["设定框架"] = frame["context"]
        context["source_version_basis"]["setting_frame_version"] = frozen["setting_frame_version"]
        instruction["构思设定框架版本"] = frozen["setting_frame_version"]
        instruction["设定框架用法"] = frame["guide"]
    elif category["id"] == "world":
        profile = _setting_profile(worker.genre_snapshot(), project)
        context["设定框架"] = setting_framework(profile)
        instruction["设定框架用法"] = (
            "以主类型条目为底稿，辅类型只取与主线相交的规则，机制按适用场景挂载。"
            "优先交代规则触发、限制、代价、适用范围和例外；冲突单独列出供作者取舍。"
            "作者既定约定优先，区分世界规则、作者所知与人物所知。"
            "为规则提供 setting_policy，使用 hard 表示明确限制或 soft 表示可调整倾向，写明适用范围与例外。"
            "条目是待确认设定，不是正文事实；题材清单按本书需要选用。"
        )
    contract = ConceptionCategoryProposal.model_json_schema()
    if len(reference_ids) <= 250:
        contract["$defs"]["ConceptionProposalItem"]["properties"]["links"]["items"]["enum"] = [
            *reference_ids,
            *(f"N{index}" for index in range(1, 9)),
        ]
    for round_number in range(2):
        proposal = await worker.call(
            instruction,
            schema=ConceptionCategoryProposal,
            context=context,
            max_output=4096,
            structure_repairs=1,
            contract=contract,
        )
        items = ConceptionCategoryProposal.model_validate(proposal).items
        ids = {
            f"N{index}": uuid5(UUID(worker.task["id"]), f"{category['id']}:{index}").hex
            for index in range(1, len(items) + 1)
        }
        links = {**reference_ids, **ids}
        invalid = [
            f"items[{index}].links[{position}]"
            for index, entry in enumerate(items)
            for position, link in enumerate(entry.links)
            if link not in links
        ]
        if not invalid:
            return {
                "id": category["id"],
                "items": [
                    {
                        "id": ids[f"N{index}"],
                        "title": item.title,
                        "detail": item.detail,
                        "links": [links[link] for link in item.links],
                        **(
                            {"setting_policy": item.setting_policy.model_dump()}
                            if category["id"] == "world" and item.setting_policy
                            else {}
                        ),
                    }
                    for index, item in enumerate(items, 1)
                ],
            }
        if round_number == 0:
            instruction = {
                **instruction,
                "上一轮候选": proposal,
                "无效关联位置": invalid,
                "修正要求": "仅修正无效关联。依据来源表中真实的编号，如果无处可关联，移除相应引用。",
            }
    raise DomainError("conception_links", "构思候选仍引用不存在的条目，已保留此前完成的分类。")


def restore_completed(worker, result, frozen):
    if not (identifier := result.get("partial_artifact_id")):
        return []
    artifact = worker.domain.artifact(identifier)
    categories = artifact["payload"]["categories"]
    if (
        artifact["task_id"] != worker.task["id"]
        or artifact["kind"] != "conception_bundle"
        or [category["id"] for category in categories] != frozen["target_ids"][: len(categories)]
        or artifact["basis"]["conception_revision"] != frozen["conception_revision"]
        or artifact["basis"]["story_revision"] != frozen["story_revision"]
    ):
        raise DomainError("conception_checkpoint", "检查点无法与冻结构思对应，未继续调用。", 409)
    return categories


def bundle_issues(frozen, proposals):
    from .conception import completeness_issues

    generated_items = {section["id"]: section["items"] for section in proposals}
    hypothetical = [
        {**section, "items": [*section["items"], *generated_items.get(section["id"], [])]}
        for section in frozen["categories"]
    ]
    return completeness_issues(hypothetical)


def needs_model_review(frozen, request):
    return (
        frozen["version"] == VERSION and request.get("category_id") is None and "world_sources" not in frozen
    )


async def review_bundle(worker, frozen, proposals):
    names = {row["id"]: row["title"] for row in frozen["categories"]}
    targets = frozen["target_ids"]
    _, references = reference_context(frozen["categories"], proposals, frozen["current_source_items"])
    instruction = {
        "action": "conception.review.bundle",
        "构思一致性复核版本": CONSISTENCY_VERSION,
        "作者本次要求": worker.task["payload"]["instruction"],
        "可报告的分类": [{"id": key, "title": names[key]} for key in targets],
        "要求": (
            "只读复核这套 AI 草案。独立逐项查找与本次作者原话、已采用创作约定和已有设定的冲突。"
            "作者明确写出的必须发生事项，对照关键事件与结局核实是否确实安排；区分必要事项遗漏、明确反转和允许开放的细节。"
            "再查人物选择是否有动机，是否突破世界规则或代价，事件因果和故事时序是否冲突，"
            "伏笔是否有明确铺设和可解释的回收，结局是否承接主线。区分故事发生顺序和正文叙述顺序。"
            "题材是参考，不能覆盖作者要求或升级为硬约束。不为跳过分类强行报缺项。"
            "每条 message 写出受影响条目、冲突的具体原话或草案说法及为何冲突；"
            "按最相关的分类 id 报告，合并同一问题。只报告可核实的具体问题，没有则返回空数组。"
            "不要修改、重写、采用草案或增补剧情。不把未来计划当作已经发生的正文事实。"
        ),
    }
    context = {
        "branch": {"id": worker.task["branch_id"], "story_revision": frozen["story_revision"]},
        "source_version_basis": {
            "conception_revision": frozen["conception_revision"],
            "story_revision": frozen["story_revision"],
            "conception_generation_version": frozen["version"],
        },
        "definitions": frozen["definitions"],
        "候选分类": [row for row in references if row["origin"] == "本次先前分类的 AI 候选"],
        "已保存的构思": [row for row in references if row["origin"] != "本次先前分类的 AI 候选"],
    }
    contract = ConceptionConsistencyReview.model_json_schema()
    contract["$defs"]["ConceptionConsistencyIssue"]["properties"]["category_id"]["enum"] = targets
    for round_number in range(2):
        result = await worker.call(
            instruction,
            schema=ConceptionConsistencyReview,
            context=context,
            max_output=4096,
            structure_repairs=1,
            contract=contract,
        )
        if all(issue["category_id"] in targets for issue in result["issues"]):
            return {"version": CONSISTENCY_VERSION, "issues": result["issues"]}
        if round_number == 0:
            instruction = {
                **instruction,
                "上一轮报告": result,
                "修正要求": "只把报告归到可报告分类的合法 id，删除与本次范围无关的意见。",
            }
    raise DomainError("conception_review", "一致性复核仍引用未知分类，草案已保留供作者查看。")


def publish(worker, result, frozen, categories, source_evidence=None, model_review=None):
    task = worker.task
    identifier = uid()
    completed_ids = [category["id"] for category in categories]
    all_generated = completed_ids == frozen["target_ids"]
    review_requested = needs_model_review(frozen, task["payload"])
    pending_review = all_generated and review_requested and model_review is None
    complete = all_generated and not pending_review
    status = "succeeded" if complete else "running"
    progress = {
        "total_categories": len(frozen["target_ids"]),
        "completed_categories": completed_ids,
        "partial_artifact_id": identifier,
        **({"pending_consistency_review": pending_review} if review_requested else {}),
        **({"artifact_id": identifier} if complete else {}),
    }
    basis = {
        "conception_revision": frozen["conception_revision"],
        "story_revision": frozen["story_revision"],
        "input_sha256": hashlib.sha256(dumps(frozen).encode()).hexdigest(),
        "genre_profile_id": result["genre_profile_snapshot"]["source_artifact_id"],
        "author_contract_id": result["author_contract_snapshot"]["source_artifact_id"],
    }
    content = {"version": frozen["version"], "categories": categories, "complete": complete}
    if source_evidence is not None:
        content["source_evidence"] = source_evidence
        content["world_extraction_version"] = WORLD_EXTRACTION_VERSION
        basis["chapter_sources"] = [
            {key: chapter[key] for key in ("chapter_id", "revision", "content_sha256")}
            for chapter in frozen["world_sources"]
        ]
    elif all_generated and task["payload"]["category_id"] is None:
        content["issues"] = bundle_issues(frozen, categories)
    if model_review is not None:
        content["model_review"] = model_review
    if version := frozen.get("setting_frame_version"):
        basis["setting_frame_version"] = version
    if project := frozen.get("confirmed_project"):
        basis["project_confirmation_id"] = project["id"]
        basis["project_revision"] = project["revision"]
    persist_generation(worker, result, identifier, "conception_bundle", content, basis, progress, status)


def persist_generation(
    worker, result, identifier, kind, content, basis, progress, status, artifact_status="candidate"
):
    task = worker.task
    with worker.db.connect(True) as db:
        worker.db.check_attempt(db, task["id"], worker.attempt)
        validate_basis_tx(db, task["branch_id"], task["payload"])
        timestamp = time()
        db.execute(
            "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,task_id,created,status) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                task["novel_id"],
                task["branch_id"],
                kind,
                dumps(content),
                dumps(basis),
                task["id"],
                timestamp,
                artifact_status,
            ),
        )
        db.execute(
            "UPDATE tasks SET status=?,result=?,updated=? WHERE id=?",
            (status, dumps({**result, **progress}), timestamp, task["id"]),
        )
        worker.db.event(db, task["id"], "conception_checkpoint", {"status": status, **progress})
    result.update(progress)


async def run(worker):
    result = worker.task["result"]
    frozen = result["conception_generation_snapshot"]
    if frozen["version"] not in {VERSION, LEGACY_VERSION}:
        raise DomainError("conception_version", "旧构思生成契约不受支持，请重新创建生成任务。", 409)
    if worker.task["payload"].get("operation") == "project_brief":
        from .project_brief import generate

        brief = await generate(worker, frozen)
        identifier = uid()
        basis = {"story_revision": frozen["story_revision"], "project_revision": frozen["project_revision"]}
        if "project_planning_scope" in frozen:
            basis["project_planning_scope"] = frozen["project_planning_scope"]
        persist_generation(
            worker,
            result,
            identifier,
            DRAFT_KIND,
            {"idea": frozen["idea"], "brief": brief},
            basis,
            {"artifact_id": identifier},
            "succeeded",
            artifact_status="accepted",
        )
        return
    if "world_sources" in frozen:
        category, evidence = await extract_world(worker, frozen)
        publish(worker, result, frozen, [category], source_evidence=evidence)
        return
    completed = restore_completed(worker, result, frozen)
    categories = {category["id"]: category for category in frozen["categories"]}
    for category_id in frozen["target_ids"][len(completed) :]:
        section = await generate_category(
            worker, worker.task["payload"], frozen, categories[category_id], completed
        )
        completed.append(section)
        publish(worker, result, frozen, completed)
    if needs_model_review(frozen, worker.task["payload"]):
        review = await review_bundle(worker, frozen, completed)
        publish(worker, result, frozen, completed, model_review=review)
