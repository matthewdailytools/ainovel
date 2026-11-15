"""Explicit fact selection never changes the saved candidate or the original prose."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
from threading import Barrier

import pytest

from ainovel.db import DomainError, dumps
from ainovel.memory_selection import facts_sha256
from ainovel.worker import Worker
from test_candidate_recheck import RecheckModel, queue_recheck, stopped_candidate as stopped_candidate_fixture


ORIGINAL = "林舟把旧稿收进抽屉。😀𠮷"
BODY = "林舟登记桥板裂纹。\n他收好旧锤。\n木匠把桩号写在木牌上。😀𠮷"
FACTS = [
    {
        "subject": "林舟",
        "kind": "event",
        "statement": "登记桥板裂纹",
        "story_time": "未知",
        "evidence": "林舟登记桥板裂纹。",
    },
    {
        "subject": "林舟",
        "kind": "resource",
        "statement": "保管旧锤",
        "story_time": "未核实的第二天午后",
        "evidence": "他收好旧锤。",
    },
    {
        "subject": "木匠",
        "kind": "event",
        "statement": "写下桩号",
        "story_time": "未知",
        "evidence": "木匠把桩号写在木牌上。",
    },
]
stopped_candidate = stopped_candidate_fixture


@pytest.fixture
def candidate(domain, story):
    original = domain.save(story["chapter_id"], 0, ORIGINAL)
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": original["revision"],
        },
        "chapter_draft",
        {
            "text": BODY,
            "review_passed": True,
            "review": {"checks": [], "findings": [], "summary": "工程样例，事实仍由作者核对。"},
            "memory": {"facts": deepcopy(FACTS), "summary": "桥头登记记录。"},
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    return original, artifact


def accept(client, artifact, **changes):
    if (
        changes.get("memory_fact_indices") is not None
        and "memory_facts_sha256" not in changes
        and artifact.get("memory_facts_sha256")
    ):
        changes["memory_facts_sha256"] = artifact["memory_facts_sha256"]
    return client.post(
        f"/api/artifacts/{artifact['id']}/accept",
        json={
            "idempotency_key": "explicit-memory-selection",
            "candidate_revision": artifact["candidate_revision"],
            **changes,
        },
    )


def test_author_can_accept_prose_while_explicitly_omitting_all_unverified_memory(client, domain, candidate):
    original, artifact = candidate
    before = deepcopy(artifact["payload"])
    response = accept(
        client,
        artifact,
        memory_fact_indices=[],
        memory_selection_reason="先保留正文，事实与时点仍待逐项核查。",
    )
    assert response.status_code == 200, response.text
    assert domain.chapter(artifact["chapter_id"])["content"] == BODY
    assert domain.chapter(artifact["chapter_id"])["revision"] == original["revision"] + 1
    assert domain.db.all("SELECT * FROM facts") == []
    assert domain.artifact(artifact["id"])["payload"] == before
    selection = response.json()["memory_selection"]
    assert selection["fact_count"] == 3
    assert selection["selected_indices"] == [] and selection["omitted_indices"] == [0, 1, 2]
    assert selection["version"] == "candidate-memory-selection/1"
    assert selection["candidate_revision"] == artifact["candidate_revision"]


@pytest.mark.parametrize("mode", ["omitted", "null", "explicit_all"])
def test_default_and_explicit_all_keep_the_original_all_facts_policy(client, domain, candidate, mode):
    _, artifact = candidate
    change = {} if mode == "omitted" else {"memory_fact_indices": None if mode == "null" else [0, 1, 2]}
    response = accept(client, artifact, **change)
    assert response.status_code == 200, response.text
    assert [row["statement"] for row in domain.db.all("SELECT * FROM facts ORDER BY rowid")] == [
        fact["statement"] for fact in FACTS
    ]
    assert response.json()["memory_selection"]["selected_indices"] == [0, 1, 2]
    assert response.json()["memory_selection"]["omitted_indices"] == []
    assert response.json()["memory_selection"]["reason"] == ""


def test_partial_selection_writes_only_original_order_facts_and_keeps_original_candidate(
    client, domain, candidate
):
    _, artifact = candidate
    response = accept(
        client,
        artifact,
        memory_fact_indices=[2, 0],
        memory_selection_reason="  中间事实的时点未经核实，保留待核对。  ",
    )
    assert response.status_code == 200, response.text
    facts = domain.db.all("SELECT * FROM facts ORDER BY rowid")
    assert [row["statement"] for row in facts] == [FACTS[0]["statement"], FACTS[2]["statement"]]
    assert all(row["story_time"] == "未知" for row in facts)
    assert response.json()["memory_selection"]["selected_indices"] == [0, 2]
    assert response.json()["memory_selection"]["omitted_indices"] == [1]
    assert response.json()["memory_selection"]["reason"] == "中间事实的时点未经核实，保留待核对。"
    assert domain.artifact(artifact["id"])["payload"] == artifact["payload"]
    assert (
        domain.artifact(artifact["id"])["payload"]["memory"]["facts"][1]["story_time"] == "未核实的第二天午后"
    )


@pytest.mark.parametrize("indices", [[-1], [0, 0], [True], [False], ["0"], [0.0], [None], [[0]], "0", 0])
def test_invalid_index_types_negative_and_duplicates_are_rejected_without_writes(
    client, domain, candidate, indices
):
    original, artifact = candidate
    response = accept(
        client, artifact, memory_fact_indices=indices, memory_selection_reason="非法选择不得采用。"
    )
    assert response.status_code == 422, response.text
    assert domain.chapter(artifact["chapter_id"]) == original
    assert domain.artifact(artifact["id"]) == artifact
    assert not domain.db.all("SELECT * FROM facts") and not domain.db.all("SELECT * FROM receipts")


@pytest.mark.parametrize("indices", [[3], [0, 99]])
def test_out_of_range_indices_are_rejected_against_the_original_fact_count(
    client, domain, candidate, indices
):
    original, artifact = candidate
    response = accept(
        client, artifact, memory_fact_indices=indices, memory_selection_reason="不猜测越界事实。"
    )
    assert response.status_code == 400 and response.json()["code"] == "memory_selection"
    assert domain.chapter(artifact["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM facts") and not domain.db.all("SELECT * FROM receipts")


@pytest.mark.parametrize("reason", ["", " \n\t "])
def test_omitting_any_fact_requires_a_nonempty_reason(client, domain, candidate, reason):
    original, artifact = candidate
    response = accept(client, artifact, memory_fact_indices=[0], memory_selection_reason=reason)
    assert response.status_code == 400 and response.json()["code"] == "memory_selection_reason"
    assert domain.chapter(artifact["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM receipts")


@pytest.mark.parametrize("reason", [None, True, "核" * 2001])
def test_reason_type_and_length_are_checked_before_adoption(client, domain, candidate, reason):
    original, artifact = candidate
    response = accept(client, artifact, memory_fact_indices=[], memory_selection_reason=reason)
    assert response.status_code == 422
    assert domain.chapter(artifact["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM receipts")


def test_repeat_adoption_requires_the_same_scope_and_reason_even_with_another_key(client, domain, candidate):
    original, artifact = candidate
    first = accept(
        client, artifact, memory_fact_indices=[0, 2], memory_selection_reason="中间事实继续待核对。"
    )
    assert first.status_code == 200
    facts = domain.db.all("SELECT * FROM facts ORDER BY rowid")
    repeated = accept(
        client, artifact, memory_fact_indices=[2, 0], memory_selection_reason="  中间事实继续待核对。  "
    )
    assert repeated.status_code == 200 and repeated.json() == first.json()
    same_scope_new_key = accept(
        client,
        artifact,
        idempotency_key="same-scope-another-key",
        memory_fact_indices=[0, 2],
        memory_selection_reason="中间事实继续待核对。",
    )
    assert same_scope_new_key.status_code == 200 and same_scope_new_key.json() == first.json()
    for changes in [
        {"memory_fact_indices": [0], "memory_selection_reason": "中间事实继续待核对。"},
        {"memory_fact_indices": [0, 2], "memory_selection_reason": "理由也已改变。"},
        {"memory_fact_indices": None},
        {"memory_fact_indices": []},
        {
            "idempotency_key": "changed-scope-new-key",
            "memory_fact_indices": [],
            "memory_selection_reason": "新key也不能重用已采用候选来更换事实范围。",
        },
    ]:
        denied = accept(client, artifact, **changes)
        assert denied.status_code == 409 and denied.json()["code"] == "idempotency", denied.text
    assert domain.chapter(artifact["chapter_id"])["revision"] == original["revision"] + 1
    assert domain.db.all("SELECT * FROM facts ORDER BY rowid") == facts
    assert len(domain.db.all("SELECT * FROM receipts")) == 1


@pytest.mark.parametrize("encoding", ["no_selection", "selection_without_hash"])
def test_old_receipts_only_replay_their_original_all_fact_scope_without_being_rewritten(
    client, domain, candidate, encoding
):
    _, artifact = candidate
    first = accept(client, artifact)
    assert first.status_code == 200
    old = {key: value for key, value in first.json().items() if key != "memory_selection"}
    if encoding == "selection_without_hash":
        old["memory_selection"] = {
            key: value
            for key, value in first.json()["memory_selection"].items()
            if key != "memory_facts_sha256"
        }
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE receipts SET result=? WHERE artifact_id=?", (dumps(old), artifact["id"]))
    replay = accept(client, artifact)
    assert replay.status_code == 200 and replay.json() == old
    denied = accept(
        client, artifact, memory_fact_indices=[], memory_selection_reason="旧全选回执不能冒充本次零事实选择。"
    )
    assert denied.status_code == 409 and denied.json()["code"] == "idempotency"
    assert (
        json.loads(
            domain.db.one("SELECT result FROM receipts WHERE artifact_id=?", (artifact["id"],))["result"]
        )
        == old
    )
    assert len(domain.db.all("SELECT * FROM facts")) == 3


def test_author_edits_during_candidate_review_still_block_all_adoption(client, domain, candidate):
    original, artifact = candidate
    newer = domain.save(artifact["chapter_id"], original["revision"], "作者并行继续写作，不允许覆盖。😀")
    response = accept(client, artifact, memory_fact_indices=[], memory_selection_reason="暂不采用事实。")
    assert response.status_code == 409 and response.json()["code"] in {"story_changed", "revision_conflict"}
    assert domain.chapter(artifact["chapter_id"]) == newer
    assert not domain.db.all("SELECT * FROM facts") and not domain.db.all("SELECT * FROM receipts")


def test_stale_candidate_revision_cannot_be_adopted_with_empty_memory_selection(client, domain, candidate):
    original, artifact = candidate
    updated = client.patch(
        f"/api/artifacts/{artifact['id']}",
        json={"candidate_revision": 0, "text": BODY + "\n作者新改的候选。"},
    )
    assert updated.status_code == 200
    response = accept(
        client, artifact, memory_fact_indices=[], memory_selection_reason="旧候选的选择不能覆盖新候选。"
    )
    assert response.status_code == 409 and response.json()["code"] == "candidate_conflict"
    assert domain.chapter(artifact["chapter_id"]) == original
    assert domain.artifact(artifact["id"]) == updated.json()


def test_selection_does_not_bypass_automatic_review_requirement(domain, candidate):
    original, artifact = candidate
    with domain.db.connect(True) as tx:
        payload = {**artifact["payload"], "review_passed": False}
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    with pytest.raises(DomainError, match="尚未通过"):
        domain.accept(
            artifact["id"],
            "review-required-even-with-no-facts",
            require_review=True,
            memory_fact_indices=[],
            memory_selection_reason="零事实也不能绕过正文核对。",
        )
    assert domain.chapter(artifact["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM receipts")


def test_concurrent_different_selections_cannot_both_write_or_create_two_revisions(domain, candidate):
    original, artifact = candidate
    barrier = Barrier(2)

    def attempt(index):
        barrier.wait()
        try:
            receipt = domain.accept(
                artifact["id"],
                "concurrent-same-memory-key",
                memory_fact_indices=[index],
                memory_selection_reason=f"仅采用第{index}项，其余保留候选。",
                memory_facts_sha256=artifact["memory_facts_sha256"],
            )
            return receipt
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 2]))
    winners = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, DomainError)]
    assert len(winners) == len(failures) == 1
    assert failures[0].code == "idempotency"
    assert len(domain.db.all("SELECT * FROM receipts")) == 1
    assert domain.chapter(artifact["chapter_id"])["revision"] == original["revision"] + 1
    assert len(domain.db.all("SELECT * FROM facts")) == 1


def test_selected_fact_failure_rolls_back_body_revision_earlier_facts_and_receipt(client, domain, candidate):
    original, artifact = candidate
    payload = deepcopy(artifact["payload"])
    payload["memory"]["facts"][2]["evidence"] = "这段引文不在候选正文。"
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), artifact["id"]))
    artifact = domain.artifact(artifact["id"])
    response = accept(
        client, artifact, memory_fact_indices=[0, 2], memory_selection_reason="保留中间未核实事实。"
    )
    assert response.status_code == 400 and response.json()["code"] == "evidence"
    assert domain.chapter(artifact["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM facts") and not domain.db.all("SELECT * FROM receipts")
    assert domain.artifact(artifact["id"])["status"] == "candidate"


@pytest.mark.parametrize("kind", ["memory", "outline", "review", "candidate_recheck", "story_extraction"])
def test_other_artifact_kinds_cannot_use_text_memory_selection(client, domain, story, kind):
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": 0,
        },
        kind,
        {},
        {},
    )
    response = accept(
        client, artifact, memory_fact_indices=[], memory_selection_reason="不应应用到非正文产物。"
    )
    assert response.status_code == 400 and response.json()["code"] == "memory_selection_kind"
    assert domain.artifact(artifact["id"]) == artifact
    assert not domain.db.all("SELECT * FROM receipts")


def test_text_patch_can_select_facts_against_the_final_patched_body(client, domain, story):
    old = "前段。林舟握住旧锤。后段。"
    saved = domain.save(story["chapter_id"], 0, old)
    selected = "林舟握住旧锤。"
    start = len("前段。".encode("utf-16-le")) // 2
    end = start + len(selected.encode("utf-16-le")) // 2
    artifact = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": story["chapter_id"],
            "base_revision": saved["revision"],
        },
        "text_patch",
        {
            "text": "林舟登记桥板裂纹。",
            "original": selected,
            "start": start,
            "end": end,
            "memory": {"facts": deepcopy(FACTS)},
        },
        {},
    )
    response = accept(
        client, artifact, memory_fact_indices=[0], memory_selection_reason="仅采用此处有对应原文的登记事实。"
    )
    assert response.status_code == 200, response.text
    assert domain.chapter(story["chapter_id"])["content"] == "前段。林舟登记桥板裂纹。后段。"
    assert [row["statement"] for row in domain.db.all("SELECT * FROM facts")] == [FACTS[0]["statement"]]


def test_selection_receipt_remains_readable_after_history_restore_and_replay_does_not_rewrite_prose(
    client, domain, candidate
):
    original, artifact = candidate
    endpoint = f"/api/artifacts/{artifact['id']}/acceptance"
    assert client.get(endpoint).json() == {"artifact_id": artifact["id"], "receipt": None}
    response = accept(
        client, artifact, memory_fact_indices=[0, 2], memory_selection_reason="未核实时点的事实保留在原候选。"
    )
    assert response.status_code == 200
    history = domain.db.one(
        "SELECT id FROM revisions WHERE chapter_id=? AND revision=?", (original["id"], original["revision"])
    )
    restored = domain.restore(original["id"], response.json()["revision"], history["id"])
    assert restored["content"] == ORIGINAL
    assert restored["revision"] == response.json()["revision"] + 1
    assert client.get(endpoint).json() == {"artifact_id": artifact["id"], "receipt": response.json()}
    repeated = accept(
        client, artifact, memory_fact_indices=[0, 2], memory_selection_reason="未核实时点的事实保留在原候选。"
    )
    assert repeated.json() == response.json()
    assert domain.chapter(original["id"]) == restored
    assert all(row["stale"] == 1 for row in domain.db.all("SELECT * FROM facts"))
    assert domain.artifact(artifact["id"])["payload"] == artifact["payload"]
    assert client.get("/api/artifacts/nonexistent-memory-candidate/acceptance").status_code == 404


def test_explicit_nonempty_selection_requires_a_server_basis_even_when_selecting_every_fact(
    client, domain, candidate
):
    original, artifact = candidate
    response = accept(client, artifact, memory_fact_indices=[0, 1, 2], memory_facts_sha256=None)
    assert response.status_code == 409 and response.json()["code"] == "memory_selection_basis"
    assert domain.chapter(artifact["chapter_id"]) == original
    assert not domain.db.all("SELECT * FROM receipts")


@pytest.mark.parametrize("bad", ["bad", "g" * 64, "0" * 63, True, 12])
def test_invalid_basis_shape_is_rejected_at_the_api_boundary(client, domain, candidate, bad):
    original, artifact = candidate
    response = accept(
        client,
        artifact,
        memory_fact_indices=[0],
        memory_selection_reason="保留其余事实。",
        memory_facts_sha256=bad,
    )
    assert response.status_code == 422
    assert domain.chapter(artifact["chapter_id"]) == original


def test_empty_selection_needs_no_hash_but_checks_any_provided_hash(client, domain, candidate):
    original, artifact = candidate
    wrong = accept(
        client,
        artifact,
        memory_fact_indices=[],
        memory_selection_reason="全部待核对。",
        memory_facts_sha256="0" * 64,
    )
    assert wrong.status_code == 409 and wrong.json()["code"] == "memory_changed"
    assert domain.chapter(artifact["chapter_id"]) == original
    accepted = accept(
        client,
        artifact,
        memory_fact_indices=[],
        memory_selection_reason="全部待核对。",
        memory_facts_sha256=None,
    )
    assert accepted.status_code == 200
    assert accepted.json()["memory_selection"]["memory_facts_sha256"] == artifact["memory_facts_sha256"]
    assert not domain.db.all("SELECT * FROM facts")


def test_fact_hash_ignores_object_key_order_but_preserves_array_order_and_source_fields():
    payload = {"memory": {"facts": deepcopy(FACTS)}}
    reordered_keys = {"memory": {"facts": [dict(reversed(list(fact.items()))) for fact in FACTS]}}
    assert facts_sha256(payload) == facts_sha256(reordered_keys)
    reordered_facts = {"memory": {"facts": list(reversed(deepcopy(FACTS)))}}
    assert facts_sha256(payload) != facts_sha256(reordered_facts)
    changed_time = deepcopy(payload)
    changed_time["memory"]["facts"][0]["story_time"] = "另一次未核实时点"
    assert facts_sha256(payload) != facts_sha256(changed_time)


def test_hex_tokens_normalize_to_the_server_hash_in_the_receipt(client, candidate):
    _, artifact = candidate
    response = accept(
        client,
        artifact,
        memory_fact_indices=[0, 1, 2],
        memory_facts_sha256=artifact["memory_facts_sha256"].upper(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["memory_selection"]["memory_facts_sha256"] == artifact["memory_facts_sha256"]


def test_artifact_get_and_list_expose_only_derived_hash_without_rewriting_payload(client, domain, candidate):
    _, artifact = candidate
    single = client.get(f"/api/artifacts/{artifact['id']}").json()
    listing = client.get(f"/api/artifacts?branch_id={artifact['branch_id']}").json()
    listed = next(row for row in listing if row["id"] == artifact["id"])
    assert single["memory_facts_sha256"] == listed["memory_facts_sha256"] == facts_sha256(artifact["payload"])
    assert single["payload"] == artifact["payload"]
    assert "memory_facts_sha256" not in json.loads(
        domain.db.one("SELECT payload FROM artifacts WHERE id=?", (artifact["id"],))["payload"]
    )


def test_historical_receipt_is_not_recomputed_if_saved_candidate_data_later_changes(
    client, domain, candidate
):
    _, artifact = candidate
    accepted = accept(client, artifact, memory_fact_indices=[], memory_selection_reason="全部事实保留候选。")
    assert accepted.status_code == 200
    changed = deepcopy(artifact["payload"])
    changed["memory"]["facts"][0]["story_time"] = "隔离故障注入后的新时点"
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(changed), artifact["id"]))
    assert domain.artifact(artifact["id"])["memory_facts_sha256"] != artifact["memory_facts_sha256"]
    response = client.get(f"/api/artifacts/{artifact['id']}/acceptance")
    assert response.json()["receipt"] == accepted.json()
    assert (
        response.json()["receipt"]["memory_selection"]["memory_facts_sha256"]
        == artifact["memory_facts_sha256"]
    )


def test_real_recheck_changes_memory_without_text_revision_and_old_selection_is_rejected(
    settings, client, domain, stopped_candidate
):
    parent, source = stopped_candidate
    first = queue_recheck(client, source)
    assert first.status_code == 202
    asyncio.run(Worker(settings, RecheckModel()).run_once())
    first_task = domain.task(first.json()["id"])
    assert first_task["status"] == "succeeded", first_task["error"]
    seen = domain.artifact(source["id"])
    assert seen["payload"]["memory"]["facts"]
    second = queue_recheck(client, seen, client_request_id="second-memory-recheck-basis")
    assert second.status_code == 202

    class DifferentMemory(RecheckModel):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
            if instruction.get("动作") == "从正文提取事实候选与章节摘要":
                self.actions.append(instruction["动作"])
                return {
                    "text": dumps(
                        {
                            "facts": [
                                {
                                    "subject": "沈砚",
                                    "kind": "event",
                                    "statement": "第二次核对重新生成的候选记录",
                                    "story_time": "未知",
                                    "evidence": instruction["可引用原文"][0]["source_id"],
                                }
                            ],
                            "summary": "新记忆候选仍需作者核对。",
                        }
                    ),
                    "usage": {"input": 1, "output": 1},
                    "call_id": "offline-changed-memory-basis",
                }
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    asyncio.run(Worker(settings, DifferentMemory()).run_once())
    second_task = domain.task(second.json()["id"])
    assert second_task["status"] == "succeeded", second_task["error"]
    fresh = domain.artifact(source["id"])
    assert fresh["candidate_revision"] == seen["candidate_revision"]
    assert fresh["payload"]["text"] == seen["payload"]["text"]
    assert fresh["memory_facts_sha256"] != seen["memory_facts_sha256"]
    response = accept(client, seen, memory_fact_indices=[0])
    assert response.status_code == 409 and response.json()["code"] == "memory_changed"
    assert domain.chapter(source["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT * FROM facts") and not domain.db.all("SELECT * FROM receipts")
    assert domain.artifact(source["id"]) == fresh


@pytest.mark.parametrize("indices", [None, []])
def test_old_empty_memory_hash_blocks_new_recheck_facts_even_for_legacy_none_or_zero_choice(
    settings, client, domain, stopped_candidate, indices
):
    _, source = stopped_candidate
    seen = domain.artifact(source["id"])
    assert seen["payload"].get("memory", {}).get("facts", []) == []
    queued = queue_recheck(client, seen)
    assert queued.status_code == 202
    asyncio.run(Worker(settings, RecheckModel()).run_once())
    task = domain.task(queued.json()["id"])
    assert task["status"] == "succeeded", task["error"]
    fresh = domain.artifact(source["id"])
    assert fresh["payload"]["memory"]["facts"]
    assert fresh["candidate_revision"] == seen["candidate_revision"]
    assert fresh["memory_facts_sha256"] != seen["memory_facts_sha256"]
    response = accept(
        client,
        seen,
        memory_fact_indices=indices,
        memory_facts_sha256=seen["memory_facts_sha256"],
        memory_selection_reason="我看到的事实列表为空，不能替换成复检新生成的事实。",
    )
    assert response.status_code == 409 and response.json()["code"] == "memory_changed"
    assert domain.chapter(source["chapter_id"])["revision"] == 0
    assert not domain.db.all("SELECT * FROM facts") and not domain.db.all("SELECT * FROM receipts")
    assert domain.artifact(source["id"]) == fresh
