from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(root / "data" / "review028-smoke.db"))
    parser.add_argument("--model", default="qwen3:1.7b")
    args = parser.parse_args()
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_core.server", "--stdio", "--db", args.db],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def request(number: int, action: str, **params: object) -> object:
        payload = {"version": "1.0", "id": str(number), "action": action, "params": params}
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if not response.get("ok"):
            raise RuntimeError(response.get("error"))
        return response["result"]

    try:
        models = request(1, "list_models")
        conversation = request(2, "create_conversation", title="REVIEW-028 smoke", mode="chat", model=args.model)
        conversation_id = conversation["id"]
        first = request(3, "send_message", conversation_id=conversation_id, content="Reply with exactly: TURN_ONE_OK")
        second = request(4, "send_message", conversation_id=conversation_id, content="What exact token did I ask you to return in my previous message?")
        history = request(5, "read_history", conversation_id=conversation_id)
        print(json.dumps({
            "models": models,
            "conversation_id": conversation_id,
            "first_reply": first["assistant_message"]["content"],
            "second_reply": second["assistant_message"]["content"],
            "message_count": len(history["messages"]),
            "mode": history["conversation"]["mode"],
        }, ensure_ascii=False, indent=2))
    finally:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
