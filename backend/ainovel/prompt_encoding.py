"""Lossless wire references for repeated target prose; storage stays unchanged."""

from copy import deepcopy
import hashlib

from .db import DomainError, dumps
from .prompt_sources import compact_sources, expand_sources
from .prompt_units import compact_units, expand_units

BODY_FIELDS = ("正文", "候选正文", "本次候选", "原稿")


def encode_prompt(instruction, context):
    frame = {"任务": instruction, "上下文": context}
    # Keep the small-request representation compatible. Larger requests benefit
    # from aliasing the exact duplicate; this is not a content-size exemption.
    if len(dumps(frame).encode()) < 60_000:
        return frame
    chapter = context.get("chapter")
    if not isinstance(chapter, dict) or not isinstance(chapter.get("content"), str):
        return compact_sources(compact_units(frame, BODY_FIELDS))
    content = chapter["content"]
    if not content:
        return compact_sources(compact_units(frame, BODY_FIELDS))
    owner, path = instruction, ["任务"]
    key = next((key for key in BODY_FIELDS if owner.get(key) == content), None)
    # Format repairs retain the same original task inside an explicit wrapper.
    # Never search arbitrary user dictionaries or prior model output for prose.
    if key is None and instruction.get("动作") == "仅修正结构化产物格式":
        for _ in range(3):
            if not isinstance(owner.get("原始任务"), dict):
                break
            owner = owner["原始任务"]
            path.append("原始任务")
            key = next((key for key in BODY_FIELDS if owner.get(key) == content), None)
            if key is not None:
                break
    if key is None:
        return compact_sources(compact_units(frame, BODY_FIELDS))
    encoded = deepcopy(frame)
    target = encoded["上下文"]["chapter"]
    del target["content"]
    target["content_ref"] = {
        "path": [*path, key],
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "utf16_length": len(content.encode("utf-16-le")) // 2,
    }
    encoded["正文引用约定"] = (
        "source-ref/1：上下文.chapter.content_ref指向同一请求任务中的完整本次正文，"
        "path按数组中的字段逐层读取；其含义与原chapter.content相同，不是空稿或被省略内容。"
        "完整正文和逐字引用单元仍在任务中；来源版本、候选身份、视角与时点边界保持。"
    )
    return compact_sources(compact_units(encoded, BODY_FIELDS))


def expand_prompt(frame):
    """Verify and reconstruct the original frame for audit/tests, never eval paths."""
    result = expand_units(expand_sources(frame))
    chapter = result.get("上下文", {}).get("chapter")
    if not isinstance(chapter, dict) or "content_ref" not in chapter:
        return result
    reference = chapter["content_ref"]
    path = reference.get("path")
    if (
        not isinstance(path, list)
        or not 2 <= len(path) <= 5
        or path[0] != "任务"
        or path[-1] not in BODY_FIELDS
        or any(part != "原始任务" for part in path[1:-1])
    ):
        raise DomainError("prompt_reference", "正文引用路径无效，未发送。")
    text = result
    for part in path:
        text = text.get(part) if isinstance(text, dict) else None
    if (
        not isinstance(text, str)
        or hashlib.sha256(text.encode()).hexdigest() != reference.get("sha256")
        or len(text.encode("utf-16-le")) // 2 != reference.get("utf16_length")
    ):
        raise DomainError("prompt_reference", "正文引用内容与哈希或UTF-16长度不一致。")
    chapter["content"] = text
    del chapter["content_ref"]
    result.pop("正文引用约定", None)
    return result
