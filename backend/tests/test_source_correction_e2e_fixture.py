"""Test-only source fixtures stay bounded to their original artificial source."""

from copy import deepcopy

import pytest

from helpers.source_correction_fixture import PROPOSE, SOURCE, VERIFY, source_correction_output


def test_explicit_factory_creates_only_stopped_artificial_scope(tmp_path):
    import pytest
    from ainovel.db import Database
    from ainovel.domain import Domain
    from ainovel import source_interpretation_correction as correction
    from helpers.source_correction_fixture import create_source_correction_fixture

    domain = Domain(Database(tmp_path / "manuscripts.sqlite"))
    with pytest.raises(ValueError):
        create_source_correction_fixture(domain)
    assert domain.db.all("SELECT id FROM novels") == []
    created = create_source_correction_fixture(domain, test_mode=True)
    parent = domain.task(created["parent_id"])
    candidate = domain.artifact(created["candidate_id"])
    assert parent["status"] == "needs_review"
    assert domain.chapter(created["chapter_id"])["content"] == ""
    assert candidate["payload"]["review_passed"] is False
    preview = correction.preview(domain, candidate["id"])
    assert preview["source_text"] == SOURCE
    assert [row["id"] for row in preview["items"]] == ["P01", "P02"]
    assert preview["items"][0]["actor"] == "林舟"
    assert preview["items"][1]["certainty"] == "ambiguous"
    assert domain.db.all("SELECT * FROM task_steps") == []
    assert domain.db.all("SELECT * FROM receipts") == []


def frame():
    return {
        "动作": PROPOSE,
        "原情节": SOURCE,
        "原解释事项": [
            {
                "item_id": "P01",
                "actor": "林舟",
                "action": "检查",
                "certainty": "explicit",
                "author_evidence": "林舟检查门锁并记录结果。",
            },
            {
                "item_id": "P02",
                "actor": "未注明",
                "action": "说明",
                "certainty": "ambiguous",
                "author_evidence": "【写作要求】先说明旧工具与刻线的对应，再写检查结果。",
            },
        ],
        "允许纠偏事项": ["P02"],
        "可引用原文": [{"source_id": "AUTHOR0001", "text": SOURCE}],
    }


def test_proposal_all_p_order_and_source_actor_are_preserved():
    source = frame()
    before = deepcopy(source)
    output = source_correction_output(source)
    assert source == before
    assert [row["item_id"] for row in output["items"]] == ["P01", "P02"]
    assert output["items"][0]["actor"] == "林舟"
    assert output["items"][0]["statement_kind"] == "story_event"
    assert output["items"][1]["actor"] == ""
    assert output["items"][1]["statement_kind"] == "narrative_requirement"
    assert output["items"][1]["certainty"] == "explicit"
    assert {row["evidence"] for row in output["items"]} == {"AUTHOR0001"}


def test_unselected_item_is_not_silently_corrected():
    source = frame()
    source["允许纠偏事项"] = ["P01"]
    output = source_correction_output(source)
    assert output["items"][1]["actor"] == "未注明"
    assert output["items"][1]["certainty"] == "ambiguous"
    assert output["items"][1]["statement_kind"] == "story_event"


def test_preservation_covers_every_p_and_fixed_without_claiming_fulfillment():
    source = frame()
    source.update(
        动作=VERIFY,
        纠偏提案=source_correction_output(source),
        原固定条件仅核对是否保持=[
            {"condition_id": "FIX001", "text": "全书结局另行核对。"},
            {"condition_id": "FIX002", "text": "本章只检查门锁。"},
        ],
    )
    output = source_correction_output(source)
    assert [row["item_id"] for row in output["item_checks"]] == ["P01", "P02"]
    assert [row["condition_id"] for row in output["fixed_checks"]] == ["FIX001", "FIX002"]
    assert all(row["condition_id"] == row["evidence"] for row in output["fixed_checks"])
    assert all("提前兑现" in row["reason"] for row in output["fixed_checks"])


def test_unrelated_story_and_invalid_source_identity_are_not_faked():
    assert source_correction_output({"动作": "审查正文"}) is None
    source = frame()
    source["原情节"] += "其他内容"
    assert source_correction_output(source) is None
    source = frame()
    source["可引用原文"][0]["text"] = "不在原来源中的引文"
    assert source_correction_output(source) is None


def test_reordered_scope_and_inconsistent_proposal_do_not_get_a_pass():
    source = frame()
    source["原解释事项"].reverse()
    assert source_correction_output(source) is None


def test_body_fixture_is_exact_and_keeps_unknown_allowed_results():
    from helpers.source_correction_fixture import BODY, source_correction_body_output

    instruction = {
        "动作": "审查正文",
        "正文": BODY,
        "可引用原文": [{"source_id": "C001P0001", "text": BODY}],
        "固定条件编号表": [
            {"condition_id": "FIX001", "scope": "book", "allowed_results": ["fail", "unknown"]}
        ],
    }
    result = source_correction_body_output(instruction)
    assert result["checks"][0]["chapter_result"] == "unknown"
    instruction["正文"] += "别的正文"
    assert source_correction_body_output(instruction) is None


@pytest.mark.parametrize("edited_candidate", [False, True])
def test_marked_source_to_ready_to_body_recheck_and_memory(tmp_path, edited_candidate):
    import asyncio
    import hashlib
    import json
    from decimal import Decimal
    from fastapi.testclient import TestClient
    from ainovel.api import create_app
    from ainovel.db import Database
    from ainovel.domain import Domain
    from ainovel.settings import Settings, Provider
    from ainovel.worker import Worker
    from helpers.source_correction_fixture import (
        BODY,
        create_source_correction_fixture,
        source_correction_body_output,
    )

    settings = Settings(
        tmp_path,
        {
            "openai_1": Provider(
                "openai_1",
                "openai",
                "test-model",
                "https://api.openai.com/v1",
                "synthetic-not-real",
                Decimal("2"),
                Decimal("8"),
            )
        },
        {"openai": Decimal("20"), "deepseek": Decimal("80")},
        test_mode=True,
    )
    domain = Domain(Database(tmp_path / "manuscripts.sqlite"))
    ids = create_source_correction_fixture(domain, test_mode=True)

    class FixtureModel:
        def __init__(self):
            self.actions = []

        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            instruction = frame["任务"]
            action = instruction["动作"]
            self.actions.append(action)
            if action in {PROPOSE, VERIFY}:
                assert "source-correction-e2e" not in json.dumps(frame, ensure_ascii=False)
                result = source_correction_output(instruction)
            else:
                result = source_correction_body_output(instruction)
            assert result is not None, action
            return {
                "text": json.dumps(result, ensure_ascii=False),
                "usage": {"input": 1, "output": 1},
                "call_id": f"fixture-{len(self.actions)}",
            }

    model = FixtureModel()
    with TestClient(create_app(settings)) as client:
        client.headers["X-Ainovel-CSRF"] = client.get("/api/session").json()["csrf"]
        if edited_candidate:
            edited = client.patch(
                f"/api/artifacts/{ids['candidate_id']}",
                json={"candidate_revision": 0, "text": BODY},
            )
            assert edited.status_code == 200, edited.text
        previous_candidate = deepcopy(domain.artifact(ids["candidate_id"]))
        previous_revision = previous_candidate["candidate_revision"]
        previous_sha256 = hashlib.sha256(previous_candidate["payload"]["text"].encode()).hexdigest()
        assert previous_revision == int(edited_candidate)
        preview = client.get(
            f"/api/artifacts/{ids['candidate_id']}/source-interpretation-correction-preview"
        ).json()
        created = client.post(
            f"/api/artifacts/{ids['candidate_id']}/source-interpretation-corrections",
            json={
                "account": "openai_1",
                "client_request_id": "source-e2e-ready",
                "candidate_revision": previous_revision,
                "item_ids": ["P02"],
                "preview_basis_sha256": preview["basis_sha256"],
            },
        )
        assert created.status_code == 202, created.text
        asyncio.run(Worker(settings, model).run_once())
        source_task = domain.task(created.json()["id"])
        assert source_task["status"] == "succeeded", source_task["error"]
        assert source_task["result"]["source_correction_state"] == "ready"
        receipt = source_task["result"]["artifact_id"]
        preview = client.get(
            f"/api/artifacts/{ids['candidate_id']}/recheck-source-correction-preview",
            params={"receipt_id": receipt},
        )
        assert preview.status_code == 200, preview.text
        created = client.post(
            f"/api/artifacts/{ids['candidate_id']}/recheck-source-correction",
            json={
                "account": "openai_1",
                "client_request_id": "body-e2e-recheck",
                "candidate_revision": previous_revision,
                "receipt_id": receipt,
                "preview_basis_sha256": preview.json()["basis_sha256"],
            },
        )
        assert created.status_code == 202, created.text
        asyncio.run(Worker(settings, model).run_once())
        body_task = domain.task(created.json()["id"])
        assert body_task["status"] == "succeeded", body_task["error"]
        snapshot = body_task["result"]["source_corrected_recheck_snapshot"]
        assert snapshot["candidate_revision"] == previous_revision
        assert snapshot["content_sha256"] == previous_sha256
        report = domain.artifact(body_task["result"]["artifact_id"])
        assert report["payload"]["previous_candidate"] == previous_candidate["payload"]
        events = domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='candidate_rechecked'",
            (ids["parent_id"],),
        )
        assert len(events) == 1
        event = json.loads(events[0]["payload"])
        assert event["artifact_id"] == ids["candidate_id"]
        assert event["review_artifact_id"] == report["id"]
        assert event["previous_candidate"] == previous_candidate["payload"]
        assert event["previous_revision"] == previous_revision
        assert event["previous_content_sha256"] == previous_sha256
        assert event["target"]["revision"] == previous_revision
        assert event["target"]["content_sha256"] == previous_sha256
        candidate = domain.artifact(ids["candidate_id"])
        assert candidate["payload"]["text"] == BODY
        assert candidate["candidate_revision"] == previous_revision
        assert candidate["payload"]["review_passed"] is True
        assert len(candidate["payload"]["memory"]["facts"]) == 1
        assert len(candidate["payload"]["review"]["book_checks_pending"]) == 1
        assert domain.chapter(ids["chapter_id"])["content"] == ""
        assert domain.task(ids["parent_id"])["status"] == "needs_review"
    assert model.actions == [
        PROPOSE,
        VERIFY,
        "审查正文",
        "逐项对照作者情节解释与本次正文",
        "从正文提取事实候选与章节摘要",
    ]
    source = frame()
    proposal = source_correction_output(source)
    proposal["items"][0]["actor"] = "新选角色"
    source.update(动作=VERIFY, 纠偏提案=proposal, 原固定条件仅核对是否保持=[])
    assert source_correction_output(source) is None


def test_factory_can_select_an_explicit_isolated_provider_id(domain):
    from helpers.source_correction_fixture import create_source_correction_fixture

    default = create_source_correction_fixture(domain, test_mode=True)
    alternate = create_source_correction_fixture(domain, test_mode=True, account="offline")
    assert domain.task(default["parent_id"])["payload"]["account"] == "openai_1"
    assert domain.task(alternate["parent_id"])["payload"]["account"] == "offline"
