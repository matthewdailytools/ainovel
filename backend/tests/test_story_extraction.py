import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from ainovel.db import DomainError, dumps
from ainovel.worker import Worker


class ExtractionModel:
    def __init__(self, invalid=False):
        self.calls = []
        self.invalid = invalid

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        value = json.loads(prompt.split("\n只返回")[0])
        self.calls.append(value)
        instruction = value["任务"]
        if "原始任务" in instruction:
            instruction = instruction["原始任务"]
        units = instruction["可引用原文"]
        unit = units[0]
        source = {k: unit[k] for k in ("chapter_id", "revision", "start", "end")}
        source["quote"] = "unknown source" if self.invalid else unit["source_id"]
        common = {"notes": "", "volume": "第一卷", "sources": [source], "depends_on": []}
        rows = [
            ("n", dict(common, kind="character", title="宁禾", aliases=["小宁"], profile="渡口来客")),
            ("q", dict(common, kind="character", title="钱叔", aliases=[], profile="掌柜")),
            (
                "e",
                dict(
                    common,
                    kind="event",
                    title="认出暗号",
                    thread="渡口线",
                    story_order=10,
                    story_time="上午",
                    status="actual",
                    participants=["n", "q"],
                    after=[],
                ),
            ),
            (
                "s",
                dict(
                    common,
                    kind="state",
                    title="暗号认知",
                    character_id="n",
                    event_id="e",
                    category="knowledge",
                    key="暗号",
                    value="已知",
                    audience="character",
                    truth="actual",
                    until_event_id=None,
                ),
            ),
            (
                "p",
                dict(
                    common,
                    kind="promise",
                    title="铜牌来历",
                    thread="渡口线",
                    stages=[
                        dict(status="planted", event_id="e", sources=[source], note="只确认已出现的疑问")
                    ],
                ),
            ),
            (
                "r",
                dict(
                    common,
                    kind="relationship",
                    title="来客与掌柜",
                    from_id="n",
                    to_id="q",
                    relation="相识",
                    event_id="e",
                    truth="actual",
                ),
            ),
        ]
        result = {
            "records": [
                dict(local_id=i, record=p, reasoning="依据所选原文，身份及作用仍需作者核对") for i, p in rows
            ],
            "unknowns": ["铜牌来历未提供"],
            "coverage": "仅本批原文",
        }
        return {"text": dumps(result), "usage": {"input": 1, "output": 1}, "call_id": "offline-extraction"}


class UpdateModel(ExtractionModel):
    async def generate(self, *args, **kwargs):
        result = await super().generate(*args, **kwargs)
        value = json.loads(result["text"])
        target = next(r for r in self.calls[-1]["任务"]["已有记录索引"] if r["kind"] == "character")
        proposal = value["records"][0]
        proposal.update(target_id=target["id"], target_revision=target["revision"], update_fields=["aliases"])
        proposal["record"]["aliases"] = ["小宁", "禾姑娘"]
        proposal["record"]["profile"] = "此字段不能覆盖原档案"
        value["records"] = [proposal]
        result["text"] = dumps(value)
        return result


@pytest.fixture
def extraction_source(domain, story):
    chapter = domain.save(story["chapter_id"], 0, "😀宁禾看懂暗号，钱叔收起铜牌。")
    return chapter


def queue(client, domain, story):
    request = {
        "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "chapter_ids": [story["chapter_id"]],
        "account": "deepseek",
        "client_request_id": uuid4().hex,
    }
    response = client.post(f"/api/branches/{story['branch_id']}/story-extractions", json=request)
    assert response.status_code == 202, response.text
    return response.json()


def produce(client, domain, settings, story, model=None):
    task = queue(client, domain, story)
    model = model or ExtractionModel()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    asyncio.run(worker.module())
    saved = domain.task(task["id"])
    assert saved["status"] == "succeeded", saved["error"]
    return domain.artifact(saved["result"]["artifact_id"]), model


def adoption(domain, story, artifact):
    return {
        "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        "candidate_revision": artifact["candidate_revision"],
        "selected_ids": [r["id"] for r in artifact["payload"]["records"]],
        "idempotency_key": uuid4().hex,
    }


def test_extraction_is_grounded_and_only_explicit_atomic_adoption_writes(
    client, domain, settings, story, extraction_source
):
    original = deepcopy(domain.chapter(story["chapter_id"]))
    artifact, model = produce(client, domain, settings, story)
    assert domain.db.all("SELECT * FROM story_records") == []
    assert artifact["payload"]["records"][0]["record"]["sources"][0]["quote"] == original["content"]
    assert model.calls[0]["上下文"]["coverage"].startswith("仅本批")
    body = adoption(domain, story, artifact)
    endpoint = f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt"
    response = client.post(endpoint, json=body)
    assert response.status_code == 200, response.text
    assert client.post(endpoint, json=body).json() == response.json()
    assert len(domain.db.all("SELECT * FROM story_records")) == 6
    assert domain.chapter(story["chapter_id"]) == original
    assert domain.db.all("SELECT * FROM facts") == []
    body["selected_ids"] = body["selected_ids"][:1]
    assert client.post(endpoint, json=body).status_code == 409


def test_missing_selected_dependencies_and_generic_accept_are_rejected(
    client, domain, settings, story, extraction_source
):
    artifact, _ = produce(client, domain, settings, story)
    body = adoption(domain, story, artifact)
    body["selected_ids"] = [
        next(r["id"] for r in artifact["payload"]["records"] if r["record"]["kind"] == "state")
    ]
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt", json=body
    )
    assert response.status_code == 409 and response.json()["code"] == "story_dependencies"
    assert domain.db.all("SELECT * FROM story_records") == []
    response = client.post(
        f"/api/artifacts/{artifact['id']}/accept",
        json={"idempotency_key": uuid4().hex, "candidate_revision": 0},
    )
    assert response.status_code == 409 and response.json()["code"] == "story_confirmation_required"


def test_changed_source_stops_queued_extraction_before_model(
    client, domain, settings, story, extraction_source
):
    queue(client, domain, story)
    domain.save(story["chapter_id"], 1, "作者后来修改了原稿。")
    model = ExtractionModel()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError, match="源稿排队后改变"):
        asyncio.run(worker.module())
    assert model.calls == []


def test_invalid_grounding_has_finite_repairs_and_never_stages(
    client, domain, settings, story, extraction_source
):
    queue(client, domain, story)
    model = ExtractionModel(invalid=True)
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError, match="引用修正"):
        asyncio.run(worker.module())
    assert (
        len(model.calls) == 2
    )  # Identical repair input reuses the persisted step, without a third billable call.
    assert domain.db.all("SELECT * FROM artifacts WHERE kind='story_extraction'") == []


def test_extraction_receives_adopted_author_and_genre_contracts(
    client, domain, settings, story, extraction_source
):
    from test_author_contract import adopt_intent
    from test_genre_integration import adopt_reader

    adopt_intent(domain, story)
    adopt_reader(domain, story)
    _, model = produce(client, domain, settings, story)
    context = model.calls[0]["上下文"]
    assert context["author_contract"]["intent_contract"]["prohibitions"] == ["不战斗"]
    assert context["genre_profile"]["audience"] == "女频"
    assert context["genre_profile"]["action"] == "按保存稿反向整理长篇结构候选"
    assert context["genre_profile"]["author_exceptions"]


def test_explicit_field_update_reuses_identity_and_preserves_unlisted_profile(
    client, domain, settings, story, extraction_source
):
    from ainovel.story_graph import SaveRecord, save_record

    saved = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={
                "kind": "character",
                "title": "宁禾",
                "aliases": ["小宁"],
                "profile": "作者完整档案，应原样保留",
            },
        ),
    )["records"][0]
    artifact, model = produce(client, domain, settings, story, UpdateModel())
    proposal = artifact["payload"]["records"][0]
    assert proposal["id"] == saved["id"] and proposal["target_revision"] == 0
    assert proposal["previous_record"]["record"]["profile"] == "作者完整档案，应原样保留"
    assert proposal["record"]["profile"] == "作者完整档案，应原样保留"
    assert "作者完整档案，应原样保留" not in dumps(model.calls)
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt",
        json=adoption(domain, story, artifact),
    )
    assert response.status_code == 200, response.text
    records = domain.db.all("SELECT * FROM story_records")
    assert len(records) == 1 and records[0]["id"] == saved["id"] and records[0]["revision"] == 1
    current = json.loads(records[0]["payload"])
    assert current["aliases"] == ["小宁", "禾姑娘"] and current["profile"] == "作者完整档案，应原样保留"
    assert len(domain.db.all("SELECT * FROM story_record_history WHERE record_id=?", (saved["id"],))) == 2


def test_update_and_new_record_roll_back_together_on_conflict(
    client, domain, settings, story, extraction_source
):
    from ainovel.story_graph import SaveRecord, save_record

    saved = save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={"kind": "character", "title": "宁禾", "aliases": ["小宁"], "profile": "原档案"},
        ),
    )["records"][0]

    class BadBatch(UpdateModel):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            value = json.loads(result["text"])
            bad = deepcopy(value["records"][0])
            bad.update(local_id="new-person", target_id=None, target_revision=None, update_fields=[])
            bad["record"].update(title="另一个人", aliases=["禾姑娘"])
            value["records"].append(bad)
            result["text"] = dumps(value)
            return result

    artifact, _ = produce(client, domain, settings, story, BadBatch())
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt",
        json=adoption(domain, story, artifact),
    )
    assert response.status_code == 409
    rows = domain.db.all("SELECT * FROM story_records")
    assert len(rows) == 1 and rows[0]["id"] == saved["id"] and rows[0]["revision"] == 0
    assert json.loads(rows[0]["payload"])["aliases"] == ["小宁"]
    assert len(domain.db.all("SELECT * FROM story_record_history")) == 1


def test_alias_conflict_rolls_back_entire_batch(client, domain, settings, story, extraction_source):
    from ainovel.story_graph import SaveRecord, save_record

    save_record(
        domain,
        story["branch_id"],
        SaveRecord(
            base_story_revision=domain.branch(story["branch_id"])["story_revision"],
            record={"kind": "character", "title": "宁禾", "profile": "private unused profile"},
        ),
    )
    artifact, model = produce(client, domain, settings, story)
    assert "private unused profile" not in dumps(model.calls[0])
    before = domain.db.all("SELECT * FROM story_records")
    result = client.post(
        f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt",
        json=adoption(domain, story, artifact),
    )
    assert result.status_code == 409 and result.json()["code"] == "character_alias"
    assert domain.db.all("SELECT * FROM story_records") == before
    assert domain.artifact(artifact["id"])["status"] == "candidate"


def test_mid_generation_edit_preserves_raw_result_without_staging(
    client, domain, settings, story, extraction_source
):
    queue(client, domain, story)

    class EditingModel(ExtractionModel):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            domain.save(story["chapter_id"], 1, "作者在生成期间修改。")
            return result

    model = EditingModel()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError):
        asyncio.run(worker.module())
    assert len(model.calls) == 1
    assert domain.chapter(story["chapter_id"])["content"] == "作者在生成期间修改。"
    assert domain.db.all("SELECT * FROM artifacts WHERE kind='story_extraction'") == []


def test_old_candidate_and_cross_branch_adoption_are_rejected(
    client, domain, settings, story, extraction_source
):
    artifact, _ = produce(client, domain, settings, story)
    body = adoption(domain, story, artifact)
    other = domain.create_novel("别的作品", "")
    assert (
        client.post(
            f"/api/branches/{other['branch_id']}/story-extractions/{artifact['id']}/adopt", json=body
        ).status_code
        == 404
    )
    domain.save(story["chapter_id"], 1, "更新原文")
    body["base_story_revision"] = domain.branch(story["branch_id"])["story_revision"]
    assert (
        client.post(
            f"/api/branches/{story['branch_id']}/story-extractions/{artifact['id']}/adopt", json=body
        ).status_code
        == 409
    )
    assert domain.db.all("SELECT * FROM story_records") == []
