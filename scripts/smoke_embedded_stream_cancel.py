from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:1.7b")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(args.executable), "--stdio", "--db", str(args.db)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )
    queues: dict[str, Queue[dict[str, object]]] = {}
    lock = threading.Lock()

    def read_loop() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            response = json.loads(line)
            with lock:
                target = queues.get(str(response.get("id")))
            if target:
                target.put(response)

    threading.Thread(target=read_loop, daemon=True).start()

    def begin(action: str, params: dict[str, object]) -> tuple[str, Queue[dict[str, object]]]:
        request_id = str(uuid.uuid4())
        target: Queue[dict[str, object]] = Queue()
        with lock:
            queues[request_id] = target
        assert process.stdin is not None
        process.stdin.write(json.dumps({"version": "1.0", "id": request_id, "action": action, "params": params}) + "\n")
        process.stdin.flush()
        return request_id, target

    def request(action: str, **params: object) -> object:
        request_id, target = begin(action, params)
        try:
            response = target.get(timeout=30)
            if not response.get("ok"):
                raise RuntimeError(response.get("error"))
            return response.get("result")
        finally:
            with lock:
                queues.pop(request_id, None)

    try:
        conversation = request("create_conversation", title="REVIEW-035 embedded stream", mode="chat", model=args.model)
        assert isinstance(conversation, dict)
        generation_id = str(uuid.uuid4())
        first_chunk = threading.Event()
        chunks: list[str] = []
        outcome: dict[str, object] = {}

        def stream() -> None:
            request_id, target = begin("stream_message", {
                "conversation_id": conversation["id"], "content": "Count from 1 to 500, one number per line.",
                "generation_id": generation_id, "mode": "chat", "model": args.model,
            })
            try:
                while True:
                    response = target.get(timeout=30)
                    event = response.get("event")
                    if isinstance(event, dict):
                        chunks.append(str(event.get("content", "")))
                        first_chunk.set()
                        continue
                    if not response.get("ok"):
                        raise RuntimeError(response.get("error"))
                    result = response.get("result")
                    if isinstance(result, dict):
                        outcome.update(result)
                    break
            finally:
                with lock:
                    queues.pop(request_id, None)

        worker = threading.Thread(target=stream)
        worker.start()
        if not first_chunk.wait(timeout=30):
            raise TimeoutError("No embedded streaming chunk arrived")
        time.sleep(0.03)
        cancel = request("cancel_generation", generation_id=generation_id)
        worker.join(timeout=30)
        if worker.is_alive():
            raise TimeoutError("Embedded streaming request did not stop")
        print(json.dumps({
            "chunk_count": len(chunks),
            "cancel_request_accepted": isinstance(cancel, dict) and cancel.get("cancelled"),
            "stream_reported_cancelled": outcome.get("cancelled"),
        }, indent=2))
    finally:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
