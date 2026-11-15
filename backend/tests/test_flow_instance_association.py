"""Distinct copies and grounded table-external actors do not pollute mail flow."""

from copy import deepcopy
import hashlib

import pytest

from ainovel.db import DomainError, dumps
from ainovel.flow_entailment import ACTION, LEGACY_VERSION, VERSION
from test_flow_source_entailment import Model, compare, flow, item


OBJECT = "检测记录的抄件"
TEXT = (
    "林舟把检测记录抄一份装进信封，寄往设备核验站请求核查，回执未到。"
    "后来修理师傅陈伯看着林舟另抄一份随身副本，再把它折好递还林舟；它不是已寄出的那份。😀"
)


def source(scope="unspecified"):
    return item(supplier="林舟", recipient="设备核验站", obj=OBJECT, stage="completed", scope=scope)


def flows():
    return [flow("林舟", "设备核验站", obj=OBJECT), flow("陈伯", "林舟", obj=OBJECT)]


class AssociatedModel(Model):
    def __init__(self, *args, participants=False, instances=False, change=None, **kwargs):
        super().__init__(*args, marker=VERSION, **kwargs)

        def mutate(action, output):
            if action != ACTION:
                return
            instruction = self.frames[-1]["instruction"]
            sid = instruction["可引用原文"][0]["source_id"]
            output.update(participants=[], instances=[])
            if participants:
                output["participants"] = [
                    {
                        "flow_id": "F02",
                        "side": "supplier_id",
                        "status": "known_other",
                        "label": "修理师傅陈伯",
                        "excluded_entity_ids": list(instruction["实体别名对照"]),
                        "evidence": sid,
                        "reason": "原文明确陈伯把另一份递还林舟，陈伯是本场另一个参与者，不是寄件人或核验站。",
                    }
                ]
            if instances:
                output["instances"] = [
                    {
                        "left_flow_id": "F01",
                        "right_flow_id": "F02",
                        "result": "different",
                        "kind": "physical",
                        "evidence": sid,
                        "reason": "连续原文明确后来另抄随身副本，不是已寄出的那份；不宣称内容不同。",
                    }
                ]
            if change:
                change(output, instruction)

        self.mutate = mutate


@pytest.mark.parametrize("scope", ["unspecified", "only"])
def test_explicit_other_actor_and_other_copy_preserve_mail_and_all_observations(scope):
    report, checks, model = compare(
        AssociatedModel(flows(), participants=True, instances=True), items=[source(scope)], text=TEXT
    )
    assert checks[0]["result"] == "pass" and "guard_override" not in checks[0]
    assert len(report["flows"]) == len(report["source_entailment"]["proposals"]) == 2
    supplement = report["source_entailment"]
    assert supplement["version"] == VERSION
    assert supplement["proposals"][1]["supplier_id"] == "unknown"
    assert supplement["participants"][0]["label"] == "修理师傅陈伯"
    assert supplement["instances"][0]["kind"] == "physical"
    assert supplement["participants"][0]["evidence"] == TEXT
    assert len(model.frames) == 2  # Reuse the existing observation and entailment calls.
    assert report["role_observation_version"] == "source-role-observation/3"
    observer, reviewer = [x["instruction"] for x in model.frames]
    assert "expected" not in str(observer) + str(reviewer)
    assert all(
        key not in reviewer for key in ("作者原话", "原情节", "direction", "transfer_scope", "旧guard")
    )
    assert all("工程原检查pass" not in str(frame) for frame in model.frames)


def test_different_physical_copy_alone_excludes_reverse_but_never_only_disclosure():
    _, checks, _ = compare(AssociatedModel(flows(), instances=True), items=[source()], text=TEXT)
    assert checks[0]["result"] == "pass"
    _, checks, _ = compare(AssociatedModel(flows(), instances=True), items=[source("only")], text=TEXT)
    assert checks[0]["result"] == "unknown"
    assert checks[0]["guard_override"]["code"] == "flow_source_unverified"


@pytest.mark.parametrize("relation", ["same", "unknown"])
def test_unknown_or_same_instance_does_not_exclude_unknown_reverse(relation):
    def change(output, _):
        output["instances"][0]["result"] = relation

    _, checks, _ = compare(
        AssociatedModel(flows(), instances=True, change=change), items=[source()], text=TEXT
    )
    assert checks[0]["result"] == "unknown"


def test_no_association_still_holds_even_if_note_claims_another_copy():
    report, checks, _ = compare(AssociatedModel(flows()), items=[source()], text=TEXT)
    assert checks[0]["result"] == "unknown"
    assert report["source_entailment"]["participants"] == []


def test_nontransfer_source_role_is_available_without_inventing_a_handover():
    permission = source()
    permission.update(
        id="P02",
        actor="修理师傅",
        action="允许带副本",
        object="许可",
        direction="",
        result="许可",
        transfer_scope="none",
        transfer_object="",
        supplier="",
        recipient="",
    )
    chosen = [flow("林舟", "设备核验站", obj=OBJECT), flow("修理师傅", "林舟", obj=OBJECT)]
    report, checks, _ = compare(AssociatedModel(chosen), items=[source(), permission], text=TEXT)
    assert "修理师傅" in report["entities"].values()
    assert checks[0]["result"] == "pass"
    assert checks[1]["result"] == "pass"
    assert permission["transfer_scope"] == "none"


def test_other_copy_does_not_invent_missing_completed_forward():
    # A known planned original locates an instance; it does not prove posting.
    observed = flows()
    observed[0]["stage"] = "planned"
    _, checks, _ = compare(AssociatedModel(observed, instances=True), items=[source()], text=TEXT)
    assert checks[0]["result"] == "unknown"
    assert "已完成" in checks[0]["reason"]


def test_other_paper_same_information_to_extra_person_still_blocks_only():
    observed = [flow("林舟", "设备核验站", obj=OBJECT), flow("林舟", "未识别人", obj=OBJECT)]
    report, checks, _ = compare(AssociatedModel(observed, instances=True), items=[source("only")], text=TEXT)
    assert checks[0]["result"] == "unknown"
    assert checks[0]["guard_override"]["code"] == "restricted_recipient_unverified"
    assert report["source_entailment"]["instances"][0]["result"] == "different"


@pytest.mark.parametrize(
    "defect",
    [
        "missing_quote",
        "foreign_quote",
        "wrong_flow",
        "duplicate",
        "unknown_excludes",
        "unsupported",
        "wrong_entity",
        "known_endpoint",
    ],
)
def test_unbound_or_unverified_participant_exclusion_is_rejected(defect):
    def change(output, instruction):
        row = output["participants"][0]
        if defect == "missing_quote":
            row["evidence"] = ""
        elif defect == "foreign_quote":
            row["evidence"] = "另一作品中的交接"
        elif defect == "wrong_flow":
            row["flow_id"] = "F99"
        elif defect == "duplicate":
            output["participants"].append(deepcopy(row))
        elif defect == "unknown_excludes":
            row["status"] = "unknown"
        elif defect == "unsupported":
            output["checks"][1].update(result="unknown", verified_fields=[])
        elif defect == "wrong_entity":
            row["excluded_entity_ids"] = ["E99"]
        else:
            row["side"] = "recipient_id"

    with pytest.raises(DomainError):
        compare(AssociatedModel(flows(), participants=True, change=change), items=[source()], text=TEXT)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_quote",
        "foreign_quote",
        "wrong_flow",
        "duplicate",
        "self",
        "unknown_kind",
        "unsupported",
        "wrong_token",
    ],
)
def test_unbound_or_unverified_instance_relation_is_rejected(defect):
    def change(output, _):
        row = output["instances"][0]
        if defect == "missing_quote":
            row["evidence"] = ""
        elif defect == "foreign_quote":
            row["evidence"] = "旧正文的另一份副本"
        elif defect == "wrong_flow":
            row["right_flow_id"] = "F99"
        elif defect == "duplicate":
            output["instances"].append(deepcopy(row))
        elif defect == "self":
            row["right_flow_id"] = "F01"
        elif defect == "unknown_kind":
            row["kind"] = "unknown"
        elif defect == "unsupported":
            output["checks"][1].update(result="unknown", verified_fields=[])
        else:
            output["input_sha256"] = "0" * 64

    with pytest.raises(DomainError):
        compare(AssociatedModel(flows(), instances=True, change=change), items=[source()], text=TEXT)


def test_genuine_unknown_identity_still_stops_and_real_reverse_still_fails():
    _, checks, _ = compare(AssociatedModel(flows()), items=[source()], text=TEXT)
    assert checks[0]["result"] == "unknown"
    reverse = [flow("设备核验站", "林舟", obj=OBJECT)]
    _, checks, _ = compare(AssociatedModel(reverse), items=[source()], text=TEXT)
    assert checks[0]["result"] == "fail"


def test_legacy_v1_keeps_original_schema_frames_and_cannot_consume_relations():
    old = Model(flows(), marker=LEGACY_VERSION)
    report, checks, _ = compare(old, items=[source()], text=TEXT)
    assert checks[0]["result"] == "unknown"
    assert report["role_observation_version"] == "source-role-observation/2"
    assert "participants" not in report["source_entailment"]
    assert "instances" not in report["source_entailment"]
    assert "参与者关联" not in old.frames[1]["instruction"]
    assert set(old.frames[1]["contract"]["properties"]) == {"input_sha256", "checks", "note"}
    # Captured with this original toy against the untouched baseline /1 engine.
    assert hashlib.sha256(dumps(old.frames).encode()).hexdigest() == (
        "86337d1500fb12f4b4ffd66a225cefd97fdb3c3f69f7a8ff467cd194b06f5dbf"
    )
