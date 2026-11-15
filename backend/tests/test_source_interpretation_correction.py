"""Source correction is explicit, independently checked and cannot rewrite plot."""

import asyncio
from copy import deepcopy
import json

import pytest

from ainovel import source_interpretation_correction as correction
from ainovel import source_corrected_candidate_recheck as corrected
from ainovel.candidate_recheck import sha
from ainovel.db import DomainError, dumps
from ainovel.worker import Worker
from test_candidate_recheck import RecheckModel, assert_parent_not_resumed
from test_workflow_scope import workflow_payload


SOURCE = (
    "林舟用借来的短尺重复检查刻线，确认两处读数不同并记录时刻，师傅要求先排除工具偏差。\n"
    "【写作要求】先说明短尺零位、固定刻线K与读数方向，"
    "再完成9.20厘米/9.10厘米的对照；每次记录时刻和原始量值。"
    "师傅要求继续排查工具偏差，林舟不把差异写成有人破坏。"
)


@pytest.fixture
def stopped_source(domain, story):
    request = workflow_payload(story)
    request.update(plot_contract_check=True, budget_limit=5, brief="FUTURE_BRIEF_SENTINEL")
    request["plots"][0].update(plot=SOURCE, fixed=["复测记录包含时刻与差异。"])
    task = domain.queue("workflow", request, story["branch_id"])
    fields = [
        ("林舟", "重复测量", "借尺"),
        ("林舟", "确认", "读数存在差异"),
        ("林舟", "记录", "测量时刻"),
        ("师傅", "要求", "先排除工具误差"),
        ("未注明", "说明", "基本校准、同刻和换算"),
        ("未注明", "完成", "9.20厘米/9.10厘米对照"),
        ("未注明", "记录", "每次时刻和原始量值"),
        ("师傅", "要求继续排查", "零点或工具系统误差"),
        ("林舟", "不升级为", "造假者已定"),
    ]
    paragraphs = SOURCE.splitlines()
    items = [
        {
            "id": f"P{i:02}",
            "actor": actor,
            "action": action,
            "object": obj,
            "author_evidence": paragraphs[0 if i <= 4 else 1],
            "direction": "",
            "result": f"保持原事项{i}、原值和未知边界",
            "certainty": "ambiguous" if i in {5, 6, 7} else "explicit",
            "transfer_object": "",
            "supplier": "",
            "recipient": "",
            "transfer_scope": "none",
            "recipient_scope": "unspecified",
            "scope_condition": "",
        }
        for i, (actor, action, obj) in enumerate(fields, 1)
    ]
    text = "CANDIDATE_SENTINEL😀。" + "林舟用短尺检查刻线，留下可以回查的记录。" * 12
    fixed = request["fixed"] + request["plots"][0]["fixed"]
    artifact = domain.stage(
        {**request, "branch_id": story["branch_id"], "chapter_id": story["chapter_id"], "base_revision": 0},
        "chapter_draft",
        {
            "text": text,
            "fixed": fixed,
            "review_passed": False,
            "memory": {},
            "review": {"summary": "OLD_REVIEW_SENTINEL"},
            "plot_audit": {
                "version": "author-plot-audit/2",
                "source_kind": "author_plot",
                "source": {"kind": "author_plot", "text_sha256": sha(SOURCE)},
                "items": items,
                "interpretation_note": "原解释只依据本章原话。",
                "checks": [{"result": "unknown", "reason": "OLD_CHECK_SENTINEL"}],
            },
        },
        {"story_revision": domain.branch(story["branch_id"])["story_revision"]},
        task["id"],
    )
    progress = {
        **task["result"],
        "plan": {"chapters": request["plots"], "ending": "FUTURE_PLAN_SENTINEL"},
        "next_chapter": 1,
        "repair_round": 0,
        "waiting_artifact": artifact["id"],
    }
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET status='needs_review',result=? WHERE id=?", (dumps(progress), task["id"])
        )
    return domain.task(task["id"]), domain.artifact(artifact["id"])


def submit(client, candidate, **overrides):
    preview = client.get(f"/api/artifacts/{candidate['id']}/source-interpretation-correction-preview")
    basis = preview.json().get("basis_sha256", "0" * 64)
    return client.post(
        f"/api/artifacts/{candidate['id']}/source-interpretation-corrections",
        json={
            "candidate_revision": candidate["candidate_revision"],
            "account": "openai_1",
            "client_request_id": "source-correction-request",
            "item_ids": ["P05", "P06", "P07"],
            "reason": "REASON_SENTINEL",
            "preview_basis_sha256": basis,
            **overrides,
        },
    )


def recheck_basis(client, candidate, receipt_id):
    response = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={"receipt_id": receipt_id},
    )
    return response.json().get("basis_sha256", "0" * 64)


class SourceModel:
    def __init__(self, verdict="pass", hook=None, mutate=None):
        self.frames = []
        self.verdict, self.hook, self.mutate = verdict, hook, mutate

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        frame = json.loads(prompt.split("\n只返回")[0])
        action = frame["任务"]["动作"]
        assert action in {correction.PROPOSE, correction.VERIFY}
        self.frames.append(deepcopy(frame))
        serialized = dumps(frame)
        for forbidden in [
            "CANDIDATE_SENTINEL",
            "OLD_REVIEW_SENTINEL",
            "OLD_CHECK_SENTINEL",
            "REASON_SENTINEL",
            "FUTURE_BRIEF_SENTINEL",
            "FUTURE_PLAN_SENTINEL",
        ]:
            assert forbidden not in serialized
        assert set(frame["上下文"]) == {"scope"}
        instruction = frame["任务"]
        ids = [row["item_id"] for row in instruction["原解释事项"]]
        if action == correction.PROPOSE:
            output = {
                "items": [
                    {
                        "item_id": row["item_id"],
                        "actor": ""
                        if row["item_id"] == "P05"
                        else "林舟"
                        if row["item_id"] in {"P06", "P07"}
                        else row["actor"],
                        "statement_kind": "narrative_requirement"
                        if row["item_id"] == "P05"
                        else "story_event",
                        "certainty": "explicit",
                        "interpretation_reason": "本段原话的叙述要求或同段明确主语回指。",
                        "evidence": "AUTHOR0002"
                        if row["item_id"] == "P05"
                        else "AUTHOR0003"
                        if row["item_id"] == "P10"
                        else "AUTHOR0001",
                    }
                    for row in instruction["原解释事项"]
                ]
            }
        else:
            output = {
                "item_checks": [
                    {
                        "item_id": item_id,
                        "result": self.verdict if item_id == "P05" else "pass",
                        "evidence": "AUTHOR0002"
                        if item_id == "P05"
                        else "AUTHOR0003"
                        if item_id == "P10"
                        else "AUTHOR0001",
                        "reason": "独立回到原话核对主体、性质及全部保留字段。",
                    }
                    for item_id in ids
                ],
                "fixed_checks": [
                    {
                        "condition_id": row["condition_id"],
                        "result": "pass",
                        "evidence": row["condition_id"],
                        "reason": "固定约定原文保留，未据此补选actor。",
                    }
                    for row in instruction["原固定条件仅核对是否保持"]
                ],
                "summary": "隔离替身核对，不代表真实文学判断。",
            }
        if self.mutate:
            self.mutate(action, output)
        if self.hook:
            self.hook(action, task_id)
        return {
            "text": dumps(output),
            "usage": {"input": 1, "output": 1},
            "call_id": f"source-correction-{len(self.frames)}",
        }


def run_source(settings, domain, client, candidate, **kwargs):
    response = submit(client, candidate)
    assert response.status_code == 202, response.text
    model = SourceModel(**kwargs)
    asyncio.run(Worker(settings, model).run_once())
    return domain.task(response.json()["id"]), model


class CorrectedRecheckModel(RecheckModel):
    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        action = instruction.get("动作")
        if action in {"审查正文", "从正文提取事实候选与章节摘要"}:
            self.actions.append(action)
            quote = instruction["可引用原文"][0]["source_id"]
            output = (
                {
                    "checks": [
                        {"condition": row["condition_id"], "result": "pass", "evidence": quote}
                        for row in instruction["固定条件编号表"]
                    ],
                    "findings": [],
                    "summary": "原创隔离替身审查",
                }
                if action == "审查正文"
                else {
                    "facts": [
                        {
                            "subject": "林舟",
                            "kind": "event",
                            "statement": "检查刻线并留存记录",
                            "evidence": quote,
                        }
                    ],
                    "summary": "原创隔离摘要",
                }
            )
            return {
                "text": dumps(output),
                "usage": {"input": 1, "output": 1},
                "call_id": "toy-corrected-" + str(len(self.actions)),
            }
        if action != "逐项对照作者情节解释与本次正文":
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)
        assert "显式来源解释纠偏元数据" in instruction
        assert all("statement_kind" not in item for item in instruction["独立整理的解释提案"])
        self.actions.append(action)
        return {
            "text": dumps(
                {
                    "checks": [
                        {
                            "item_id": item["id"],
                            "result": "pass",
                            "observed": "隔离替身按当前文本逐项核对。",
                            "evidence": instruction["可引用原文"][0]["source_id"],
                            "reason": "仅验证新回执接线，不代表文学通过。",
                            "repair": "无需生成正文",
                        }
                        for item in instruction["独立整理的解释提案"]
                    ],
                    "summary": "隔离工程逐项核对",
                }
            ),
            "usage": {"input": 1, "output": 1},
            "call_id": "corrected-plot-fixture",
        }


def test_preview_create_query_and_two_source_only_stages(settings, domain, client, stopped_source):
    parent, candidate = stopped_source
    before_chapter = domain.chapter(candidate["chapter_id"])
    preview = client.get(f"/api/artifacts/{candidate['id']}/source-interpretation-correction-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_text"] == SOURCE
    assert "CANDIDATE_SENTINEL" not in preview.text
    task, model = run_source(settings, domain, client, candidate)
    assert task["status"] == "succeeded", task["error"]
    assert task["result"]["source_correction_state"] == "ready"
    assert len(model.frames) == 2
    receipt = domain.artifact(task["result"]["artifact_id"])
    assert receipt["kind"] == correction.KIND
    old = candidate["payload"]["plot_audit"]["items"]
    new = receipt["payload"]["corrected_contract"]["items"]
    for before, after in zip(old, new, strict=True):
        assert all(
            after[key] == value for key, value in before.items() if key not in {"id", "actor", "certainty"}
        )
    assert new[4]["actor"] == ""
    assert receipt["payload"]["proposal"]["items"][4]["statement_kind"] == "narrative_requirement"
    assert all(set(item) == set(before) - {"id"} for item, before in zip(new, old, strict=True))
    assert new[5]["actor"] == new[6]["actor"] == "林舟"
    assert domain.artifact(candidate["id"]) == candidate
    assert domain.chapter(candidate["chapter_id"]) == before_chapter
    assert_parent_not_resumed(domain, parent)
    repeated = submit(client, candidate)
    assert repeated.status_code == 202 and repeated.json()["id"] == task["id"]
    queried = client.get(
        f"/api/artifacts/{candidate['id']}/source-interpretation-corrections/source-correction-request"
    )
    assert queried.status_code == 200 and queried.json()["id"] == task["id"]
    assert submit(client, candidate, account="openai_2").status_code == 409


@pytest.mark.parametrize("verdict", ["unknown", "fail"])
def test_independent_nonpass_receipt_never_rechecks_or_modifies_prose(
    settings, domain, client, stopped_source, verdict
):
    parent, candidate = stopped_source
    task, model = run_source(settings, domain, client, candidate, verdict=verdict)
    assert task["status"] == "succeeded", task["error"]
    receipt = domain.artifact(task["result"]["artifact_id"])
    assert receipt["payload"]["state"] == "unverified"
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "receipt_id": receipt["id"],
            "preview_basis_sha256": recheck_basis(client, candidate, receipt["id"]),
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "unready-recheck-request",
        },
    )
    assert response.status_code == 409
    assert len(model.frames) == 2 and domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)


@pytest.mark.parametrize(
    "damage", ["action", "object", "direction", "result", "transfer_scope", "scope_condition"]
)
def test_proposal_schema_cannot_change_immutable_event_fields(
    settings, domain, client, stopped_source, damage
):
    _, candidate = stopped_source

    def mutate(action, output):
        if action == correction.PROPOSE:
            output["items"][4][damage] = "反向、改数值或计划变完成均不允许"

    task, model = run_source(settings, domain, client, candidate, mutate=mutate)
    assert task["status"] == "blocked_runtime"
    assert len(model.frames) == 1
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


@pytest.mark.parametrize(
    "damage", ["missing", "reordered", "new_name", "actual_event_to_instruction", "unselected", "bad_quote"]
)
def test_source_scope_rejects_deletion_new_actor_and_fake_narration(
    settings, domain, client, stopped_source, damage
):
    _, candidate = stopped_source

    def mutate(action, output):
        if action != correction.PROPOSE:
            return
        rows = output["items"]
        if damage == "missing":
            rows.pop()
        elif damage == "reordered":
            rows.reverse()
        elif damage == "new_name":
            rows[5]["actor"] = "新选测工赵甲"
        elif damage == "actual_event_to_instruction":
            rows[5].update(actor="", statement_kind="narrative_requirement")
        elif damage == "unselected":
            rows[0]["certainty"] = "ambiguous"
        else:
            rows[5]["evidence"] = "PLOT0001"

    task, _ = run_source(settings, domain, client, candidate, mutate=mutate)
    assert task["status"] == "blocked_runtime"
    assert domain.artifact(candidate["id"]) == candidate


def test_true_actor_choice_remains_unknown(settings, domain, client, stopped_source):
    _, candidate = stopped_source

    def mutate(action, output):
        if action == correction.PROPOSE:
            output["items"][5].update(
                actor="",
                certainty="ambiguous",
                interpretation_reason="原话确实可能由不同执行者承担，不替作者选择。",
            )

    task, _ = run_source(settings, domain, client, candidate, verdict="unknown", mutate=mutate)
    assert task["result"]["source_correction_state"] == "unverified"
    assert not domain.db.all("SELECT * FROM tasks WHERE kind=?", (corrected.KIND,))


@pytest.mark.parametrize("stage", [correction.PROPOSE, correction.VERIFY])
def test_source_change_between_calls_or_before_publication_blocks(
    settings, domain, client, stopped_source, stage
):
    parent, candidate = stopped_source

    def hook(action, _):
        if action == stage:
            payload = deepcopy(parent["payload"])
            payload["plots"][0]["plot"] += "来源变更"
            with domain.db.connect(True) as tx:
                tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(payload), parent["id"]))

    task, model = run_source(settings, domain, client, candidate, hook=hook)
    assert task["status"] == "blocked_runtime"
    assert len(model.frames) == (1 if stage == correction.PROPOSE else 2)
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))
    assert domain.artifact(candidate["id"]) == candidate


def test_explicit_new_recheck_uses_ready_receipt_and_preserves_old_reports(
    settings, domain, client, stopped_source
):
    parent, candidate = stopped_source
    task, _ = run_source(settings, domain, client, candidate)
    receipt = domain.artifact(task["result"]["artifact_id"])
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "receipt_id": receipt["id"],
            "preview_basis_sha256": recheck_basis(client, candidate, receipt["id"]),
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "ready-source-recheck-request",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == corrected.KIND
    assert response.json()["result"]["source_corrected_recheck_version"] == corrected.VERSION
    model = CorrectedRecheckModel()
    asyncio.run(Worker(settings, model).run_once())
    finished = domain.task(response.json()["id"])
    assert finished["status"] == "succeeded", finished["error"]
    saved = domain.artifact(candidate["id"])
    assert saved["payload"]["text"] == candidate["payload"]["text"]
    assert saved["payload"]["previous_plot_audit"] == candidate["payload"]["plot_audit"]
    assert saved["payload"]["previous_review"] == candidate["payload"]["review"]
    assert domain.artifact(receipt["id"]) == receipt
    assert domain.chapter(candidate["chapter_id"])["revision"] == 0
    assert_parent_not_resumed(domain, parent)
    assert all(action not in {correction.PROPOSE, correction.VERIFY} for action in model.actions)
    report = domain.artifact(finished["result"]["artifact_id"])
    assert saved["payload"]["plot_audit"]["source_correction"]["receipt_id"] == receipt["id"]
    for readonly in [receipt, report]:
        assert (
            client.patch(
                f"/api/artifacts/{readonly['id']}", json={"candidate_revision": 0, "text": "不可改"}
            ).status_code
            == 409
        )

        assert (
            client.post(f"/api/artifacts/{readonly['id']}/reject", json={"candidate_revision": 0}).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/artifacts/{readonly['id']}/accept",
                json={"idempotency_key": "readonly-accept-" + readonly["id"], "candidate_revision": 0},
            ).status_code
            == 409
        )
    # A later author edit must retain an old-schema readable interpretation.
    edited = client.patch(
        f"/api/artifacts/{candidate['id']}",
        json={
            "candidate_revision": 0,
            "text": candidate["payload"]["text"] + "作者再改。",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["candidate_revision"] == 1
    assert "review" not in edited.json()["payload"]
    preview = client.get(f"/api/artifacts/{candidate['id']}/source-interpretation-correction-preview")
    assert preview.status_code == 200, preview.text
    ordinary = client.post(
        f"/api/artifacts/{candidate['id']}/recheck",
        json={
            "candidate_revision": 1,
            "account": "openai_1",
            "client_request_id": "ordinary-after-source-correction",
        },
    )
    assert ordinary.status_code == 202, ordinary.text
    stale = client.get(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction-preview",
        params={
            "receipt_id": receipt["id"],
        },
    )
    assert stale.status_code == 409
    assert domain.artifact(receipt["id"]) == receipt
    assert domain.artifact(report["id"]) == report


def test_edited_candidate_invalidates_ready_receipt_before_new_recheck(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source
    task, _ = run_source(settings, domain, client, candidate)
    edited = client.patch(
        f"/api/artifacts/{candidate['id']}",
        json={"candidate_revision": 0, "text": candidate["payload"]["text"] + "新段落"},
    )
    assert edited.status_code == 200
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "receipt_id": task["result"]["artifact_id"],
            "preview_basis_sha256": recheck_basis(client, candidate, task["result"]["artifact_id"]),
            "candidate_revision": 1,
            "account": "openai_1",
            "client_request_id": "changed-target-recheck-request",
        },
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_revision": True},
        {"item_ids": ["P05", "P05"]},
        {"item_ids": ["P99"]},
        {"source_text": "不能重写源"},
    ],
)
def test_strict_requests_do_not_expand_scope(client, stopped_source, overrides):
    _, candidate = stopped_source
    response = submit(client, candidate, **overrides)
    assert response.status_code in {409, 422}


def test_real_actor_choice_cannot_be_reclassified_as_narration(domain, stopped_source):
    _, candidate = stopped_source
    data = correction.request_payload(
        domain,
        candidate["id"],
        correction.CorrectionInput(
            candidate_revision=0,
            account="openai_1",
            client_request_id="choice-source-request",
            item_ids=["P05"],
            preview_basis_sha256=correction.preview(domain, candidate["id"])["basis_sha256"],
        ),
    )
    snapshot = correction.freeze(domain, data)
    choice = "由甲或乙说明测量规矩，具体人员尚未选择。"
    snapshot["plot"]["plot"] = choice
    snapshot["prepared_contract"]["items"] = [
        {**snapshot["prepared_contract"]["items"][4], "author_evidence": choice, "actor": "未注明"}
    ]
    snapshot["item_ids"] = ["P01"]
    value = {
        "items": [
            {
                "item_id": "P01",
                "actor": "",
                "statement_kind": "narrative_requirement",
                "certainty": "explicit",
                "interpretation_reason": "不能用分类逃避选择。",
                "evidence": "AUTHOR0001",
            }
        ]
    }
    with pytest.raises(DomainError, match="原文未明确支持"):
        correction.resolve_proposal(value, snapshot)
    value["items"][0].update(statement_kind="story_event", certainty="explicit")
    with pytest.raises(DomainError, match="主体仍未知"):
        correction.resolve_proposal(value, snapshot)
    value["items"][0].update(statement_kind="story_event", certainty="ambiguous")
    proposal, _ = correction.resolve_proposal(value, snapshot)
    assert not correction.ready(proposal, {"item_checks": [], "fixed_checks": []})


@pytest.mark.parametrize("damage", ["candidate", "story", "fixed", "reader"])
def test_target_and_configuration_changes_before_publication_are_blocked(
    settings, domain, client, stopped_source, damage
):
    parent, candidate = stopped_source

    def hook(action, _):
        if action != correction.VERIFY:
            return
        with domain.db.connect(True) as tx:
            if damage == "candidate":
                payload = deepcopy(candidate["payload"])
                payload["text"] += "候选改变"
                tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))
            elif damage == "story":
                tx.execute(
                    "UPDATE branches SET story_revision=story_revision+1 WHERE id=?",
                    (candidate["branch_id"],),
                )
            else:
                key = "payload" if damage == "fixed" else "result"
                value = deepcopy(parent[key])
                if damage == "fixed":
                    value["fixed"].append("新固定条件")
                else:
                    value["genre_profile_snapshot"]["author_notes"] = "changed adopted configuration"
                tx.execute(f"UPDATE tasks SET {key}=? WHERE id=?", (dumps(value), parent["id"]))

    task, model = run_source(settings, domain, client, candidate, hook=hook)
    assert task["status"] == "blocked_runtime"
    assert len(model.frames) == 2
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


def test_receipt_change_during_explicit_recheck_keeps_original_candidate(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source
    task, _ = run_source(settings, domain, client, candidate)
    receipt_id = task["result"]["artifact_id"]
    queued = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "receipt_id": receipt_id,
            "preview_basis_sha256": recheck_basis(client, candidate, receipt_id),
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "receipt-race-recheck-request",
        },
    )
    assert queued.status_code == 202

    def hook(action, _):
        if action != "审查正文":
            return
        receipt = domain.artifact(receipt_id)
        receipt["payload"]["note"] += "changed"
        with domain.db.connect(True) as tx:
            tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(receipt["payload"]), receipt_id))

    model = RecheckModel(hook=hook)
    asyncio.run(Worker(settings, model).run_once())
    assert domain.task(queued.json()["id"])["status"] == "blocked_runtime"
    assert domain.artifact(candidate["id"]) == candidate
    assert model.actions == ["审查正文"]


def test_source_budget_is_not_reset_and_unknown_costs_block(settings, domain, client, stopped_source):
    from ainovel.budget import Budget

    parent, candidate = stopped_source
    queued = submit(client, candidate, budget_limit=10)
    assert queued.status_code == 202
    worker = Worker(settings, SourceModel())
    worker.task = domain.task(queued.json()["id"])
    budget = Budget(settings)
    provider = settings.providers["openai_1"]
    call = budget.reserve(provider, parent["id"], 10, 10)
    with pytest.raises(DomainError, match="未决费用"):
        correction.bounded_run_limit(worker)
    budget.settle(call, {"input": 10, "output": 10})
    assert correction.bounded_run_limit(worker) == pytest.approx(5 - 0.0001)
    own = budget.reserve(provider, worker.task["id"], 1, 1)
    budget.settle(own, {"input": 1, "output": 1})
    assert correction.bounded_run_limit(worker) == pytest.approx(5 - 0.0001)
    assert len(budget.report()[0]["calls"]) == 2


def test_recovery_reuses_completed_step_but_never_replays_dispatched_unknown(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source

    def interrupted(action, _):
        if action == correction.VERIFY:
            raise RuntimeError("isolated interruption after dispatch")

    task, first = run_source(settings, domain, client, candidate, hook=interrupted)
    assert task["status"] == "blocked_runtime" and len(first.frames) == 2
    stored = domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task["id"],))
    assert len(stored) == 1
    resumed = client.post(f"/api/tasks/{task['id']}/resume", json={"acknowledge_unknown": True})
    assert resumed.status_code == 200, resumed.text
    second = SourceModel()
    asyncio.run(Worker(settings, second).run_once())
    after = domain.task(task["id"])
    assert after["status"] == "blocked_runtime" and "未盲目重发" in after["error"]
    assert second.frames == []
    assert domain.db.all("SELECT fingerprint,result FROM task_steps WHERE task_id=?", (task["id"],)) == stored
    assert domain.artifact(candidate["id"]) == candidate


@pytest.mark.parametrize("damage", ["report", "contract", "progress"])
def test_same_revision_changes_after_source_preview_require_explicit_refresh(
    domain, client, stopped_source, damage
):
    parent, candidate = stopped_source
    preview = client.get(f"/api/artifacts/{candidate['id']}/source-interpretation-correction-preview").json()
    with domain.db.connect(True) as tx:
        if damage == "progress":
            state = deepcopy(parent["result"])
            state["repair_round"] = 1
            tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(state), parent["id"]))
        else:
            payload = deepcopy(candidate["payload"])
            if damage == "report":
                payload["review"]["summary"] = "same revision different review"
            else:
                payload["plot_audit"]["interpretation_note"] += "同修订解释改变"
            tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))
    assert domain.artifact(candidate["id"])["candidate_revision"] == 0
    response = client.post(
        f"/api/artifacts/{candidate['id']}/source-interpretation-corrections",
        json={
            "candidate_revision": 0,
            "account": "openai_1",
            "client_request_id": "stale-preview-request",
            "item_ids": ["P05", "P06", "P07"],
            "preview_basis_sha256": preview["basis_sha256"],
        },
    )
    assert response.status_code == 409, response.text
    assert not domain.db.all("SELECT id FROM tasks WHERE kind=?", (correction.KIND,))


def test_explicit_recheck_same_revision_report_change_after_preview_is_rejected(
    settings, domain, client, stopped_source
):
    _, candidate = stopped_source
    task, _ = run_source(settings, domain, client, candidate)
    receipt_id = task["result"]["artifact_id"]
    basis = recheck_basis(client, candidate, receipt_id)
    payload = deepcopy(candidate["payload"])
    payload["review"]["summary"] = "changed report without editing prose"
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))
    response = client.post(
        f"/api/artifacts/{candidate['id']}/recheck-source-correction",
        json={
            "candidate_revision": 0,
            "account": "openai_1",
            "receipt_id": receipt_id,
            "client_request_id": "same-revision-recheck-request",
            "preview_basis_sha256": basis,
        },
    )
    assert response.status_code == 409
    assert not domain.db.all("SELECT id FROM tasks WHERE kind=?", (corrected.KIND,))


@pytest.mark.parametrize("preserved", [True, False])
def test_future_fixed_condition_preservation_is_separate_from_chapter_fulfillment(
    settings, domain, client, stopped_source, preserved
):
    parent, candidate = stopped_source
    future = "第1—25章若涉及母亲，去向与生死保持未证实；第26章才由回信确认去向。"
    request = deepcopy(parent["payload"])
    request["fixed"].append(future)
    payload = deepcopy(candidate["payload"])
    payload["fixed"] = request["fixed"] + request["plots"][0]["fixed"]
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET payload=? WHERE id=?", (dumps(request), parent["id"]))
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))

    def mutate(action, output):
        if action == correction.VERIFY and not preserved:
            output["fixed_checks"][0].update(result="fail", reason="解释与原母亲未知约定相反，应保持阻断。")

    task, model = run_source(settings, domain, client, domain.artifact(candidate["id"]), mutate=mutate)
    assert future not in dumps(model.frames[0])
    assert future in dumps(model.frames[1])
    assert "不判断本章是否已经兑现约定" in dumps(model.frames[1])
    assert task["result"]["source_correction_state"] == ("ready" if preserved else "unverified")


@pytest.mark.parametrize("placeholder", ["", "未注明"])
def test_explicit_event_cannot_hide_missing_actor(settings, domain, client, stopped_source, placeholder):
    _, candidate = stopped_source

    def mutate(action, output):
        if action == correction.PROPOSE:
            output["items"][5]["actor"] = placeholder

    task, _ = run_source(settings, domain, client, candidate, mutate=mutate)
    assert task["status"] == "blocked_runtime"
    assert not domain.db.all("SELECT id FROM artifacts WHERE task_id=?", (task["id"],))


def test_edited_revision_uses_retained_previous_plot_interpretation(settings, domain, client, stopped_source):
    parent, original = stopped_source
    response = client.patch(
        f"/api/artifacts/{original['id']}",
        json={
            "candidate_revision": 0,
            "text": original["payload"]["text"] + "独立未采用的修订段落。",
        },
    )
    assert response.status_code == 200, response.text
    candidate = response.json()
    assert candidate["candidate_revision"] == 1
    assert "plot_audit" not in candidate["payload"] and "previous_plot_audit" in candidate["payload"]
    task, _ = run_source(settings, domain, client, candidate)
    assert task["status"] == "succeeded", task["error"]
    assert task["result"]["source_correction_state"] == "ready"
    assert domain.artifact(candidate["id"]) == candidate
    assert_parent_not_resumed(domain, parent)


def test_unselected_explicit_environment_event_keeps_legal_empty_actor(
    settings, domain, client, stopped_source
):
    parent, candidate = stopped_source
    source = SOURCE + "\n堤上起了风。"
    request = deepcopy(parent["payload"])
    request["plots"][0]["plot"] = source
    progress = deepcopy(parent["result"])
    progress["plan"]["chapters"] = request["plots"]
    payload = deepcopy(candidate["payload"])
    audit = payload["plot_audit"]
    audit["source"]["text_sha256"] = sha(source)
    event = {
        **deepcopy(audit["items"][0]),
        "id": "P10",
        "actor": "",
        "action": "起风",
        "object": "堤上天气",
        "author_evidence": "堤上起了风。",
        "result": "环境已起风，不指定人物主体",
        "certainty": "explicit",
    }
    audit["items"].append(event)
    with domain.db.connect(True) as tx:
        tx.execute(
            "UPDATE tasks SET payload=?,result=? WHERE id=?", (dumps(request), dumps(progress), parent["id"])
        )
        tx.execute("UPDATE artifacts SET payload=? WHERE id=?", (dumps(payload), candidate["id"]))
    task, _ = run_source(settings, domain, client, domain.artifact(candidate["id"]))
    assert task["status"] == "succeeded", task["error"]
    assert task["result"]["source_correction_state"] == "ready"
    receipt = domain.artifact(task["result"]["artifact_id"])
    kept = receipt["payload"]["corrected_contract"]["items"][-1]
    assert kept["actor"] == "" and kept["certainty"] == "explicit"
    assert all(kept[key] == value for key, value in event.items() if key != "id")


def test_inline_writing_requirement_uses_verified_item_boundary_not_author_chunk():
    directive = "【写作要求】先说明旧工具与刻线的对应，再写检查结果。"
    inline = "林舟检查门锁并记录结果。" + directive
    assert correction.narrative_requirement_supported({"author_evidence": directive}, inline)
    assert not correction.narrative_requirement_supported(
        {"author_evidence": "先说明旧工具与刻线的对应"}, "林舟先说明旧工具与刻线的对应，再检查门锁。"
    )


def test_workflow_and_continuation_keep_auxiliary_costs_inside_original_envelope(
    settings, domain, client, stopped_source
):
    from ainovel.budget import Budget

    parent, candidate = stopped_source
    worker = Worker(settings, SourceModel())
    worker.task = parent
    assert correction.bounded_run_limit(worker) == 5
    queued = submit(client, candidate)
    assert queued.status_code == 202
    ledger = Budget(settings)
    provider = settings.providers["openai_1"]
    old = ledger.reserve(provider, parent["id"], 10, 10)
    ledger.settle(old, {"input": 10, "output": 10})
    auxiliary = ledger.reserve(provider, queued.json()["id"], 1, 1)
    ledger.settle(auxiliary, {"input": 1, "output": 1})
    # Budget.reserve counts the workflow's own paid cost; this ceiling subtracts
    # only the separate auxiliary cost and never mutates the frozen payload.
    assert correction.bounded_run_limit(worker) == pytest.approx(5 - 0.00001)
    payload = deepcopy(parent["payload"])
    payload.update(client_request_id="continued-family-budget-test", budget_limit=5 - 0.0001)
    child = domain.queue("workflow", payload, parent["branch_id"])
    progress = {**child["result"], "continued_from": parent["id"]}
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(progress), child["id"]))
    worker.task = domain.task(child["id"])
    assert correction.bounded_run_limit(worker) == pytest.approx(5 - 0.0001 - 0.00001)
    assert domain.task(parent["id"])["payload"] == parent["payload"]
