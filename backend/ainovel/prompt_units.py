"""Use existing verbatim evidence units as a reversible canonical prose copy."""

from copy import deepcopy
import hashlib

from .db import DomainError, dumps


def compact_units(frame, body_fields):
    task = frame["任务"]
    units_key = next(
        (key for key in ("可引用原文", "可替换原文") if isinstance(task.get(key), list) and task[key]), None
    )
    if units_key is None:
        return frame
    units = task[units_key]
    if any(
        not isinstance(unit, dict) or not {"source_id", "start", "end", "text"} <= unit.keys()
        for unit in units
    ):
        return frame
    if len({unit["source_id"] for unit in units}) != len(units):
        return frame
    encoded = deepcopy(frame)
    changed = False
    for key in body_fields:
        text = task.get(key)
        if not isinstance(text, str):
            continue
        raw = text.encode("utf-16-le")
        ordered = sorted(units, key=lambda unit: unit["start"])
        cursor, gaps, order = 0, [], []
        for unit in ordered:
            start, end = unit["start"], unit["end"]
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or not cursor <= start < end <= len(raw) // 2
            ):
                break
            try:
                if raw[start * 2 : end * 2].decode("utf-16-le") != unit["text"]:
                    break
                gaps.append(raw[cursor * 2 : start * 2].decode("utf-16-le"))
            except UnicodeDecodeError:
                break
            order.append(unit["source_id"])
            cursor = end
        else:
            gaps.append(raw[cursor * 2 :].decode("utf-16-le"))
            encoded["任务"][key] = {
                "body_units_ref": {
                    "path": ["任务", units_key],
                    "order": order,
                    "gaps": gaps,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "utf16_length": len(raw) // 2,
                }
            }
            changed = True
    if not changed:
        return frame
    encoded["逐字正文约定"] = (
        "body-units/1：任务正文若为body_units_ref，完整正文就是path指定可引用/可替换原文数组中的逐字text，"
        "按order中的source_id顺序读取，各单元前插入对应gaps，最后插入末尾gaps。"
        "这些单元包含本次实际正文，不是摘要或删节；source_id仍引用其紧邻的完整原句，未改变选区、证据或检查范围。"
    )
    return encoded if len(dumps(encoded).encode()) < len(dumps(frame).encode()) else frame


def expand_units(frame):
    result = deepcopy(frame)
    for key, value in list(result.get("任务", {}).items()):
        if not isinstance(value, dict) or set(value) != {"body_units_ref"}:
            continue
        ref = value["body_units_ref"]
        try:
            if ref["path"] not in (["任务", "可引用原文"], ["任务", "可替换原文"]):
                raise ValueError("path")
            units = result["任务"][ref["path"][1]]
            indexed = {unit["source_id"]: unit for unit in units}
            if (
                len(indexed) != len(units)
                or len(ref["order"]) != len(units)
                or set(ref["order"]) != set(indexed)
                or len(ref["gaps"]) != len(units) + 1
            ):
                raise ValueError("coverage")
            text = ""
            for index, identifier in enumerate(ref["order"]):
                text += ref["gaps"][index]
                unit = indexed[identifier]
                if len(text.encode("utf-16-le")) // 2 != unit["start"]:
                    raise ValueError("start")
                text += unit["text"]
                if len(text.encode("utf-16-le")) // 2 != unit["end"]:
                    raise ValueError("end")
            text += ref["gaps"][-1]
            if (
                hashlib.sha256(text.encode()).hexdigest() != ref["sha256"]
                or len(text.encode("utf-16-le")) // 2 != ref["utf16_length"]
            ):
                raise ValueError("hash/length")
            result["任务"][key] = text
        except (KeyError, TypeError, ValueError, UnicodeError) as error:
            raise DomainError("prompt_reference", "逐字正文单元的范围、顺序或哈希不一致。") from error
    result.pop("逐字正文约定", None)
    return result
