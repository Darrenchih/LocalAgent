from __future__ import annotations

import argparse

from agent_config import load_config
from agent_launcher import run_dummy_test
from agent_runtime import run_configured_agent
def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dummy-test", action="store_true")
    mode.add_argument("--configured-agent", action="store_true")
    arguments = parser.parse_args()

    if arguments.dummy_test:
        run_dummy_test()
        return 0

    config = load_config()
    if not config["enabled"]:
        print("Configured Agent is disabled by agent-config.json.")
        return 2

    return run_configured_agent(config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Manager failed: {error}")
        raise SystemExit(1)
