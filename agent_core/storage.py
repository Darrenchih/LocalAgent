from __future__ import annotations

import sqlite3
import threading
import uuid
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tool_events import TOOL_EVENT_SCHEMA_VERSION, validate_tool_event


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ConversationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('chat', 'agent')),
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool_event')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_conversation_id_id
                    ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    tool_call_id TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    operation_summary TEXT NOT NULL,
                    execution_state TEXT NOT NULL,
                    approval_state TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    error_code TEXT,
                    result_summary TEXT
                );
                CREATE INDEX IF NOT EXISTS tool_events_conversation_id_id
                    ON tool_events(conversation_id, id);
                CREATE TABLE IF NOT EXISTS plugins (
                    plugin_id TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    connection_status TEXT NOT NULL,
                    error_message TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    capability_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attachments_conversation_id
                    ON attachments(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS approval_requests (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    risk_summary TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    deadline_at TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS protected_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    execution_state TEXT NOT NULL,
                    approval_state TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_event TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    result_summary TEXT
                );
                CREATE INDEX IF NOT EXISTS protected_events_conversation_id_id
                    ON protected_events(conversation_id, id);
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    memory_type TEXT NOT NULL CHECK(memory_type IN ('long_term','project')),
                    scope TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_conversation_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
                );
                CREATE INDEX IF NOT EXISTS memories_scope_enabled
                    ON memories(scope, enabled, updated_at);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_conversation(self, title: str, mode: str, model: str) -> dict[str, Any]:
        if mode not in {"chat", "agent"}:
            raise ValueError("mode must be chat or agent")
        conversation_id = str(uuid.uuid4())
        timestamp = utc_now()
        title = title.strip() or "New conversation"
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO conversations(id,title,mode,model,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (conversation_id, title, mode, model, timestamp, timestamp),
            )
        return self.get_conversation(conversation_id)

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def search_conversations(self, query: str) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.list_conversations()
        pattern = f"%{query}%"
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT c.* FROM conversations c
                LEFT JOIN messages m ON m.conversation_id=c.id
                WHERE c.title LIKE ? COLLATE NOCASE OR m.content LIKE ? COLLATE NOCASE
                ORDER BY c.updated_at DESC
                """,
                (pattern, pattern),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise KeyError("conversation not found")
        return dict(row)

    def update_settings(self, conversation_id: str, mode: str, model: str) -> dict[str, Any]:
        if mode not in {"chat", "agent"}:
            raise ValueError("mode must be chat or agent")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE conversations SET mode=?, model=?, updated_at=? WHERE id=?",
                (mode, model, utc_now(), conversation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("conversation not found")
        return self.get_conversation(conversation_id)

    def rename_conversation(self, conversation_id: str, title: str) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("title cannot be empty")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title, utc_now(), conversation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("conversation not found")
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> dict[str, bool]:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM conversations WHERE id=?", (conversation_id,)
            )
        if cursor.rowcount != 1:
            raise KeyError("conversation not found")
        return {"deleted": True}

    def add_message(self, conversation_id: str, role: str, content: str) -> dict[str, Any]:
        timestamp = utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                (conversation_id, role, content, timestamp),
            )
            self._connection.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (timestamp, conversation_id)
            )
            row = self._connection.execute(
                "SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def history(self, conversation_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT id,conversation_id,role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_tool_event(
        self,
        conversation_id: str,
        tool_name: str,
        operation_summary: str,
    ) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        timestamp = utc_now()
        run_id = str(uuid.uuid4())
        tool_call_id = str(uuid.uuid4())
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO tool_events(
                    schema_version,run_id,conversation_id,tool_call_id,tool_name,
                    operation_summary,execution_state,approval_state,timestamp,error_code,result_summary
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    TOOL_EVENT_SCHEMA_VERSION, run_id, conversation_id, tool_call_id, tool_name,
                    operation_summary, "awaiting_approval", "pending", timestamp, None, None,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM tool_events WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        event = dict(row)
        validate_tool_event(event)
        return event

    def list_tool_events(self, conversation_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM tool_events WHERE conversation_id=? ORDER BY id DESC",
                (conversation_id,),
            ).fetchall()
        events = [dict(row) for row in rows]
        for event in events:
            validate_tool_event(event)
        return events

    def decide_mock_approval(self, tool_call_id: str, approved: bool) -> dict[str, Any]:
        approval_state = "approved" if approved else "rejected"
        execution_state = "approved" if approved else "rejected"
        summary = (
            "Mock approval recorded; no grant created and no tool executed."
            if approved
            else "Mock request rejected; no grant created and no tool executed."
        )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tool_events
                SET approval_state=?, execution_state=?, timestamp=?, result_summary=?
                WHERE tool_call_id=? AND approval_state='pending'
                """,
                (approval_state, execution_state, utc_now(), summary, tool_call_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("mock approval is missing or already decided")
            row = self._connection.execute(
                "SELECT * FROM tool_events WHERE tool_call_id=?", (tool_call_id,)
            ).fetchone()
        event = dict(row)
        validate_tool_event(event)
        return event

    def upsert_plugin(self, manifest: dict[str, Any]) -> dict[str, Any]:
        from .plugin_manifest import validate_manifest

        validate_manifest(manifest)
        status = "disabled" if not manifest["enabled"] else "configured_not_connected"
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO plugins(plugin_id,manifest_json,connection_status,error_message,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(plugin_id) DO UPDATE SET manifest_json=excluded.manifest_json,
                    connection_status=excluded.connection_status,error_message=NULL,updated_at=excluded.updated_at
                """,
                (manifest["plugin_id"], json.dumps(manifest), status, None, utc_now()),
            )
        return self.get_plugin(manifest["plugin_id"])

    def get_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM plugins WHERE plugin_id=?", (plugin_id,)
            ).fetchone()
        if row is None:
            raise KeyError("plugin not found")
        value = json.loads(row["manifest_json"])
        value.update(connection_status=row["connection_status"], error_message=row["error_message"])
        return value

    def list_plugins(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT plugin_id FROM plugins ORDER BY plugin_id").fetchall()
        return [self.get_plugin(row["plugin_id"]) for row in rows]

    def remove_plugin(self, plugin_id: str) -> dict[str, bool]:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM plugins WHERE plugin_id=?", (plugin_id,))
        if cursor.rowcount != 1:
            raise KeyError("plugin not found")
        return {"removed": True}

    def set_setting(self, key: str, value: str) -> dict[str, str]:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        return {"key": key, "value": value}

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def add_attachment(self, conversation_id: str, path: str) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise ValueError("attachment must be a file")
        allowed = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".txt", ".md", ".pdf"}
        if source.suffix.lower() not in allowed:
            raise ValueError("unsupported attachment type")
        size = source.stat().st_size
        if size > 10 * 1024 * 1024:
            raise ValueError("attachment exceeds the 10 MB limit")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        capability = "preview_only_not_sent_to_model"
        attachment_id = str(uuid.uuid4())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?)",
                (attachment_id, conversation_id, source.name, str(source), media_type, size, digest.hexdigest(), capability, utc_now()),
            )
        return self.get_attachment(attachment_id)

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            raise KeyError("attachment not found")
        return dict(row)

    def list_attachments(self, conversation_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM attachments WHERE conversation_id=? ORDER BY created_at", (conversation_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_attachment(self, attachment_id: str) -> dict[str, bool]:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        if cursor.rowcount != 1:
            raise KeyError("attachment not found")
        return {"deleted": True, "original_file_preserved": True}

    def save_approval_request(self, conversation_id: str, request: dict[str, Any], status: str) -> None:
        self.get_conversation(conversation_id)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO approval_requests(
                    conversation_id,request_id,run_id,call_id,thread_id,tool_name,operation,
                    risk_summary,requested_at,deadline_at,source_kind,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(conversation_id,request_id) DO UPDATE SET
                    run_id=excluded.run_id,call_id=excluded.call_id,thread_id=excluded.thread_id,
                    tool_name=excluded.tool_name,operation=excluded.operation,risk_summary=excluded.risk_summary,
                    requested_at=excluded.requested_at,deadline_at=excluded.deadline_at,
                    source_kind=excluded.source_kind,status=excluded.status""",
                (conversation_id, request["request_id"], request["run_id"], request["call_id"],
                 request["thread_id"], request["tool_name"], request["operation"],
                 request["risk_summary"], request["requested_at"], request["deadline_at"],
                 request["source_kind"], status),
            )

    def get_approval_request(self, conversation_id: str, request_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approval_requests WHERE conversation_id=? AND request_id=?",
                (conversation_id, request_id),
            ).fetchone()
        if row is None:
            raise KeyError("approval request not found")
        return dict(row)

    def update_approval_request(self, conversation_id: str, request_id: str, status: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE approval_requests SET status=? WHERE conversation_id=? AND request_id=? AND status='pending'",
                (status, conversation_id, request_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("approval request is missing or already decided")

    def append_protected_event(self, conversation_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO protected_events(
                    schema_version,conversation_id,run_id,call_id,thread_id,request_id,tool_name,
                    operation,event_type,execution_state,approval_state,source_kind,source_event,
                    timestamp,result_summary
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("1.0", conversation_id, event["run_id"], event["call_id"], event["thread_id"],
                 event["request_id"], event["tool_name"], event["operation"], event["event_type"],
                 event["execution_state"], event["approval_state"], event["source_kind"],
                 event["source_event"], utc_now(), event.get("result_summary")),
            )
            row = self._connection.execute("SELECT * FROM protected_events WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_protected_events(self, conversation_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM protected_events WHERE conversation_id=? ORDER BY id DESC",
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_memory(
        self, memory_type: str, scope: str, content: str,
        source_conversation_id: str | None, enabled: bool,
    ) -> dict[str, Any]:
        memory_id = str(uuid.uuid4())
        timestamp = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?)",
                (memory_id, "1.0", memory_type, scope, content, source_conversation_id,
                 timestamp, timestamp, int(enabled)),
            )
        return self.get_memory(memory_id)

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError("memory not found")
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        return value

    def update_memory(self, memory_id: str, memory_type: str, scope: str, content: str, enabled: bool) -> dict[str, Any]:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE memories SET memory_type=?,scope=?,content=?,enabled=?,updated_at=? WHERE memory_id=?",
                (memory_type, scope, content, int(enabled), utc_now(), memory_id),
            )
        if cursor.rowcount != 1:
            raise KeyError("memory not found")
        return self.get_memory(memory_id)

    def set_memory_enabled(self, memory_id: str, enabled: bool) -> dict[str, Any]:
        item = self.get_memory(memory_id)
        return self.update_memory(memory_id, item["memory_type"], item["scope"], item["content"], enabled)

    def delete_memory(self, memory_id: str) -> dict[str, bool]:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
        if cursor.rowcount != 1:
            raise KeyError("memory not found")
        return {"deleted": True}

    def search_memories(
        self, query: str, memory_type: str, scope: str, enabled: bool | None, limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for term in query.split():
            clauses.append("lower(content) LIKE ?")
            values.append(f"%{term.lower()}%")
        if memory_type:
            clauses.append("memory_type=?")
            values.append(memory_type)
        if scope:
            clauses.append("scope=?")
            values.append(scope)
        if enabled is not None:
            clauses.append("enabled=?")
            values.append(int(enabled))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM memories{where} ORDER BY updated_at DESC LIMIT ?", values
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["enabled"] = bool(item["enabled"])
        return result

    def memories_for_context(self, project_id: str) -> list[dict[str, Any]]:
        scopes = ["(memory_type='long_term' AND scope='global')"]
        values: list[Any] = []
        if project_id:
            scopes.append("(memory_type='project' AND scope=?)")
            values.append(f"project:{project_id}")
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM memories WHERE enabled=1 AND ({' OR '.join(scopes)}) ORDER BY updated_at DESC",
                values,
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["enabled"] = bool(item["enabled"])
        return result

    def append_agent_event(self, task_id: str, conversation_id: str, event: dict[str, Any]) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        data = json.dumps(event, ensure_ascii=False, allow_nan=False)
        if len(data.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('agent audit event exceeds limit')
        timestamp = utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                'INSERT INTO agent_audit(task_id,conversation_id,event_json,created_at) VALUES(?,?,?,?)',
                (task_id, conversation_id, data, timestamp))
        return dict(id=cursor.lastrowid, task_id=task_id, conversation_id=conversation_id, event=json.loads(data), created_at=timestamp)

    def agent_history(self, conversation_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        with self._lock:
            rows = self._connection.execute('SELECT * FROM agent_audit WHERE conversation_id=? ORDER BY id', (conversation_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['event'] = json.loads(item.pop('event_json'))
            result.append(item)
        return result
