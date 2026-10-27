from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:1.7b")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(args.executable), "--stdio", "--db", str(args.db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def request(number: int, action: str, **params: object) -> object:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({"version": "1.0", "id": str(number), "action": action, "params": params}, ensure_ascii=False) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if not response.get("ok"):
            raise RuntimeError(response.get("error"))
        return response["result"]

    try:
        conversation = request(1, "create_conversation", title="新對話", mode="chat", model=args.model)
        first = request(2, "send_message", conversation_id=conversation["id"], content="Reply with exactly: EMBEDDED_CORE_OK")
        history = request(3, "read_history", conversation_id=conversation["id"])
        print(json.dumps({
            "conversation_id": conversation["id"],
            "reply": first["assistant_message"]["content"],
            "message_count": len(history["messages"]),
            "core_executable": str(args.executable),
        }, ensure_ascii=False, indent=2))
    finally:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
