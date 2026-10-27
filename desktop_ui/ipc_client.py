from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from queue import Queue
from pathlib import Path
from typing import Any

API_VERSION = "1.0"


class CoreClient:
    def __init__(self, root: Path) -> None:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._process = subprocess.Popen(
            [sys.executable, "-u", "-m", "agent_core.server", "--stdio"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creation_flags,
        )
        self._write_lock = threading.Lock()
        self._queues: dict[str, Queue[dict[str, Any]]] = {}
        self._queues_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            try:
                response = json.loads(line)
                request_id = response.get("id")
                with self._queues_lock:
                    target = self._queues.get(request_id)
                if target is not None:
                    target.put(response)
            except (json.JSONDecodeError, AttributeError):
                continue
        closed = {
            "ok": False,
            "error": {"message": "Agent Core closed the IPC channel"},
        }
        with self._queues_lock:
            pending = list(self._queues.values())
        for target in pending:
            target.put(closed)

    def _begin(self, action: str, params: dict[str, Any]) -> tuple[str, Queue[dict[str, Any]]]:
        if self._process.poll() is not None:
            raise RuntimeError("Agent Core stopped unexpectedly")
        request_id = str(uuid.uuid4())
        target: Queue[dict[str, Any]] = Queue()
        with self._queues_lock:
            self._queues[request_id] = target
        payload = {"version": API_VERSION, "id": request_id, "action": action, "params": params}
        assert self._process.stdin is not None
        try:
            with self._write_lock:
                self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
        except Exception:
            self._finish(request_id)
            raise
        return request_id, target

    def _finish(self, request_id: str) -> None:
        with self._queues_lock:
            self._queues.pop(request_id, None)

    def request(self, action: str, **params: Any) -> Any:
        request_id, target = self._begin(action, params)
        try:
            response = target.get()
            if not response.get("ok"):
                error = response.get("error", {})
                raise RuntimeError(error.get("message", "Unknown Agent Core error"))
            return response.get("result")
        finally:
            self._finish(request_id)

    def stream_request(
        self,
        action: str,
        on_event: Any,
        **params: Any,
    ) -> Any:
        request_id, target = self._begin(action, params)
        try:
            while True:
                response = target.get()
                if "event" in response:
                    on_event(response["event"])
                    continue
                if not response.get("ok"):
                    error = response.get("error", {})
                    raise RuntimeError(error.get("message", "Unknown Agent Core error"))
                return response.get("result")
        finally:
            self._finish(request_id)

    def close(self) -> None:
        if self._process.poll() is None:
            if self._process.stdin:
                self._process.stdin.close()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.terminate()
