"""Explicitly selected setup and plans, isolated from another draft's actual history."""

from copy import deepcopy
from time import time

from .db import DomainError, dumps, uid
from .story_graph import graph_snapshot, references, validate_record
from .story_extraction import rewrite_references


def preview_snapshot(snapshot):
    items = []
    for row in snapshot["records"]:
        p = row["payload"]
        reason = ""
        if row["archived"]:
            reason = "已归档"
        elif row["invalid_reasons"]:
            reason = "来源或依赖已失效，需先核对"
        elif p["kind"] == "event" and p["status"] != "planned":
            reason = "已发生、未知或冲突事件不继承为新版本经历"
        elif p["kind"] in {"state", "relationship"} and p["truth"] != "planned":
            reason = "实际、未知或冲突状态不继承为新版本事实"
        elif p["kind"] == "promise" and any(s["status"] != "planned" for s in p["stages"]):
            reason = "已埋设、回收或裁定的历史承诺不继承"
        candidate = deepcopy(p)
        candidate["sources"] = []
        if p["kind"] == "promise":
            for stage in candidate["stages"]:
                stage["sources"] = []
        items.append(
            {
                "id": row["id"],
                "revision": row["revision"],
                "kind": row["kind"],
                "title": p["title"],
                "record": candidate,
                "eligible": not reason,
                "reason": reason,
                "required_ids": list(dict.fromkeys(references(candidate))),
            }
        )
    # A planned state may still depend on an actual event; do not silently convert it.
    indexed = {i["id"]: i for i in items}
    for _ in items:
        changed = False
        for item in items:
            if item["eligible"] and any(
                not indexed.get(ref, {}).get("eligible") for ref in item["required_ids"]
            ):
                item.update(eligible=False, reason="关联包含不可继承的历史或失效记录，请先另行整理计划")
                changed = True
        if not changed:
            break
    return {
        "source_branch_id": snapshot["branch_id"],
        "source_story_revision": snapshot["story_revision"],
        "items": items,
        "coverage": "只复制作者勾选的人物档案与计划，并建立本版独立身份；不复制正文、原文选区、实际状态或已发生经历。人物档案由作者确认为本版设定，不表示旧版经历发生。",
    }


def inherit(tx, source_branch_id, target_branch_id, novel_id, selected_ids, source_revision):
    if not selected_ids:
        return
    snapshot = graph_snapshot(tx, source_branch_id)
    if source_revision != snapshot["story_revision"]:
        raise DomainError("story_changed", "继承目录所依据的来源版本已改变，请重新读取并核对勾选。", 409)
    preview = preview_snapshot(snapshot)
    indexed = {i["id"]: i for i in preview["items"]}
    chosen = set(selected_ids)
    if len(chosen) != len(selected_ids) or not chosen.issubset(indexed):
        raise DomainError("story_inheritance", "继承选择重复或不属于所选来源版本。", 409)
    for identifier in selected_ids:
        item = indexed[identifier]
        if not item["eligible"]:
            raise DomainError("story_inheritance", item["title"] + "：" + item["reason"], 409)
        if not set(item["required_ids"]).issubset(chosen):
            raise DomainError(
                "story_dependencies", "请明确一并勾选所依赖的人物或计划；不自动扩大继承范围。", 409
            )
    mapping = {identifier: uid() for identifier in selected_ids}
    records = {mapping[i]: rewrite_references(indexed[i]["record"], mapping) for i in selected_ids}
    now = time()
    for identifier, payload in records.items():
        tx.execute(
            "INSERT INTO story_records VALUES(?,?,?,?,?,?,?,?)",
            (identifier, target_branch_id, payload["kind"], 0, dumps(payload), 0, now, now),
        )
    for identifier, payload in records.items():
        validate_record(tx, target_branch_id, identifier, payload)
        tx.execute(
            "INSERT INTO story_record_history VALUES(?,?,?,?,?)", (identifier, 0, dumps(payload), 0, now)
        )
        for parent in references(payload):
            tx.execute("INSERT INTO story_record_dependencies VALUES(?,?,?)", (identifier, parent, 0))
    manifest = {
        "source_branch_id": source_branch_id,
        "source_story_revision": source_revision,
        "records": [
            {
                "source_id": i,
                "source_revision": indexed[i]["revision"],
                "target_id": mapping[i],
                "kind": indexed[i]["kind"],
                "title": indexed[i]["title"],
            }
            for i in selected_ids
        ],
        "policy": "author_selected_setup_and_plans_only",
        "copied_manuscripts": False,
    }
    tx.execute(
        "INSERT INTO artifacts(id,novel_id,branch_id,kind,payload,basis,status,created) VALUES(?,?,?,?,?,?,'accepted',?)",
        (
            uid(),
            novel_id,
            target_branch_id,
            "structure_inheritance",
            dumps(manifest),
            dumps({"source_branch_id": source_branch_id, "source_story_revision": source_revision}),
            now,
        ),
    )
    tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (target_branch_id,))


def register_routes(app, domain):
    @app.get("/api/branches/{branch_id}/story-inheritance")
    def preview(branch_id: str):
        domain.branch(branch_id)
        with domain.db.connect() as tx:
            tx.execute("BEGIN")
            return preview_snapshot(graph_snapshot(tx, branch_id))
