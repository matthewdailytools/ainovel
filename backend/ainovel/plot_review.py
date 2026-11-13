"""Opt-in plot comparison for ordinary writing and frozen workflow chapter plans."""

from copy import deepcopy
import hashlib
import json

from .db import DomainError
from .plot_audit import VERSION, LEGACY_VERSION, audit_plot


def module_plot_source(request):
    """Strip the existing direction handoff envelope, retaining only author text."""
    value = request["instruction"]
    prefix, marker = "作者本次确认并可编辑的情节方向：\n", "\n\n方向来源记录："
    if not value.startswith(prefix):
        return value
    try:
        start = value.rindex(marker)
        end = value.index("\n以上来源只用于追踪", start)
        basis = json.loads(value[start + len(marker) : end])
        if not isinstance(basis, dict) or any(
            not isinstance(basis.get(key), str) or not basis[key]
            for key in ("advice_id", "novel_id", "branch_id", "chapter_id")
        ):
            raise ValueError
        if any(basis[key] != request[key] for key in ("novel_id", "branch_id", "chapter_id")):
            raise ValueError
        for key in ("candidate_revision", "loaded_revision", "source_revision"):
            v = basis.get(key)
            if key == "source_revision" and v is None:
                continue
            if type(v) is not int or v < 0:
                raise ValueError
        if start < len(prefix) or not value[len(prefix) : start].strip():
            raise ValueError
    except (ValueError, KeyError, TypeError) as error:
        raise DomainError("plot_source", "选定方向来源无法核对，未将追踪记录解释为作者情节。", 409) from error
    return value[len(prefix) : start]


def review_passed(review):
    return (
        all(check["result"] == "pass" for check in review["checks"])
        and not any(finding["severity"] == "blocker" for finding in review["findings"])
        and review.get("plot_audit", {}).get("status", "model_pass") == "model_pass"
    )


def unresolved_without_blocker(review):
    return review.get("plot_audit", {}).get("status") == "unverified" and not any(
        finding["severity"] == "blocker" for finding in review["findings"]
    )


def bind_plot_audit(audit, author_text, text, source_kind, *, candidate_revision=0, target=None):
    """Bind a newly returned report without mutating saved reports or paid inputs."""
    bound = deepcopy(audit)
    version = audit.get("version", LEGACY_VERSION)
    if version not in {LEGACY_VERSION, VERSION} or (
        version == VERSION
        and any(
            item.get("recipient_scope") not in {"only", "unrestricted", "unspecified"}
            or not isinstance(item.get("scope_condition"), str)
            for item in audit["items"]
        )
    ):
        raise DomainError("plot_version", "情节报告未执行对应的接收范围契约，未盖成新版本。", 409)
    bound.update(
        version=version,
        source_kind=source_kind,
        source={"kind": source_kind, "text_sha256": hashlib.sha256(author_text.encode()).hexdigest()},
        candidate_sha256=hashlib.sha256(text.encode()).hexdigest(),
        candidate_revision=candidate_revision,
    )
    if target is not None:
        bound["target"] = {**target, "content_sha256": bound["candidate_sha256"]}
    return bound


async def attach_plot_review(worker, review, author_text, text, number, source_kind, *, target=None):
    """Keep the original review; an AI interpretation never creates author facts."""
    result = deepcopy(review)
    audit = bind_plot_audit(
        await audit_plot(worker, author_text, text, number, source_kind=source_kind),
        author_text,
        text,
        source_kind,
        target=target,
    )
    result["summary_before_plot_audit"] = result["summary"]
    result["findings"].extend(audit["findings"])
    result["plot_audit"] = audit
    result["summary"] += (
        "\n补充本次情节逐项核对："
        + {
            "failed": "发现明确不符，保留依据并按有限修订政策处理。",
            "unverified": "仍有未知或歧义，不据此标通过，也不强行改判作者条件。",
            "model_pass": "模型标通过，仍待作者核对。",
        }[audit["status"]]
    )
    return result
