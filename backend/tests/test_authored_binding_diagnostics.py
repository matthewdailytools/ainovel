"""Item-local core diagnostics must identify the original author item, not P01."""

from copy import deepcopy

import pytest

from ainovel import authored_source_scope as authored
from ainovel import workflow_plan_recipient_scope as core
from ainovel.plot_audit import ScopeBoundAuthorPlot


def binding_case():
    """Original anonymous fixture: one real only and one specific-person exclusion."""
    items, observations, checks = [], [], []
    for number in range(1, 11):
        sender, recipient, material = f"投递员{number}", f"驿站{number}", f"文件{number}"
        text = f"{sender}把{material}送到{recipient}。"
        kind, extent, stage = "named_destination", "unspecified", "completed"
        if number == 3:
            text = f"{sender}只把{material}交给{recipient}。"
            kind, extent = "recipient_exclusivity", "only"
        elif number == 10:
            sender, recipient, material = "值守人", "访客", "排班消息"
            text = "值守人不把排班消息告诉访客。"
            kind, extent, stage = "specific_recipient_exclusion", "only", "none"
        observation_id = f"O{number:02}"
        items.append(
            {
                "author_evidence": text,
                "actor": sender,
                "action": "不告知" if number == 10 else "投递",
                "object": material,
                "direction": f"{sender}→{recipient}",
                "result": "未告知该访客" if number == 10 else "已投递",
                "certainty": "explicit",
                "transfer_object": material,
                "supplier": sender,
                "recipient": recipient,
                "transfer_scope": stage,
                "recipient_scope": extent,
                "scope_condition": "",
                "scope_observation_ids": [observation_id],
                "scope_binding_status": "supported",
                "scope_binding_reason": "仅核对当前匿名原句，特定对象否定不证明排除所有其他人。",
            }
        )
        observations.append(
            {
                "observation_id": observation_id,
                "kind": kind,
                "supplier": sender,
                "recipient": recipient,
                "transfer_object": material,
                "transfer_scope": stage,
                "evidence": text,
                "restriction_evidence": text if number in {3, 10} else "",
                "condition": "",
                "reason": "原句来源，与生成正文或真实案例无关。",
            }
        )
        checks.append(
            {
                "source_id": f"SCOPE{number:04}",
                "status": "covered",
                "observation_ids": [observation_id],
                "reason": "本句直接绑定本项。",
            }
        )
    value = ScopeBoundAuthorPlot.model_validate({"items": items, "note": "匿名工程用例。"}).model_dump()
    inventory = core.ScopeInventory.model_validate(
        {"input_sha256": "a" * 64, "observations": observations, "unit_checks": checks, "note": "匿名观察。"}
    ).model_dump()
    return value, {"version": core.CURRENT_VERSION, "inventory": inventory}


def test_p10_invalid_exclusivity_is_rejected_and_diagnostic_names_p10():
    value, inventory = binding_case()
    before = deepcopy(value)
    issues = authored.binding_issues(value, inventory)
    assert len(issues) == 1
    assert issues[0].startswith("P10的only没有同一传递的明确来源观察")
    assert "P01" not in issues[0] and "P03" not in issues[0]
    assert value == before
    assert not authored.binding_valid(authored.core_value(value), authored.binding_data(value), inventory)


@pytest.mark.parametrize("version", sorted(authored.SUPPORTED))
def test_fix_only_p10_keeps_the_genuine_p03_only_and_valid_binding(version):
    value, inventory = binding_case()
    before = deepcopy(value)
    value["items"][9]["recipient_scope"] = "unspecified"
    assert not authored.binding_issues(value, inventory)
    assert not authored.binding_issues(value, inventory, legacy_diagnostic_labels=True)
    assert value["items"][0] == before["items"][0]
    assert value["items"][2] == before["items"][2]
    assert value["items"][2]["recipient_scope"] == "only"
    assert authored.binding_valid(
        authored.core_value(value), authored.binding_data(value, version), inventory
    )


def test_multiple_invalid_items_each_keep_their_outer_item_number():
    value, inventory = binding_case()
    value["items"][2]["scope_condition"] = "额外条件"
    value["items"][5]["recipient_scope"] = "only"
    issues = authored.binding_issues(value, inventory)
    assert len(issues) == 3
    assert [issue[:3] for issue in issues] == ["P03", "P06", "P10"]
    assert "限制条件须逐字对应" in issues[0]
    assert "only没有同一传递" in issues[1] and "only没有同一传递" in issues[2]
    legacy = authored.binding_issues(value, inventory, legacy_diagnostic_labels=True)
    assert [issue[:3] for issue in legacy] == ["P01", "P01", "P01"]
    assert [issue[3:] for issue in issues] == [issue[3:] for issue in legacy]


def test_p01_core_diagnostic_text_remains_byte_identical():
    value, inventory = binding_case()
    value["items"][9]["recipient_scope"] = "unspecified"
    value["items"][0]["recipient_scope"] = "only"
    subset = {
        "version": core.CURRENT_VERSION,
        "inventory": {"observations": [inventory["inventory"]["observations"][0]]},
    }
    expected = core.binding_issues({"items": [value["items"][0]], "note": value["note"]}, subset)
    assert len(expected) == 1 and expected[0].startswith("P01")
    assert authored.binding_issues(value, inventory) == expected


@pytest.mark.parametrize("legacy", [False, True])
def test_unprefixed_core_global_issue_is_preserved_verbatim(monkeypatch, legacy):
    value, inventory = binding_case()
    for item in value["items"][:-1]:
        item["scope_binding_status"] = "unknown"
    global_issue = "原文范围观察均须在解释中保持并绑定，不能遗漏真实限制或只挑有利观察。"
    embedded = "全局文字中出现P01仍须原样保留。"

    def core_issues(local_value, subset):
        assert len(local_value["items"]) == 1
        assert local_value["items"][0] is value["items"][9]
        assert subset["inventory"]["observations"][0]["observation_id"] == "O10"
        return ["P01的only缺少来源。", global_issue, embedded]

    monkeypatch.setattr(core, "binding_issues", core_issues)
    wanted = "P01" if legacy else "P10"
    assert authored.binding_issues(value, inventory, legacy_diagnostic_labels=legacy) == [
        wanted + "的only缺少来源。",
        global_issue,
        embedded,
    ]


@pytest.mark.parametrize("legacy", [False, True])
def test_unknown_binding_and_outer_full_observation_guard_remain_unchanged(legacy):
    value, inventory = binding_case()
    value["items"][9]["scope_binding_status"] = "unknown"
    before = deepcopy(value)
    assert not authored.binding_issues(value, inventory, legacy_diagnostic_labels=legacy)
    assert value == before
    value["items"][9]["scope_observation_ids"] = []
    assert authored.binding_issues(value, inventory, legacy_diagnostic_labels=legacy) == [
        "所有原文范围观察均须关联并保留，未明可unknown，不能删除真实限制。"
    ]


@pytest.mark.parametrize("index", [0, 2, 5, 9])
def test_legacy_and_default_both_reject_same_invalid_item_at_each_position(index):
    value, inventory = binding_case()
    value["items"][9]["recipient_scope"] = "unspecified"
    if index == 2:
        value["items"][index]["scope_condition"] = "无来源的条件"
    else:
        value["items"][index]["recipient_scope"] = "only"
    default = authored.binding_issues(value, inventory)
    legacy = authored.binding_issues(value, inventory, legacy_diagnostic_labels=True)
    subset = {
        "version": core.CURRENT_VERSION,
        "inventory": {"observations": [inventory["inventory"]["observations"][index]]},
    }
    original_local = core.binding_issues({"items": [value["items"][index]], "note": value["note"]}, subset)
    assert len(default) == len(legacy) == 1
    assert bool(default) == bool(legacy) is True
    assert default[0].startswith(f"P{index + 1:02}")
    assert legacy == original_local
    assert legacy[0].startswith("P01")
    assert default[0][3:] == legacy[0][3:]
    assert not authored.binding_valid(authored.core_value(value), authored.binding_data(value), inventory)


def test_internal_legacy_labels_reproduce_frozen_anonymous_event_issues_exactly():
    value, inventory = binding_case()
    old_event_issues = [
        "P01的only没有同一传递的明确来源观察；命名目的地、一份抄件、另项许可或特定接收人排除都不能证明排他。保留原收件方与原动作，不新增范围。"
    ]
    assert authored.binding_issues(value, inventory, legacy_diagnostic_labels=True) == old_event_issues
    current = authored.binding_issues(value, inventory)
    assert len(current) == len(old_event_issues) == 1
    assert current[0].startswith("P10") and current[0][3:] == old_event_issues[0][3:]
