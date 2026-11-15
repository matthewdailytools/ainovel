"""Source support, speech/content direction and frozen-policy regression tests."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from ainovel.db import DomainError, dumps
from ainovel.flow_entailment import ACTION, KEY, VERSION, review_flows
from ainovel.models import ModuleInput
from ainovel.plot_audit import VERSION as PLOT_VERSION, LEGACY_VERSION, independent_flows
from ainovel.worker import Worker

TEXT = "甲问乙：“桥板怎么安装？”乙答：“先校零，再固定。”😀𠮷"
OBJECT = "桥规的询问"


def item(*, supplier="甲", recipient="乙", scope="unspecified", stage="attempted", number=1, obj=OBJECT):
    return {
        "id": f"P{number:02}",
        "author_evidence": "甲向乙询问桥规。",
        "actor": supplier,
        "action": "询问",
        "object": obj,
        "direction": f"{supplier}向{recipient}",
        "result": "询问发生",
        "certainty": "explicit",
        "transfer_object": obj,
        "supplier": supplier,
        "recipient": recipient,
        "transfer_scope": stage,
        "recipient_scope": scope,
        "scope_condition": "核验前" if scope == "only" else "",
    }


def check(number=1):
    return {
        "item_id": f"P{number:02}",
        "result": "pass",
        "observed": "原模型说一致",
        "evidence": TEXT,
        "reason": "工程替身",
        "repair": "",
    }


def flow(supplier="甲", recipient="乙", *, stage="completed", obj=OBJECT, certainty="explicit"):
    return {
        "object": obj,
        "supplier": supplier,
        "recipient": recipient,
        "stage": stage,
        "certainty": certainty,
    }


class Model:
    def __init__(self, flows=None, verdicts=None, *, marker=VERSION, version=PLOT_VERSION, mutate=None):
        self.task = {"result": {"plot_contract_version": version}}
        if marker is not None:
            self.task["result"][KEY] = marker
        self.flows = flows if flows is not None else [flow()]
        self.verdicts = verdicts or ["supported"] * len(self.flows)
        self.frames = []
        self.mutate = mutate

    async def grounded(self, instruction, schema, context, validate, **kwargs):
        self.frames.append(
            {
                "instruction": deepcopy(instruction),
                "context": deepcopy(context),
                "contract": deepcopy(kwargs.get("contract")),
            }
        )
        sid = instruction["可引用原文"][0]["source_id"]
        if instruction["动作"] == "只从本次正文独立提取物品与信息流向":
            names = {name: eid for eid, name in instruction["实体别名对照"].items()}
            objects = {name: oid for oid, name in instruction["待识别物品名称"].items()}
            output = {
                "flows": [
                    {
                        "object_id": objects.get(row["object"], "other"),
                        "supplier_id": names.get(row["supplier"], "unknown"),
                        "recipient_id": names.get(row["recipient"], "unknown"),
                        "stage": row["stage"],
                        "certainty": row["certainty"],
                        "evidence": sid,
                        "note": "本次正文观察",
                    }
                    for row in self.flows
                ],
                "note": "隔离工程观察",
            }
        elif instruction["动作"] == ACTION:
            output = {
                "input_sha256": instruction["input_sha256"],
                "checks": [
                    {
                        "flow_id": row["flow_id"],
                        "result": self.verdicts[int(row["flow_id"][1:]) - 1],
                        "verified_fields": [
                            "object_id",
                            "supplier_id",
                            "recipient_id",
                            "stage",
                            "certainty",
                            "evidence",
                        ]
                        if self.verdicts[int(row["flow_id"][1:]) - 1] == "supported"
                        else [],
                        "evidence": sid,
                        "reason": "仅从本次原文核对对象、双方、阶段和原引文，不看预期",
                    }
                    for row in instruction["待核对观察提案"]
                ],
                "note": "工程支持复核，不代表真实模型能力",
            }
        else:
            raise AssertionError(instruction["动作"])
        if self.mutate:
            self.mutate(instruction["动作"], output)
        result = schema.model_validate(output).model_dump()
        issues = validate(result)
        if issues:
            raise DomainError("evidence", "；".join(issues))
        return result


def compare(model=None, *, items=None, text=TEXT, version=PLOT_VERSION):
    model = model or Model()
    items = items or [item()]
    rows = [check(index) for index in range(1, len(items) + 1)]
    before = deepcopy(items)
    result = asyncio.run(independent_flows(model, items, text, rows, version=version))
    assert items == before
    return result, rows, model


def test_supported_question_direction_has_its_own_positive_source_observation():
    report, rows, model = compare()
    assert rows[0]["result"] == "pass" and "original_model_check" not in rows[0]
    assert report["source_entailment"]["checks"][0]["result"] == "supported"
    assert report["source_entailment"]["supported_observations"] == report["flows"]
    observer, reviewer = (frame["instruction"] for frame in model.frames)
    assert observer["角色流向观察版本"] == "source-role-observation/2"
    assert "索取者是recipient" not in observer["独立角色映射"]
    assert "询问／请求／邀请／通知本身" in observer["要求"]
    assert "答案／知识／实物" in observer["要求"]
    assert reviewer["本次正文"] == TEXT
    assert all("作者原话" not in str(frame) and "原模型说一致" not in str(frame) for frame in model.frames)
    assert "direction" not in reviewer and "transfer_scope" not in reviewer
    assert all("预期方向" not in str(frame["instruction"].get("实体别名对照")) for frame in model.frames)
    assert (
        "genre_profile" not in model.frames[1]["context"]
        and "author_contract" not in model.frames[1]["context"]
    )


def test_confusing_the_requested_answer_with_the_question_does_not_cause_prose_repair():
    report, rows, _ = compare(Model([flow("乙", "甲")], ["contradicted"]))
    assert rows[0]["result"] == "unknown" and "guard_override" not in rows[0]
    assert rows[0]["original_model_check"]["result"] == "pass"
    assert report["source_entailment"]["checks"][0]["result"] == "contradicted"
    assert report["source_entailment"]["supported_observations"] == []


def test_actual_reverse_question_supported_by_source_still_fails():
    text = "乙问甲：“桥板怎么安装？”"
    report, rows, _ = compare(Model([flow("乙", "甲")]), text=text)
    assert rows[0]["result"] == "fail"
    assert rows[0]["guard_override"]["code"] == "reverse_transfer"
    assert rows[0]["evidence"] == text
    assert report["source_entailment"]["checks"][0]["result"] == "supported"


@pytest.mark.parametrize("verdict", ["unknown", "contradicted"])
def test_a_real_quote_without_complete_semantic_support_cannot_prove_forward(verdict):
    report, rows, _ = compare(Model(verdicts=[verdict]))
    assert rows[0]["result"] == "unknown"
    assert rows[0]["original_model_check"]["result"] == "pass"
    assert report["source_entailment"]["supported_observations"] == []


def test_unknown_observation_is_not_hidden_by_a_supported_forward_example():
    report, rows, _ = compare(Model([flow(), flow("乙", "甲")], ["supported", "unknown"]))
    assert rows[0]["result"] == "unknown"
    assert rows[0]["guard_override"]["code"] == "flow_source_unverified"
    assert rows[0]["guard_override"]["expected_supplier"] == "甲"
    assert rows[0]["guard_override"]["expected_recipient"] == "乙"
    assert len(report["source_entailment"]["checks"]) == 2


@pytest.mark.parametrize("stage", ["offered", "requested", "planned", "refused", "unknown"])
def test_source_support_does_not_turn_an_offer_or_future_arrangement_into_delivery(stage):
    _, rows, _ = compare(Model([flow(stage=stage)]), items=[item(stage="completed")])
    assert rows[0]["result"] == "unknown"


@pytest.mark.parametrize("stage,expected", [("offered", "unknown"), ("completed", "fail")])
def test_actual_reverse_delivery_is_not_hidden_by_stage_filter(stage, expected):
    _, rows, _ = compare(Model([flow("乙", "甲", stage=stage)]), items=[item(stage="completed")])
    assert rows[0]["result"] == expected


def test_known_extra_recipient_keeps_only_scope_condition_for_separate_review():
    items = [item(scope="only"), item(supplier="丙", recipient="乙", obj="别的内容", number=2)]
    model = Model(
        [flow(stage="completed"), flow("甲", "丙", stage="completed"), flow("丙", "乙", obj="别的内容")]
    )
    report, rows, _ = compare(model, items=items)
    assert rows[0]["result"] == "unknown"
    assert rows[0]["guard_override"]["code"] == "restricted_recipient_unverified"
    assert rows[0]["guard_override"]["scope_condition"] == "核验前"
    assert len(report["source_entailment"]["checks"]) == 3


def test_other_topics_are_not_added_to_the_semantic_review_batch():
    report, rows, model = compare(Model([flow(), flow(obj="天气闲聊")]))
    assert rows[0]["result"] == "pass"
    assert len(report["flows"]) == 2 and len(report["source_entailment"]["checks"]) == 1
    assert len(model.frames[1]["instruction"]["待核对观察提案"]) == 1


@pytest.mark.parametrize("defect", ["body_quote", "empty_quote", "wrong_id", "duplicate", "token"])
def test_unsupported_or_misbound_semantic_results_cannot_be_used(defect):
    def mutate(action, output):
        if action != ACTION:
            return
        row = output["checks"][0]
        if defect == "body_quote":
            row["evidence"] = "另一部书才有的原句"
        elif defect == "empty_quote":
            row["evidence"] = ""
        elif defect == "wrong_id":
            row["flow_id"] = "F99"
        elif defect == "duplicate":
            output["checks"].append(deepcopy(row))
        else:
            output["input_sha256"] = "0" * 64

    with pytest.raises(DomainError):
        compare(Model(mutate=mutate))


@pytest.mark.parametrize("marker", [True, "future/9", {}, 3])
def test_unrecognized_or_incompatible_frozen_policy_stops_before_observation(marker):
    model = Model(marker=marker)
    with pytest.raises(DomainError) as caught:
        compare(model)
    assert caught.value.code == "flow_entailment_version" and model.frames == []


def test_legacy_plot_version_cannot_silently_use_new_source_policy():
    with pytest.raises(DomainError):
        compare(Model(version=LEGACY_VERSION), version=LEGACY_VERSION)


@pytest.mark.parametrize("version", [LEGACY_VERSION, PLOT_VERSION])
def test_old_tasks_without_marker_keep_exact_observation_frame_and_no_extra_call(version):
    report, rows, model = compare(Model(marker=None, version=version), version=version)
    assert rows[0]["result"] == "pass"
    assert len(model.frames) == 1 and "source_entailment" not in report
    assert "流向来源蕴含版本" not in model.frames[0]["instruction"]
    # Digests were obtained by running this exact fixture against source 853d62d.
    expected = {
        LEGACY_VERSION: "341a6732746dd96b5da1fe7727e34ddc95e7a3e8df319d932a4180ab18513c3d",
        PLOT_VERSION: "b0df89f03dd7807fb3d5e62ce90dcf80a6cdef3518dc58a52993c43510d3b82f",
    }
    assert hashlib.sha256(dumps(model.frames[0]).encode()).hexdigest() == expected[version]


def module_request(story, nonce="flow-policy-queue"):
    return ModuleInput(
        novel_id=story["id"],
        branch_id=story["branch_id"],
        chapter_id=story["chapter_id"],
        base_revision=0,
        action="review.chapter",
        instruction="甲向乙询问桥规。",
        account="openai_1",
        plot_contract_check=True,
        client_request_id=nonce,
    ).model_dump()


def test_new_eligible_tasks_freeze_policy_and_nonce_replay_keeps_old_version(domain, story):
    payload = module_request(story)
    queued = domain.queue("module", payload, story["branch_id"])
    assert queued["result"][KEY] == VERSION
    old = deepcopy(queued["result"])
    old.pop(KEY)
    with domain.db.connect(True) as tx:
        tx.execute("UPDATE tasks SET result=? WHERE id=?", (dumps(old), queued["id"]))
    replay = domain.queue("module", payload, story["branch_id"])
    assert replay["id"] == queued["id"] and replay["result"] == old
    plain = domain.queue(
        "module",
        {**payload, "client_request_id": "flow-disabled-queue", "plot_contract_check": False},
        story["branch_id"],
    )
    assert KEY not in plain["result"]


def test_supported_review_is_durable_across_worker_instances_without_model_replay(settings, domain, story):
    task = domain.queue("module", module_request(story), story["branch_id"])

    class SourceModel:
        calls = 0

        async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
            self.calls += 1
            frame = json.loads(prompt.split("\n只返回")[0])
            instruction = frame["任务"]
            assert instruction["动作"] == ACTION
            assert "genre_profile" not in frame["上下文"] and "author_contract" not in frame["上下文"]
            return {
                "text": dumps(
                    {
                        "input_sha256": instruction["input_sha256"],
                        "checks": [
                            {
                                "flow_id": row["flow_id"],
                                "result": "supported",
                                "verified_fields": [
                                    "object_id",
                                    "supplier_id",
                                    "recipient_id",
                                    "stage",
                                    "certainty",
                                    "evidence",
                                ],
                                "evidence": instruction["可引用原文"][0]["source_id"],
                                "reason": "全部观察由冻结正文支持",
                            }
                            for row in instruction["待核对观察提案"]
                        ],
                        "note": "工程回归",
                    }
                ),
                "call_id": "durable-flow-fixture",
                "usage": {"input": 1, "output": 1},
            }

    model = SourceModel()
    worker = Worker(settings, model)
    worker.task = worker.claim()
    worker.attempt = worker.task["attempt"]
    observations = [
        {
            "object_id": "O01",
            "supplier_id": "E01",
            "recipient_id": "E02",
            "stage": "requested",
            "certainty": "explicit",
            "evidence": TEXT,
            "note": "问话本身",
        }
    ]
    args = (TEXT, {"E01": "甲", "E02": "乙"}, {"O01": OBJECT}, observations)
    first = asyncio.run(review_flows(worker, *args))
    new = Worker(settings, model)
    new.task, new.attempt = domain.task(task["id"]), worker.attempt
    second = asyncio.run(review_flows(new, *args))
    assert first == second and model.calls == 1
    assert len(domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (task["id"],))) == 1
    dispatch = domain.db.all(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='step_dispatched'", (task["id"],)
    )
    assert json.loads(dispatch[0]["payload"])["request"][KEY] == VERSION


@pytest.mark.parametrize(
    "text,stage,verdict,expected",
    [
        ("甲问乙：“桥板怎么安装？”乙摇头拒绝回答。", "completed", "supported", "pass"),
        ("甲打算明日再向乙询问桥规。", "planned", "supported", "unknown"),
        ("甲刚要问桥规，就被乙打断，问题没问出口。", "completed", "contradicted", "unknown"),
    ],
)
def test_speech_act_completion_is_separate_from_answer_refusal_and_future_intention(
    text, stage, verdict, expected
):
    report, rows, model = compare(
        Model([flow(stage=stage)], [verdict]), text=text, items=[item(stage="completed")]
    )
    assert rows[0]["result"] == expected
    instruction = model.frames[1]["instruction"]
    assert instruction["本次正文"] == text
    assert "拒绝答复不否定" in instruction["内容与阶段口径"]
    assert "未问出口不能当已经发问" in instruction["内容与阶段口径"]


def test_answer_content_keeps_supplier_to_requester_and_does_not_become_delivered():
    obj = "桥规的回答"
    _, rows, _ = compare(
        Model([flow("乙", "甲", stage="requested", obj=obj)]),
        items=[item(supplier="乙", recipient="甲", obj=obj, stage="completed")],
    )
    assert rows[0]["result"] == "unknown"


def test_unknown_timeout_retains_reservation_and_blocks_unacknowledged_resume(
    settings, domain, story, client
):
    import httpx
    from ainovel.budget import Budget
    from ainovel.providers import ModelClient

    queued = domain.queue("module", module_request(story, "flow-unknown-timeout"), story["branch_id"])
    calls = []

    def transport(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown outcome")

    ledger = Budget(settings)
    model = ModelClient(settings, ledger, httpx.MockTransport(transport))

    class SourceWorker(Worker):
        async def module(self):
            await review_flows(
                self,
                TEXT,
                {"E01": "甲", "E02": "乙"},
                {"O01": OBJECT},
                [
                    {
                        "object_id": "O01",
                        "supplier_id": "E01",
                        "recipient_id": "E02",
                        "stage": "completed",
                        "certainty": "explicit",
                        "evidence": TEXT,
                        "note": "问话本身",
                    }
                ],
            )

    assert asyncio.run(SourceWorker(settings, model).run_once())
    failed = domain.task(queued["id"])
    assert failed["status"] == "blocked_runtime"
    assert len(calls) == 1 and ledger.report()[0]["reserved_unknown"] > 0
    amount = ledger.report()[0]["reserved_unknown"]
    assert client.post(f"/api/tasks/{queued['id']}/resume", json={}).status_code == 409
    assert not asyncio.run(SourceWorker(settings, model).run_once())
    assert len(calls) == 1 and ledger.report()[0]["reserved_unknown"] == amount
    assert not domain.db.all("SELECT * FROM task_steps WHERE task_id=?", (queued["id"],))


def test_unrelated_verified_third_parties_do_not_add_a_new_obligation_to_an_unrestricted_transfer():
    items = [item(), item(supplier="丙", recipient="丁", obj="别的内容", number=2)]

    def partial(action, output):
        if action == ACTION:
            output["checks"][1]["verified_fields"] = ["supplier_id", "recipient_id"]

    model = Model(
        [flow(), flow("丙", "丁", stage="unknown"), flow("丙", "丁", obj="别的内容")],
        ["supported", "unknown", "supported"],
        mutate=partial,
    )
    report, rows, _ = compare(model, items=items, text=TEXT + "丙也向丁询问桥规，具体时点未明。")
    assert [row["result"] for row in rows] == ["pass", "pass"]
    assert report["source_entailment"]["checks"][1]["result"] == "unknown"


@pytest.mark.parametrize("stage_verified,expected", [(True, "pass"), (False, "unknown")])
def test_only_future_unknown_observation_does_not_obstruct_a_verified_current_transfer(
    stage_verified, expected
):
    def partial(action, output):
        if action == ACTION and stage_verified:
            output["checks"][1]["verified_fields"] = ["stage"]

    model = Model([flow(), flow("甲", "未识别者", stage="planned")], ["supported", "unknown"], mutate=partial)
    report, rows, _ = compare(
        model, items=[item(scope="only")], text=TEXT + "甲打算明日另向一位尚未识别者询问桥规。"
    )
    assert rows[0]["result"] == expected
    assert report["source_entailment"]["checks"][1]["result"] == "unknown"


@pytest.mark.parametrize("defect", ["incomplete_supported", "duplicate", "unquoted_partial"])
def test_partial_field_confirmation_cannot_be_fabricated_to_bypass_unknown(defect):
    def partial(action, output):
        if action != ACTION:
            return
        row = output["checks"][0]
        if defect == "incomplete_supported":
            row["verified_fields"] = ["stage"]
        elif defect == "duplicate":
            row["verified_fields"] = ["stage", "stage"]
        else:
            row.update(result="unknown", verified_fields=["stage"], evidence="")

    with pytest.raises(DomainError):
        compare(Model(mutate=partial))


def test_contradicted_observation_cannot_also_claim_every_original_field_is_supported():
    def inconsistent(action, output):
        if action == ACTION:
            output["checks"][0]["result"] = "contradicted"

    with pytest.raises(DomainError):
        compare(Model(mutate=inconsistent))


@pytest.mark.parametrize("object_verified,expected", [(True, "pass"), (False, "unknown")])
def test_unverified_other_object_label_cannot_hide_a_possible_reverse_of_this_item(object_verified, expected):
    answer = "桥规的回答"
    items = [item(), item(supplier="乙", recipient="甲", obj=answer, number=2)]

    def partial(action, output):
        if action == ACTION:
            output["checks"][1]["verified_fields"] = ["object_id"] if object_verified else []

    model = Model(
        [flow(), flow("乙", "甲", obj=answer), flow("乙", "甲", obj=answer)],
        ["supported", "unknown", "supported"],
        mutate=partial,
    )
    report, rows, _ = compare(model, items=items)
    assert rows[0]["result"] == expected
    assert report["source_entailment"]["checks"][1]["result"] == "unknown"


def test_only_other_topics_need_no_extra_semantic_model_call_and_cannot_prove_the_requested_flow():
    report, rows, model = compare(Model([flow(obj="天气闲聊")]))
    assert rows[0]["result"] == "unknown"
    assert len(model.frames) == 1
    assert report["source_entailment"]["status"] == "not_applicable"
    assert report["source_entailment"]["checks"] == report["source_entailment"]["proposals"] == []
