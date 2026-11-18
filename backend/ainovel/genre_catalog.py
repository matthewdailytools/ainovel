"""Versioned, composable writing guidance from the existing reviewed design catalog."""

from collections.abc import Mapping
from copy import deepcopy
from functools import cache
import json
from pathlib import Path
import re
from typing import Any


@cache
def _catalog() -> dict[str, Any]:
    return json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))


def get_catalog() -> dict[str, Any]:
    """Return an independent JSON-serializable copy, never mutable process state."""
    return deepcopy(_catalog())


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def _topic(value: dict[str, Any], group: str | None = None) -> dict[str, Any]:
    return {
        "id": value["id"],
        "name": value["title"],
        "title": value["title"],
        "group": group or value.get("group", "mechanisms"),
        "owner": value.get("owner", "M02"),
        "items": list(value["items"]),
    }


def _select(values: list[str], entries: list[dict[str, Any]], aliases: dict[str, str]):
    matches: dict[str, set[str]] = {}
    by_id = {entry["id"]: entry for entry in entries}
    for entry in entries:
        for name in [entry["id"], entry["name"], *entry["name"].split(" · "), *entry.get("tags", [])]:
            matches.setdefault(name.casefold(), set()).add(entry["id"])
    for name, entry_id in aliases.items():
        matches[name.casefold()] = {entry_id}
    selected, custom, seen = [], [], set()
    for value in values:
        found = matches.get(value.casefold(), set())
        if len(found) != 1:
            custom.append(value)
            selected.append({"id": None, "name": value, "custom": True})
            continue
        entry_id = next(iter(found))
        if entry_id not in seen:
            selected.append({**by_id[entry_id], "custom": False})
            seen.add(entry_id)
    return selected, custom


def assemble_profile(reader: Mapping[str, object] | None) -> dict[str, Any]:
    """Assemble the selected book's guidance without creating facts or hard plot constraints.

    Older definitions store display names; newer controls store stable IDs. Both are
    supported, while unknown and ambiguous values remain visible custom requests.
    """
    catalog = _catalog()
    reader = reader or {}
    audience_requested = str(reader.get("audience", "不限"))
    channel_id = {"男频": "male", "女频": "female", "不限": "unrestricted"}.get(
        audience_requested, audience_requested
    )
    channels = {a["id"]: a for a in catalog["audiences"]}
    channel = channels.get(channel_id, channels["unrestricted"])
    channel_id = channel["id"]
    genres, custom_genres = _select(
        _strings(reader.get("genres")),
        catalog["genres"],
        {"修仙": "xianxia", "修真": "xianxia", "玄幻": "eastern", "科幻": "science", "种田": "farm"},
    )
    requested_mechanisms = _strings(reader.get("mechanisms"))
    mechanisms, custom_mechanisms = _select(
        requested_mechanisms, catalog["mechanisms"], {"无CP": "plugin-20", "无 CP": "plugin-20"}
    )
    exceptions = _strings(reader.get("author_exceptions"))
    for request in requested_mechanisms:
        if re.fullmatch(r"无\s*cp", request, re.IGNORECASE) and request not in exceptions:
            exceptions.append(request)
    notes = reader.get("text", "")
    notes = notes if isinstance(notes, str) else ""
    selected_genres = [g for g in genres if not g["custom"]]
    selected_mechanisms = [m for m in mechanisms if not m["custom"]]
    topic_ids = {"core-1", "core-2", "core-3", "core-4", "core-11", "core-12", "core-13", "core-16"}
    required = [_topic(t) for t in catalog["core_topics"] if t["id"] in topic_ids]
    optional = [_topic(t) for t in catalog["core_topics"] if t["id"] not in topic_ids]
    required += [_topic(t, g["id"]) for g in selected_genres for t in g["topics"]]
    required += [_topic(m, m["id"]) for m in selected_mechanisms]
    focus, promises = [], []
    checks = [
        "作者明确要求、固定情节、禁止项与已采纳事实优先于题材和频道预设。",
        "不因女频自动增加爱情主线，不因男频自动增加后宫、升级或称帝终局。",
        "只检查当前场景与阶段相关内容，不要求每章填满题材设计目录或提前兑现后章情节。",
        "频道与题材偏好作为可解释建议，不能单独冒充严重事实错误或新增作者固定条件。",
    ]
    if reader:
        promises.append(channel["promise"])
    for genre in selected_genres:
        variant = genre["profiles"].get(channel_id)
        if variant:
            focus.append(variant["focus"])
            promises.extend([variant["emotion"], variant["hook"]])
            checks.extend(variant["checks"])
        else:
            focus.append(genre["description"])
        checks.append(genre["audit"])
    if reader:
        focus.extend([channel["emotion"], channel["character"], channel["relationship"], channel["pacing"]])
    conflicts = []
    genre_ids = {g["id"] for g in selected_genres}
    if len(genres) > 1:
        conflicts.append(
            "主辅题材共享同一套人物、世界、时间线和资源规则；冲突保留为待取舍，不能各造一套事实。"
        )
    if genre_ids & {"romance", "ancient", "beastworld"} and any(
        re.search(r"无\s*cp|不写恋爱|不要恋爱|不增加爱情", e, re.IGNORECASE) for e in exceptions
    ):
        conflicts.append(
            "爱情相关题材与无CP约定并存：保留作者约定，可只取世界或非恋爱关系设计；若需爱情主线须另作明确决定。"
        )
    if {"xianxia", "science"} <= genre_ids:
        conflicts.append("修仙与科幻组合须统一能量、技术和验证边界，区分现实科学与虚构前提。")
    if {"rules", "infinite"} <= genre_ids:
        conflicts.append("规则怪谈与副本组合须区分真规则、人物误读及跨副本保留条件，不临时改规则替角色解围。")
    if channel_id == "unrestricted" and audience_requested not in {"不限", "unrestricted"}:
        conflicts.append(
            f"未识别旧频道“{audience_requested}”，保留该原值并按不限处理，未推断人物或作者性别。"
        )
    if custom_genres or custom_mechanisms:
        conflicts.append("自定义或有歧义的题材／机制原词已保留；没有套用未明确对应的目录预设。")
    if exceptions:
        checks.append("逐项核对作者例外，预设与例外相冲突时保留作者例外并指出影响。")
    return {
        "catalog_version": catalog["version"],
        "configured": bool(reader),
        "audience": channel["name"],
        "audience_requested": audience_requested,
        "primary_genre": genres[0]["name"] if genres else None,
        "secondary_genres": [g["name"] for g in genres[1:]],
        "genre_ids": [g["id"] for g in selected_genres],
        "custom_genres": custom_genres,
        "mechanisms": [m["name"] for m in mechanisms],
        "mechanism_ids": [m["id"] for m in selected_mechanisms],
        "custom_mechanisms": custom_mechanisms,
        "mechanism_requests": requested_mechanisms,
        "author_exceptions": exceptions,
        "author_notes": notes,
        "reading_promises": list(dict.fromkeys(promises)),
        "focus": list(dict.fromkeys(focus)),
        "checks": list(dict.fromkeys(checks)),
        "required_topics": required,
        "optional_topics": optional,
        "conflicts": conflicts,
        "priority": list(catalog["priority"]),
        "policy": "目录和频道是可调建议，不产生正文事实或新硬约束；作者明确取舍优先。偏好问题只能作为有依据的建议，不能单凭预设否决稿件。",
        "coverage": "选中题材与机制的设计主题可按任务展开；未选择其他题材。共同主题按当前任务相关性分组，不要求逐章逐项填满。",
    }


# Topic owners in the shared catalogue describe the domain each topic serves.
# A category can draw on several domains when they affect its decisions.
_SETTING_TOPIC_OWNERS = {
    "core": "M01 M15 M17 M16 T01 T03 T04 T05 T06 T10 T11 T02 T07 T08 T09 T12 M14".split(),
    "characters": "M12 M06 T01 M13 M07 M05 T05 T08 T07 T11 T02 T06 T09 M14 M11 M08 T04 T10 T12 M16".split(),
    "relationships": "M13 T05 M08 T01 T08 T11 M12 M10 T03 T06 T02 T07 T09 M05 M11 T04 T10 T12 M16".split(),
    "world": "M04 M05 M06 M07 M08 M09 M10 M11 M03 T02 T03 T04 T06 T07 T08 T09 T10 T11 T01 T05 T12 M14 M19".split(),
}

# These topics share broad catalogue owners but serve different setting decisions.
_SETTING_TOPIC_PRIORITY = {
    "xianxia": {
        "core": ("xianxia-24", "xianxia-23", "xianxia-12"),
        "characters": ("xianxia-3", "xianxia-4", "xianxia-23"),
        "relationships": ("xianxia-13", "xianxia-14", "xianxia-8", "xianxia-17"),
        "world": ("xianxia-2", "xianxia-4", "xianxia-12"),
    },
    "mystery": {
        "core": ("mystery-1", "mystery-2", "mystery-3", "mystery-7", "mystery-8"),
        "characters": ("mystery-4", "mystery-5", "mystery-6"),
        "relationships": ("mystery-5", "mystery-4", "mystery-1"),
        "world": ("mystery-6", "mystery-1", "mystery-3"),
    },
}


def _setting_topics(topics: list[dict], identifier: str, category: str, *, primary: bool) -> list[dict]:
    if category == "world" and primary:
        return topics
    owners = {owner: index for index, owner in enumerate(_SETTING_TOPIC_OWNERS[category])}
    priorities = _SETTING_TOPIC_PRIORITY.get(identifier, {}).get(category, ())
    preferred = {topic_id: index for index, topic_id in enumerate(priorities)}
    relevant = [topic for topic in topics if topic["id"] in preferred or topic["owner"] in owners]
    ordered = sorted(
        relevant,
        key=lambda topic: (
            preferred.get(topic["id"], len(preferred)),
            owners.get(topic["owner"], len(owners)),
        ),
    )
    return ordered[: 5 if primary else 3]


def setting_framework(profile: Mapping[str, Any], *, category: str | None = None) -> dict[str, Any]:
    """Select setting topics from the frozen catalogue profile for M3."""
    topics = profile.get("required_topics", [])

    def group(identifier):
        return [topic for topic in topics if topic["group"] == identifier]

    def display(grouped):
        return [
            {**({"id": topic["id"]} if category else {}), "title": topic["title"], "items": topic["items"]}
            for topic in grouped
        ]

    genre_ids = profile.get("genre_ids", [])
    genres = {entry["id"]: entry["name"] for entry in _catalog()["genres"]}
    selected = []
    for identifier in genre_ids:
        name = genres[identifier]
        entry = {"id": identifier, "name": name}
        grouped = group(identifier)
        if category:
            expanded = _setting_topics(
                grouped, identifier, category, primary=name == profile.get("primary_genre")
            )
            included = {topic["id"] for topic in expanded}
            entry["deferred_topic_ids"] = [topic["id"] for topic in grouped if topic["id"] not in included]
        else:
            expanded = grouped
        entry["topics"] = display(expanded)
        selected.append(entry)
    primary = next((entry for entry in selected if entry["name"] == profile.get("primary_genre")), None)
    return {
        "catalog_version": profile.get("catalog_version"),
        **({"category": category} if category else {}),
        "common": ["人物与关系", "时间地点", "资源与组织", "主要冲突", "终局与披露边界"],
        "primary": primary,
        "secondary": [entry for entry in selected if entry is not primary],
        "mechanisms": [
            {"id": identifier, "topics": display(group(identifier))}
            for identifier in profile.get("mechanism_ids", [])
        ],
        "custom_genres": profile.get("custom_genres", []),
        "author_exceptions": profile.get("author_exceptions", []),
        "conflicts": profile.get("conflicts", []),
    }


_ACTIONS = {
    "writing.advise": "scene",
    "writing.expand_plot": "scene",
    "writing.rewrite_selection": "expression",
    "review.chapter": "review",
    "memory.extract": "memory",
    "story.extract": "memory",
    "按保存稿反向整理长篇结构候选": "memory",
    "拆解所选原文写法": "expression",
    "将参考写法迁移到本书": "planning",
    "按确认参考应用方案原创试写": "scene",
    "参考写法对照检查": "review",
    "outline.plan": "planning",
    "character.generate": "character",
    "world.generate": "world",
    "ideation.explore": "ideation",
    "ideation.structure": "ideation",
    "全书滚动规划": "planning",
    "阶段批次规划": "planning",
    "根据情节补写正文": "scene",
    "按章纲写完整正文": "scene",
    "审查正文": "review",
    "用局部替换修订正文": "review",
    "从正文提取事实候选与章节摘要": "memory",
    "整书完结检查": "completion",
    "批次范围检查": "completion",
}
_OWNERS = {
    "scene": {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M20", "M21"},
    "expression": {"M12", "M16", "M18", "M19", "M20", "M21"},
    "review": {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M20", "M21", "M24"},
    "memory": {"M12", "M13", "M14", "M17"},
    "planning": {"M01", "M12", "M13", "M14", "M15", "M16", "M17", "M24"},
    "character": {"M07", "M08", "M10", "M11", "M12", "M13", "M14"},
    "world": {"M03", "M04", "M05", "M06", "M07", "M08", "M09", "M10", "M11"},
    "ideation": {"M01", "M12", "M15", "M16", "M24"},
    "completion": {"M12", "M13", "M14", "M15", "M16", "M17", "M24"},
}


def profile_for_action(profile: Mapping[str, Any], action: str) -> dict[str, Any]:
    """Clip only catalog guidance, retaining author text and all provenance fields.

    Selected genre-specific topics use dedicated T modules as well as common M
    owners; their full titles remain visible even when not expanded this time.
    """
    result = deepcopy(dict(profile))
    stage = _ACTIONS.get(action, "ideation")
    owners = _OWNERS[stage]
    topics = [*result.get("required_topics", []), *result.get("optional_topics", [])]
    loaded, deferred = [], []
    for topic in topics:
        selected_mechanism = topic["group"] in result.get("mechanism_ids", [])
        specialized = topic["owner"].startswith("T")
        if (
            topic["owner"] in owners
            or selected_mechanism
            or (specialized and stage in {"world", "review", "planning", "completion", "scene"})
        ):
            loaded.append(topic)
        else:
            deferred.append({"id": topic["id"], "title": topic["title"], "group": topic["group"]})
    result["required_topics"] = loaded
    result["optional_topics"] = []
    result["deferred_topics"] = deferred
    result["action"] = action
    result["coverage"] = (
        f"当前动作仅展开 {len(loaded)} 个相关设计主题；另 {len(deferred)} 个本书主题保留标题但未展开。不加载未选题材。"
    )
    return result
