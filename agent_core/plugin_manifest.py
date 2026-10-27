from __future__ import annotations

import re
from typing import Any

PLUGIN_SCHEMA_VERSION = "1.0"
SOURCE_TYPES = frozenset({"mcp", "github", "gmail", "custom", "built_in"})
PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("plugin manifest must be an object")
    required = {
        "schema_version", "plugin_id", "name", "source_type", "version",
        "description", "enabled", "approval_required", "executable", "config",
    }
    missing = required.difference(value)
    if missing:
        raise ValueError(f"plugin manifest missing fields: {sorted(missing)}")
    if value["schema_version"] != PLUGIN_SCHEMA_VERSION:
        raise ValueError("unsupported plugin manifest schema version")
    if not isinstance(value["plugin_id"], str) or not PLUGIN_ID.fullmatch(value["plugin_id"]):
        raise ValueError("invalid plugin_id")
    for field in ("name", "version", "description"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
    if value["source_type"] not in SOURCE_TYPES:
        raise ValueError("unsupported plugin source_type")
    if not isinstance(value["enabled"], bool) or not isinstance(value["approval_required"], bool):
        raise ValueError("enabled and approval_required must be booleans")
    if value["executable"] is not False:
        raise ValueError("plugins must remain non-executable in REVIEW-030")
    if not isinstance(value["config"], dict):
        raise ValueError("plugin config must be an object")
    return value
