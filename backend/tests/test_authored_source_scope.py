"""Author sources stay independent of body, reference text and automatic choices."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from ainovel.db import DomainError, dumps
from ainovel.models import ModuleInput
from ainovel.providers import ModelClient
from ainovel.worker import Worker
from ainovel.plot_audit import interpret_plot
from ainovel import authored_source_scope as authored
from test_openai_schema import completed
from test_workflow_plan_recipient_scope import inventory, SOURCE
from test_workflow_plan_source_roles import proposal


@pytest.fixture(autouse=True)
def legacy_policy_frames(monkeypatch):
    """These existing tests preserve /1 requests and exact historical step frames."""
    monkeypatch.setattr(authored, "NEW_TASK_VERSION", authored.VERSION)


def module_task(
    settings, domain, story, *, action="writing.expand_plot", enabled=True, nonce="author-source-test"
):
    payload = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action=action,
        instruction=SOURCE,
        account="openai_1",
        plot_contract_check=enabled,
        client_request_id=nonce,
    ).model_dump(exclude_none=True)
    queued = domain.queue("module", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    assert worker.task["id"] == queued["id"]
    return worker, payload


def answer(frame, *, only=False, unknown=False, ambiguous=False):
    instruction = frame["任务"]
    if instruction["动作"] == authored.ACTION:
        assert set(frame["上下文"]) == {"scope"}
        assert "scope_observation_ids" not in dumps(frame)
        assert "先前字段" not in dumps(frame) and "open_fields" not in dumps(frame)
        value = deepcopy(inventory(SOURCE, unknown=unknown)["inventory"])
        value["input_sha256"] = instruction["input_sha256"]
        return value
    assert instruction["动作"] == "仅依据作者原话整理本场景情节"
    assert set(frame["上下文"]) == {"scope"}
    value = proposal(permission_scope="unspecified")
    for index, item in enumerate(value["items"], 1):
        item.pop("open_fields")
        item.pop("open_reason")
        item["scope_observation_ids"] = [f"O{index:02}"]
        item["scope_binding_status"] = "supported"
        item["scope_binding_reason"] = "仅本段原话范围，不选新人物。"
    value["items"][1]["recipient"] = ""
    value["items"][0]["recipient_scope"] = "only" if only else "unspecified"
    if ambiguous:
        value["items"][0]["certainty"] = "ambiguous"
    return value


def native(worker, *, bad_first=False, unknown=False, ambiguous=False, timeout=False):
    frames = []
    interpretations = 0

    def handler(request):
        nonlocal interpretations
        wire = json.loads(request.content)
        prompt = wire["input"][1]["content"]
        frame = json.loads(prompt)
        frames.append(frame)
        if timeout:
            raise httpx.ReadTimeout("unknown paid source", request=request)
        if frame["任务"]["动作"] == "仅依据作者原话整理本场景情节":
            interpretations += 1
        value = answer(frame, only=bad_first and interpretations == 1, unknown=unknown, ambiguous=ambiguous)
        return completed(dumps(value))

    worker.client = ModelClient(worker.settings, worker.budget, httpx.MockTransport(handler))
    return frames


def test_native_new_module_checks_source_then_repairs_false_only_without_open_choice(settings, domain, story):
    worker, payload = module_task(settings, domain, story)
    frames = native(worker, bad_first=True)
    contract = asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    assert len(frames) == 3
    assert contract["items"][0]["recipient_scope"] == "unspecified"
    assert contract["items"][0]["recipient"] == "校核站"
    assert contract["items"][0]["transfer_scope"] == "completed"
    assert "scope_observation_ids" not in dumps(contract) and "open_fields" not in dumps(contract)
    assert domain.chapter(story["chapter_id"])["content"] == ""
    assert authored.validate_task(worker.task, domain)
    assert asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction")) == contract
    assert len(frames) == 3
    assert domain.queue("module", payload, story["branch_id"])["id"] == worker.task["id"]


@pytest.mark.parametrize("unknown,ambiguous", [(True, False), (False, True)])
def test_unresolved_author_source_never_generates_or_selects(settings, domain, story, unknown, ambiguous):
    worker, _ = module_task(settings, domain, story)
    frames = native(worker, unknown=unknown, ambiguous=ambiguous)
    with pytest.raises(DomainError, match="作者原话"):
        asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    assert len(frames) == (1 if unknown else 2)
    assert not any("开放细节" in f["任务"]["动作"] for f in frames)
    assert not domain.db.all("SELECT * FROM artifacts")
    count = len(frames)
    with pytest.raises(DomainError):
        asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    assert len(frames) == count


def test_unknown_native_result_is_not_reissued_and_reserve_is_retained(settings, domain, story):
    worker, _ = module_task(settings, domain, story)
    frames = native(worker, timeout=True)
    with pytest.raises(DomainError):
        asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    held = worker.budget.report()[0]["reserved_unknown"]
    assert len(frames) == 1 and held > 0
    worker.task = domain.task(worker.task["id"])
    with pytest.raises(DomainError):
        asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    assert len(frames) == 1 and worker.budget.report()[0]["reserved_unknown"] == held


@pytest.mark.parametrize("field", [authored.KEY, authored.SNAPSHOT])
def test_creation_marker_deletion_does_not_downgrade(settings, domain, story, field):
    worker, _ = module_task(settings, domain, story)
    worker.task["result"].pop(field)
    with pytest.raises(DomainError, match="版本|见证"):
        asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))


def test_source_observation_is_not_author_truth_disagreement_can_stay_unknown(settings, domain, story):
    worker, _ = module_task(settings, domain, story)
    frames = []

    def handler(request):
        frame = json.loads(json.loads(request.content)["input"][1]["content"])
        frames.append(frame)
        value = answer(frame)
        if frame["任务"]["动作"] == authored.ACTION:
            value["observations"][0].update(kind="recipient_exclusivity", restriction_evidence="SCOPE0001")
        else:
            value["items"][0].update(
                scope_binding_status="unknown",
                scope_binding_reason="普通寄往句没有排他原话，不能为了符合先行观察改成only。",
            )
        return completed(dumps(value))

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    with pytest.raises(DomainError, match="作者原话"):
        asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    receipt = worker.task["result"][authored.RECEIPTS]["module"]
    assert receipt["state"] == "unverified"
    assert receipt["contract"]["items"][0]["recipient_scope"] == "unspecified"
    assert len(frames) == 2 and not domain.db.all("SELECT * FROM artifacts")


@pytest.mark.parametrize("mode", ["module", "assisted"])
def test_source_is_checked_before_any_original_prose_request(settings, domain, story, mode):
    if mode == "module":
        worker, _ = module_task(settings, domain, story)
    else:
        from test_workflow_scope import workflow_payload

        payload = workflow_payload(story, mode="assisted")
        payload.update(
            plot_contract_check=True,
            plots=[{"number": 1, "title": "当前作者情节", "plot": SOURCE, "fixed": []}],
            brief="未来秘密与本章来源无关",
        )
        domain.queue("workflow", payload, story["branch_id"])
        worker = Worker(settings)
        worker.task = worker.claim()
        worker.attempt = worker.task["attempt"]
        assert "workflow_plan_open_details_version" not in worker.task["result"]
    frames = native(worker, unknown=True)
    if mode == "module":
        with pytest.raises(DomainError):
            asyncio.run(worker.module())
    else:
        with pytest.raises(DomainError):
            asyncio.run(worker.workflow())
    assert len(frames) == 1 and frames[0]["任务"]["动作"] == authored.ACTION
    assert "未来秘密" not in dumps(frames)
    assert not domain.db.all("SELECT * FROM artifacts")
    assert all(row["content"] == "" for row in domain.db.all("SELECT * FROM chapters"))


def reference_draft(client, settings, domain, reference, *, plot_check=True):
    from test_reference_flow import run, update
    from test_plot_audit import AUTHOR, Integrated

    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot=AUTHOR,
        application_plan="只迁移写法，不从参考正文补本书条件。",
    )
    model = Integrated()
    candidate, worker = run(
        client, settings, domain, row["id"], "draft", model=model, plot_contract_check=plot_check
    )
    return row, candidate, worker


from test_reference_flow import reference as reference  # noqa: E402, F401


@pytest.mark.parametrize("edit_body", [False, True])
def test_reference_first_explicit_check_uses_frozen_source_and_current_body_policy(
    client, settings, domain, reference, edit_body
):
    from test_reference_flow import run
    from test_plot_audit import Integrated
    from ainovel.flow_entailment import KEY as FLOW_KEY, VERSION as FLOW_VERSION

    row, candidate, producer = reference_draft(client, settings, domain, reference, plot_check=False)
    original_task = deepcopy(domain.task(producer.task["id"]))
    assert authored.artifact_audit(candidate) is None
    assert authored.KEY not in original_task["result"]
    if edit_body:
        response = client.patch(
            "/api/artifacts/" + candidate["id"],
            json={
                "candidate_revision": 0,
                "text": candidate["payload"]["text"] + "候选新增的旁白不构成作者条件。",
            },
        )
        assert response.status_code == 200
    before_check = deepcopy(domain.task(producer.task["id"]))
    assert {k: v for k, v in before_check.items() if k != "updated"} == {
        k: v for k, v in original_task.items() if k != "updated"
    }
    source_frames = []

    class Recorded(Integrated):
        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            frame = json.loads(prompt.split("\n只返回")[0])
            if frame["任务"].get("动作") == authored.ACTION:
                source_frames.append(deepcopy(frame))
            return await super().generate(account, task_id, system, prompt, max_output, **kwargs)

    model = Recorded()
    report, worker = run(
        client,
        settings,
        domain,
        row["id"],
        "check",
        model=model,
        plot_contract_check=True,
        candidate_id=candidate["id"],
    )
    assert worker.task["result"]["plot_contract_version"] == "author-plot-audit/2"
    assert worker.task["result"][FLOW_KEY] == FLOW_VERSION
    assert worker.task["result"][authored.KEY] == authored.VERSION
    snapshot = worker.task["result"][authored.SNAPSHOT]
    assert snapshot["sources"][0]["text"] == original_task["result"]["reference_snapshot"]["payload"]["plot"]
    receipt = worker.task["result"][authored.RECEIPTS]["module"]
    assert receipt["state"] == "ready" and receipt["inherited"] is None
    assert len(receipt["phases"]) == 2
    assert len(source_frames) == 1 and set(source_frames[0]["上下文"]) == {"scope"}
    assert "候选新增的旁白" not in dumps(source_frames)
    assert report["payload"]["plot_audit"]["authored_source_scope"]["state"] == "ready"
    assert domain.task(producer.task["id"]) == before_check


def test_reference_candidate_reuses_exact_source_receipt_after_edit_without_interpretation(
    client, settings, domain, reference
):
    from test_reference_flow import run
    from test_plot_audit import Integrated

    row, candidate, producer = reference_draft(client, settings, domain, reference)
    initial = domain.task(candidate["task_id"])["result"][authored.RECEIPTS]["module"]
    assert initial["state"] == "ready"
    response = client.patch(
        "/api/artifacts/" + candidate["id"],
        json={"candidate_revision": 0, "text": candidate["payload"]["text"] + "保留记录。"},
    )
    assert response.status_code == 200
    model = Integrated()
    report, worker = run(
        client,
        settings,
        domain,
        row["id"],
        "check",
        model=model,
        plot_contract_check=True,
        candidate_id=candidate["id"],
    )
    inherited = worker.task["result"][authored.SNAPSHOT]["inherited"]
    assert inherited["scope_receipt"] == initial
    assert inherited["candidate_revision"] == 1
    assert worker.task["result"][authored.RECEIPTS]["module"]["contract"] == initial["contract"]
    assert not any(
        f["任务"].get("动作") in {authored.ACTION, "仅依据作者原话整理本场景情节"}
        for f in model.requests + model.audits
    )
    assert report["payload"]["plot_audit"]["authored_source_scope"]["state"] == "ready"
    assert report["payload"]["target"]["candidate"] == {"id": candidate["id"], "revision": 1}
    assert domain.task(producer.task["id"])["result"][authored.RECEIPTS]["module"] == initial


def test_reference_changed_author_plot_cannot_reinterpret_old_candidate(client, settings, domain, reference):
    from test_reference_flow import update

    row, candidate, _ = reference_draft(client, settings, domain, reference)
    row = update(client, domain, row, plot="改为另一场景，由新角色发起交易。")
    response = client.post(
        f"/api/reference-sessions/{row['id']}/actions",
        json={
            "base_revision": row["revision"],
            "action": "check",
            "account": "deepseek",
            "client_request_id": "changed-ref-source",
            "candidate_id": candidate["id"],
            "plot_contract_check": True,
        },
    )
    assert response.status_code == 409 and response.json()["code"] == "reference_source_changed"
    assert not domain.db.all("SELECT * FROM task_requests WHERE key='changed-ref-source'")


def test_reference_candidate_different_work_target_is_rejected_before_queue(
    client, settings, domain, reference
):
    from test_reference_flow import update

    row, candidate, _ = reference_draft(client, settings, domain, reference)
    other = domain.create_novel("另一个作品")
    branch = domain.branch(other["branch_id"])
    chapter = domain.create_chapter(branch["id"], "另章")
    row = update(client, domain, row, branch_id=branch["id"], chapter_id=chapter["id"])
    before = domain.db.all("SELECT id FROM tasks ORDER BY id")
    response = client.post(
        f"/api/reference-sessions/{row['id']}/actions",
        json={
            "base_revision": row["revision"],
            "action": "check",
            "account": "deepseek",
            "client_request_id": "cross-ref-source",
            "candidate_id": candidate["id"],
            "plot_contract_check": True,
        },
    )
    assert response.status_code == 403 and response.json()["code"] == "ownership"
    assert domain.db.all("SELECT id FROM tasks ORDER BY id") == before


def test_new_saved_review_unknown_source_does_not_check_body_against_unqualified_proposal(
    settings, domain, story
):
    from ainovel.plot_audit import audit_plot

    worker, _ = module_task(settings, domain, story, action="review.chapter")
    frames = native(worker, unknown=True)
    result = asyncio.run(
        audit_plot(worker, SOURCE, "无关正文不能反向澄清作者原话。", 1, source_kind="author_instruction")
    )
    assert result["status"] == "unverified" and result["checks"] == [] and result["findings"] == []
    assert len(frames) == 1 and result["authored_source_scope"]["state"] == "unverified"
    assert "无关正文" not in dumps(frames)


@pytest.mark.parametrize("account", ["openai_1", "deepseek"])
def test_native_provider_schema_and_settlement_are_bound_to_source_task(settings, domain, story, account):
    from test_workflow_plan_source_roles import deepseek_completed

    payload = ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="writing.expand_plot",
        instruction=SOURCE,
        account=account,
        plot_contract_check=True,
        client_request_id="native-authored-" + account,
    ).model_dump(exclude_none=True)
    domain.queue("module", payload, story["branch_id"])
    worker = Worker(settings)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    wires = []

    def handler(request):
        wire = json.loads(request.content)
        prompt = wire.get("input", wire.get("messages"))[1]["content"]
        frame = json.loads(prompt.split("\n只返回")[0])
        schema = (
            wire["text"]["format"]["schema"]
            if "input" in wire
            else json.loads(prompt.split("\n只返回符合以下 JSON Schema 的对象：\n")[1])
        )
        wires.append((frame, schema))
        if frame["任务"]["动作"] == authored.ACTION:
            assert schema["properties"]["unit_checks"]["minItems"] == len(frame["任务"]["可引用原文"])
        else:
            assert schema["$defs"]["ScopeBoundAuthorItem"]["properties"]["scope_binding_status"]["enum"] == [
                "supported",
                "unknown",
            ]
        value = answer(frame)
        return completed(dumps(value)) if "input" in wire else deepseek_completed(value)

    worker.client = ModelClient(settings, worker.budget, httpx.MockTransport(handler))
    contract = asyncio.run(interpret_plot(worker, SOURCE, source_kind="author_instruction"))
    assert len(wires) == 2 and contract["items"][0]["recipient_scope"] == "unspecified"
    events = [
        json.loads(r["payload"])
        for r in domain.db.all(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (worker.task["id"],)
        )
    ]
    assert len(events) == 2 and all(e["request"][authored.KEY] == authored.VERSION for e in events)
    with worker.budget.db() as tx:
        calls = [dict(r) for r in tx.execute("SELECT * FROM calls")]
    assert len(calls) == 2 and all(c["status"] == "settled" and c["account"] == account for c in calls)


def test_reference_source_receipt_and_new_body_execution_version_are_separate(
    client, settings, domain, reference
):
    row, candidate, producer = reference_draft(client, settings, domain, reference)
    current = {
        "reference_snapshot": deepcopy(producer.task["result"]["reference_snapshot"]),
        "plot_contract_version": "author-plot-audit/2",
        "flow_source_entailment_version": "source-flow-entailment/2",
    }
    payload = {
        **producer.task["payload"],
        "action": "reference.check",
        "reference_check_candidate": {"id": candidate["id"], "revision": 0},
    }
    inherited = authored.inherited_source(domain, payload, current)
    assert inherited["contract"] == producer.task["result"][authored.RECEIPTS]["module"]["contract"]
    assert inherited["scope_receipt"] == producer.task["result"][authored.RECEIPTS]["module"]
    assert current["flow_source_entailment_version"] == "source-flow-entailment/2"


def test_reference_legacy_scoped_candidate_is_frozen_without_new_source_interpretation(
    client, settings, domain, reference
):
    # Archived pre-execution-scope row: construct the old shape directly instead
    # of stripping a fresh task's creation witness to pretend compatibility.
    from time import time
    from ainovel.db import uid
    from ainovel.reference import reference_basis
    from test_reference_flow import run, update
    from test_plot_audit import AUTHOR, Integrated

    analysis, _ = run(client, settings, domain, reference["id"], "analyze")
    cards = analysis["payload"]["cards"]
    row = update(
        client,
        domain,
        reference,
        cards=cards,
        selected_ids=[cards[0]["id"]],
        plot=AUTHOR,
        application_plan="历史旧方案",
    )
    request = ModuleInput(
        novel_id=story_id(row, domain),
        branch_id=row["payload"]["branch_id"],
        chapter_id=row["payload"]["chapter_id"],
        base_revision=0,
        action="reference.draft",
        instruction=row["payload"]["goal"],
        account="deepseek",
        plot_contract_check=True,
        reference_session_id=row["id"],
        reference_revision=row["revision"],
    ).model_dump(exclude_none=True)
    initial = {
        "genre_profile_snapshot": domain.genre_profile(request["branch_id"]),
        "author_contract_snapshot": domain.author_contract(request["branch_id"]),
        "plot_contract_version": "author-plot-audit/2",
        "flow_source_entailment_version": "source-flow-entailment/1",
        "reference_snapshot": reference_basis(domain, request),
    }
    identifier = uid()
    now = time()
    with domain.db.connect(True) as tx:
        tx.execute(
            "INSERT INTO tasks(id,kind,novel_id,branch_id,status,payload,result,created,updated) VALUES(?,'module',?,?,'queued',?,?,?,?)",
            (identifier, request["novel_id"], request["branch_id"], dumps(request), dumps(initial), now, now),
        )
        domain.db.event(tx, identifier, "queued", {"kind": "module"})
    worker = Worker(settings, Integrated())
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    asyncio.run(worker.module())
    candidate = domain.artifact(domain.task(identifier)["result"]["artifact_id"])
    assert authored.KEY not in domain.task(identifier)["result"]
    model = Integrated()
    report, check = run(
        client,
        settings,
        domain,
        row["id"],
        "check",
        model=model,
        plot_contract_check=True,
        candidate_id=candidate["id"],
    )
    saved = check.task["result"][authored.RECEIPTS]["module"]
    assert saved["state"] == "legacy_frozen" and saved["phases"] == {}
    assert saved["inherited"]["source_policy_version"] is None
    assert report["payload"]["plot_audit"]["authored_source_scope"]["scope_policy_version"] is None
    assert not any(
        f["任务"].get("动作") in {authored.ACTION, "仅依据作者原话整理本场景情节"}
        for f in model.requests + model.audits
    )


def story_id(row, domain):
    return domain.branch(row["payload"]["branch_id"])["novel_id"]
