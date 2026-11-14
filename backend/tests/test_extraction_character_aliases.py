"""Character identity format is repaired before staging, never silently cleaned."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.worker import Worker
from test_story_extraction import ExtractionModel, queue


class AliasModel(ExtractionModel):
    def __init__(self, variants, target=None, update_fields=None):
        super().__init__()
        self.variants = deepcopy(variants)
        self.returned = []
        self.target = target
        self.update_fields = update_fields

    async def generate(self, *args, **kwargs):
        output = await super().generate(*args, **kwargs)
        value = json.loads(output["text"])
        item = value["records"][0]
        item["record"].update(profile="", notes="")
        item["record"].update(self.variants[min(len(self.returned), len(self.variants) - 1)])
        if self.target:
            item.update(
                target_id=self.target["id"],
                target_revision=self.target["revision"],
                update_fields=self.update_fields,
            )
        item["reasoning"] = f"仅本项原文；第{len(self.returned) + 1}次候选，身份仍待作者核对。"
        value["records"] = [item]
        self.returned.append(deepcopy(value))
        return {**output, "text": dumps(value)}


def run_candidate(client, domain, settings, story, model):
    source = domain.save(
        story["chapter_id"], 0, "宁禾来到渡口。钱叔叫她小宁，她应了一声，也请他叫自己禾姑娘。"
    )
    records = deepcopy(domain.db.all("SELECT * FROM story_records"))
    task = queue(client, domain, story)
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    asyncio.run(worker.module())
    saved = domain.task(task["id"])
    assert saved["status"] == "succeeded", saved["error"]
    assert domain.chapter(story["chapter_id"]) == source
    assert domain.db.all("SELECT * FROM story_records") == records
    assert domain.db.all("SELECT * FROM facts") == []
    return domain.artifact(saved["result"]["artifact_id"]), task


def repairs(domain, task):
    return [
        json.loads(row["payload"])
        for row in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='citation_repair' ORDER BY id",
            (task["id"],),
        )
    ]


def character(client, domain, story):
    response = client.post(
        f"/api/branches/{story['branch_id']}/story-records",
        json={
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
            "record": {"kind": "character", "title": "宁禾", "aliases": ["小宁"], "notes": "原备注"},
        },
    )
    assert response.status_code == 201, response.text
    return next(row for row in response.json()["records"] if row["kind"] == "character")


def test_name_repeated_as_alias_is_repaired_before_staging(client, domain, settings, story):
    model = AliasModel([{"aliases": ["宁禾"]}, {"aliases": ["小宁"]}])
    artifact, task = run_candidate(client, domain, settings, story, model)
    assert len(model.calls) == 2
    assert artifact["payload"]["records"][0]["record"]["aliases"] == ["小宁"]
    assert model.calls[1]["任务"]["前次产物"]["records"][0]["record"]["aliases"] == ["宁禾"]
    assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.db.all("SELECT * FROM facts") == []
    issue = "；".join(repairs(domain, task)[0]["issues"])
    assert "records[0](local_id=n).record.title/aliases" in issue
    assert "本项原文" in issue and "不改名" in issue
    assert model.calls[0]["任务"]["身份格式契约版本"] == "character-name-format/1"


@pytest.mark.parametrize(
    "aliases",
    [["小宁", "小宁"], [" "], [""], ["别" * 201]],
    ids=["repeated-aliases", "whitespace", "empty", "over-200"],
)
def test_bad_alias_formats_get_one_bounded_model_correction(client, domain, settings, story, aliases):
    model = AliasModel([{"aliases": aliases}, {"aliases": ["小宁"]}])
    artifact, task = run_candidate(client, domain, settings, story, model)
    assert len(model.calls) == 2
    assert len(repairs(domain, task)) == 1
    assert artifact["payload"]["records"][0]["record"]["aliases"] == ["小宁"]
    assert model.calls[1]["任务"]["前次产物"]["records"][0]["record"]["aliases"] == aliases


def test_whitespace_name_is_not_silently_normalized(client, domain, settings, story):
    model = AliasModel([{"title": "  "}, {"title": "宁禾"}])
    artifact, task = run_candidate(client, domain, settings, story, model)
    assert len(model.calls) == 2 and len(repairs(domain, task)) == 1
    assert artifact["payload"]["records"][0]["record"]["title"] == "宁禾"
    assert model.calls[1]["任务"]["前次产物"]["records"][0]["record"]["title"] == "  "


def test_three_invalid_rounds_stop_without_artifact_or_formal_writes(client, domain, settings, story):
    source = domain.save(story["chapter_id"], 0, "宁禾来到渡口，钱叔叫她小宁。")
    task = queue(client, domain, story)
    model = AliasModel([{"aliases": ["宁禾"]}])
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError, match="两轮引用修正后仍未通过校验") as stopped:
        asyncio.run(worker.module())
    assert stopped.value.code == "evidence"
    assert "records[0](local_id=n).record.title/aliases" in str(stopped.value)
    assert len(model.calls) == 3
    assert [row["round"] for row in repairs(domain, task)] == [0, 1, 2]
    assert domain.db.all("SELECT * FROM artifacts") == []
    assert domain.db.all("SELECT * FROM story_records") == []
    assert domain.db.all("SELECT * FROM facts") == []
    assert domain.chapter(story["chapter_id"]) == source


def test_valid_literal_aliases_keep_spelling_and_are_not_trimmed_or_deduplicated(
    client, domain, settings, story
):
    aliases = ["小宁", "小宁 ", "Ning", "ning", "称" * 200]
    model = AliasModel([{"aliases": aliases}])
    artifact, task = run_candidate(client, domain, settings, story, model)
    assert len(model.calls) == 1 and repairs(domain, task) == []
    assert artifact["payload"]["records"][0]["record"]["aliases"] == aliases


@pytest.mark.parametrize(
    ("variant", "fields", "expected_aliases"),
    [
        ({"aliases": ["宁禾"], "notes": "应声后报出称呼"}, ["notes"], ["小宁"]),
        ({"title": "  ", "aliases": ["禾姑娘"]}, ["aliases"], ["禾姑娘"]),
    ],
    ids=["unselected-raw-aliases", "unselected-raw-title"],
)
def test_unselected_identity_fields_use_formal_values_without_format_repairs(
    client, domain, settings, story, variant, fields, expected_aliases
):
    target = character(client, domain, story)
    before = deepcopy(target)
    model = AliasModel([variant], target=target, update_fields=fields)
    artifact, task = run_candidate(client, domain, settings, story, model)
    item = artifact["payload"]["records"][0]
    assert len(model.calls) == 1 and repairs(domain, task) == []
    assert item["target_id"] == target["id"] and item["update_fields"] == fields
    assert item["record"]["title"] == before["payload"]["title"]
    assert item["record"]["aliases"] == expected_aliases
    assert item["previous_record"]["record"] == before["payload"]


def test_selected_aliases_compare_against_effective_formal_name(client, domain, settings, story):
    target = character(client, domain, story)
    model = AliasModel(
        [
            {"title": "未选的临时称呼", "aliases": ["宁禾"]},
            {"title": "未选的临时称呼", "aliases": ["禾姑娘"]},
        ],
        target=target,
        update_fields=["aliases"],
    )
    artifact, task = run_candidate(client, domain, settings, story, model)
    item = artifact["payload"]["records"][0]
    assert len(model.calls) == 2 and len(repairs(domain, task)) == 1
    assert item["record"]["title"] == "宁禾"
    assert item["record"]["aliases"] == ["禾姑娘"]
    assert model.calls[1]["任务"]["前次产物"]["records"][0]["record"]["title"] == "未选的临时称呼"
