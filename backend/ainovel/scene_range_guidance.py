"""Explain the unchanged scene-source boundary without rewriting its evidence."""

from .db import DomainError, dumps

VERSION = "scene-range-evidence/1"
KEY = "scene_range_guidance_version"
INSTRUCTION = (
    "order_basis.refs是本场景的直接原文依据，不是泛相关事件列表；kind=unknown时也一样。"
    "每个ref都须为有效actual事件，且其sources至少一处与本场景当前章节、修订和原文覆盖范围相交。"
    "同章有关系但来源全在场景外仍不满足；只写未知理由不豁免这个边界。"
    "一个实际事件可在原文多处呈现，同一事件仍用同一标识，不为不同呈现重复创建事件或应用状态。"
    "确为同一事实时，回到所给原文为未采纳事件提案补上本场景对应的独立来源，保留旧证据；"
    "正式记录仍须明确更新提案并沿用原采纳政策，不能直接改历史。"
    "只是泛相关、没有直接依据的关联放note或unknowns保留说明，不能当作refs；"
    "其他合法refs、事件事实、已知故事顺序和正文保持，不复制无关原文、不改status为actual以通过。"
)


def frozen_version(task):
    result = task.get("result", {})
    if KEY not in result:
        return None
    if result[KEY] != VERSION or result.get("scene_contract_version") != "scene-event-refs/2":
        raise DomainError("scene_range_guidance_version", "场景来源范围提示的冻结版本无法确认，未派发。", 409)
    return VERSION


def preview(values, limit=8):
    values = list(values)
    clipped = [v[:120] + ("…" if len(v) > 120 else "") if isinstance(v, str) else v for v in values[:limit]]
    return dumps(clipped) + (f"（共{len(values)}项）" if len(values) > limit else "")


class RangeEvidence:
    def __init__(self, units):
        self.groups = {}
        for unit in units:
            self.groups.setdefault((unit["chapter_id"], unit["revision"]), []).append(unit)
        # One validation round owns immutable effective records. Reuse a source
        # footprint when several scenes reference the same event.
        self.footprints = {}

    def footprint(self, record, chapter, revision):
        key = id(record), chapter, revision
        if key not in self.footprints:
            sources = [
                s for s in record["sources"] if (s["chapter_id"], s["revision"]) == (chapter, revision)
            ]
            self.footprints[key] = [
                unit["source_id"]
                for unit in self.groups.get((chapter, revision), [])
                if any(max(unit["start"], s["start"]) < min(unit["end"], s["end"]) for s in sources)
            ]
        return self.footprints[key]

    def failure(self, ref, node, selected):
        prefix = "refs[" + dumps(ref[:120] + ("…" if len(ref) > 120 else "")) + "]"
        if not node:
            return prefix + "未在当前正式或本批事件索引中声明，不按名称猜测绑定。"
        record = node["record"]
        if record["kind"] != "event":
            return prefix + f"类型为{record['kind']}，需要event，不自动改绑。"
        if node["archived"] or node["invalid"]:
            return prefix + "事件已归档或失效，先核对其版本和来源，不换成其他身份以通过。"
        if record["status"] != "actual":
            return prefix + f"当前status={record['status']}，需要actual；不把计划或未知改标为已发生。"
        chapter, revision = selected[0]["chapter_id"], selected[0]["revision"]
        same_chapter = [s for s in record["sources"] if s["chapter_id"] == chapter]
        current = [s for s in same_chapter if s["revision"] == revision]
        location = f"本场景chapter={preview([chapter])},revision={revision},units={preview(u['source_id'] for u in selected)}"
        if not current:
            if same_chapter:
                return (
                    prefix
                    + f"来源修订与场景不符；{location}，事件来源revision={preview(sorted({s['revision'] for s in same_chapter}))}；不挪用旧版本位置。"
                )
            return (
                prefix
                + f"没有本章当前版本来源；{location}，事件来源chapter={preview(dict.fromkeys(s['chapter_id'] for s in record['sources']))}；不借其他章节。"
            )
        return (
            prefix + f"来源不在本场景覆盖范围（同章当前版本）；{location}，"
            f"事件现有来源触及单元={preview(self.footprint(record, chapter, revision))}，当前覆盖交集=0。"
            "确为同一事件多处呈现时回原文为未采纳提案补相应独立来源；"
            "泛相关放note/unknowns，保留其他有效refs，不改事实、状态或已知顺序以通过。"
        )
