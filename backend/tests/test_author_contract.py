import asyncio
import json
from copy import deepcopy

import pytest

from ainovel.author_contract import AuthorIntentFields, assemble_contract, authored_payload
from ainovel.db import DomainError, dumps
from ainovel.models import DefinitionInput, ModuleInput, WorkflowInput
from ainovel.worker import Worker


TEXT = "林舟用旧尺测量潮水，把两次读数记在纸上。" * 15
WORDS = "让读者先紧张后释然，追问是谁改了账本。交易最终失败。不战斗。结尾保住渡船。"
FIELDS = {
    "emotion_target": "先紧张后释然",
    "core_hook": "是谁改了账本",
    "direction": "调查账本后拒绝交易",
    "fixed_results": ["交易最终失败"],
    "prohibitions": ["不战斗"],
    "open_details": ["测潮工具和船边对白可以补足"],
    "ending": "保住渡船",
}


def adopt_intent(domain, story, *, text=WORDS, fields=FIELDS):
    data = DefinitionInput(
        kind="intent",
        text=text,
        intent_contract=fields,
        base_story_revision=domain.branch(story["branch_id"])["story_revision"],
    )
    artifact = domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "intent",
        authored_payload(data),
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    domain.accept(artifact["id"], "author-" + artifact["id"])
    return artifact


def request(story, chapter=None, action="writing.advise", **changes):
    return ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=chapter["id"] if chapter else None,
        base_revision=chapter["revision"] if chapter else None,
        action=action,
        instruction=WORDS,
        fixed=["不战斗"],
        account="openai_1",
        selection_start=0 if action == "writing.rewrite_selection" else None,
        selection_end=2 if action == "writing.rewrite_selection" else None,
        selection_text=TEXT[:2] if action == "writing.rewrite_selection" else None,
        **changes,
    ).model_dump()


class ContractModel:
    def __init__(self, *, repair=False, invalid_lock=False):
        self.requests = []
        self.repair, self.invalid_lock, self.failed = repair, invalid_lock, False

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        data = json.loads(prompt.split("\n只返回")[0])
        self.requests.append({**data, "system": system})
        task = data["任务"]
        action = task.get("动作", task.get("action"))
        if action == "整理作者情绪钩子与方向契约":
            output = deepcopy(FIELDS)
            if self.invalid_lock and not self.failed:
                self.failed = True
                output["fixed_results"] = ["女主最后必须成婚"]
        elif action == "全书滚动规划":
            output = {
                "chapters": [{"number": 1, "title": "潮尺", "plot": "测潮后拒绝交易", "fixed": []}],
                "ending": "保住渡船",
            }
        elif action == "审查正文":
            output = {
                "checks": [
                    {"condition": value, "result": "pass", "evidence": task["可引用原文"][0]["source_id"]}
                    for value in task["固定条件"]
                ],
                "findings": [],
                "summary": "工程替身，仅验证契约传递。",
            }
            if self.repair and not self.failed:
                self.failed = True
                output["findings"] = [
                    {
                        "chapter": task["章节号"],
                        "severity": "blocker",
                        "quote": task["可引用原文"][0]["source_id"],
                        "reason": "尺具不明",
                        "repair": "明确尺具",
                    }
                ]
        elif action == "用局部替换修订正文":
            output = {
                "edits": [
                    {
                        "original": task["可替换原文"][0]["source_id"],
                        "replacement": task["可替换原文"][0]["text"].replace("旧尺", "木尺", 1),
                    }
                ],
                "reason": "最小尺具修改",
            }
        elif action == "从正文提取事实候选与章节摘要":
            output = {"facts": [], "summary": "测潮工程样例。"}
        elif action == "整书完结检查":
            output = {
                "closed": True,
                "promises": [
                    {"condition": value, "result": "pass", "evidence": task["可引用原文"][0]["source_id"]}
                    for value in task.get("必须逐项核对的作者硬约定", []) or ["测试收束"]
                ],
                "findings": [],
                "summary": "离线替身，非创作验收。",
            }
        elif task.get("action") == "review.chapter":
            output = {
                "results": [
                    {
                        "finding_index": 0,
                        "result": "pass",
                        "reason": "已明确尺具",
                        "evidence": task["可引用原文"][0]["source_id"],
                    }
                ]
            }
        else:
            output = TEXT
        return {
            "text": dumps(output) if isinstance(output, dict) else output,
            "usage": {"input": 1, "output": 1},
            "call_id": "offline",
        }


def run(settings, domain, task, model):
    asyncio.run(Worker(settings, model).run_once())
    return domain.task(task["id"])


def assert_contract(model, frozen):
    assert model.requests
    for record in model.requests:
        assert record["上下文"]["author_contract"] == frozen
        assert "作品锁定冲突" in record["system"]


def test_preview_save_source_and_legacy_raw_text_are_distinct(client, domain, story):
    path = f"/api/branches/{story['branch_id']}/author-contract"
    initial = client.get(path).json()
    assert initial["branch_id"] == story["branch_id"] and not initial["configured"]
    payload = {
        "kind": "intent",
        "text": WORDS,
        "intent_contract": FIELDS,
        "base_story_revision": initial["story_revision"],
    }
    preview = client.post("/api/author-contract/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["preview"]
    assert client.get(path).json() == initial
    saved = client.post(f"/api/branches/{story['branch_id']}/definitions", json=payload)
    assert saved.status_code == 201
    current = client.get(path).json()
    assert current["author_text"] == WORDS and current["intent_contract"] == FIELDS
    assert current["raw_definition"]["provenance"]["origin"] == "author_form"
    assert current["source_story_revision"] == current["story_revision"] == initial["story_revision"] + 1
    assert client.post("/api/author-contract/preview", json={**payload, "kind": "world"}).status_code == 400
    assert (
        client.post(
            f"/api/branches/{story['branch_id']}/definitions", json={**payload, "kind": "world"}
        ).status_code
        == 400
    )
    raw = assemble_contract({"text": "只想写一场雨，终局尚未决定。"})
    assert raw["legacy_unstructured"]
    assert raw["author_text"] and raw["intent_contract"] == AuthorIntentFields().model_dump()


@pytest.mark.parametrize("had_intent", [False, True])
@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_submission_checks_the_contract_seen_by_user_before_creating_work(
    client, domain, story, had_intent, kind
):
    if had_intent:
        adopt_intent(domain, story)
    seen = domain.author_contract(story["branch_id"])
    basis = {key: seen[key] for key in ("source_artifact_id", "contract_version")}
    adopt_intent(domain, story, text="另一套方向", fields={"prohibitions": ["禁止离船"]})
    if kind == "module":
        result = client.post("/api/modules", json=request(story, author_contract_basis=basis))
    else:
        result = client.post(
            "/api/workflows",
            json=WorkflowInput(
                novel_id=story["id"],
                source_branch_id=story["branch_id"],
                mode="automatic",
                brief="独立测试",
                account="openai_1",
                author_contract_basis=basis,
            ).model_dump(),
        )
    assert result.status_code == 409 and result.json()["code"] == "author_contract_changed"
    assert not domain.db.all("SELECT id FROM tasks")
    assert len(domain.db.all("SELECT id FROM branches")) == 1


@pytest.mark.parametrize(
    "action",
    [
        value
        for value in ModuleInput.model_fields["action"].annotation.__args__
        if value != "revision.targeted"
    ],
)
def test_all_auxiliary_actions_receive_frozen_contract(settings, domain, story, action):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    source = adopt_intent(domain, story)
    task = domain.queue("module", request(story, chapter, action))
    model = ContractModel()
    result = run(settings, domain, task, model)
    assert result["status"] == "succeeded", result["error"]
    frozen = task["result"]["author_contract_snapshot"]
    assert frozen["source_artifact_id"] == source["id"]
    assert_contract(model, frozen)
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert artifact["basis"]["author_contract"] == frozen
    for record in model.requests:
        if record["任务"].get("动作") == "审查正文":
            assert record["任务"]["固定条件"] == ["不战斗"]
            assert "fixed_results和ending是全书约定" in record["任务"]["作者契约核对"]


@pytest.mark.parametrize("mode", ["assisted", "automatic"])
def test_both_workflows_keep_independent_contract_through_completion(client, settings, domain, story, mode):
    source = adopt_intent(domain, story)
    result = client.post(
        "/api/workflows",
        json=WorkflowInput(
            novel_id=story["id"],
            source_branch_id=story["branch_id"],
            mode=mode,
            brief="测试契约，不采用半自动正文",
            fixed=["不战斗"],
            chapters=1,
            min_chars=200,
            max_chars=600,
            account="openai_1",
            adoption="automatic",
            plots=[{"number": 1, "title": "潮尺", "plot": "测潮后拒绝交易", "fixed": []}]
            if mode == "assisted"
            else [],
        ).model_dump(),
    )
    assert result.status_code == 202
    task = result.json()
    frozen = task["result"]["author_contract_snapshot"]
    assert frozen["source_artifact_id"] != source["id"]
    assert frozen["author_text"] == WORDS and frozen["branch_id"] == task["branch_id"]
    adopt_intent(domain, story, text="源版本改动不能影响独立稿", fields={})
    model = ContractModel(repair=True)
    finished = run(settings, domain, task, model)
    assert finished["status"] == "production_complete", finished["error"]
    assert_contract(model, frozen)
    actions = {item["任务"].get("动作") for item in model.requests}
    assert {
        "按章纲写完整正文",
        "审查正文",
        "用局部替换修订正文",
        "从正文提取事实候选与章节摘要",
        "整书完结检查",
    } <= actions
    assert ("全书滚动规划" in actions) == (mode == "automatic")
    assert (
        finished["result"]["completion"]["author_contract"]["source_artifact_id"]
        == frozen["source_artifact_id"]
    )
    completion = next(item for item in model.requests if item["任务"].get("动作") == "整书完结检查")
    assert "逐项核对冻结作者契约fixed_results" in completion["任务"]["作者契约核对"]


def test_compile_preserves_raw_words_and_requires_author_confirmation(client, settings, domain, story):
    task = client.post("/api/modules", json=request(story, action="intent.compile")).json()
    model = ContractModel(invalid_lock=True)
    result = run(settings, domain, task, model)
    assert result["status"] == "succeeded", result["error"]
    assert len(model.requests) == 2  # A non-verbatim invented hard lock must be repaired.
    proposal = domain.artifact(result["result"]["artifact_id"])
    assert "text" not in proposal["payload"] and proposal["payload"]["author_text"] == WORDS
    origins = proposal["payload"]["provenance"]["field_sources"]
    assert origins["direction"][0]["origin"] == "ai_proposal"
    assert origins["fixed_results"][0]["origin"] == "author_quote"
    assert not domain.author_contract(story["branch_id"])["configured"]
    with pytest.raises(DomainError, match="表单"):
        domain.accept(proposal["id"], "do-not-auto-adopt")
    changed = {**FIELDS, "direction": "作者改成当面退还账册"}
    response = client.post(
        f"/api/branches/{story['branch_id']}/definitions",
        json={
            "kind": "intent",
            "text": WORDS,
            "intent_contract": changed,
            "intent_source_candidate_id": proposal["id"],
            "base_story_revision": domain.branch(story["branch_id"])["story_revision"],
        },
    )
    assert response.status_code == 201
    adopted = domain.author_contract(story["branch_id"])
    assert adopted["intent_contract"]["direction"] == changed["direction"]
    provenance = adopted["provenance"]
    assert provenance["source_candidate_id"] == proposal["id"]
    assert provenance["field_sources"]["direction"]["origin"] == "author_edited"
    assert provenance["field_sources"]["emotion_target"]["origin"] == "author_confirmed_proposal"


def test_compiled_source_must_belong_to_branch_and_current_story(client, settings, domain, story):
    task = domain.queue("module", request(story, action="intent.compile"))
    proposal = domain.artifact(run(settings, domain, task, ContractModel())["result"]["artifact_id"])
    other = domain.create_novel("另一部作品")
    body = {
        "kind": "intent",
        "text": WORDS,
        "intent_contract": FIELDS,
        "intent_source_candidate_id": proposal["id"],
        "base_story_revision": 0,
    }
    assert client.post(f"/api/branches/{other['branch_id']}/definitions", json=body).status_code == 409
    adopt_intent(domain, story, text="契约已改变", fields={})
    body["base_story_revision"] = domain.branch(story["branch_id"])["story_revision"]
    assert client.post(f"/api/branches/{story['branch_id']}/definitions", json=body).status_code == 409


def test_queued_old_task_and_candidate_cannot_cross_contract_change(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_intent(domain, story)
    first = domain.queue("module", request(story, chapter))
    done = run(settings, domain, first, ContractModel())
    old_candidate = done["result"]["artifact_id"]
    queued = domain.queue("module", request(story, chapter))
    adopt_intent(domain, story, fields={"prohibitions": ["不提账册"]})
    model = ContractModel()
    result = run(settings, domain, queued, model)
    assert result["status"] == "blocked_runtime" and not model.requests
    with pytest.raises(DomainError, match="版本已改变"):
        domain.accept(old_candidate, "old-direction-conflict")
    assert domain.chapter(chapter["id"])["content"] == TEXT


def test_unchanged_basis_survives_normal_save_and_legacy_tasks_gain_no_contract(
    client, settings, domain, story
):
    adopt_intent(domain, story)
    seen = domain.author_contract(story["branch_id"])
    basis = {key: seen[key] for key in ("source_artifact_id", "contract_version")}
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    task = client.post("/api/modules", json=request(story, chapter, author_contract_basis=basis)).json()
    assert task["result"]["author_contract_snapshot"]["source_story_revision"] > seen["source_story_revision"]
    model = ContractModel()
    assert run(settings, domain, task, model)["status"] == "succeeded"
    legacy = domain.queue("module", request(story, chapter))
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result='{}' WHERE id=?", (legacy["id"],))
    model = ContractModel()
    assert run(settings, domain, legacy, model)["status"] == "succeeded"
    contract = model.requests[0]["上下文"]["author_contract"]
    assert contract["contract_version"] == "legacy-unconfigured" and not contract["configured"]
    assert contract["author_text"] == "" and contract["intent_contract"] == AuthorIntentFields().model_dump()
    assert any(row["payload"].get("text") == WORDS for row in model.requests[0]["上下文"]["definitions"])
    assert all(
        "intent_contract" not in row["payload"] and "provenance" not in row["payload"]
        for row in model.requests[0]["上下文"]["definitions"]
        if row["kind"] == "intent"
    )


@pytest.mark.parametrize("kind", ["module", "workflow"])
def test_new_optional_fields_do_not_break_old_nonce_replay(client, domain, story, monkeypatch, kind):
    body = (
        request(story)
        if kind == "module"
        else WorkflowInput(
            novel_id=story["id"], mode="automatic", brief="旧任务", account="openai_1"
        ).model_dump()
    )
    for key in ("genre_profile_basis", "author_contract_basis", "source_review_id", "finding_indices"):
        body.pop(key, None)
    body["client_request_id"] = "legacy-contract-" + kind
    path = "/api/modules" if kind == "module" else "/api/workflows"
    task = client.post(path, json=body).json()
    replay = {**task["payload"], "author_contract_basis": None}
    if kind == "module":
        replay.update(source_review_id=None, finding_indices=[])
    assert domain.find_request(kind, replay)["id"] == task["id"]
    with monkeypatch.context() as patch:
        patch.setattr(domain, "find_request", lambda *_: None)
        assert domain.queue(kind, replay, task["branch_id"])["id"] == task["id"]
    assert client.post(path, json={**body, "author_contract_basis": None}).json()["id"] == task["id"]


def test_last_model_response_cannot_stage_against_a_new_contract(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_intent(domain, story)
    task = domain.queue("module", request(story, chapter))

    class ChangeDuringResponse(ContractModel):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            adopt_intent(domain, story, fields={"prohibitions": ["不再使用尺具"]})
            return result

    result = run(settings, domain, task, ChangeDuringResponse())
    assert result["status"] == "blocked_runtime"
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert domain.db.all("SELECT result FROM task_steps WHERE task_id=?", (task["id"],))
    assert domain.chapter(chapter["id"])["content"] == TEXT


def test_contract_changes_block_acknowledged_resume_and_account_continuation(client, domain, story):
    adopt_intent(domain, story)
    task = client.post(
        "/api/workflows",
        json=WorkflowInput(
            novel_id=story["id"],
            source_branch_id=story["branch_id"],
            mode="automatic",
            brief="独立契约稿",
            account="openai_1",
        ).model_dump(),
    ).json()
    assert client.post(f"/api/tasks/{task['id']}/pause").json()["status"] == "paused"
    adopt_intent(domain, {**story, "branch_id": task["branch_id"]}, fields={"prohibitions": ["不再交易"]})
    resumed = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_changes": True})
    assert resumed.status_code == 409 and resumed.json()["code"] == "author_contract_changed"
    continued = client.post(
        f"/api/tasks/{task['id']}/continue",
        json={
            "account": "openai_2",
            "client_request_id": "changed-contract-continuation",
            "reason": "更换账户也不应混用契约",
            "acknowledge_unknown": True,
        },
    )
    assert continued.status_code == 409 and continued.json()["code"] == "author_contract_changed"
    assert domain.task(task["id"])["status"] == "paused"
    assert len(domain.db.all("SELECT id FROM tasks")) == 1


def test_targeted_revision_recheck_uses_same_frozen_contract_and_genre(client, settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, "林舟核对旧尺。\n\n" + TEXT)
    adopt_intent(domain, story)
    reader = domain.stage(
        {"novel_id": story["id"], "branch_id": story["branch_id"]},
        "reader",
        {
            "text": "无CP悬疑经营",
            "audience": "女频",
            "genres": ["xianxia", "farm"],
            "mechanisms": ["plugin-20"],
            "author_exceptions": ["无CP"],
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    domain.accept(reader["id"], "reader-for-targeted")
    report = domain.stage(
        {
            "novel_id": story["id"],
            "branch_id": story["branch_id"],
            "chapter_id": chapter["id"],
            "base_revision": chapter["revision"],
        },
        "review",
        {
            "fixed": ["不新增同伴"],
            "checks": [],
            "summary": "最小修订工程夹具",
            "findings": [
                {
                    "chapter": 1,
                    "severity": "blocker",
                    "quote": "林舟核对旧尺。",
                    "reason": "尺具不明",
                    "repair": "明确为木尺",
                }
            ],
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
    )
    response = client.post(
        "/api/modules",
        json=request(story, chapter, "revision.targeted", source_review_id=report["id"], finding_indices=[0]),
    )
    assert response.status_code == 202
    task, model = response.json(), ContractModel()
    result = run(settings, domain, task, model)
    assert result["status"] == "succeeded", result["error"]
    assert_contract(model, task["result"]["author_contract_snapshot"])
    assert all(
        record["上下文"]["genre_profile"]["source_artifact_id"] == reader["id"] for record in model.requests
    )
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert artifact["payload"]["fixed"] == ["不新增同伴", "不战斗"]
    assert artifact["payload"]["revision_trace"]["selected_problems_resolved"]
    assert domain.chapter(chapter["id"])["content"] == chapter["content"]


def test_author_prohibition_is_mandatory_but_emotion_and_book_ending_are_not_chapter_checks(
    settings, domain, story
):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_intent(domain, story, fields={**FIELDS, "prohibitions": ["不战斗", "不公开账本"]})
    task = domain.queue("module", request(story, chapter, "review.chapter"))
    model = ContractModel()
    result = run(settings, domain, task, model)
    assert result["status"] == "succeeded", result["error"]
    instruction = model.requests[0]["任务"]
    assert instruction["固定条件"] == ["不战斗", "不公开账本"]
    assert FIELDS["ending"] not in instruction["固定条件"]
    assert FIELDS["emotion_target"] not in instruction["固定条件"]
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert [item["condition"] for item in artifact["payload"]["checks"]] == ["不战斗", "不公开账本"]


def test_omitted_book_contract_promise_cannot_pass_completion(settings, domain, story):
    chapter = domain.save(story["chapter_id"], 0, TEXT)
    adopt_intent(domain, story)
    domain.queue("module", request(story))

    class OmitPromise(ContractModel):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            result["text"] = dumps(
                {"closed": True, "promises": [], "findings": [], "summary": "遗漏作者约定的错误报告"}
            )
            return result

    worker = Worker(settings, OmitPromise())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    with pytest.raises(DomainError, match="两轮引用修正"):
        asyncio.run(worker.completion_report([chapter], {"brief": "简报", "fixed": []}, {}, "全书"))
    assert len(worker.client.requests) >= 2  # Repeated identical repair may reuse its saved step.


def test_accepting_generic_ideation_keeps_existing_structured_author_locks(settings, domain, story):
    source = adopt_intent(domain, story)
    before = domain.author_contract(story["branch_id"])
    task = domain.queue("module", request(story, action="ideation.explore"))
    result = run(settings, domain, task, ContractModel())
    artifact = domain.artifact(result["result"]["artifact_id"])
    assert artifact["kind"] == "advice"
    domain.accept(artifact["id"], "save-ideation-as-reference")
    assert domain.author_contract(story["branch_id"]) == before
    assert before["source_artifact_id"] == source["id"]
