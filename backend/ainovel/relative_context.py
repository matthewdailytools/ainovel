"""Read author-maintained event dependencies without inventing a total order."""

from collections import deque
from copy import deepcopy

from .db import DomainError


class EventOrder:
    def __init__(self, events):
        self.events = events
        self.parents = {
            identifier: tuple(dict.fromkeys(p for p in r["after"] if p in events))
            for identifier, r in events.items()
        }
        self.children = {identifier: [] for identifier in events}
        degree = {identifier: len(parents) for identifier, parents in self.parents.items()}
        for identifier, parents in self.parents.items():
            for parent in parents:
                self.children[parent].append(identifier)
        ready = deque(identifier for identifier, count in degree.items() if not count)
        valid = set()
        maximum, self.inconsistent = {}, set()
        while ready:
            current = ready.popleft()
            valid.add(current)
            inherited = max(
                (maximum[p] for p in self.parents[current] if maximum[p] is not None), default=None
            )
            own = events[current].get("story_order")
            if any(p in self.inconsistent for p in self.parents[current]) or (
                own is not None and inherited is not None and own <= inherited
            ):
                self.inconsistent.add(current)
            maximum[current] = max((value for value in (own, inherited) if value is not None), default=None)
            for child in self.children[current]:
                degree[child] -= 1
                if degree[child] == 0:
                    ready.append(child)
        self.cyclic = set(events) - valid
        self.invalid = self.cyclic | self.inconsistent
        self.cache = {}
        self.future_cache = {}

    def ancestors(self, identifier):
        if identifier in self.cyclic:
            raise DomainError("event_order_cycle", "事件先后依赖存在循环，请核对记录后再查询。", 409)
        if identifier in self.inconsistent:
            raise DomainError(
                "event_order_conflict",
                "已有整数故事顺序与相对依赖链矛盾，请核对记录；没有选择其中一种顺序或自动改号。",
                409,
            )
        if identifier in self.cache:
            return self.cache[identifier]
        active, done = set(), set()
        stack = [(identifier, False)]
        while stack:
            current, leaving = stack.pop()
            if leaving:
                active.remove(current)
                done.add(current)
            elif current not in done:
                if current in active:
                    raise DomainError("event_order_cycle", "事件先后依赖存在循环，请核对记录后再查询。", 409)
                active.add(current)
                stack.append((current, True))
                stack.extend((parent, False) for parent in reversed(self.parents.get(current, ())))
        self.cache[identifier] = done - {identifier}
        return self.cache[identifier]

    def path(self, earlier, later):
        """One shortest declared path, returned in story direction for review."""
        if earlier in self.invalid or later in self.invalid:
            return []
        queue, next_step = deque([later]), {later: None}
        while queue:
            current = queue.popleft()
            if current == earlier:
                result = [earlier]
                while next_step[result[-1]] is not None:
                    result.append(next_step[result[-1]])
                return result
            for parent in self.parents.get(current, ()):
                if parent not in next_step:
                    next_step[parent] = current
                    queue.append(parent)
        return []

    def relation(self, identifier, anchor, past):
        if identifier not in self.events or identifier in self.invalid:
            return "unknown"
        if identifier == anchor or identifier in past:
            return "past"
        if anchor not in self.future_cache:
            # Classifying every future event must not store the entire
            # transitive closure of a long book, one ancestor set per event.
            future, queue = set(), deque([anchor])
            while queue:
                for child in self.children.get(queue.popleft(), ()):
                    if child not in future and child not in self.invalid:
                        future.add(child)
                        queue.append(child)
            self.future_cache[anchor] = future
        return "future" if identifier in self.future_cache[anchor] else "unknown"


def at_event(baseline, snapshot, identifier):
    """A new explicit mode; the existing scalar query remains unchanged."""
    originals = {row["id"]: row for row in snapshot["records"]}

    def complete(row):
        # A partially disclosed record may contain later assertions in its free
        # fields. Do not expose it just because one older quotation is visible.
        return row["payload"]["sources"] == originals[row["id"]]["payload"]["sources"]

    events = {
        row["id"]: row["payload"]
        for row in baseline["events"]
        if complete(row) and row["payload"]["status"] == "actual" and row["payload"]["sources"]
    }
    if identifier not in events:
        raise DomainError(
            "event_anchor", "所选事件不是当前位置可见的有效实际事件，请核对版本、来源及原文位置。", 409
        )
    order = EventOrder(events)
    past = order.ancestors(identifier)
    known = past | {identifier}
    result = deepcopy(baseline)
    uncertain = {}

    def defer(row, reason):
        uncertain[row["id"]] = {**deepcopy(row), "time_reason": reason}

    states, assertions = [], []
    for row in baseline["uncertain"]:
        if not complete(row):
            continue
        p = row["payload"]
        relation = order.relation(p["event_id"], identifier, past)
        if p["truth"] != "actual" or relation == "unknown":
            defer(row, "状态性质或获得事件尚不能与查询事件比较，未当作当前事实。")
            continue
        if relation == "future":
            continue
        assertions.append(row)
        end = p["until_event_id"]
        if end:
            boundary = order.relation(end, identifier, past)
            if boundary == "past":
                defer(row, "已到失效事件；其后状态尚无可确认的替代断言，不恢复已被取代的旧值。")
                continue
            if boundary == "unknown":
                defer(row, "失效事件与查询事件先后不明，无法确认此时仍有效。")
                continue
        states.append(
            {
                **deepcopy(row),
                "time_basis": {
                    "start_path": order.path(p["event_id"], identifier),
                    "until_path": order.path(identifier, end) if end else [],
                },
            }
        )

    def slot(row):
        p = row["payload"]
        return p["character_id"], p["category"], p["key"], p["audience"]

    unplaced = [
        row
        for row in uncertain.values()
        if row["payload"]["truth"] == "actual" and row["payload"]["event_id"] in events
    ]
    eligible = {row["id"]: row for row in states}
    groups = {}
    for row in assertions:
        groups.setdefault(slot(row), []).append(row)
    current, conflicts = [], []
    for key, group in groups.items():
        latest = [
            row
            for row in group
            if not any(
                row["payload"]["event_id"] in order.ancestors(other["payload"]["event_id"]) for other in group
            )
        ]
        latest_ids = {row["id"] for row in latest}
        for row in group:
            if row["id"] not in latest_ids:
                uncertain.pop(row["id"], None)
        # A later assertion remains a temporal boundary after it expires. An
        # earlier absolute value cannot silently become current again.
        if any(row["id"] not in eligible for row in latest):
            for row in latest:
                if row["id"] not in uncertain:
                    defer(
                        eligible.get(row["id"], row),
                        "同一事项还有不能确认仍有效的并列或较晚断言，当前值待判断。",
                    )
            continue
        latest = [eligible[row["id"]] for row in latest]
        if len({row["payload"]["value"] for row in latest}) > 1:
            conflicts.append(latest)
            for row in latest:
                defer(row, "同一状态事项存在不同断言，且没有已证实的更晚断言可唯一取代；未按保存时间选值。")
        else:
            ambiguous = False
            for other in unplaced:
                if slot(other) != key or all(
                    other["payload"]["value"] == row["payload"]["value"] for row in latest
                ):
                    continue
                try:
                    superseded = any(
                        other["payload"]["event_id"] in order.ancestors(row["payload"]["event_id"])
                        for row in latest
                    )
                except DomainError:
                    superseded = False
                if not superseded:
                    ambiguous = True
            if ambiguous:
                for row in latest:
                    defer(
                        row,
                        "同一事项还有无法定位到查询事件前后的实际变化，当前值待判断；没有把旧值当作确定现状。",
                    )
            else:
                current.extend(latest)

    result["events"] = []
    for row in baseline["events"]:
        if not complete(row):
            continue
        relation = order.relation(row["id"], identifier, past)
        if relation == "past":
            result["events"].append(row)
        elif relation == "unknown" and row["payload"]["status"] != "planned":
            reason = (
                "已有整数顺序与相对依赖链矛盾，相关时点待核对；未选择其中一种来放行。"
                if row["id"] in order.inconsistent
                else "事件与查询事件的先后没有有效实际依赖可证明；未按章节或叙述位置排序。"
            )
            defer(row, reason)

    result["relationships"] = []
    for row in baseline["relationships"]:
        if not complete(row):
            continue
        p = row["payload"]
        relation = order.relation(p["event_id"], identifier, past)
        if p["truth"] == "actual" and relation == "past":
            result["relationships"].append(row)
        elif p["truth"] != "planned" and relation != "future":
            defer(row, "关系缺少可与查询事件比较的实际发生依据，未当作该时点事实。")

    result["promises"] = []
    for row in baseline["promises"]:
        if not complete(row):
            continue
        stages = row["payload"]["stages"]
        proved, pending = [], []
        for stage in stages:
            relation = order.relation(stage["event_id"], identifier, past)
            if relation == "past":
                proved.append(stage)
            elif relation == "unknown" and stage["status"] != "planned":
                pending.append(stage)
        if proved:
            result["promises"].append({**row, "payload": {**row["payload"], "stages": proved}})
        if pending:
            defer(
                {**row, "payload": {**row["payload"], "stages": pending}},
                "这些承诺阶段缺少可比较的事件时点，未据此认定当时已回收或放弃。",
            )

    for key in ("characters", "plans"):
        result[key] = [row for row in baseline[key] if complete(row)]
    result.update(
        states=current,
        uncertain=list(uncertain.values()),
        conflicts=conflicts,
        source_story_revision=snapshot["story_revision"],
        anchor={
            "kind": "event_after/1",
            "event_id": identifier,
            "title": events[identifier]["title"],
            "past_event_ids": sorted(known),
            "unusable_predecessors": sum(
                parent not in events for event in known for parent in events[event]["after"]
            ),
            "source_scope_excluded": len(
                {
                    row["id"]
                    for key in ("events", "characters", "plans", "uncertain", "relationships", "promises")
                    for row in baseline[key]
                    if not complete(row)
                }
            ),
        },
        coverage="仅按当前位置可见、有效且已发生事件的显式after依赖查询；所选事件自身包含在内，不由章节、保存时间或未知整数编号推断先后。并行、缺证、失效边界及冲突保持待判断；原记录不改。",
    )
    return result
