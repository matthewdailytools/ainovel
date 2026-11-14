from copy import deepcopy

import pytest

from ainovel.db import DomainError
from ainovel.evidence import source_units
from ainovel.relative_context import EventOrder, at_event
from ainovel.story_graph import graph, query_context


@pytest.fixture
def relative_story(client, domain, story):
    text = "😀宁禾领取铜牌，身上有两枚铜钱。\n她带着铜牌来到渡口。\n她付掉一枚铜钱。\n付钱后才听到暗号。\n同时，北岸掌柜检修灯罩。"
    chapter = domain.save(story["chapter_id"], 0, text)
    late = domain.create_chapter(story["branch_id"], "后文披露")
    late = domain.save(late["id"], 0, "后文才披露的秘密。")
    sources = [
        {
            "chapter_id": chapter["id"],
            "revision": chapter["revision"],
            "start": u["start"],
            "end": u["end"],
            "quote": u["text"],
        }
        for u in source_units(text, "T")
    ]
    late_source = {
        "chapter_id": late["id"],
        "revision": late["revision"],
        "start": 0,
        "end": len(late["content"]),
        "quote": late["content"],
    }

    def save(record, expected=201):
        before = graph(domain, story["branch_id"])
        response = client.post(
            f"/api/branches/{story['branch_id']}/story-records",
            json={"base_story_revision": before["story_revision"], "record": record},
        )
        assert response.status_code == expected, response.text
        if expected != 201:
            assert graph(domain, story["branch_id"]) == before
            return response.json()
        old = {r["id"] for r in before["records"]}
        return next(r for r in response.json()["records"] if r["id"] not in old)

    person = save({"kind": "character", "title": "宁禾", "sources": [sources[0]]})

    def event(name, source, after=(), status="actual"):
        return save(
            {
                "kind": "event",
                "title": name,
                "status": status,
                "story_order": None,
                "participants": [person["id"]],
                "after": list(after),
                "sources": [source],
            }
        )

    first = event("领取铜牌", sources[0])
    arrival = event("抵达渡口", sources[1], [first["id"]])
    paid = event("付款", sources[2], [arrival["id"]])
    heard = event("听到暗号", sources[3], [paid["id"]])
    parallel = event("北岸检灯", sources[4])

    def state(event_id, value, key="铜钱", audience="character", until=None, evidence=None):
        return save(
            {
                "kind": "state",
                "title": key + value,
                "character_id": person["id"],
                "event_id": event_id,
                "category": "resource",
                "key": key,
                "value": value,
                "audience": audience,
                "truth": "actual",
                "until_event_id": until,
                "sources": evidence or [sources[0]],
            }
        )

    def query(anchor, **kwargs):
        return query_context(domain, story["branch_id"], audience="character", at_event_id=anchor, **kwargs)

    return {
        "save": save,
        "event": event,
        "state": state,
        "query": query,
        "person": person,
        "first": first,
        "arrival": arrival,
        "paid": paid,
        "heard": heard,
        "parallel": parallel,
        "sources": sources,
        "late_source": late_source,
        "chapter": chapter,
    }


def ids(value, key):
    return {row["id"] for row in value[key]}


def test_author_can_query_relative_states_without_filling_in_invented_numbers(relative_story, domain, story):
    r = relative_story
    old = r["state"](r["first"]["id"], "两枚")
    new = r["state"](r["paid"]["id"], "一枚", evidence=[r["sources"][2]])
    knowledge = r["state"](r["heard"]["id"], "已知", "暗号", evidence=[r["sources"][3]])
    before = graph(domain, story["branch_id"])
    arrived = r["query"](r["arrival"]["id"])
    assert ids(arrived, "states") == {old["id"]}
    assert knowledge["id"] not in ids(arrived, "uncertain")
    paid = r["query"](r["paid"]["id"])
    assert ids(paid, "states") == {new["id"]}
    assert paid["states"][0]["time_basis"]["start_path"] == [r["paid"]["id"]]
    assert arrived["states"][0]["time_basis"]["start_path"] == [r["first"]["id"], r["arrival"]["id"]]
    assert ids(r["query"](r["heard"]["id"]), "states") == {new["id"], knowledge["id"]}
    assert (
        query_context(domain, story["branch_id"], audience="reader", at_event_id=r["heard"]["id"])["states"]
        == []
    )
    assert query_context(domain, story["branch_id"], story_order=20, audience="character")["states"] == []
    assert graph(domain, story["branch_id"]) == before
    assert all(row["payload"]["story_order"] is None for row in before["records"] if row["kind"] == "event")


def test_relative_expiry_is_editable_and_end_event_itself_is_expired(relative_story):
    r = relative_story
    state = r["state"](r["first"]["id"], "持有", "铜牌", until=r["paid"]["id"])
    value = r["query"](r["arrival"]["id"])
    assert state["id"] in ids(value, "states")
    assert value["states"][0]["time_basis"]["until_path"] == [r["arrival"]["id"], r["paid"]["id"]]
    assert state["id"] not in ids(r["query"](r["paid"]["id"]), "states")
    record = {**state["payload"], "title": "无依据的失效边界", "until_event_id": r["parallel"]["id"]}
    assert r["save"](record, 400)["code"] == "story_order"


def test_unordered_expiry_remains_uncertain_even_if_both_events_happened(relative_story):
    r = relative_story
    state = r["state"](r["first"]["id"], "持有", "铜牌", until=r["paid"]["id"])
    between = r["event"]("另一支线已见铜牌", r["sources"][4], [r["first"]["id"]])
    value = r["query"](between["id"])
    assert state["id"] not in ids(value, "states")
    assert state["id"] in ids(value, "uncertain")
    assert "失效事件" in next(row for row in value["uncertain"] if row["id"] == state["id"])["time_reason"]


@pytest.mark.parametrize("same_event", [True, False])
def test_competing_assertions_never_use_last_save_wins(relative_story, same_event):
    r = relative_story
    first = r["state"](r["paid"]["id"], "一枚", evidence=[r["sources"][2]])
    other_event = r["paid"] if same_event else r["parallel"]
    second = r["state"](other_event["id"], "三枚", evidence=[r["sources"][4]])
    anchor = r["event"]("两线会合", r["sources"][4], [r["paid"]["id"], r["parallel"]["id"]])
    value = r["query"](anchor["id"])
    assert value["states"] == [] and len(value["conflicts"]) == 1
    assert {first["id"], second["id"]}.issubset(ids(value, "uncertain"))


def test_parallel_same_value_is_not_arbitrarily_discarded(relative_story):
    r = relative_story
    a = r["state"](r["paid"]["id"], "一枚")
    b = r["state"](r["parallel"]["id"], "一枚")
    anchor = r["event"]("两线会合", r["sources"][4], [r["paid"]["id"], r["parallel"]["id"]])
    value = r["query"](anchor["id"])
    assert ids(value, "states") == {a["id"], b["id"]} and not value["conflicts"]


def test_actual_unordered_change_makes_the_current_slot_uncertain(relative_story):
    r = relative_story
    old = r["state"](r["first"]["id"], "两枚")
    unknown = r["state"](r["parallel"]["id"], "三枚", evidence=[r["sources"][4]])
    value = r["query"](r["arrival"]["id"])
    assert value["states"] == []
    assert {old["id"], unknown["id"]}.issubset(ids(value, "uncertain"))


def test_older_uncertain_expiry_does_not_reactivate_a_superseded_value(relative_story):
    r = relative_story
    end = r["event"]("另一支线失效", r["sources"][4], [r["first"]["id"]])
    r["state"](r["first"]["id"], "两枚", until=end["id"])
    latest = r["state"](r["paid"]["id"], "一枚", evidence=[r["sources"][2]])
    value = r["query"](r["heard"]["id"])
    assert ids(value, "states") == {latest["id"]}


def test_an_ended_later_assertion_does_not_resurrect_an_older_value(relative_story):
    r = relative_story
    old = r["state"](r["first"]["id"], "两枚")
    ended = r["state"](r["paid"]["id"], "一枚", until=r["heard"]["id"], evidence=[r["sources"][2]])
    value = r["query"](r["heard"]["id"])
    assert value["states"] == []
    assert old["id"] not in ids(value, "uncertain")
    assert ended["id"] in ids(value, "uncertain")


def test_a_planned_change_does_not_replace_an_actual_state(relative_story):
    r = relative_story
    old = r["state"](r["first"]["id"], "两枚")
    planned = r["event"]("计划将来补发", r["sources"][4], [r["arrival"]["id"]], status="planned")
    proposal = r["save"](
        {
            **old["payload"],
            "title": "计划三枚",
            "value": "三枚",
            "truth": "planned",
            "event_id": planned["id"],
        }
    )
    value = r["query"](r["arrival"]["id"])
    assert ids(value, "states") == {old["id"]}
    assert proposal["id"] in ids(value, "plans") | ids(value, "uncertain")


def test_relationships_do_not_cross_an_unproved_or_future_event(relative_story):
    r = relative_story
    other = r["save"]({"kind": "character", "title": "摆渡人", "sources": [r["sources"][0]]})

    def relation(title, event):
        return r["save"](
            {
                "kind": "relationship",
                "title": title,
                "from_id": r["person"]["id"],
                "to_id": other["id"],
                "relation": title,
                "truth": "actual",
                "event_id": event,
                "sources": [r["sources"][0]],
            }
        )

    before = relation("早先约定", r["first"]["id"])
    future = relation("更晚约定", r["heard"]["id"])
    unknown = relation("无时点约定", None)
    value = r["query"](r["arrival"]["id"])
    assert ids(value, "relationships") == {before["id"]}
    assert future["id"] not in ids(value, "uncertain")
    assert unknown["id"] in ids(value, "uncertain")


def test_promises_show_only_proved_stages_and_retain_unanchored_uncertainty(relative_story):
    r = relative_story
    promise = r["save"](
        {
            "kind": "promise",
            "title": "铜钱线索",
            "sources": [r["sources"][0]],
            "stages": [
                {"status": "planted", "event_id": r["first"]["id"], "sources": [r["sources"][0]]},
                {"status": "resolved", "event_id": r["heard"]["id"], "sources": [r["sources"][3]]},
            ],
        }
    )
    unknown = r["save"](
        {
            "kind": "promise",
            "title": "未定时点的回收",
            "sources": [r["sources"][0]],
            "stages": [{"status": "resolved", "sources": [r["sources"][0]]}],
        }
    )
    value = r["query"](r["arrival"]["id"])
    row = next(row for row in value["promises"] if row["id"] == promise["id"])
    assert [stage["status"] for stage in row["payload"]["stages"]] == ["planted"]
    assert unknown["id"] not in ids(value, "promises") and unknown["id"] in ids(value, "uncertain")
    later = r["query"](r["heard"]["id"])
    row = next(row for row in later["promises"] if row["id"] == promise["id"])
    assert [stage["status"] for stage in row["payload"]["stages"]] == ["planted", "resolved"]


def test_later_narration_cannot_supply_an_invisible_dependency_bridge(relative_story):
    r = relative_story
    state = r["state"](r["first"]["id"], "两枚")
    bridge = r["event"]("后文披露的中间事件", r["late_source"], [r["first"]["id"]])
    anchor = r["event"]("先叙结果", r["sources"][1], [bridge["id"]])
    value = r["query"](
        anchor["id"],
        chapter_id=r["chapter"]["id"],
        text_position=len(r["chapter"]["content"].encode("utf-16-le")) // 2,
    )
    assert state["id"] not in ids(value, "states") and state["id"] in ids(value, "uncertain")
    assert bridge["id"] not in ids(value, "events") | ids(value, "uncertain")
    assert value["anchor"]["unusable_predecessors"] == 1


def test_partially_disclosed_free_fields_are_not_leaked(relative_story):
    r = relative_story
    state = r["state"](r["first"]["id"], "后文才披露的秘密", evidence=[r["sources"][0], r["late_source"]])
    value = r["query"](
        r["arrival"]["id"],
        chapter_id=r["chapter"]["id"],
        text_position=len(r["chapter"]["content"].encode("utf-16-le")) // 2,
    )
    assert state["id"] not in ids(value, "states") | ids(value, "uncertain")
    assert "后文才披露的秘密" not in str(value)
    assert value["anchor"]["source_scope_excluded"] == 1


@pytest.mark.parametrize(
    "case", ["planned", "conflict", "character", "archived", "foreign", "missing", "invisible", "stale"]
)
def test_invalid_anchors_do_not_fall_back_to_latest_state(relative_story, client, domain, story, case):
    r = relative_story
    anchor = r["arrival"]["id"]
    params = {"at_event_id": anchor}
    if case in {"planned", "conflict"}:
        params["at_event_id"] = r["event"]("尚未确认的事件", r["sources"][1], status=case)["id"]
    elif case == "character":
        params["at_event_id"] = r["person"]["id"]
    elif case == "archived":
        response = client.put(
            f"/api/branches/{story['branch_id']}/story-records/{anchor}",
            json={
                "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
                "base_revision": r["arrival"]["revision"],
                "record": r["arrival"]["payload"],
                "archived": True,
            },
        )
        assert response.status_code == 200
    elif case == "foreign":
        other = domain.create_novel("其他版本", "")
        response = client.get(f"/api/branches/{other['branch_id']}/story-context", params=params)
        assert response.status_code == 409
        return
    elif case == "missing":
        params["at_event_id"] = "missing"
    elif case == "invisible":
        params.update(chapter_id=r["chapter"]["id"], text_position=0)
    else:
        domain.save(r["chapter"]["id"], r["chapter"]["revision"], "原文改变")
    response = client.get(f"/api/branches/{story['branch_id']}/story-context", params=params)
    assert response.status_code == 409 and response.json()["code"] == "event_anchor"


def test_query_version_collision_and_utf16_boundaries_are_explicit(relative_story, client, domain, story):
    r = relative_story
    route = f"/api/branches/{story['branch_id']}/story-context"
    current = domain.branch(story["branch_id"])["story_revision"]
    response = client.get(route, params={"at_event_id": r["arrival"]["id"], "base_story_revision": current})
    assert response.status_code == 200 and response.json()["source_story_revision"] == current
    assert (
        client.get(route, params={"story_order": 10, "base_story_revision": current - 1}).status_code == 409
    )
    assert "source_story_revision" not in client.get(route, params={"story_order": 10}).json()
    assert client.get(route, params={"at_event_id": r["arrival"]["id"], "story_order": 10}).status_code == 400
    for offset in (1, 999999):
        assert (
            client.get(
                route,
                params={
                    "at_event_id": r["arrival"]["id"],
                    "chapter_id": r["chapter"]["id"],
                    "text_position": offset,
                },
            ).status_code
            == 400
        )
    assert (
        client.get(route, params={"at_event_id": r["arrival"]["id"], "text_position": 2}).status_code == 400
    )


def test_reader_position_is_bound_to_chapter_revision(relative_story, client, domain, story):
    r = relative_story
    route = f"/api/branches/{story['branch_id']}/story-context"
    params = {
        "at_event_id": r["arrival"]["id"],
        "chapter_id": r["chapter"]["id"],
        "chapter_revision": 1,
        "text_position": len(r["chapter"]["content"].encode("utf-16-le")) // 2,
    }
    assert client.get(route, params=params).status_code == 200
    assert client.get(route, params={"chapter_revision": 1}).status_code == 400
    for position in (1, 999999):
        assert (
            client.get(
                route,
                params={
                    "chapter_id": r["chapter"]["id"],
                    "chapter_revision": 1,
                    "text_position": position,
                    "story_order": 10,
                },
            ).status_code
            == 400
        )
    domain.save(r["chapter"]["id"], 1, r["chapter"]["content"] + "\n正文已改。")
    response = client.get(route, params=params)
    assert response.status_code == 409 and response.json()["code"] == "revision_conflict"


def test_legacy_cycle_is_rejected_without_repairing_the_snapshot(relative_story, domain, story):
    r = relative_story
    snapshot = graph(domain, story["branch_id"])
    for row in snapshot["records"]:
        if row["id"] == r["first"]["id"]:
            row["payload"]["after"] = [r["arrival"]["id"]]
    original = deepcopy(snapshot)
    baseline = query_context(domain, story["branch_id"], audience="character", snapshot=snapshot)
    with pytest.raises(DomainError, match="循环"):
        at_event(baseline, snapshot, r["arrival"]["id"])
    assert snapshot == original


def test_long_relative_chain_does_not_use_python_recursion():
    rows = {str(i): {"after": [str(i - 1)] if i else []} for i in range(1800)}
    order = EventOrder(rows)
    assert len(order.ancestors("1799")) == 1799
    assert order.path("0", "1799") == [str(i) for i in range(1800)]


def test_a_cyclic_future_component_does_not_prove_a_future_boundary():
    order = EventOrder(
        {"a": {"after": []}, "b": {"after": ["a", "c"]}, "c": {"after": ["b"]}, "d": {"after": ["b"]}}
    )
    assert order.ancestors("a") == set()
    assert order.relation("b", "a", set()) == "unknown"
    assert order.relation("d", "a", set()) == "unknown"
    assert order.path("a", "d") == []


def test_known_number_conflict_through_an_unnumbered_event_is_not_ignored():
    order = EventOrder(
        {
            "a": {"after": [], "story_order": 20},
            "b": {"after": ["a"], "story_order": None},
            "c": {"after": ["b"], "story_order": 10},
            "d": {"after": ["c"], "story_order": None},
        }
    )
    assert order.ancestors("a") == set()
    assert order.relation("c", "a", set()) == "unknown"
    with pytest.raises(DomainError, match="整数故事顺序"):
        order.ancestors("d")
    assert order.path("a", "d") == []


def test_conflicting_legacy_numbers_reject_anchor_without_rewriting_records(relative_story, domain, story):
    r = relative_story
    snapshot = graph(domain, story["branch_id"])
    for row in snapshot["records"]:
        if row["id"] == r["first"]["id"]:
            row["payload"]["story_order"] = 20
        if row["id"] == r["paid"]["id"]:
            row["payload"]["story_order"] = 10
    original = deepcopy(snapshot)
    baseline = query_context(domain, story["branch_id"], audience="character", snapshot=snapshot)
    with pytest.raises(DomainError, match="整数故事顺序"):
        at_event(baseline, snapshot, r["paid"]["id"])
    value = at_event(baseline, snapshot, r["first"]["id"])
    assert r["paid"]["id"] in ids(value, "uncertain")
    assert snapshot == original


def test_full_relative_query_spans_1800_events_and_preserves_snapshots():
    source = {"chapter_id": "c", "revision": 1, "start": 0, "end": 1, "quote": "原"}
    events = [
        {
            "id": str(i),
            "kind": "event",
            "archived": 0,
            "revision": 0,
            "invalid_reasons": [],
            "payload": {
                "kind": "event",
                "title": str(i),
                "after": [str(i - 1)] if i else [],
                "sources": [source],
                "status": "actual",
                "story_order": None,
            },
        }
        for i in range(1800)
    ]
    baseline = {
        "events": events,
        "characters": [],
        "uncertain": [],
        "plans": [],
        "relationships": [],
        "promises": [],
    }
    snapshot = {"records": events, "story_revision": 1}
    before = deepcopy((baseline, snapshot))
    first = at_event(baseline, snapshot, "0")
    assert ids(first, "events") == {"0"} and not first["uncertain"]
    last = at_event(baseline, snapshot, "1799")
    assert len(last["events"]) == 1800 and not last["uncertain"]
    assert (baseline, snapshot) == before
