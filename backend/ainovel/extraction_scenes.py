"""Source-unit partitions become versioned scene ranges only on explicit adoption."""

import json
from time import time
from typing import Literal

from pydantic import Field

from .db import DomainError, dumps, uid
from .story_graph import Strict
from .scene_time import SceneTime, event_basis_valid


class SceneProposal(Strict):
    title: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=2000)
    story_order: int | None = None
    story_time: str = Field(default="未知", max_length=200)
    audience: Literal["author", "character", "reader"] = "author"
    character_id: str | None = None
    note: str = Field(default="", max_length=2000)


def freeze_scenes(domain, chapters):
    return [
        {**r, "payload": json.loads(r["payload"])}
        for c in chapters
        for r in domain.db.all(
            "SELECT id,chapter_id,revision,payload FROM scene_times WHERE chapter_id=? AND archived=0 ORDER BY created,id",
            (c["id"],),
        )
    ]


def scene_issues(value, units, known, proposals):
    indexed = {u["source_id"]: u for u in units}
    actors = {i for i, r in known.items() if r["kind"] == "character"}
    actors.update(p["local_id"] for p in proposals if p["record"]["kind"] == "character")
    expected = {}
    for u in units:
        expected.setdefault(u["chapter_id"], []).append(u["source_id"])
    actual = {c: [] for c in expected}
    issues = []
    for scene in value:
        if not scene["title"].strip():
            issues.append("场景名称不能仅有空白。")
        selected = [indexed.get(i) for i in scene["source_ids"]]
        if any(u is None for u in selected) or len({u["chapter_id"] for u in selected if u}) != 1:
            issues.append("场景source_ids必须来自同一章的有效原文编号。")
            continue
        chapter_id = selected[0]["chapter_id"]
        actual[chapter_id].extend(scene["source_ids"])
        if scene["audience"] == "character" and not scene["character_id"]:
            issues.append("人物视角必须明确正式人物或本批人物local_id。")
        if scene["character_id"] and scene["character_id"] not in actors:
            issues.append("场景人物必须属于当前版本或本批明确的人物提案。")
        if scene["story_order"] is not None:
            events = {i: r["payload"] for i, r in known.items() if r["kind"] == "event"}
            for proposal in proposals:
                if proposal["record"]["kind"] != "event":
                    continue
                old = known.get(proposal["target_id"])
                event = dict(old["payload"]) if old else dict(proposal["record"])
                if old:
                    event.update({k: proposal["record"][k] for k in proposal["update_fields"]})
                    event["sources"] = [*old["payload"]["sources"], *proposal["record"]["sources"]]
                events[proposal["target_id"] or proposal["local_id"]] = event
            orders = {
                e["story_order"]
                for e in events.values()
                if e["status"] == "actual"
                and any(
                    s["chapter_id"] == chapter_id
                    and s["revision"] == selected[0]["revision"]
                    and any(max(s["start"], u["start"]) < min(s["end"], u["end"]) for u in selected)
                    for s in e["sources"]
                )
            } - {None}
            if orders != {scene["story_order"]}:
                issues.append(
                    "场景故事顺序须依据覆盖范围内的实际事件；没有依据时使用null和未知说明，不按章号猜测。"
                )
    if actual != expected:
        issues.append("每章场景须按原文编号连续分组、完整覆盖且每段只出现一次，不漏段、重复或倒序。")
    return issues


def normalize_scenes(value, units, chapters, allocated):
    from .domain import utf16_slice

    indexed = {u["source_id"]: u for u in units}
    result = []
    for chapter in chapters:
        groups = [s for s in value if indexed[s["source_ids"][0]]["chapter_id"] == chapter["id"]]
        for n, group in enumerate(groups):
            start = 0 if n == 0 else indexed[group["source_ids"][0]]["start"]
            end = (
                indexed[groups[n + 1]["source_ids"][0]]["start"]
                if n + 1 < len(groups)
                else len(chapter["content"].encode("utf-16-le")) // 2
            )
            actor = group["character_id"] or None
            scene = SceneTime(
                **{k: v for k, v in group.items() if k not in {"source_ids", "character_id"}},
                source_revision=chapter["revision"],
                start=start,
                end=end,
                character_id=allocated.get(actor, actor),
            )
            result.append(
                {
                    "id": uid(),
                    "chapter_id": chapter["id"],
                    "scene": scene.model_dump(),
                    "quote": utf16_slice(chapter["content"], start, end)[1],
                    "source_ids": group["source_ids"],
                }
            )
    return result


def can_auto_replace(domain, payload, task):
    previous = payload.get("scene_replacements", [])
    if not previous:
        return True
    lineage = [task["id"]]
    ancestor = task["result"].get("continued_from")
    while ancestor and ancestor not in lineage:
        prior = domain.task(ancestor)
        if prior["branch_id"] != task["branch_id"]:
            return False
        lineage.append(ancestor)
        ancestor = prior["result"].get("continued_from")
    placeholders = ",".join("?" for _ in lineage)
    receipts = domain.db.all(
        f"SELECT r.result FROM receipts r JOIN artifacts a ON a.id=r.artifact_id WHERE a.branch_id=? AND a.task_id IN ({placeholders}) AND a.kind='story_extraction'",
        (task["branch_id"], *lineage),
    )
    owned = {}
    for r in receipts:
        owned.update(json.loads(r["result"]).get("scene_revisions", {}))
    return all(owned.get(s["id"]) == s["revision"] for s in previous)


def adopt_scenes(tx, branch_id, payload, chosen_ids, confirm_replace, required=False):
    indexed = {s["id"]: s for s in payload.get("scenes", [])}
    chosen = set(chosen_ids)
    if len(chosen) != len(chosen_ids) or not chosen.issubset(indexed):
        raise DomainError("scene_selection", "场景选择重复或不存在。", 409)
    if required and (not indexed or chosen != set(indexed)):
        raise DomainError("scene_selection", "批次场景维护需要核对并选择完整场景清单，不能跳过后推进。", 409)
    chapter_ids = {indexed[i]["chapter_id"] for i in chosen}
    replacements = [s for s in payload.get("scene_replacements", []) if s["chapter_id"] in chapter_ids]
    if replacements and not confirm_replace:
        raise DomainError("scene_replacement", "请明确确认归档旧场景绑定，旧范围和历史仍保留。", 409)
    for old in replacements:
        row = tx.execute(
            "SELECT * FROM scene_times WHERE id=? AND branch_id=?", (old["id"], branch_id)
        ).fetchone()
        if (
            not row
            or row["archived"]
            or row["revision"] != old["revision"]
            or json.loads(row["payload"]) != old["payload"]
        ):
            raise DomainError("scene_changed", "旧绑定已改变，请重新生成场景提案。", 409)
        revision = old["revision"] + 1
        tx.execute("UPDATE scene_times SET archived=1,revision=? WHERE id=?", (revision, old["id"]))
        tx.execute(
            "INSERT INTO scene_time_history VALUES(?,?,?,?,?)",
            (old["id"], revision, dumps(old["payload"]), 1, time()),
        )
    revisions = {}
    from .domain import utf16_slice
    from .story_graph import graph_snapshot

    effective = graph_snapshot(tx, branch_id)
    for identifier in chosen_ids:
        item = indexed[identifier]
        p = SceneTime.model_validate(item["scene"]).model_dump()
        chapter = tx.execute(
            "SELECT * FROM chapters WHERE id=? AND branch_id=? AND archived=0",
            (item["chapter_id"], branch_id),
        ).fetchone()
        if not chapter or chapter["revision"] != p["source_revision"] or chapter["stale"]:
            raise DomainError("revision_conflict", "场景依据的保存稿已改变或待复检。", 409)
        quote = utf16_slice(chapter["content"], p["start"], p["end"])[1]
        if "quote" in item and item["quote"] != quote:
            raise DomainError("scene_evidence", "场景原文快照与保存稿不一致，未采用。", 409)
        if (
            not p["title"].strip()
            or p["end"] <= p["start"]
            or (p["audience"] == "character" and not p["character_id"])
        ):
            raise DomainError("scene_range", "场景范围或视角不完整。", 409)
        if (
            p["character_id"]
            and not tx.execute(
                "SELECT id FROM story_records WHERE id=? AND branch_id=? AND kind='character' AND archived=0",
                (p["character_id"], branch_id),
            ).fetchone()
        ):
            raise DomainError("scene_viewpoint", "请同时选择场景所需的本批人物，不能使用其他版本身份。", 409)
        if p["story_order"] is not None:
            anchors = [
                r
                for r in effective["records"]
                if r["kind"] == "event"
                and not r["archived"]
                and not r["invalid_reasons"]
                and r["payload"]["status"] == "actual"
                and any(
                    s["chapter_id"] == chapter["id"]
                    and s["revision"] == p["source_revision"]
                    and max(s["start"], p["start"]) < min(s["end"], p["end"])
                    for s in r["payload"]["sources"]
                )
            ]
            orders = {r["payload"]["story_order"] for r in anchors} - {None}
            if orders != {p["story_order"]}:
                raise DomainError(
                    "scene_time_evidence",
                    "场景时点缺少唯一有效的实际事件依据，请核对所选事件，拆分场景或保留未知。",
                    409,
                )
            anchor = next(r for r in anchors if r["payload"]["story_order"] == p["story_order"])
            p["event_id"], p["event_revision"] = anchor["id"], anchor["revision"]
        for row in tx.execute(
            "SELECT payload FROM scene_times WHERE chapter_id=? AND archived=0", (chapter["id"],)
        ):
            other = json.loads(row["payload"])
            if other["source_revision"] == p["source_revision"] and max(other["start"], p["start"]) < min(
                other["end"], p["end"]
            ):
                raise DomainError("scene_overlap", "场景范围与现有绑定重叠，未修改任何记录。", 409)
        tx.execute(
            "INSERT INTO scene_times VALUES(?,?,?,?,?,?,?)",
            (identifier, branch_id, chapter["id"], 0, dumps(p), 0, time()),
        )
        tx.execute("INSERT INTO scene_time_history VALUES(?,?,?,?,?)", (identifier, 0, dumps(p), 0, time()))
        revisions[identifier] = 0
    return revisions, [s["id"] for s in replacements]


def coverage(tx, branch_id):
    from .story_graph import graph_snapshot

    indexed = {r["id"]: r for r in graph_snapshot(tx, branch_id)["records"]}
    issues, unknown, total = [], 0, 0
    chapter_count = 0
    identities = {
        r[0]
        for r in tx.execute(
            "SELECT id FROM story_records WHERE branch_id=? AND kind='character' AND archived=0", (branch_id,)
        )
    }
    for chapter in tx.execute(
        "SELECT id,position,revision,content FROM chapters WHERE branch_id=? AND archived=0 ORDER BY position",
        (branch_id,),
    ):
        chapter_count += 1
        rows = [
            json.loads(r[0])
            for r in tx.execute(
                "SELECT payload FROM scene_times WHERE chapter_id=? AND archived=0", (chapter["id"],)
            )
        ]
        cursor = 0
        valid = bool(rows)
        for p in sorted(rows, key=lambda p: (p["start"], p["end"])):
            valid = (
                valid
                and p["source_revision"] == chapter["revision"]
                and p["start"] == cursor
                and p["end"] > p["start"]
                and (not p["character_id"] or p["character_id"] in identities)
                and (p["audience"] != "character" or bool(p["character_id"]))
                and event_basis_valid(tx, p, chapter["id"], indexed)
            )
            cursor = p["end"]
            unknown += p["story_order"] is None
        total += len(rows)
        if not valid or cursor != len(chapter["content"].encode("utf-16-le")) // 2:
            issues.append(
                {
                    "chapter_id": chapter["id"],
                    "position": chapter["position"],
                    "reason": "场景缺失、来源失效、身份失效或范围不完整",
                }
            )
    if not chapter_count:
        issues.append({"chapter_id": None, "position": 0, "reason": "目录为空，尚无场景范围可核对"})
    return {"complete": not issues, "scenes": total, "unknown_story_times": unknown, "issues": issues}
