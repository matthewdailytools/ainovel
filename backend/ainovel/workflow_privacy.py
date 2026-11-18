"""Recognize frozen M6 copies of an entire reference selection."""

import json

REFERENCE_SCOPE = "历史体检含参考选段原句，原报告和关联交付已保留本机。"


def _object(value):
    return value if isinstance(value, dict) else {}


def _json_object(value):
    if isinstance(value, str):
        try:
            return _object(json.loads(value))
        except ValueError, RecursionError:
            pass
    return {}


def has_reference_selection(items):
    if not isinstance(items, list):
        return False
    for item in items:
        if _object(item).get("check_id") != "08":
            continue
        basis = _object(item.get("basis"))
        for field in ("card_references", "planned_card_references"):
            rows = basis.get(field)
            if isinstance(rows, list) and any(
                isinstance(quote := _object(_object(row).get("source")).get("quote"), str) and quote
                for row in rows
            ):
                return True
    return False


def evidence_has_reference_selection(document):
    return _object(document).get("kind") == "inspection" and has_reference_selection(
        _json_object(document.get("body")).get("items")
    )


def sync_has_reference_selection(payload):
    match _object(payload).get("kind"):
        case "workflow-inspection":
            return has_reference_selection(
                _object(_json_object(payload.get("entry_json")).get("report")).get("items")
            )
        case "evidence":
            return evidence_has_reference_selection(payload.get("document"))
    return False


def sync_completion_report(payload):
    if _object(payload).get("kind") == "workflow-completion":
        manifest = _object(_json_object(payload.get("entry_json")).get("manifest"))
        identifier = manifest.get("inspection_id")
        if isinstance(identifier, str):
            return identifier
    return None
