"""Narrow explicit unknown-reason edits preserve all narrative/time bindings."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from time import time

import pytest

from ainovel.db import DomainError, dumps
from ainovel.scene_time import SceneTime, SaveScene, save_scene, scenes
from ainovel.scene_order import payload_sha
from ainovel.scene_reconfirmation import raw_sha
from ainovel.scene_unknown_reason_correction import VERSION, AUTHORITY, preview, receipt
from ainovel.story_graph import SaveRecord, save_record

TEXT = "😀𠮷次日来信仍未到。林舟只记录测量，未下结论。"
OLD_REASON = "模型未对齐全局位置，疑有前文差异。"
NEW_REASON = "前后安排可以相容；精确全局位置仍未绑定，保持未知。"


@pytest.fixture
def unknown(domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    source = {
        "chapter_id": chapter["id"],
        "revision": chapter["revision"],
        "start": 0,
        "end": len(TEXT.encode("utf-16-le")) // 2,
        "quote": TEXT,
    }

    def record(value):
        result = save_record(
            domain,
            story["branch_id"],
            SaveRecord(base_story_revision=domain.branch(story["branch_id"])["story_revision"], record=value),
        )
        return result["records"][-1]

    character = record({"kind": "character", "title": "林舟", "sources": [source]})
    event = record(
        {
            "kind": "event",
            "title": "记录测量",
            "status": "actual",
            "story_order": None,
            "participants": [character["id"]],
            "sources": [source],
        }
    )
    ref = {
        "ref": "E1",
        "event_id": event["id"],
        "event_revision": event["revision"],
        "story_order": None,
        "record_sha256": payload_sha(event["payload"]),
    }
    p = SceneTime(
        title="次日记录",
        source_revision=chapter["revision"],
        start=0,
        end=source["end"],
        story_order=None,
        story_time="次日",
        audience="character",
        character_id=character["id"],
        note="保留提取说明",
        order_basis={
            "version": "scene-event-refs/2",
            "kind": "unknown",
            "reason": OLD_REASON,
            "refs": [ref],
            "covered_events": [ref],
        },
    ).model_dump()
    identifier = "unknown-original-scene"
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO scene_times VALUES(?,?,?,?,?,?,?)",
            (identifier, story["branch_id"], chapter["id"], 0, dumps(p), 0, time()),
        )
        tx.execute("INSERT INTO scene_time_history VALUES(?,?,?,?,?)", (identifier, 0, dumps(p), 0, time()))
        tx.execute("UPDATE branches SET story_revision=story_revision+1 WHERE id=?", (story["branch_id"],))
    return {"id": identifier, "branch": story["branch_id"], "chapter": chapter, "payload": p}


def request(
    domain, seed, *, nonce="explicit-unknown-reason-1", actor="authorized_operator", reason=NEW_REASON
):
    value = preview(domain, seed["branch"], seed["chapter"]["id"], seed["id"])
    basis = value["request_basis"]
    return SaveScene(
        base_story_revision=basis["base_story_revision"],
        base_revision=basis["base_revision"],
        scene=value["scene"]["payload"],
        unknown_reason_correction={
            "version": VERSION,
            "client_request_id": nonce,
            "actor": actor,
            "base_payload_sha256": basis["base_payload_sha256"],
            "source_revision": basis["source_revision"],
            "content_sha256": basis["content_sha256"],
            "reason": reason,
            "evidence": [value["source"]],
        },
    )


def url(seed):
    return f"/api/branches/{seed['branch']}/chapters/{seed['chapter']['id']}/scene-times/{seed['id']}"


@pytest.mark.parametrize("actor", ["author", "authorized_operator"])
def test_explicit_put_retains_identity_source_unknown_refs_and_raw_history(domain, client, unknown, actor):
    old = domain.db.one("SELECT * FROM scene_times WHERE id=?", (unknown["id"],))
    data = request(domain, unknown, actor=actor)
    response = client.put(url(unknown), json=data.model_dump())
    assert response.status_code == 200, response.text
    row = response.json()["scenes"][0]
    p = row["payload"]
    proof = p["unknown_reason_corrected"]
    assert row["id"] == unknown["id"] and row["revision"] == 1
    expected = deepcopy(unknown["payload"])
    expected["order_basis"]["reason"] = NEW_REASON
    assert {k: v for k, v in p.items() if k != "unknown_reason_corrected"} == expected
    assert p["story_order"] is None and p["order_basis"]["kind"] == "unknown"
    assert proof["actor"] == actor and proof["authority"] == AUTHORITY
    assert proof["previous_reason"] == proof["original_reason"] == OLD_REASON
    assert (
        not proof["model_review_performed"]
        and not proof["literary_review_performed"]
        and not proof["story_time_inferred"]
    )
    history = domain.db.all(
        "SELECT * FROM scene_time_history WHERE scene_id=? ORDER BY revision", (unknown["id"],)
    )
    assert len(history) == 2 and history[0]["payload"] == old["payload"]
    got = client.get(url(unknown) + "/unknown-reason-corrections/explicit-unknown-reason-1").json()["receipt"]
    assert got["payload_sha256"] == row["payload_sha256"]
    assert got["scene_revision"] == 1 and got["story_revision"] == data.base_story_revision + 1
    assert domain.chapter(unknown["chapter"]["id"])["content"] == TEXT
    assert not domain.db.all("SELECT id FROM tasks")
    assert client.get(url(unknown) + "/reconfirmation-preview").status_code == 409


def test_repeat_nonce_is_exact_and_second_correction_keeps_original_reason(domain, unknown):
    first = request(domain, unknown)
    save_scene(domain, unknown["branch"], unknown["chapter"]["id"], first, unknown["id"])
    before = domain.db.all("SELECT * FROM scene_time_history")
    save_scene(domain, unknown["branch"], unknown["chapter"]["id"], first, unknown["id"])
    assert domain.db.all("SELECT * FROM scene_time_history") == before
    changed = first.model_copy(
        update={
            "unknown_reason_correction": first.unknown_reason_correction.model_copy(
                update={"reason": "同标识不同更正"}
            )
        }
    )
    with pytest.raises(DomainError, match="标识"):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], changed, unknown["id"])
    second = request(
        domain, unknown, nonce="explicit-unknown-reason-2", reason="补充明确来源，时点继续未知。"
    )
    row = save_scene(domain, unknown["branch"], unknown["chapter"]["id"], second, unknown["id"])["scenes"][0]
    assert row["payload"]["unknown_reason_corrected"]["original_reason"] == OLD_REASON
    assert row["payload"]["unknown_reason_corrected"]["previous_reason"] == NEW_REASON
    assert (
        receipt(domain, unknown["branch"], unknown["chapter"]["id"], unknown["id"], "not-submitted-nonce")[
            "receipt"
        ]
        is None
    )


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "note",
        "start",
        "end",
        "story_order",
        "story_time",
        "audience",
        "character_id",
        "event_id",
        "event_revision",
        "reason",
        "refs",
        "covered_events",
    ],
)
def test_reason_request_cannot_mix_any_other_scene_edit(domain, unknown, field):
    data = request(domain, unknown)
    p = data.scene.model_dump()
    if field in ("reason", "refs", "covered_events"):
        p["order_basis"][field] = "伪改原理由" if field == "reason" else []
    else:
        p[field] = {
            "title": "另一标题",
            "note": "另一说明",
            "start": 2,
            "end": 2,
            "story_order": 1,
            "story_time": "已推断日期",
            "audience": "author",
            "character_id": None,
            "event_id": "another",
            "event_revision": 1,
        }[field]
    data = data.model_copy(update={"scene": SceneTime.model_validate(p)})
    before = domain.db.all("SELECT * FROM scene_times")
    with pytest.raises(DomainError):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, unknown["id"])
    assert domain.db.all("SELECT * FROM scene_times") == before


@pytest.mark.parametrize(
    "damage",
    [
        "story",
        "scene",
        "payload_sha",
        "content_sha",
        "source_revision",
        "wrong_quote",
        "half_surrogate",
        "outside_scene",
    ],
)
def test_changed_or_nonentailing_source_is_rejected_without_history(domain, unknown, damage):
    data = request(domain, unknown)
    if damage == "story":
        data = data.model_copy(update={"base_story_revision": data.base_story_revision + 1})
    elif damage == "scene":
        data = data.model_copy(update={"base_revision": data.base_revision + 1})
    else:
        correction = data.unknown_reason_correction
        if damage in ("payload_sha", "content_sha", "source_revision"):
            key = {
                "payload_sha": "base_payload_sha256",
                "content_sha": "content_sha256",
                "source_revision": "source_revision",
            }[damage]
            correction = correction.model_copy(update={key: 999 if damage == "source_revision" else "0" * 64})
        else:
            evidence = correction.evidence[0]
            updates = {
                "wrong_quote": {"quote": "不在原文"},
                "half_surrogate": {"start": 1, "end": 2, "quote": "😀"},
                "outside_scene": {"start": 0, "end": 0, "quote": "😀"},
            }[damage]
            correction = correction.model_copy(update={"evidence": [evidence.model_copy(update=updates)]})
        data = data.model_copy(update={"unknown_reason_correction": correction})
    before = domain.db.all("SELECT * FROM scene_time_history")
    with pytest.raises(DomainError):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, unknown["id"])
    assert domain.db.all("SELECT * FROM scene_time_history") == before


@pytest.mark.parametrize("scope", ["create", "archive", "already_archived", "known"])
def test_only_existing_active_unknown_scene_is_eligible(domain, unknown, scope):
    data = request(domain, unknown)
    identifier = unknown["id"]
    if scope == "create":
        identifier = None
    elif scope == "archive":
        data = data.model_copy(update={"archived": True})
    elif scope == "already_archived":
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE scene_times SET archived=1 WHERE id=?", (identifier,))
    else:
        p = deepcopy(unknown["payload"])
        p["story_order"] = 7
        p["order_basis"]["kind"] = "event_refs"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE scene_times SET payload=? WHERE id=?", (dumps(p), identifier))
    before = domain.db.all("SELECT * FROM scene_times")
    with pytest.raises(DomainError):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, identifier)
    assert domain.db.all("SELECT * FROM scene_times") == before


def test_cross_branch_evidence_and_forged_receipt_are_rejected(domain, unknown):
    data = request(domain, unknown)
    other = domain.create_novel("另一作品")
    chapter = domain.create_chapter(other["branch_id"], "另一章")
    chapter = domain.save(chapter["id"], 0, TEXT)
    evidence = data.unknown_reason_correction.evidence[0].model_copy(update={"chapter_id": chapter["id"]})
    bad = data.model_copy(
        update={
            "unknown_reason_correction": data.unknown_reason_correction.model_copy(
                update={"evidence": [evidence]}
            )
        }
    )
    with pytest.raises(DomainError, match="工作版本"):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], bad, unknown["id"])
    row = save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, unknown["id"])["scenes"][0]
    p = deepcopy(row["payload"])
    p["unknown_reason_corrected"]["actor"] = "author"
    fake = SaveScene(
        base_story_revision=domain.branch(unknown["branch"])["story_revision"], base_revision=1, scene=p
    )
    with pytest.raises(DomainError, match="伪造"):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], fake, unknown["id"])


def test_optional_covered_absence_and_original_raw_are_preserved(domain, unknown):
    p = deepcopy(unknown["payload"])
    p["order_basis"].pop("covered_events")
    raw = dumps(p)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE scene_times SET payload=? WHERE id=?", (raw, unknown["id"]))
        tx.execute(
            "UPDATE scene_time_history SET payload=? WHERE scene_id=? AND revision=0", (raw, unknown["id"])
        )
    save_scene(domain, unknown["branch"], unknown["chapter"]["id"], request(domain, unknown), unknown["id"])
    saved = json.loads(
        domain.db.one("SELECT payload FROM scene_times WHERE id=?", (unknown["id"],))["payload"]
    )
    assert "covered_events" not in saved["order_basis"]
    assert (
        domain.db.one(
            "SELECT payload FROM scene_time_history WHERE scene_id=? AND revision=0", (unknown["id"],)
        )["payload"]
        == raw
    )


def test_concurrent_distinct_decisions_have_one_revision_and_same_nonce_is_idempotent(domain, unknown):
    data = request(domain, unknown)

    def submit(nonce):
        try:
            changed = data.model_copy(
                update={
                    "unknown_reason_correction": data.unknown_reason_correction.model_copy(
                        update={"client_request_id": nonce}
                    )
                }
            )
            save_scene(domain, unknown["branch"], unknown["chapter"]["id"], changed, unknown["id"])
            return "saved"
        except DomainError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        out = list(pool.map(submit, ["parallel-one", "parallel-two"]))
    assert sorted(out) == ["conflict", "saved"]
    assert domain.db.one("SELECT revision FROM scene_times WHERE id=?", (unknown["id"],))["revision"] == 1
    assert len(domain.db.all("SELECT * FROM scene_unknown_reason_correction_receipts")) == 1


def test_legacy_none_serialization_and_schema_migration(domain):
    scene = SceneTime(title="旧场景", source_revision=1, start=0, end=2, story_order=None, audience="author")
    expected = {
        "event_id": None,
        "event_revision": None,
        "title": "旧场景",
        "source_revision": 1,
        "start": 0,
        "end": 2,
        "story_order": None,
        "story_time": "未知",
        "audience": "author",
        "character_id": None,
        "note": "",
    }
    assert scene.model_dump_json() == dumps(expected)
    data = SaveScene(base_story_revision=1, scene=scene)
    assert data.model_dump_json() == dumps(
        {"base_story_revision": 1, "base_revision": None, "scene": expected, "archived": False}
    )
    assert domain.db.one("SELECT name FROM extension_migrations WHERE name=?", (VERSION,))
    columns = domain.db.all("PRAGMA table_info(scene_unknown_reason_correction_receipts)")
    assert [c["name"] for c in columns] == ["key", "scene_id", "request_sha256", "result", "created"]


def test_cross_chapter_evidence_checks_each_complete_saved_source_version(domain, unknown):
    other = domain.create_chapter(unknown["branch"], "另一份同书保存稿")
    other = domain.save(other["id"], 0, "前一日的安排可以作为显式更正依据。")
    data = request(domain, unknown)
    from ainovel.scene_unknown_reason_correction import CorrectionEvidence

    additional = CorrectionEvidence(
        chapter_id=other["id"],
        revision=other["revision"],
        start=0,
        end=len(other["content"].encode("utf-16-le")) // 2,
        quote=other["content"],
        content_sha256=raw_sha(other["content"]),
    )
    data = data.model_copy(
        update={
            "unknown_reason_correction": data.unknown_reason_correction.model_copy(
                update={"evidence": [*data.unknown_reason_correction.evidence, additional]}
            )
        }
    )
    domain.save(other["id"], other["revision"], "保存稿已经改变。")
    data = data.model_copy(update={"base_story_revision": domain.branch(unknown["branch"])["story_revision"]})
    before = domain.db.all("SELECT * FROM scene_time_history")
    with pytest.raises(DomainError, match="SHA"):
        save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, unknown["id"])
    assert domain.db.all("SELECT * FROM scene_time_history") == before


def test_same_nonce_concurrent_replay_and_readonly_proof_carry_forward(domain, unknown):
    data = request(domain, unknown)

    def submit(_):
        return save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, unknown["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert len(domain.db.all("SELECT * FROM scene_unknown_reason_correction_receipts")) == 1
    p = deepcopy(results[0]["scenes"][0]["payload"])
    proof = p.pop("unknown_reason_corrected")
    p["title"] = "只修改标题，沿用更正来源"
    edited = SaveScene(
        base_story_revision=domain.branch(unknown["branch"])["story_revision"], base_revision=1, scene=p
    )
    row = save_scene(domain, unknown["branch"], unknown["chapter"]["id"], edited, unknown["id"])["scenes"][0]
    assert row["payload"]["unknown_reason_corrected"] == proof
    assert row["payload"]["order_basis"]["reason"] == NEW_REASON


def test_actor_is_explicit_required_and_old_put_cannot_edit_audit_reason(domain, client, unknown):
    data = request(domain, unknown).model_dump()
    data["unknown_reason_correction"].pop("actor")
    assert client.put(url(unknown), json=data).status_code == 422
    old = {
        "base_story_revision": domain.branch(unknown["branch"])["story_revision"],
        "base_revision": 0,
        "scene": deepcopy(unknown["payload"]),
    }
    old["scene"]["order_basis"]["reason"] = NEW_REASON
    response = client.put(url(unknown), json=old)
    assert response.status_code == 409 and response.json()["code"] == "scene_basis_readonly"
    assert domain.db.one("SELECT revision FROM scene_times WHERE id=?", (unknown["id"],))["revision"] == 0


def test_real_worker_local_and_whole_chapter_frames_exclude_history_and_future_evidence(
    settings, domain, unknown
):
    import asyncio
    from ainovel.models import ModuleInput
    from ainovel.worker import Worker
    from ainovel.scene_unknown_reason_correction import CorrectionEvidence

    future = domain.create_chapter(unknown["branch"], "较后保存稿")
    future = domain.save(future["id"], 0, "FUTURE_PRIVATE_EVIDENCE_ONLY。")
    data = request(domain, unknown)
    extra = CorrectionEvidence(
        chapter_id=future["id"],
        revision=future["revision"],
        start=0,
        end=len(future["content"].encode("utf-16-le")) // 2,
        quote=future["content"],
        content_sha256=raw_sha(future["content"]),
    )
    data = data.model_copy(
        update={
            "unknown_reason_correction": data.unknown_reason_correction.model_copy(
                update={"evidence": [*data.unknown_reason_correction.evidence, extra]}
            )
        }
    )
    saved = save_scene(domain, unknown["branch"], unknown["chapter"]["id"], data, unknown["id"])
    payload = ModuleInput(
        action="writing.advise",
        novel_id=domain.branch(unknown["branch"])["novel_id"],
        branch_id=unknown["branch"],
        chapter_id=unknown["chapter"]["id"],
        base_revision=unknown["chapter"]["revision"],
        instruction="仅给建议。",
        account="openai_1",
    ).model_dump()
    domain.queue("module", payload, unknown["branch"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    frames = []

    class Model:
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frames.append(json.loads(prompt))
            return {
                "text": "保留当前未知。",
                "usage": {"input": 1, "output": 1},
                "call_id": f"offline-context-{len(frames)}",
            }

    worker.client = Model()
    for whole in (False, True):
        context = worker.context(unknown["branch"], unknown["chapter"]["id"])
        if whole:
            context = worker.whole_chapter_context(context)
        before = deepcopy(context)
        asyncio.run(worker.call({"动作": "隔离写作建议"}, context=context))
        assert context == before
        frame = frames[-1]["上下文"]
        binding = (
            frame["chapter_scenes"]["scenes"][0]["binding"] if whole else frame["longform"]["scene_binding"]
        )
        assert binding["payload"]["story_order"] is None
        assert binding["payload"]["order_basis"]["reason"] == NEW_REASON
        notice = binding["payload"]["unknown_reason_correction_notice"]
        assert notice["actor"] == "authorized_operator" and notice["still_unknown"]
        assert not notice["model_review_performed"] and not notice["literary_review_performed"]
        assert "unknown_reason_corrected" not in binding["payload"]
        assert OLD_REASON not in dumps(frame)
        assert "FUTURE_PRIVATE_EVIDENCE_ONLY" not in dumps(frame)
        assert data.unknown_reason_correction.client_request_id not in dumps(frame)
    current = scenes(domain, unknown["branch"], unknown["chapter"]["id"])
    assert current == saved
    assert current["scenes"][0]["payload"]["unknown_reason_corrected"]["original_reason"] == OLD_REASON


def test_no_proof_context_is_identical_and_arbitrary_same_named_document_key_is_untouched():
    from ainovel.scene_unknown_reason_correction import model_context

    legacy = {
        "longform": {
            "scene_binding": {
                "id": "legacy",
                "payload": {"story_order": None, "order_basis": {"kind": "unknown", "reason": "旧理由"}},
            }
        },
        "definitions": [{"payload": {"unknown_reason_corrected": {"literal": "作品中同名资料字段"}}}],
    }
    before = dumps(legacy)
    assert model_context(legacy) is legacy
    assert dumps(legacy) == before
