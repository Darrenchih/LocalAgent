from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AGENT_DIRECTORY = Path(r"D:\Agent")
CONFIG_FILE = AGENT_DIRECTORY / "agent-config.json"
REQUIRED_DISABLED_FLAGS = (
    "remote_control",
    "inbound_listeners",
    "public_ports",
    "scheduled_execution",
    "autonomous_execution",
)


def load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise ValueError(f"Unable to read agent configuration: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid agent configuration JSON: {error}") from error

    if not isinstance(config, dict):
        raise ValueError("Agent configuration must be a JSON object.")

    if config.get("schema_version") != 1:
        raise ValueError("Unsupported configuration schema.")

    if config.get("enabled") is not False:
        raise ValueError("enabled must remain false until explicitly reviewed.")

    command = config.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ValueError("command must be a non-empty list of strings.")

    executable = Path(command[0])
    if not executable.is_file():
        raise ValueError("Configured executable does not exist.")

    working_directory = config.get("working_directory")
    if not isinstance(working_directory, str) or not Path(working_directory).is_dir():
        raise ValueError("working_directory must be an existing directory.")

    for flag in REQUIRED_DISABLED_FLAGS:
        if config.get(flag) is not False:
            raise ValueError(f"{flag} must remain false.")

    timeout = config.get("graceful_shutdown_seconds")
    if not isinstance(timeout, int) or not 1 <= timeout <= 60:
        raise ValueError("graceful_shutdown_seconds must be an integer from 1 to 60.")

    if config.get("notifications") is not True:
        raise ValueError("notifications must remain enabled.")

    return config

