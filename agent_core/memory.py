from __future__ import annotations

from typing import Any


MEMORY_SCHEMA_VERSION = "1.0"
MEMORY_TYPES = frozenset({"long_term", "project"})
MAX_CONTEXT_ITEMS = 5
MAX_CONTEXT_CHARS = 2000


class MemoryService:
    """Model-independent memory policy and retrieval owned by Agent Core."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def create(
        self, memory_type: str, scope: str, content: str,
        source_conversation_id: str | None = None, enabled: bool = True,
    ) -> dict[str, Any]:
        self._validate(memory_type, scope, content)
        if source_conversation_id:
            self.store.get_conversation(source_conversation_id)
        return self.store.create_memory(
            memory_type, scope, content.strip(), source_conversation_id, bool(enabled)
        )

    def update(
        self, memory_id: str, memory_type: str, scope: str, content: str,
        enabled: bool,
    ) -> dict[str, Any]:
        self._validate(memory_type, scope, content)
        return self.store.update_memory(memory_id, memory_type, scope, content.strip(), bool(enabled))

    def list(self, query: str = "", memory_type: str = "", scope: str = "", enabled: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.search_memories(query, memory_type, scope, enabled, min(max(int(limit), 1), 200))

    def set_enabled(self, memory_id: str, enabled: bool) -> dict[str, Any]:
        return self.store.set_memory_enabled(memory_id, bool(enabled))

    def delete(self, memory_id: str) -> dict[str, bool]:
        return self.store.delete_memory(memory_id)

    def conversation_history(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.store.history(conversation_id)

    def set_chat_enabled(self, enabled: bool) -> dict[str, bool]:
        self.store.set_setting("memory_chat_enabled", "1" if enabled else "0")
        return {"enabled": bool(enabled)}

    def chat_enabled(self) -> bool:
        return self.store.get_setting("memory_chat_enabled", "0") == "1"

    def set_conversation_project(self, conversation_id: str, project_id: str) -> dict[str, str]:
        self.store.get_conversation(conversation_id)
        normalized = project_id.strip()
        self.store.set_setting(f"conversation_project:{conversation_id}", normalized)
        return {"conversation_id": conversation_id, "project_id": normalized}

    def conversation_project(self, conversation_id: str) -> str:
        return self.store.get_setting(f"conversation_project:{conversation_id}", "")

    def context_for_chat(self, conversation_id: str, query: str) -> dict[str, Any]:
        if not self.chat_enabled():
            return {"enabled": False, "items": [], "text": ""}
        project_id = self.conversation_project(conversation_id)
        candidates = self.store.memories_for_context(project_id)
        terms = {part.lower() for part in query.split() if len(part) >= 3}
        ranked = sorted(
            candidates,
            key=lambda item: sum(item["content"].lower().count(term) for term in terms),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        used = 0
        for item in ranked:
            line = f"[{item['memory_id']} | {item['memory_type']} | {item['scope']}] {item['content']}"
            if selected and used + len(line) > MAX_CONTEXT_CHARS:
                continue
            selected.append(item)
            used += len(line)
            if len(selected) >= MAX_CONTEXT_ITEMS:
                break
        text = "\n".join(
            f"[{item['memory_id']} | {item['memory_type']} | {item['scope']}] {item['content']}"
            for item in selected
        )
        return {"enabled": True, "items": selected, "text": text}

    @staticmethod
    def _validate(memory_type: str, scope: str, content: str) -> None:
        if memory_type not in MEMORY_TYPES:
            raise ValueError("memory_type must be long_term or project")
        if not content.strip():
            raise ValueError("memory content cannot be empty")
        if len(content) > 4000:
            raise ValueError("memory content exceeds 4000 characters")
        if memory_type == "long_term" and scope != "global":
            raise ValueError("long_term memory scope must be global")
        if memory_type == "project" and (not scope.startswith("project:") or len(scope) <= 8):
            raise ValueError("project memory scope must be project:<id>")
