"""Explicit manual adoption scope; original memory candidates stay immutable."""

import hashlib
import json

from .db import DomainError

VERSION = "candidate-memory-selection/1"
TEXT_KINDS = {"chapter_draft", "text_patch"}


def facts_sha256(payload):
    memory = payload.get("memory", {})
    if not isinstance(memory, dict) or not isinstance(memory.get("facts", []), list):
        return None
    try:
        encoded = json.dumps(
            memory.get("facts", []),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except ValueError, TypeError, UnicodeError:
        return None
    return hashlib.sha256(encoded).hexdigest()


def selection(artifact, indices=None, reason="", *, require_reason=True, expected_sha256=None):
    if not isinstance(reason, str) or len(reason) > 2000:
        raise DomainError("memory_selection", "事实选择理由须是最多2000字的文字。", 400)
    reason = reason.strip()
    if artifact["kind"] not in TEXT_KINDS:
        if indices is not None or reason or expected_sha256 is not None:
            raise DomainError("memory_selection_kind", "仅正文或局部替换候选支持逐项选择附带事实。", 400)
        return None
    memory = artifact["payload"].get("memory", {})
    if not isinstance(memory, dict) or not isinstance(memory.get("facts", []), list):
        raise DomainError("memory_selection", "候选的事实列表无法核对，未采用正文或事实。", 409)
    count = len(memory.get("facts", []))
    fingerprint = facts_sha256(artifact["payload"])
    if fingerprint is None:
        raise DomainError("memory_selection", "候选事实无法按规范JSON核对，未采用正文或事实。", 409)
    if indices is None:
        selected = list(range(count))
    else:
        if (
            not isinstance(indices, list)
            or any(type(index) is not int or index < 0 or index >= count for index in indices)
            or len(set(indices)) != len(indices)
        ):
            raise DomainError(
                "memory_selection", "事实编号须为不重复、未越界的非负整数，不能使用布尔值。", 400
            )
        selected = sorted(indices)
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in expected_sha256)
    ):
        raise DomainError("memory_selection_basis", "事实选择来源须为服务器返回的64位SHA256。", 400)
    if indices and expected_sha256 is None:
        raise DomainError("memory_selection_basis", "请重新读取本候选的事实来源，再逐项选择采用。", 409)
    if expected_sha256 is not None and expected_sha256.lower() != fingerprint:
        raise DomainError(
            "memory_changed", "附带事实已被复检或其他窗口更新，请重新核对选择；正文未改动。", 409
        )
    omitted = [index for index in range(count) if index not in selected]
    if omitted and not reason and require_reason:
        raise DomainError(
            "memory_selection_reason", "遗漏任何附带事实时，请说明保留为待核对候选的理由。", 400
        )
    return {
        "version": VERSION,
        "fact_count": count,
        "selected_indices": selected,
        "omitted_indices": omitted,
        "reason": reason,
        "candidate_revision": artifact["payload"].get("_candidate_revision", 0),
        "memory_facts_sha256": fingerprint,
    }


def check_replay(receipt, chosen, artifact, *, explicit_selection=False):
    previous = receipt.get("memory_selection")
    if chosen is not None and (previous is None or "memory_facts_sha256" not in previous):
        # Old receipts can only replay the unchanged legacy all-facts call.
        default = selection(artifact)
        old_default = {key: value for key, value in default.items() if key != "memory_facts_sha256"}
        if explicit_selection or chosen != default or (previous is not None and previous != old_default):
            raise DomainError(
                "idempotency", "旧回执未绑定事实来源，只能重放原默认全选；不能用于新选择范围。", 409
            )
        return
    if previous != chosen:
        raise DomainError("idempotency", "采纳回执对应的事实选择或理由不同，未重放旧范围或再次写入。", 409)
