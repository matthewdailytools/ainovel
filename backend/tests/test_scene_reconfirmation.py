"""Isolated author reconfirmation: retain IDs, frozen audit and all safety guards."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
from threading import Barrier
from time import time

import pytest

from ainovel.db import dumps
from ainovel.scene_order import VERSION as AUDIT_VERSION, payload_sha
from ainovel.scene_reconfirmation import ReconfirmationInput, reconfirm, raw_sha
from ainovel.scene_time import SceneTime, scenes
from ainovel.story_graph import SaveRecord, graph, save_record

TEXT = "😀𠮷回铺时天还没黑。他决定先理，再细看，未抄。"


def source(chapter, quote=None):
    quote = quote or chapter["content"]
    at = chapter["content"].index(quote)
    start = len(chapter["content"][:at].encode("utf-16-le")) // 2
    return {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": start,
        "end": start + len(quote.encode("utf-16-le")) // 2,
        "quote": quote,
    }


def record(domain, branch, value, old=None):
    result = save_record(
        domain,
        branch,
        SaveRecord(
            base_story_revision=domain.branch(branch)["story_revision"],
            base_revision=old["revision"] if old else None,
            record=value,
        ),
        old["id"] if old else None,
    )
    return next(r for r in result["records"] if r["id"] == old["id"]) if old else result["records"][-1]


@pytest.fixture
def seed(domain, story):
    branch = story["branch_id"]
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    character = record(domain, branch, {"kind": "character", "title": "林舟", "sources": [source(chapter)]})
    events = [
        record(
            domain,
            branch,
            {
                "kind": "event",
                "title": f"实际事件{i}",
                "status": "actual",
                "story_order": 7,
                "story_time": "回铺后至傍晚",
                "participants": [character["id"]],
                "sources": [source(chapter)],
            },
        )
        for i in range(2)
    ]
    refs = [
        {
            "ref": f"E{i}",
            "event_id": e["id"],
            "event_revision": e["revision"],
            "story_order": 7,
            "record_sha256": payload_sha(e["payload"]),
        }
        for i, e in enumerate(events)
    ]
    payload = SceneTime(
        title="回铺",
        source_revision=chapter["revision"],
        start=0,
        end=len(TEXT.encode("utf-16-le")) // 2,
        story_order=7,
        story_time="回铺后至傍晚",
        audience="character",
        character_id=character["id"],
        order_basis={
            "version": AUDIT_VERSION,
            "kind": "event_refs",
            "refs": refs,
            "covered_events": deepcopy(refs),
        },
        note="原提取说明",
    ).model_dump()
    identifier = "original-extracted-scene"
    raw = dumps(payload)
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO scene_times VALUES(?,?,?,?,?,?,?)",
            (identifier, branch, chapter["id"], 0, raw, 0, time()),
        )
        tx.execute("INSERT INTO scene_time_history VALUES(?,?,?,?,?)", (identifier, 0, raw, 0, time()))
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (branch,))
    return {
        "branch": branch,
        "chapter": chapter,
        "character": character,
        "events": events,
        "id": identifier,
        "payload": payload,
        "raw": raw,
    }


def current(domain, seed):
    return next(
        r for r in scenes(domain, seed["branch"], seed["chapter"]["id"])["scenes"] if r["id"] == seed["id"]
    )


def request(domain, seed, **changes):
    row = current(domain, seed)
    indexed = {e["id"]: e for e in graph(domain, seed["branch"])["records"]}
    refs = []
    for old in row["payload"]["order_basis"]["refs"]:
        e = indexed[old["event_id"]]
        refs.append(
            {
                **old,
                "event_revision": e["revision"],
                "story_order": e["payload"]["story_order"],
                "record_sha256": payload_sha(e["payload"]),
            }
        )
    return {
        "client_request_id": "explicit-reconfirmation-001",
        "base_story_revision": domain.branch(seed["branch"])["story_revision"],
        "base_revision": row["revision"],
        "base_payload_sha256": row["payload_sha256"],
        "source_revision": seed["chapter"]["revision"],
        "content_sha256": raw_sha(seed["chapter"]["content"]),
        "reason": "作者重读原文：只确认回铺时天未黑，不推断持续至傍晚；保留原事件身份。",
        "refs": refs,
        "story_time": "回铺时天未黑，具体时段未明",
        "time_evidence": [source(seed["chapter"], "回铺时天还没黑。")],
        **changes,
    }


def url(seed):
    return f"/api/branches/{seed['branch']}/chapters/{seed['chapter']['id']}/scene-times/{seed['id']}"


def snapshot(domain, seed):
    return {
        table: domain.db.all(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "branches",
            "chapters",
            "revisions",
            "story_records",
            "story_record_history",
            "story_record_dependencies",
            "scene_times",
            "scene_time_history",
            "scene_reconfirmation_receipts",
            "tasks",
            "task_events",
            "facts",
        )
    }


def test_same_id_refreshes_current_refs_without_mutating_frozen_covered_or_prose(client, domain, seed):
    old_event = seed["events"][0]
    updated = record(
        domain, seed["branch"], {**old_event["payload"], "story_time": "回铺时天未黑"}, old_event
    )
    assert current(domain, seed)["stale"]
    baseline = snapshot(domain, seed)
    data = request(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 200, response.text
    receipt = response.json()
    row = current(domain, seed)
    assert row["id"] == seed["id"] and row["revision"] == 1 and not row["stale"]
    assert row["payload"]["order_basis"]["covered_events"] == seed["payload"]["order_basis"]["covered_events"]
    assert row["payload"]["order_basis"]["refs"][0]["event_revision"] == updated["revision"]
    assert row["payload"]["author_reconfirmed"]["authority"] == "author_confirmation_not_model_review"
    assert row["payload"]["story_time"] == data["story_time"]
    for field in (
        "start",
        "end",
        "source_revision",
        "story_order",
        "audience",
        "character_id",
        "title",
        "note",
    ):
        assert row["payload"][field] == seed["payload"][field]
    assert receipt["payload_sha256"] == row["payload_sha256"]
    history = client.get(url(seed) + "/history").json()
    assert len(history) == 2 and history[-1]["payload"] == seed["payload"]
    assert (
        domain.db.one(
            "SELECT payload FROM scene_time_history WHERE scene_id=? AND revision=0", (seed["id"],)
        )["payload"]
        == seed["raw"]
    )
    after = snapshot(domain, seed)
    for table in (
        "chapters",
        "revisions",
        "story_records",
        "story_record_history",
        "story_record_dependencies",
        "tasks",
        "task_events",
        "facts",
    ):
        assert after[table] == baseline[table]
    assert client.post(url(seed) + "/reconfirm", json=data).json() == receipt
    assert len(domain.db.all("SELECT * FROM scene_time_history")) == 2
    assert (
        client.get(url(seed) + "/reconfirmations/" + data["client_request_id"]).json()["receipt"] == receipt
    )


def test_old_payload_defaults_and_absent_metadata_have_one_public_hash_contract(client, domain, seed):
    legacy = deepcopy(seed["payload"])
    for key in ("event_id", "event_revision", "note"):
        legacy.pop(key)
    raw = dumps(legacy)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE scene_times SET payload=? WHERE id=?", (raw, seed["id"]))
        tx.execute("UPDATE scene_time_history SET payload=? WHERE scene_id=?", (raw, seed["id"]))
    row = current(domain, seed)
    assert "author_reconfirmed" not in row["payload"]
    assert row["payload"]["event_id"] is None
    assert row["payload_sha256"] == payload_sha(row["payload"]) != payload_sha(legacy)
    data = request(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 200, response.text
    assert response.json()["old_payload_sha256"] == row["payload_sha256"]
    assert response.json()["old_raw_json_sha256"] == raw_sha(raw)
    assert (
        domain.db.one(
            "SELECT payload FROM scene_time_history WHERE scene_id=? AND revision=0", (seed["id"],)
        )["payload"]
        == raw
    )
    assert (
        "author_reconfirmed" not in SceneTime(title="旧手工", source_revision=0, start=0, end=0).model_dump()
    )


@pytest.mark.parametrize("change", ["request_id_same_reason_different", "same_key_other_scope"])
def test_same_key_different_request_is_conflict_without_second_history(client, domain, seed, change):
    data = request(domain, seed)
    first = client.post(url(seed) + "/reconfirm", json=data)
    assert first.status_code == 200
    before = snapshot(domain, seed)
    altered = (
        {**data, "reason": "同一个标识不允许替换确认理由。"}
        if change.startswith("request")
        else {**data, "story_time": "未知"}
    )
    response = client.post(url(seed) + "/reconfirm", json=altered)
    assert response.status_code == 409 and response.json()["code"] == "idempotency"
    assert snapshot(domain, seed) == before


@pytest.mark.parametrize(
    "field",
    ["base_story_revision", "base_revision", "base_payload_sha256", "content_sha256", "source_revision"],
)
def test_stale_version_or_hash_never_writes(client, domain, seed, field):
    data = request(domain, seed)
    data[field] = "0" * 64 if "sha256" in field else data[field] + 1
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 409, response.text
    assert snapshot(domain, seed) == before


@pytest.mark.parametrize(
    "damage", ["swap", "drop", "duplicate", "event_id", "alias", "revision", "hash", "order"]
)
def test_exact_ordered_refs_and_actual_current_payload_are_required(client, domain, seed, damage):
    data = request(domain, seed)
    if damage == "swap":
        data["refs"].reverse()
    elif damage == "drop":
        data["refs"].pop()
    elif damage == "duplicate":
        data["refs"][1] = deepcopy(data["refs"][0])
    elif damage == "event_id":
        data["refs"][0]["event_id"] = "other-branch-event"
    elif damage == "alias":
        data["refs"][0]["ref"] = "different-alias"
    elif damage == "revision":
        data["refs"][0]["event_revision"] += 1
    elif damage == "hash":
        data["refs"][0]["record_sha256"] = "0" * 64
    else:
        data["refs"][0]["story_order"] += 1
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 409, response.text
    assert snapshot(domain, seed) == before


@pytest.mark.parametrize(
    "damage", ["planned", "archived", "invalid_dependency", "no_overlap", "source_quote"]
)
def test_event_must_be_actual_valid_in_same_source_range(client, domain, seed, damage):
    event = deepcopy(seed["events"][0])
    if damage == "planned":
        event["payload"]["status"] = "planned"
    elif damage == "archived":
        event["archived"] = 1
    elif damage == "invalid_dependency":
        char = seed["character"]
        record(domain, seed["branch"], {**char["payload"], "profile": "作者改变的档案"}, char)
    elif damage == "no_overlap":
        second = domain.create_chapter(seed["branch"], "其他段落")
        second = domain.save(second["id"], 0, "其他段落。")
        event["payload"]["sources"] = [source(second)]
    else:
        event["payload"]["sources"][0]["quote"] = "不对应原文。"
    if damage != "invalid_dependency":
        with domain.db.connect(True) as tx:
            tx.execute(
                "UPDATE story_records SET payload=?,archived=? WHERE id=?",
                (dumps(event["payload"]), event["archived"], event["id"]),
            )
    data = request(domain, seed)
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 409, response.text
    assert snapshot(domain, seed) == before


@pytest.mark.parametrize("damage", ["manual", "unknown", "scalar"])
def test_legacy_manual_or_unknown_basis_cannot_be_promoted(client, domain, seed, damage):
    data = request(domain, seed)
    value = deepcopy(seed["payload"])
    if damage == "manual":
        value.pop("order_basis")
    elif damage == "unknown":
        value["order_basis"].update(kind="unknown", reason="实际顺序未核定")
        value["story_order"] = None
    else:
        value.update(event_id=seed["events"][0]["id"], event_revision=0)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE scene_times SET payload=? WHERE id=?", (dumps(value), seed["id"]))
    data["base_payload_sha256"] = current(domain, seed)["payload_sha256"]
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 409 and response.json()["code"] == "scene_reconfirmation_scope"
    assert snapshot(domain, seed) == before


@pytest.mark.parametrize(
    "damage", ["missing", "quote", "outside", "wrong_chapter", "wrong_revision", "utf16_split"]
)
def test_changed_time_needs_continuous_grounded_author_evidence(client, domain, seed, damage):
    data = request(domain, seed)
    if damage == "missing":
        data["time_evidence"] = []
    elif damage == "quote":
        data["time_evidence"][0]["quote"] = "到了精确的傍晚六点。"
    elif damage == "outside":
        data["time_evidence"][0]["end"] = 999
    elif damage == "wrong_chapter":
        data["time_evidence"][0]["chapter_id"] = "other"
    elif damage == "wrong_revision":
        data["time_evidence"][0]["revision"] += 1
    else:
        data["time_evidence"][0].update(start=1, end=2)
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code in (400, 409), response.text
    assert snapshot(domain, seed) == before


def test_unchanged_time_may_refresh_refs_without_time_evidence(client, domain, seed):
    data = request(domain, seed, story_time=seed["payload"]["story_time"], time_evidence=[])
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 200 and response.json()["story_time"] == seed["payload"]["story_time"]


def test_source_change_and_cross_branch_ownership_are_held(client, domain, seed):
    data = request(domain, seed)
    other = domain.create_novel("其他作品", "隔离样例")
    wrong = url(seed).replace(seed["branch"], other["branch_id"])
    assert client.post(wrong + "/reconfirm", json=data).status_code == 404
    domain.save(seed["chapter"]["id"], seed["chapter"]["revision"], TEXT + "正文改动。")
    data["base_story_revision"] = domain.branch(seed["branch"])["story_revision"]
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json=data)
    assert response.status_code == 409 and response.json()["code"] == "story_changed"
    assert snapshot(domain, seed) == before


def test_ordinary_put_keeps_readonly_basis_and_inherits_omitted_server_metadata(client, domain, seed):
    data = request(domain, seed)
    assert client.post(url(seed) + "/reconfirm", json=data).status_code == 200
    row = current(domain, seed)
    for field, value in [("story_time", "虚构精确时刻"), ("start", 1), ("story_order", 9)]:
        p = {**row["payload"], field: value}
        response = client.put(
            url(seed),
            json={
                "base_story_revision": domain.branch(seed["branch"])["story_revision"],
                "base_revision": row["revision"],
                "scene": p,
            },
        )
        assert response.status_code == 409 and response.json()["code"] == "scene_basis_readonly"
    edited = deepcopy(row["payload"])
    edited.pop("author_reconfirmed")
    edited["title"] = "作者只改标题"
    response = client.put(
        url(seed),
        json={
            "base_story_revision": domain.branch(seed["branch"])["story_revision"],
            "base_revision": row["revision"],
            "scene": edited,
        },
    )
    assert response.status_code == 200, response.text
    latest = current(domain, seed)
    assert latest["payload"]["author_reconfirmed"] == row["payload"]["author_reconfirmed"]
    for forged in ({**latest["payload"]["author_reconfirmed"], "reason": "伪造确认"}, None):
        response = client.put(
            url(seed),
            json={
                "base_story_revision": domain.branch(seed["branch"])["story_revision"],
                "base_revision": latest["revision"],
                "scene": {**latest["payload"], "author_reconfirmed": forged},
            },
        )
        assert response.status_code == 409 and response.json()["code"] == "scene_reconfirmation_readonly"


def test_server_metadata_cannot_be_created_on_a_new_manual_scene(client, domain, seed):
    data = request(domain, seed)
    assert client.post(url(seed) + "/reconfirm", json=data).status_code == 200
    row = current(domain, seed)
    value = deepcopy(row["payload"])
    value.pop("order_basis")
    response = client.post(
        url(seed).rsplit("/", 1)[0],
        json={"base_story_revision": domain.branch(seed["branch"])["story_revision"], "scene": value},
    )
    assert response.status_code == 409 and response.json()["code"] == "scene_reconfirmation_readonly"


@pytest.mark.parametrize(
    "field,value", [("start", 0), ("audience", "author"), ("order_basis", {}), ("author_reconfirmed", {})]
)
def test_special_endpoint_cannot_edit_scope_or_supply_server_metadata(client, domain, seed, field, value):
    before = snapshot(domain, seed)
    response = client.post(url(seed) + "/reconfirm", json={**request(domain, seed), field: value})
    assert response.status_code == 422
    assert snapshot(domain, seed) == before


def test_last_receipt_insert_failure_rolls_back_all_prior_scene_and_history_writes(client, domain, seed):
    before = snapshot(domain, seed)
    with domain.db.connect(True) as tx:
        tx.execute(
            "CREATE TRIGGER reject_reconfirmation BEFORE INSERT ON scene_reconfirmation_receipts BEGIN SELECT RAISE(ABORT, 'injected late failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        client.post(url(seed) + "/reconfirm", json=request(domain, seed))
    assert snapshot(domain, seed) == before


def test_duplicate_concurrent_request_is_one_history_and_receipt(domain, seed):
    data = ReconfirmationInput.model_validate(request(domain, seed))
    barrier = Barrier(2)

    def call(_):
        barrier.wait()
        return reconfirm(domain, seed["branch"], seed["chapter"]["id"], seed["id"], data)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(call, [0, 1]))
    assert values[0] == values[1]
    assert len(domain.db.all("SELECT * FROM scene_time_history")) == 2
    assert len(domain.db.all("SELECT * FROM scene_reconfirmation_receipts")) == 1


def test_unknown_result_lookup_and_replay_survive_later_changes(client, domain, seed):
    data = request(domain, seed)
    key = data["client_request_id"]
    assert client.get(url(seed) + "/reconfirmations/" + key).json()["receipt"] is None
    first = client.post(url(seed) + "/reconfirm", json=data).json()
    domain.save(seed["chapter"]["id"], 1, TEXT + "后续改稿。")
    before = snapshot(domain, seed)
    assert client.get(url(seed) + "/reconfirmations/" + key).json()["receipt"] == first
    assert client.post(url(seed) + "/reconfirm", json=data).json() == first
    assert snapshot(domain, seed) == before


@pytest.mark.parametrize(
    "location", ["base_revision", "ref_revision", "ref_order", "source_revision", "time_start"]
)
def test_version_and_range_indices_reject_boolean_coercion(client, domain, seed, location):
    data = request(domain, seed)
    if location == "base_revision":
        data["base_revision"] = False
    elif location == "ref_revision":
        data["refs"][0]["event_revision"] = False
    elif location == "ref_order":
        data["refs"][0]["story_order"] = True
    elif location == "source_revision":
        data["source_revision"] = True
    else:
        data["time_evidence"][0]["start"] = False
    before = snapshot(domain, seed)
    assert client.post(url(seed) + "/reconfirm", json=data).status_code == 422
    assert snapshot(domain, seed) == before


def test_original_receipt_does_not_follow_later_put_inheriting_same_metadata(client, domain, seed):
    data = request(domain, seed)
    original = client.post(url(seed) + "/reconfirm", json=data).json()
    row = current(domain, seed)
    payload = deepcopy(row["payload"])
    payload.pop("author_reconfirmed")
    payload["title"] = "后续作者编辑"
    response = client.put(
        url(seed),
        json={
            "base_story_revision": domain.branch(seed["branch"])["story_revision"],
            "base_revision": row["revision"],
            "scene": payload,
        },
    )
    assert response.status_code == 200
    latest = current(domain, seed)
    assert latest["revision"] == 2
    assert latest["payload"]["author_reconfirmed"]["scene_revision"] == 1
    assert latest["payload_sha256"] != original["payload_sha256"]
    before = snapshot(domain, seed)
    assert client.post(url(seed) + "/reconfirm", json=data).json() == original
    assert (
        client.get(url(seed) + "/reconfirmations/" + data["client_request_id"]).json()["receipt"] == original
    )
    assert snapshot(domain, seed) == before


def test_reconfirmation_schema_migration_preserves_legacy_payload_and_history(domain, seed):
    from ainovel.db import Database

    before = snapshot(domain, seed)
    with domain.db.connect(True) as tx:
        tx.execute("DROP TABLE scene_reconfirmation_receipts")
        tx.execute("DELETE FROM extension_migrations WHERE name='scene-author-reconfirmation/1'")
    Database(domain.db.path)
    assert snapshot(domain, seed) == before
    assert domain.db.one("SELECT name FROM extension_migrations WHERE name='scene-author-reconfirmation/1'")


def test_reconfirmation_receipt_and_exact_scene_history_survive_actual_backup_restore(
    settings, domain, seed, tmp_path
):
    from ainovel.backups import create_backup, unpack_verified
    from ainovel.db import Database
    from ainovel.domain import Domain
    from ainovel.scene_reconfirmation import receipt

    data = ReconfirmationInput.model_validate(request(domain, seed))
    accepted = reconfirm(domain, seed["branch"], seed["chapter"]["id"], seed["id"], data)
    original = snapshot(domain, seed)
    backup = create_backup(settings, domain.db)
    folder = tmp_path / "actual-isolated-restore"
    folder.mkdir()
    unpack_verified(settings.data_dir / "backups" / (backup["id"] + ".zip"), folder)
    restored = Domain(Database(folder / "manuscripts.sqlite"))
    assert snapshot(restored, seed) == original
    assert (
        receipt(restored, seed["branch"], seed["chapter"]["id"], seed["id"], data.client_request_id)[
            "receipt"
        ]
        == accepted
    )
    assert reconfirm(restored, seed["branch"], seed["chapter"]["id"], seed["id"], data) == accepted
    assert snapshot(restored, seed) == original


@pytest.mark.parametrize("coverage", ["absent", "optional_fields_absent"])
def test_frozen_covered_events_keep_exact_legacy_json_shape(client, domain, seed, coverage):
    import json

    legacy = deepcopy(seed["payload"])
    if coverage == "absent":
        legacy["order_basis"].pop("covered_events")
    else:
        for witness in legacy["order_basis"]["covered_events"]:
            witness.pop("event_revision")
            witness.pop("story_order")
    raw = dumps(legacy)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE scene_times SET payload=? WHERE id=?", (raw, seed["id"]))
        tx.execute("UPDATE scene_time_history SET payload=? WHERE scene_id=?", (raw, seed["id"]))
    response = client.post(url(seed) + "/reconfirm", json=request(domain, seed))
    assert response.status_code == 200, response.text
    stored = json.loads(domain.db.one("SELECT payload FROM scene_times WHERE id=?", (seed["id"],))["payload"])
    assert stored["order_basis"].get("covered_events") == legacy["order_basis"].get("covered_events")
    assert ("covered_events" in stored["order_basis"]) == ("covered_events" in legacy["order_basis"])
    public = current(domain, seed)
    assert response.json()["payload_sha256"] == public["payload_sha256"]
    edited = {**public["payload"], "title": "只编辑标题，不改变冻结审计"}
    changed = client.put(
        url(seed),
        json={
            "base_story_revision": domain.branch(seed["branch"])["story_revision"],
            "base_revision": public["revision"],
            "scene": edited,
        },
    )
    assert changed.status_code == 200, changed.text
    after = json.loads(domain.db.one("SELECT payload FROM scene_times WHERE id=?", (seed["id"],))["payload"])
    assert after["order_basis"] == stored["order_basis"]
    assert (
        domain.db.one(
            "SELECT payload FROM scene_time_history WHERE scene_id=? AND revision=0", (seed["id"],)
        )["payload"]
        == raw
    )


def test_migration_and_reconfirmation_leave_nonempty_facts_and_paid_usage_untouched(
    settings, client, domain, seed, tmp_path
):
    from ainovel.db import Database
    from ainovel.budget import Budget
    from ainovel.domain import Domain
    from ainovel.backups import create_backup, unpack_verified

    budget = Budget(settings)
    call = budget.reserve(settings.providers["deepseek"], "isolated-historical-call", 100, 200)
    budget.settle(call, {"input": 31, "output": 47})
    with domain.db.connect(True) as tx:
        domain.add_fact_tx(
            tx,
            seed["branch"],
            seed["chapter"]["id"],
            1,
            {
                "subject": "林舟",
                "kind": "event",
                "statement": "回铺时天未黑",
                "truth": "fact",
                "story_time": "具体时段未知",
                "evidence": "回铺时天还没黑。",
            },
            TEXT,
        )
    original_facts = domain.db.all("SELECT * FROM facts")
    original_usage = budget.report()
    with domain.db.connect(True) as tx:
        tx.execute("DROP TABLE scene_reconfirmation_receipts")
        tx.execute("DELETE FROM extension_migrations WHERE name='scene-author-reconfirmation/1'")
    Database(domain.db.path)
    assert client.post(url(seed) + "/reconfirm", json=request(domain, seed)).status_code == 200
    assert domain.db.all("SELECT * FROM facts") == original_facts
    assert budget.report() == original_usage
    after_reconfirmation = snapshot(domain, seed)
    backup = create_backup(settings, domain.db)
    folder = tmp_path / "nonempty-actual-restore"
    folder.mkdir()
    unpack_verified(settings.data_dir / "backups" / (backup["id"] + ".zip"), folder)
    restored = Domain(Database(folder / "manuscripts.sqlite"))
    assert snapshot(restored, seed) == after_reconfirmation
    assert restored.db.all("SELECT * FROM facts") == original_facts
    assert budget.report() == original_usage
    with budget.db() as tx:
        original_calls = [tuple(r) for r in tx.execute("SELECT * FROM calls ORDER BY id")]
    with sqlite3.connect(folder / "usage-evidence.sqlite") as tx:
        assert list(tx.execute("SELECT * FROM calls ORDER BY id")) == original_calls


def test_preview_is_readonly_server_derived_and_defaults_to_unchanged_time(client, domain, seed):
    before = snapshot(domain, seed)
    response = client.get(url(seed) + "/reconfirmation-preview")
    assert response.status_code == 200, response.text
    value = response.json()
    row = current(domain, seed)
    assert value["scene"]["payload_sha256"] == row["payload_sha256"]
    assert value["request_basis"]["base_payload_sha256"] == row["payload_sha256"]
    assert value["request_basis"]["content_sha256"] == raw_sha(TEXT)
    assert value["source"]["quote"] == TEXT
    assert value["source"]["start"] == row["payload"]["start"]
    assert value["source"]["end"] == row["payload"]["end"]
    assert value["request_basis"]["story_time"] == row["payload"]["story_time"]
    assert value["request_basis"]["time_evidence"] == []
    assert snapshot(domain, seed) == before
    data = {
        **value["request_basis"],
        "client_request_id": "preview-default-time-key",
        "reason": "作者逐项核对当前事件来源，仅刷新引用，不改时间。",
    }
    accepted = client.post(url(seed) + "/reconfirm", json=data)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["story_time"] == row["payload"]["story_time"]


def test_preview_provides_current_event_versions_without_changing_original_frozen_audit(client, domain, seed):
    old_event = seed["events"][0]
    changed = record(
        domain, seed["branch"], {**old_event["payload"], "story_time": "回铺时天未黑"}, old_event
    )
    before = snapshot(domain, seed)
    response = client.get(url(seed) + "/reconfirmation-preview")
    assert response.status_code == 200
    value = response.json()
    assert value["scene"]["payload"]["order_basis"]["refs"][0]["event_revision"] == 0
    assert value["request_basis"]["refs"][0]["event_revision"] == changed["revision"]
    assert value["request_basis"]["refs"][0]["record_sha256"] == payload_sha(changed["payload"])
    assert snapshot(domain, seed) == before


def test_unselected_conflicting_actual_event_in_range_is_not_ignored(client, domain, seed):
    record(
        domain,
        seed["branch"],
        {
            "kind": "event",
            "title": "新增不同时点的覆盖事件",
            "status": "actual",
            "story_order": 8,
            "sources": [source(seed["chapter"])],
        },
    )
    data = request(domain, seed)
    before = snapshot(domain, seed)
    for response in (
        client.get(url(seed) + "/reconfirmation-preview"),
        client.post(url(seed) + "/reconfirm", json=data),
    ):
        assert response.status_code == 409 and response.json()["code"] == "scene_time_evidence"
    assert snapshot(domain, seed) == before


def test_reconfirmation_keeps_existing_csrf_and_origin_boundaries(client, domain, seed):
    before = snapshot(domain, seed)
    data = request(domain, seed)
    assert client.post(url(seed) + "/reconfirm", json=data, headers={"X-Ainovel-CSRF": ""}).status_code == 403
    assert (
        client.post(
            url(seed) + "/reconfirm", json=data, headers={"Origin": "https://untrusted.invalid"}
        ).status_code
        == 403
    )
    assert snapshot(domain, seed) == before
