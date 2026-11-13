"""Reversible source pooling in the model wire view, without selecting away facts."""

from copy import deepcopy
import hashlib
import json

from .db import DomainError, dumps


def compact_sources(frame):
    pool, indexed = {}, {}
    recent = frame.get("上下文", {}).get("recent", [])

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        if {"chapter_id", "revision", "start", "end", "quote"} <= value.keys() and isinstance(
            value["quote"], str
        ):
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if key in indexed:
                return {"source_ref": indexed[key]}
            identifier = f"S{len(pool) + 1:04}"
            indexed[key] = identifier
            source = deepcopy(value)
            for index, chapter in enumerate(recent):
                if chapter.get("id") != source["chapter_id"] or chapter.get("revision") != source["revision"]:
                    continue
                text = chapter.get("content")
                start, end = source["start"], source["end"]
                if (
                    not isinstance(text, str)
                    or not isinstance(start, int)
                    or not isinstance(end, int)
                    or not 0 <= start <= end <= len(text.encode("utf-16-le")) // 2
                ):
                    continue
                try:
                    quote = text.encode("utf-16-le")[start * 2 : end * 2].decode("utf-16-le")
                except UnicodeDecodeError:
                    continue
                if quote == source["quote"]:
                    del source["quote"]
                    source["quote_ref"] = {
                        "recent_index": index,
                        "sha256": hashlib.sha256(quote.encode()).hexdigest(),
                    }
                    break
            pool[identifier] = source
            return {"source_ref": identifier}
        return {key: walk(item) for key, item in value.items()}

    encoded = walk(frame)
    if not pool:
        return frame
    encoded["来源引用表"] = pool
    encoded["来源引用约定"] = (
        "source-pool/1：source_ref在来源引用表读取完整来源对象；quote_ref按recent_index在上下文.recent"
        "同章节同修订content中读取来源start/end的UTF-16范围原句，SHA256校验同文。"
        "仅共享原文，不改变人物、状态或场景entry/exit列表的时点、视角和可见性；不是新增事实或被省略的证据。"
    )
    return encoded if len(dumps(encoded).encode()) < len(dumps(frame).encode()) else frame


def expand_sources(frame):
    if "来源引用表" not in frame:
        return deepcopy(frame)
    result = deepcopy(frame)
    pool = result.pop("来源引用表")
    recent = result.get("上下文", {}).get("recent", [])
    for source in pool.values():
        if "quote_ref" not in source:
            continue
        ref = source.pop("quote_ref")
        try:
            index = ref["recent_index"]
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise ValueError("index")
            chapter = recent[index]
            if chapter["id"] != source["chapter_id"] or chapter["revision"] != source["revision"]:
                raise ValueError("source version")
            raw = chapter["content"].encode("utf-16-le")
            start, end = source["start"], source["end"]
            if not 0 <= start <= end <= len(raw) // 2:
                raise ValueError("range")
            quote = raw[start * 2 : end * 2].decode("utf-16-le")
            if hashlib.sha256(quote.encode()).hexdigest() != ref["sha256"]:
                raise ValueError("hash")
            source["quote"] = quote
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as error:
            raise DomainError("prompt_reference", "来源引用的版本、范围或哈希不一致。") from error

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"source_ref"}:
            if value["source_ref"] not in pool:
                raise DomainError("prompt_reference", "来源引用标识不存在。")
            return deepcopy(pool[value["source_ref"]])
        return {key: walk(item) for key, item in value.items()}

    result.pop("来源引用约定", None)
    return walk(result)
