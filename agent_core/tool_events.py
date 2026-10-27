from __future__ import annotations

from typing import Any

TOOL_EVENT_SCHEMA_VERSION = "1.0"
EXECUTION_STATES = frozenset({
    "requested",
    "awaiting_approval",
    "approved",
    "rejected",
    "running",
    "completed",
    "failed",
    "cancelled",
})
APPROVAL_STATES = frozenset({"not_required", "pending", "approved", "rejected"})
REQUIRED_FIELDS = frozenset({
    "run_id",
    "conversation_id",
    "tool_call_id",
    "tool_name",
    "execution_state",
    "approval_state",
    "timestamp",
    "error_code",
    "result_summary",
})


def validate_tool_event(event: dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS.difference(event)
    if missing:
        raise ValueError(f"tool event missing fields: {sorted(missing)}")
    if event.get("schema_version") != TOOL_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported tool event schema version")
    if event["execution_state"] not in EXECUTION_STATES:
        raise ValueError("invalid tool execution state")
    if event["approval_state"] not in APPROVAL_STATES:
        raise ValueError("invalid tool approval state")
