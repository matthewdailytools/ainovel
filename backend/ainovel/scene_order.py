"""Versioned, explicit event references for generated scene time decisions."""

from copy import deepcopy
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field

from .db import DomainError
from .story_graph import Strict, references, metadata_revision_compatible, metadata_extension, sources

LEGACY_VERSION = "scene-event-order/1"
VERSION = "scene-event-refs/2"


def execution_version(task):
    value = task.get("result", {}).get("scene_contract_version", LEGACY_VERSION)
    if value not in {LEGACY_VERSION, VERSION}:
        raise DomainError("scene_version", "场景契约版本无法确认，未升级或派发旧任务。", 409)
    return value


class EventOrder(Strict):
    kind: Literal["event_refs"]
    refs: list[str] = Field(min_length=1, max_length=60)


class UnknownOrder(Strict):
    kind: Literal["unknown"]
    reason: str = Field(min_length=1, max_length=2000)
    refs: list[str] = Field(default_factory=list, max_length=60)


class LinkedSceneProposal(Strict):
    title: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=2000)
    order_basis: Annotated[EventOrder | UnknownOrder, Field(discriminator="kind")]
    story_time: str = Field(default="未知", max_length=200)
    audience: Literal["author", "character", "reader"] = "author"
    character_id: str | None = None
    note: str = Field(default="", max_length=2000)


class EventWitness(Strict):
    ref: str = Field(min_length=1, max_length=100)
    event_id: str = Field(min_length=1)
    event_revision: int | None = Field(default=None, ge=0)
    story_order: int | None = None
    record_sha256: str = Field(min_length=64, max_length=64)


class SceneOrderAudit(Strict):
    version: Literal["scene-event-refs/2"]
    kind: Literal["event_refs", "unknown"]
    reason: str = Field(default="", max_length=2000)
    refs: list[EventWitness] = Field(default_factory=list, max_length=60)
    covered_events: list[EventWitness] = Field(
        default_factory=list,
        description="候选生成时覆盖事件的冻结审计快照，可能未被选择采用；不是当前正式事件依赖。正式依赖仅refs。",
    )


def payload_sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def effective_records(known, proposals, domain=None, branch_id=None):
    """Apply exactly the adoption merge, including explicit source replacement."""
    nodes = {
        identifier: {
            "id": identifier,
            "revision": row["revision"],
            "record": deepcopy(row["payload"]),
            "archived": row.get("archived", 0),
            "invalid": list(row.get("invalid_reasons", [])),
        }
        for identifier, row in known.items()
    }
    for item in proposals:
        previous = known.get(item.get("target_id"))
        record = deepcopy(previous["payload"] if previous else item["record"])
        merge_issues = []
        if previous:
            if previous["kind"] != item["record"]["kind"]:
                merge_issues.append("更新目标类型不符")
            for field in item["update_fields"]:
                if field == "kind" or field not in record or field not in item["record"]:
                    merge_issues.append("更新字段不属于同类型目标")
                    continue
                record[field] = (
                    deepcopy(previous["payload"]["stages"]) + deepcopy(item["record"]["stages"])
                    if field == "stages" and item.get("append_stages")
                    else deepcopy(item["record"][field])
                )
            if "sources" not in item["update_fields"]:
                unique = {
                    (s["chapter_id"], s["revision"], s["start"], s["end"], s["quote"]): s
                    for s in [*record["sources"], *item["record"]["sources"]]
                }
                record["sources"] = list(unique.values())
        identifier = item.get("target_id") or item["local_id"]
        node = {
            "id": identifier,
            "revision": previous["revision"] if previous else None,
            "record": record,
            "archived": 0,
            "invalid": list(merge_issues),
            "merge_issues": merge_issues,
        }
        nodes[identifier] = nodes[item["local_id"]] = node
    if domain is not None:
        from .domain import utf16_slice

        chapters = {}
        for node in {id(n): n for n in nodes.values()}.values():
            node["invalid"] = list(node.get("merge_issues", []))
            for source in sources(node["record"]):
                key = source["chapter_id"]
                try:
                    if key not in chapters:
                        chapters[key] = domain.chapter(key, branch_id)
                    chapter = chapters[key]
                    if (
                        chapter["revision"] != source["revision"]
                        or chapter["stale"]
                        or utf16_slice(chapter["content"], source["start"], source["end"])[1]
                        != source["quote"]
                    ):
                        node["invalid"].append("来源正文已变更")
                except DomainError:
                    node["invalid"].append("来源无法回到当前版本")
        updated = {item.get("target_id") for item in proposals if item.get("target_id")}
        # Unchanged records retain their historical dependency receipts. An update
        # proposal will instead rebind its dependencies in the adoption transaction.
        with domain.db.connect() as tx:
            for dep in tx.execute(
                "SELECT d.* FROM story_record_dependencies d JOIN story_records r ON r.id=d.record_id WHERE r.branch_id=?",
                (branch_id,),
            ):
                node, parent = nodes.get(dep["record_id"]), nodes.get(dep["parent_id"])
                before = known.get(dep["parent_id"])
                if (
                    node
                    and node["id"] not in updated
                    and (
                        not parent
                        or not metadata_revision_compatible(tx, before, dep["parent_revision"])
                        or (
                            parent["id"] in updated
                            and not metadata_extension(before["payload"], parent["record"])
                        )
                    )
                ):
                    node["invalid"].append("关联记录版本已改变，需重新确认")
        for _ in nodes:
            changed = False
            for node in nodes.values():
                if not node["invalid"] and any(
                    ref not in nodes or nodes[ref]["archived"] or nodes[ref]["invalid"]
                    for ref in references(node["record"])
                ):
                    node["invalid"].append("依赖失效")
                    changed = True
            if not changed:
                break
    return nodes


def overlaps(record, chapter_id, revision, ranges):
    return any(
        s["chapter_id"] == chapter_id
        and s["revision"] == revision
        and any(max(s["start"], start) < min(s["end"], end) for start, end in ranges)
        for s in record["sources"]
    )


def resolve_basis(scene, selected, nodes):
    """Never choose an order or convert a failed event decision to unknown."""
    basis = scene["order_basis"]
    issues, resolved = [], []
    if len(basis["refs"]) != len(set(basis["refs"])):
        issues.append("refs不得重复。")
    ranges = [(u["start"], u["end"]) for u in selected]
    chapter, revision = selected[0]["chapter_id"], selected[0]["revision"]
    for ref in basis["refs"]:
        node = nodes.get(ref)
        if not node or node["record"]["kind"] != "event" or node["archived"] or node["invalid"]:
            issues.append(f"refs[{ref}]不是当前有效的事件。")
        elif node["record"]["status"] != "actual" or not overlaps(node["record"], chapter, revision, ranges):
            issues.append(f"refs[{ref}]须为本场景原文支持的实际事件，不得借计划或其它章节。")
        else:
            resolved.append((ref, node))
    if len({node["id"] for _, node in resolved}) != len(resolved):
        issues.append("refs不得以不同别名重复指向同一事件。")
    covered = {
        node["id"]: node
        for node in nodes.values()
        if node["record"]["kind"] == "event"
        and node["record"]["status"] == "actual"
        and not node["archived"]
        and not node["invalid"]
        and overlaps(node["record"], chapter, revision, ranges)
    }
    order = None
    if basis["kind"] == "unknown":
        if not basis["reason"].strip():
            issues.append("unknown须明确非空理由，不能自动清空时点以通过。")
    else:
        orders = {node["record"]["story_order"] for _, node in resolved}
        coverage_orders = {node["record"]["story_order"] for node in covered.values()} - {None}
        if not resolved or None in orders or len(orders) != 1 or orders != coverage_orders:
            issues.append(
                "event_refs与全部覆盖实际事件须精确对应唯一已知order；多顺序须有依据拆分或明确unknown及理由。"
            )
        elif not issues:
            order = next(iter(orders))
    return issues, order, resolved, list(covered.values())


def linked_scene_issues(scenes, units, known, proposals, domain=None, branch_id=None):
    # Reuse legacy coverage/viewpoint checks without supplying any guessed scalar.
    from .extraction_scenes import scene_issues

    issues = scene_issues(
        [{**{k: v for k, v in s.items() if k != "order_basis"}, "story_order": None} for s in scenes],
        units,
        known,
        proposals,
    )
    nodes = effective_records(known, proposals, domain, branch_id)
    indexed = {u["source_id"]: u for u in units}
    for index, scene in enumerate(scenes):
        selected = [indexed.get(ref) for ref in scene["source_ids"]]
        if not selected or any(s is None for s in selected) or len({s["chapter_id"] for s in selected}) != 1:
            continue
        errors, _, _, _ = resolve_basis(scene, selected, nodes)
        issues.extend(f"scenes[{index}].order_basis: {message}" for message in errors)
    return issues


def make_audit(scene, selected, nodes, allocated):
    errors, order, refs, covered = resolve_basis(scene, selected, nodes)
    if errors:
        raise DomainError("scene_order", "；".join(errors), 409)

    def witness(ref, node):
        from .story_extraction import rewrite_references

        return {
            "ref": ref,
            "event_id": allocated.get(node["id"], node["id"]),
            "event_revision": node["revision"],
            "story_order": node["record"]["story_order"],
            "record_sha256": payload_sha(rewrite_references(node["record"], allocated)),
        }

    audit = {
        "version": VERSION,
        "kind": scene["order_basis"]["kind"],
        "reason": scene["order_basis"].get("reason", ""),
        "refs": [witness(ref, node) for ref, node in refs],
        "covered_events": [witness(node["id"], node) for node in covered],
    }
    return order, SceneOrderAudit.model_validate(audit).model_dump()


def audit_valid(tx, payload, chapter_id, indexed, *, adopting=False):
    audit = payload.get("order_basis")
    if audit is None:
        return True
    try:
        audit = SceneOrderAudit.model_validate(audit).model_dump()
    except ValueError, TypeError:
        return False
    if audit["kind"] == "unknown" and (payload["story_order"] is not None or not audit["reason"].strip()):
        return False
    if audit["kind"] == "event_refs" and (not audit["refs"] or payload["story_order"] is None):
        return False
    ids = [r["event_id"] for r in audit["refs"]]
    if len(set(ids)) != len(ids):
        return False
    for ref in audit["refs"]:
        event = indexed.get(ref["event_id"])
        if (
            not event
            or event["archived"]
            or event["invalid_reasons"]
            or event["kind"] != "event"
            or event["payload"]["status"] != "actual"
            or not overlaps(
                event["payload"], chapter_id, payload["source_revision"], [(payload["start"], payload["end"])]
            )
            or (
                not adopting
                and (
                    ref["event_revision"] is None
                    or not metadata_revision_compatible(tx, event, ref["event_revision"])
                )
            )
            or (adopting and payload_sha(event["payload"]) != ref["record_sha256"])
        ):
            return False
        if audit["kind"] == "event_refs" and event["payload"]["story_order"] != payload["story_order"]:
            return False
    if audit["kind"] == "event_refs":
        orders = {
            event["payload"]["story_order"]
            for event in indexed.values()
            if event["kind"] == "event"
            and not event["archived"]
            and not event["invalid_reasons"]
            and event["payload"]["status"] == "actual"
            and overlaps(
                event["payload"], chapter_id, payload["source_revision"], [(payload["start"], payload["end"])]
            )
        } - {None}
        if orders != {payload["story_order"]}:
            return False
    return True


def adopted_audit(tx, payload, chapter_id, indexed):
    if not audit_valid(tx, payload, chapter_id, indexed, adopting=True):
        raise DomainError("scene_time_evidence", "场景显式事件依据、选择或有效顺序已改变，未采用。", 409)
    audit = deepcopy(payload["order_basis"])
    for ref in audit["refs"]:
        ref["event_revision"] = indexed[ref["event_id"]]["revision"]
    return audit
