"""Keep the current text target distinct from its saved version baseline."""

from copy import deepcopy
import hashlib


def text_context(context, text, purpose):
    result = deepcopy(context or {})
    target_hash = hashlib.sha256(text.encode()).hexdigest()
    chapter = result.get("chapter")
    if chapter:
        result.setdefault(
            "source_version_basis",
            {
                "chapter_id": chapter.get("id"),
                "chapter_revision": chapter.get("revision"),
                "story_revision": result.get("branch", {}).get("story_revision"),
                "saved_content_sha256": hashlib.sha256(chapter.get("content", "").encode()).hexdigest(),
            },
        )
        baseline = result["source_version_basis"]
        is_candidate = target_hash != baseline["saved_content_sha256"]
        target_chapter = {
            **chapter,
            "content": text,
            "base_revision": baseline["chapter_revision"],
            "content_sha256": target_hash,
            "content_status": "unadopted_candidate_under_review"
            if is_candidate
            else "saved_source_under_review",
            "content_role": "候选待审正文，仅供本次处理，尚未持久化或采纳；base_revision是原稿来源基线。"
            if is_candidate
            else "本次处理的已保存来源正文。",
        }
        if is_candidate:
            target_chapter.pop("revision", None)
        result["chapter"] = target_chapter
    result["text_target"] = {
        "purpose": purpose,
        "content_sha256": target_hash,
        "base_revision": result.get("source_version_basis", {}).get("chapter_revision"),
        "utf16_length": len(text.encode("utf-16-le")) // 2,
        "policy": "只处理本次正文与其可引用原文。原审稿、旧引文和历史问题只是待验证意见，不能视为本次正文仍有该问题。",
    }
    return result
