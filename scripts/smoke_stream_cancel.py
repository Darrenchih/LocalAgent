from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop_ui.ipc_client import CoreClient


def main() -> int:
    root = ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:1.7b")
    args = parser.parse_args()
    client = CoreClient(root)
    try:
        conversation = client.request(
            "create_conversation",
            title="REVIEW-029 stream cancellation",
            mode="chat",
            model=args.model,
        )
        generation_id = str(uuid.uuid4())
        first_chunk = threading.Event()
        chunks: list[str] = []
        outcome: dict[str, object] = {}

        def on_event(event: dict[str, object]) -> None:
            if event.get("type") == "delta":
                chunks.append(str(event.get("content", "")))
                first_chunk.set()

        def stream() -> None:
            outcome.update(client.stream_request(
                "stream_message",
                on_event,
                conversation_id=conversation["id"],
                content="Count from 1 to 500, one number per line, with no explanation.",
                generation_id=generation_id,
                mode="chat",
                model=args.model,
            ))

        worker = threading.Thread(target=stream)
        worker.start()
        if not first_chunk.wait(timeout=30):
            raise TimeoutError("No Ollama streaming chunk arrived")
        time.sleep(0.03)
        cancel = client.request("cancel_generation", generation_id=generation_id)
        worker.join(timeout=30)
        if worker.is_alive():
            raise TimeoutError("Streaming request did not stop")
        history = client.request("read_history", conversation_id=conversation["id"])
        print(json.dumps({
            "conversation_id": conversation["id"],
            "chunk_count_before_stop": len(chunks),
            "cancel_request_accepted": cancel["cancelled"],
            "stream_reported_cancelled": outcome.get("cancelled"),
            "persisted_messages": len(history["messages"]),
            "partial_reply_chars": len("".join(chunks)),
        }, indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
