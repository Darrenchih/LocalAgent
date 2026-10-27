from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from .protocol import ProtocolError, Request, failure, success
from .providers.ollama import OllamaProvider
from .service import AgentCore
from .storage import ConversationStore


def serve(core: AgentCore) -> None:
    write_lock = threading.Lock()

    def emit(response: dict[str, object]) -> None:
        with write_lock:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def stream_worker(request: Request) -> None:
        try:
            def on_chunk(chunk: str) -> None:
                emit({
                    "version": "1.0",
                    "id": request.request_id,
                    "ok": True,
                    "event": {"type": "delta", "content": chunk},
                })

            result = core.stream_message(on_chunk=on_chunk, **request.params)
            emit(success(request.request_id, result))
        except Exception as error:
            emit(failure(request.request_id, "core_error", str(error)))

    def agent_worker(request: Request) -> None:
        try:
            emit(success(request.request_id, core.dispatch(request.action, request.params)))
        except Exception as error:
            emit(failure(request.request_id, 'core_error', str(error)))

    for line in sys.stdin:
        request_id = "unknown"
        try:
            payload = json.loads(line)
            if isinstance(payload, dict) and isinstance(payload.get("id"), str):
                request_id = payload["id"]
            request = Request.parse(payload)
            request_id = request.request_id
            if request.action in {"agent_start", "agent_continue"}:
                threading.Thread(target=agent_worker, args=(request,), daemon=True).start()
                continue
            if request.action == "stream_message":
                threading.Thread(target=stream_worker, args=(request,), daemon=True).start()
                continue
            response = success(request_id, core.dispatch(request.action, request.params))
        except (json.JSONDecodeError, ProtocolError, TypeError, ValueError, KeyError) as error:
            response = failure(request_id, "invalid_request", str(error))
        except Exception as error:
            response = failure(request_id, "core_error", str(error))
        emit(response)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Windows Ollama Agent Core stdio server")
    parser.add_argument("--stdio", action="store_true", required=True)
    parser.add_argument("--db", default=str(root / "data" / "conversations.db"))
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    store = ConversationStore(args.db)
    try:
        serve(AgentCore(store, OllamaProvider(args.ollama_base_url)))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
