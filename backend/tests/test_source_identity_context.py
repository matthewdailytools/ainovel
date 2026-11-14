"""Resolve source-side identity qualifiers without exposing expected directions to the observer."""

import asyncio
from copy import deepcopy
import pytest

from ainovel.db import DomainError
from ainovel.plot_audit import VERSION as PLOT_VERSION, LEGACY_VERSION, audit_plot, independent_flows


SOURCE = "沈砚另从渡口一位修船匠借得旧尺，约定后日归还。"
TEXT = "陈师傅在修船铺里修船。渡口的老吴是另一位修船匠，他从箱里取出旧尺，递给沈砚。"
IDENTITY_VERSION = "source-identity-context/1"


class IdentityWorker:
    def __init__(self, *, enabled=True, supported=True, mutate=None, reverse=False, no_transfer=False):
        self.task = {
            "kind": "module",
            "payload": {"plot_contract_check": True, "source_identity_context": enabled},
            "result": {"plot_contract_version": PLOT_VERSION},
        }
        if enabled:
            self.task["result"]["source_identity_context_version"] = IDENTITY_VERSION
        self.supported = supported
        self.mutate = mutate
        self.reverse = reverse
        self.no_transfer = no_transfer
        self.frames = []

    async def grounded(self, instruction, schema, context, validator, **kwargs):
        self.frames.append((deepcopy(instruction), deepcopy(context)))
        action = instruction["动作"]
        unit = instruction.get("可引用原文", [{}])[0].get("source_id", "")
        if action == "仅依据作者原话整理本场景情节":
            value = {
                "items": [
                    {
                        "author_evidence": unit,
                        "actor": "沈砚",
                        "action": "借得",
                        "object": "旧尺",
                        "direction": "修船匠向沈砚借出",
                        "result": "借得旧尺",
                        "certainty": "explicit",
                        "transfer_object": "旧尺",
                        "supplier": "修船匠",
                        "recipient": "沈砚",
                        "transfer_scope": "completed",
                        "recipient_scope": "unspecified",
                        "scope_condition": "",
                    }
                ],
                "note": "原角色名称过于笼统，解释条目仍保留。",
            }
        elif action == "仅从情节原话补全角色称谓限定":
            assert TEXT not in str(instruction) and TEXT not in str(context)
            value = {
                "labels": [
                    {
                        "entity_id": eid,
                        "label": "渡口一位修船匠" if name == "修船匠" else name,
                        "status": "supported",
                        "evidence": unit,
                        "reason": "限定来自原话，不指定给出或取得方向。",
                    }
                    for eid, name in instruction["原角色名称表"].items()
                ]
            }
        elif action == "独立核对原话角色称谓限定":
            assert TEXT not in str(instruction) and TEXT not in str(context)
            value = {
                "checks": [
                    {
                        "entity_id": row["entity_id"],
                        "result": "pass" if self.supported else "unknown",
                        "evidence": unit if self.supported else "",
                        "reason": "核对来源与身份限定，未看候选。",
                    }
                    for row in instruction["限定提案"]
                ]
            }
        elif action == "逐项对照作者情节解释与本次正文":
            value = {
                "checks": [
                    {
                        "item_id": "P01",
                        "result": "pass",
                        "observed": "借尺",
                        "evidence": unit,
                        "reason": "仅为工程替身",
                        "repair": "",
                    }
                ],
                "summary": "不能代替真实语义评测。",
            }
        elif action == "只从本次正文独立提取物品与信息流向":
            assert SOURCE not in str(instruction) and "独立整理的解释提案" not in instruction
            roles = instruction["实体别名对照"]
            supplier = next((eid for eid, name in roles.items() if name == "渡口一位修船匠"), "unknown")
            recipient = next(eid for eid, name in roles.items() if name == "沈砚")
            value = {
                "flows": [
                    {
                        "object_id": "O01",
                        "supplier_id": supplier,
                        "recipient_id": recipient,
                        "stage": "completed",
                        "certainty": "explicit",
                        "evidence": unit,
                        "note": "泛称时不能确定身份，限定到渡口后才能映射老吴。",
                    }
                ],
                "note": "独立观察只读候选和名称表。",
            }
        else:
            raise AssertionError(action)
        if action == "仅依据作者原话整理本场景情节" and self.no_transfer:
            value["items"][0].update(transfer_scope="none", transfer_object="", supplier="", recipient="")
        if action == "只从本次正文独立提取物品与信息流向" and self.reverse:
            row = value["flows"][0]
            row["supplier_id"], row["recipient_id"] = row["recipient_id"], row["supplier_id"]
        if self.mutate:
            self.mutate(action, value)
        issues = validator(value)
        if issues:
            raise DomainError("evidence", "；".join(issues))
        return value


def test_qualified_source_role_keeps_original_items_and_resolves_independent_identity():
    worker = IdentityWorker()
    result = asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert result["status"] == "model_pass"
    assert result["items"][0]["supplier"] == "修船匠"
    assert result["direction_guard"]["entities"]["E01"] == "渡口一位修船匠"
    assert len(worker.frames) == 5


def test_legacy_without_opt_in_keeps_original_names_calls_and_unknown():
    worker = IdentityWorker(enabled=False)
    result = asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert result["status"] == "unverified" and len(worker.frames) == 3
    assert result["direction_guard"]["entities"]["E01"] == "修船匠"
    assert "source_identity_context" not in result["direction_guard"]


def test_unverified_source_qualifiers_never_support_a_pass_or_override_original_items():
    worker = IdentityWorker(supported=False)
    result = asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert result["status"] == "unverified"
    assert result["checks"][0]["result"] == "unknown"
    assert result["checks"][0]["original_model_check"]["result"] == "pass"
    context = result["direction_guard"]["source_identity_context"]
    assert context["unverified_ids"] == ["E01", "E02"]
    assert context["original_entities"] == context["entities"]
    assert result["items"][0]["supplier"] == "修船匠"


def test_qualified_identity_does_not_allow_reverse_transfer():
    worker = IdentityWorker(reverse=True)
    result = asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert result["status"] == "failed"
    assert result["checks"][0]["guard_override"]["code"] == "reverse_transfer"
    assert result["checks"][0]["original_model_check"]["result"] == "pass"


def test_nontransfer_scene_has_no_extra_identity_calls():
    worker = IdentityWorker(no_transfer=True)
    result = asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert result["direction_guard"]["status"] == "not_applicable"
    assert len(worker.frames) == 2


def test_new_identity_option_cannot_run_against_a_legacy_source_contract():
    worker = IdentityWorker()
    items = [
        {
            "id": "P01",
            "certainty": "explicit",
            "transfer_scope": "completed",
            "transfer_object": "旧尺",
            "supplier": "修船匠",
            "recipient": "沈砚",
        }
    ]
    with pytest.raises(DomainError, match="需要本次v2情节来源"):
        asyncio.run(independent_flows(worker, items, TEXT, [], version=LEGACY_VERSION, source_text=SOURCE))
    assert worker.frames == []


@pytest.mark.parametrize("marker", [None, "future-version"])
def test_missing_or_unknown_frozen_policy_does_not_upgrade_or_dispatch(marker):
    worker = IdentityWorker()
    if marker is None:
        worker.task["result"].pop("source_identity_context_version")
    else:
        worker.task["result"]["source_identity_context_version"] = marker
    with pytest.raises(DomainError, match="冻结版本无法确认"):
        asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert len(worker.frames) == 2


@pytest.mark.parametrize(
    "damage", ["candidate_name", "candidate_quote", "whitespace", "duplicate", "unknown_label", "sentence"]
)
def test_invalid_source_labels_stop_before_observation(damage):
    def mutate(action, value):
        if action != "仅从情节原话补全角色称谓限定":
            return
        row = value["labels"][0]
        if damage == "candidate_name":
            row["label"] = "老吴修船匠"
        elif damage == "candidate_quote":
            row["evidence"] = TEXT
        elif damage == "whitespace":
            row["label"] = " "
        elif damage == "duplicate":
            value["labels"][1]["entity_id"] = row["entity_id"]
        elif damage == "unknown_label":
            row["status"] = "unknown"
        else:
            row["label"] = SOURCE

    worker = IdentityWorker(mutate=mutate)
    with pytest.raises(DomainError):
        asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert len(worker.frames) == 3


@pytest.mark.parametrize("damage", ["candidate_quote", "duplicate", "whitespace", "unverified_pass"])
def test_invalid_independent_source_check_cannot_reach_observation(damage):
    def mutate(action, value):
        if action == "仅从情节原话补全角色称谓限定" and damage == "unverified_pass":
            value["labels"][0].update(status="unknown", label="", evidence="")
        if action != "独立核对原话角色称谓限定":
            return
        row = value["checks"][0]
        if damage == "candidate_quote":
            row["evidence"] = TEXT
        elif damage == "duplicate":
            value["checks"][1]["entity_id"] = row["entity_id"]
        elif damage == "whitespace":
            row["evidence"] = "\u3000"

    worker = IdentityWorker(mutate=mutate)
    with pytest.raises(DomainError):
        asyncio.run(audit_plot(worker, SOURCE, TEXT, 1, source_kind="author_plot"))
    assert len(worker.frames) == 4
