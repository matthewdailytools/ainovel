"""Lossless condition references for the versioned conditional chapter review."""

from copy import deepcopy

VERSION = "fixed-check-id/1"
RESULTS = ["pass", "fail", "unknown"]
REPAIR_GUIDANCE = (
    "checks[].condition只填写固定条件编号表的FIX编号，严格按表顺序各一次，不能重抄、缩写或改写长原话。"
    "本章判断只输出chapter_result；编号表allowed_results约束此字段，不输出整书履约结论。"
    "前次产物可能已由后端解引用为原话；重新输出时仍须使用对应编号。保持原有unknown/fail及其依据，"
    "不得为了格式通过改成pass或跳过事项；只有编号表允许的项才可not_triggered，并保留全部适用性字段。"
)


def condition_table(fixed, origins, eligible, assessment=None):
    table = []
    for index, (condition, origin) in enumerate(zip(fixed, origins, strict=True), 1):
        identifier = f"FIX{index:03}"
        allowed = [*RESULTS, *(["not_triggered"] if condition in eligible else [])]
        if assessment is not None and condition in eligible:
            observed = assessment.get(identifier, {})
            trigger = observed.get("trigger", "uncertain")
            obligation = observed.get("chapter_obligation", "uncertain")
            if trigger not in {"triggered", "not_triggered"} or obligation not in {
                "required",
                "not_required",
            }:
                allowed = ["fail", "unknown"]
            elif trigger == "not_triggered" and obligation == "not_required":
                allowed = ["fail", "unknown", *(["not_triggered"] if condition in eligible else [])]
            else:
                allowed = RESULTS[:]
        table.append(
            {
                "condition_id": identifier,
                "condition": condition,
                "scope": origin["scope"],
                "allowed_results": allowed,
            }
        )
    return table


def id_contract(contract, table):
    """Keep quote enums; specialize each ID with nested, wire-supported unions."""
    result = deepcopy(contract)
    base = result["$defs"]["ConditionalReviewCheck"]
    verdict = "chapter_result" if "chapter_result" in base["properties"] else "result"
    if table:
        base["properties"]["condition"]["enum"] = [row["condition_id"] for row in table]
    else:
        base["properties"]["condition"].pop("enum", None)
    variants = []
    for row in table:
        ordinary = deepcopy(base)
        properties = ordinary["properties"]
        properties["condition"]["enum"] = [row["condition_id"]]
        properties[verdict]["enum"] = [value for value in row["allowed_results"] if value != "not_triggered"]
        properties["scope"]["enum"] = [row["scope"]]
        variants.append(ordinary)
        if "not_triggered" in row["allowed_results"]:
            conditional = deepcopy(ordinary)
            properties = conditional["properties"]
            for key, value in (
                (verdict, "not_triggered"),
                ("scope", "book"),
                ("applicability", "not_triggered"),
                ("chapter_obligation", "not_required"),
            ):
                properties[key]["enum"] = [value]
            properties["explanation"]["minLength"] = 1
            variants.append(conditional)
    checks = result["properties"]["checks"]
    if variants:
        checks["items"] = {"anyOf": variants}
    else:
        # A zero-length checks array still needs a valid item schema on the wire.
        checks["items"] = deepcopy(base)
        checks["items"]["properties"][verdict]["enum"] = RESULTS[:]
    return result


def resolve_checks(checks, table):
    """Decode exact IDs/verbatim originals without reordering or editing judgments."""
    by_id = {row["condition_id"]: row for row in table}
    issues, actual = [], []
    for index, check in enumerate(checks):
        raw = check["condition"]
        expected = table[index] if index < len(table) else None
        if raw in by_id:
            source = by_id[raw]
            kind = "id"
        elif expected is not None and raw == expected["condition"]:
            # Compatibility for exact pre-ID offline adapters, never paraphrases.
            source = expected
            kind = "verbatim"
        else:
            issues.append(
                f"checks[{index}].condition不是对应的合法FIX编号或完全相同的原话；"
                "核对未知编号、改写、漏项或错序，按编号表重填，不改原话或结论。"
            )
            continue
        actual.append({"condition_id": source["condition_id"], "input_kind": kind})
        check["condition"] = source["condition"]
        if check["result"] not in source["allowed_results"]:
            issues.append(
                f"checks[{index}]的result不在{source['condition_id']}允许范围；"
                "依编号表及独立适用性依据重新核对，不能借改编号或改写原话跳过。"
            )
        if expected is None or source["condition_id"] != expected["condition_id"]:
            issues.append(f"checks[{index}]编号错序或重复；须按冻结编号原顺序各一次，不排序修正。")
    return issues, actual


def bind_ids(review, table, actual):
    review.update(
        condition_id_version=VERSION,
        condition_id_map=deepcopy(table),
        condition_check_ids=deepcopy(actual),
    )
    return review
