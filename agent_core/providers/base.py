from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class ModelProvider(ABC):
    name = "provider"

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def chat(self, model: str, messages: list[dict[str, Any]]) -> str:
        raise NotImplementedError

    def stream_chat(self, model: str, messages: list[dict[str, Any]]) -> Iterator[str]:
        """Providers may override this for native streaming."""
        yield self.chat(model, messages)

    def tool_turn(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        raise NotImplementedError('provider does not support tool turns')
