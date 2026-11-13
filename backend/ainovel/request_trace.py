"""Allowlisted failed-request summaries joined to the provider's real ledger row."""

import re
import sqlite3

from .openai_schema import VERSION


def failure_trace(error, settings, budget, task):
    details = getattr(error, "provider_details", None)
    if not isinstance(details, dict):
        if details is not None:
            error.provider_details = None
        return {}
    keys = [provider.key for provider in settings.providers.values() if provider.key]

    def safe_string(value, pattern):
        return (
            isinstance(value, str)
            and re.fullmatch(pattern, value) is not None
            and not any(key in value for key in keys)
        )

    # Replacing the exception details also keeps the outer stopped event from
    # serializing injected schema/prompt/config fields on a failed adapter.
    safe_details = {
        key: value
        for key, value in details.items()
        if key in {"event_type", "status", "provider_code", "reason", "service_tier"}
        and safe_string(value, r"[A-Za-z0-9_.:-]{0,160}")
    }
    error.provider_details = safe_details
    raw = details.get("request_metadata")
    provider = settings.providers.get(task["payload"]["account"])
    if not isinstance(raw, dict) or not provider:
        return {}
    if (
        raw.get("wire_schema_version") != VERSION
        or not safe_string(raw.get("wire_schema_sha256"), r"[0-9a-f]{64}")
        or not safe_string(raw.get("request_sha256"), r"[0-9a-f]{64}")
        or type(raw.get("request_bytes")) is not int
        or not 0 < raw["request_bytes"] <= provider.max_input_bytes
        or not safe_string(details.get("call_id"), r"[0-9a-f]{32}")
        or type(details.get("sent")) is not bool
    ):
        return {}
    try:
        with budget.db() as db:
            row = db.execute(
                "SELECT task_id,account,provider FROM calls WHERE id=?", (details["call_id"],)
            ).fetchone()
    except sqlite3.Error, OSError:
        return {}
    if (
        not row
        or row["task_id"] != task["id"]
        or row["account"] != provider.id
        or row["provider"] != provider.kind
    ):
        return {}
    trace = {
        "call_id": details["call_id"],
        "sent": details["sent"],
        "request_metadata": {
            key: raw[key]
            for key in ("wire_schema_version", "wire_schema_sha256", "request_sha256", "request_bytes")
        },
    }
    safe_details.update(trace)
    return trace
