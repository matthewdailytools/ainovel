"""Browser source fixtures must satisfy the same public source-binding schemas."""

from copy import deepcopy
import importlib.util
from pathlib import Path

from ainovel import authored_source_scope as authored
from ainovel import workflow_plan_recipient_scope as scope
from ainovel.plot_audit import ScopeBoundAuthorPlot

SPEC = importlib.util.spec_from_file_location(
    "isolated_authored_scope_fixture",
    Path(__file__).resolve().parents[2] / "scripts/e2e_authored_scope_fixture.py",
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def test_explicit_only_browser_source_has_complete_binding_evidence():
    text = "plot-scope-exclusive-e2e：本章中，沈砚把潮水读数只告诉师傅，并让师傅记下。"
    instruction = {
        "动作": authored.ACTION,
        "作者本次情节原话": text,
        "input_sha256": authored.sha(text),
        "可引用原文": scope.units(text),
    }
    inventory = scope.ScopeInventory.model_validate(FIXTURE.source_observation(instruction)).model_dump()
    assert not scope.inventory_issues(
        inventory, instruction["input_sha256"], {u["source_id"]: u for u in scope.units(text)}
    )
    proposal = {
        "items": [
            {
                "author_evidence": text,
                "actor": "沈砚",
                "action": "告诉",
                "object": "潮水读数",
                "direction": "沈砚向师傅告知潮水读数",
                "result": "让师傅记下",
                "certainty": "explicit",
                "transfer_object": "潮水读数",
                "supplier": "沈砚",
                "recipient": "师傅",
                "transfer_scope": "completed",
                "recipient_scope": "only",
                "scope_condition": "本章中",
            }
        ],
        "note": "确定性工程样例",
    }
    bound = FIXTURE.bind_interpretation(
        {"动作": "仅依据作者原话整理本场景情节", authored.KEY: authored.VERSION, "作者原话": text}, proposal
    )
    bound = ScopeBoundAuthorPlot.model_validate(bound).model_dump()
    assert not authored.binding_issues(bound, {"inventory": inventory})
    broken = deepcopy(bound)
    broken["items"][0]["recipient_scope"] = "unspecified"
    assert authored.binding_issues(broken, {"inventory": inventory})


def test_legacy_browser_interpretation_is_not_given_source_scope_fields():
    value = {"items": [{"certainty": "ambiguous"}], "note": "旧提案"}
    assert FIXTURE.source_observation({"动作": "其他动作"}) is None
    assert FIXTURE.bind_interpretation({"动作": "仅依据作者原话整理本场景情节"}, deepcopy(value)) == value
