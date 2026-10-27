from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import ModelProvider


class OllamaError(RuntimeError):
    pass


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        try:
            with urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise OllamaError('provider_error: expected Ollama JSON object')
                return result
        except TimeoutError as error:
            raise OllamaError(f"model_timeout: Ollama request failed: {error}") from error
        except json.JSONDecodeError as error:
            raise OllamaError("provider_error: invalid Ollama JSON response") from error
        except (HTTPError, URLError) as error:
            prefix = 'model_timeout' if isinstance(getattr(error, 'reason', None), TimeoutError) else 'provider_error'
            raise OllamaError(f"{prefix}: Ollama request failed: {error}") from error

    def list_models(self) -> list[str]:
        payload = self._request("/api/tags")
        return sorted(
            item["name"] for item in payload.get("models", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )

    def chat(self, model: str, messages: list[dict[str, Any]]) -> str:
        payload = self._request(
            "/api/chat",
            {"model": model, "messages": messages, "stream": False},
        )
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama returned no assistant content")
        return content.strip()

    def stream_chat(self, model: str, messages: list[dict[str, Any]]) -> Iterator[str]:
        request = Request(
            self.base_url + "/api/chat",
            data=json.dumps({"model": model, "messages": messages, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    payload = json.loads(raw_line.decode("utf-8"))
                    message = payload.get("message")
                    content = message.get("content") if isinstance(message, dict) else None
                    if isinstance(content, str) and content:
                        yield content
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OllamaError(f"Ollama streaming request failed: {error}") from error

    def tool_turn(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        plan_only = len(tools)==1 and tools[0].get('function',{}).get('name')=='plan_task'
        payload = self._request('/api/chat', {'model':model, 'messages':messages, 'tools':tools, 'stream':False, 'think':False, 'options':{'num_predict':1536 if plan_only else 512, 'temperature':0}}, timeout=min(15.0, self.timeout))
        if not isinstance(payload, dict):
            raise OllamaError('provider_error: expected Ollama JSON object')
        message = payload.get('message')
        if not isinstance(message, dict):
            raise OllamaError('provider_error: Ollama returned no assistant message')
        return message
