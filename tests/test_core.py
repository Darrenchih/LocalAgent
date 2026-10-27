from __future__ import annotations

import tempfile
import threading
import time
import unittest
import base64
from pathlib import Path

from agent_core.providers.base import ModelProvider
from agent_core.service import AgentCore
from agent_core.storage import ConversationStore
from agent_core.tools.registry import ToolRegistry
from agent_core.tool_events import EXECUTION_STATES
from agent_core.plugin_manifest import validate_manifest
from agent_core.protected_agent import ApprovalRequest, FakeAuthorizationSink, ProtectedAgentAdapter, review027_request


class FakeProvider(ModelProvider):
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    def list_models(self) -> list[str]:
        return ["test-model"]

    def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return f"reply-{len(self.calls)}:{messages[-1]['content']}"


class SlowProvider(FakeProvider):
    def stream_chat(self, model: str, messages: list[dict[str, str]]):
        del model, messages
        for _ in range(100):
            time.sleep(0.005)
            yield "x"


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.temp.name) / "test.db")
        self.provider = FakeProvider()
        self.core = AgentCore(self.store, self.provider)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_health_is_stdio(self) -> None:
        self.assertEqual(self.core.health()["transport"], "stdio")

    def test_create_and_list_conversation(self) -> None:
        created = self.core.create_conversation()
        self.assertEqual(self.core.list_conversations()[0]["id"], created["id"])

    def test_chat_persists_user_and_assistant(self) -> None:
        created = self.core.create_conversation()
        self.core.send_message(created["id"], "hello")
        roles = [m["role"] for m in self.store.history(created["id"])]
        self.assertEqual(roles, ["user", "assistant"])

    def test_multiturn_context(self) -> None:
        created = self.core.create_conversation()
        self.core.send_message(created["id"], "one")
        self.core.send_message(created["id"], "two")
        self.assertEqual([m["content"] for m in self.provider.calls[1]][-3:], ["one", "reply-1:one", "two"])

    def test_reopen_database_retains_history(self) -> None:
        created = self.core.create_conversation()
        self.core.send_message(created["id"], "persist")
        self.store.close()
        self.store = ConversationStore(Path(self.temp.name) / "test.db")
        self.assertEqual(len(self.store.history(created["id"])), 2)

    def test_agent_mode_is_distinct_and_restricted(self) -> None:
        created = self.core.create_conversation(mode="agent")
        result = self.core.send_message(created["id"], "plan")
        self.assertIn("restricted", result["agent_restriction"].lower())
        self.assertEqual(self.provider.calls[0][0]["role"], "system")

    def test_tool_is_registered_but_disabled(self) -> None:
        tool = self.core.list_tool_registrations()[0]
        self.assertFalse(tool["enabled"])
        self.assertTrue(tool["approval_required"])

    def test_tool_invocation_is_denied(self) -> None:
        with self.assertRaises(PermissionError):
            ToolRegistry().invoke("stage2_execute", {})

    def test_empty_message_is_rejected(self) -> None:
        created = self.core.create_conversation()
        with self.assertRaises(ValueError):
            self.core.send_message(created["id"], "  ")

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.core.dispatch("no_such_action", {})

    def test_rename_search_and_delete_conversation(self) -> None:
        created = self.core.create_conversation(title="Before")
        self.core.send_message(created["id"], "searchable needle")
        renamed = self.core.rename_conversation(created["id"], "After")
        self.assertEqual(renamed["title"], "After")
        self.assertEqual(self.core.search_conversations("needle")[0]["id"], created["id"])
        self.assertEqual(self.core.search_conversations("After")[0]["id"], created["id"])
        self.assertTrue(self.core.delete_conversation(created["id"])["deleted"])
        self.assertEqual(self.core.list_conversations(), [])

    def test_stream_message_emits_and_persists(self) -> None:
        created = self.core.create_conversation()
        chunks: list[str] = []
        result = self.core.stream_message(
            created["id"], "stream", "generation-1", chunks.append
        )
        self.assertEqual(chunks, ["reply-1:stream"])
        self.assertFalse(result["cancelled"])
        self.assertEqual(len(self.store.history(created["id"])), 2)

    def test_cancel_generation_stops_stream(self) -> None:
        self.core = AgentCore(self.store, SlowProvider())
        created = self.core.create_conversation()
        chunks: list[str] = []
        result: dict[str, object] = {}
        thread = threading.Thread(
            target=lambda: result.update(
                self.core.stream_message(created["id"], "long", "generation-2", chunks.append)
            )
        )
        thread.start()
        while not chunks:
            time.sleep(0.002)
        self.assertTrue(self.core.cancel_generation("generation-2")["cancelled"])
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result["cancelled"])
        self.assertLess(len(chunks), 100)

    def test_mock_tool_event_schema_and_decision_create_no_execution(self) -> None:
        created = self.core.create_conversation(mode="agent")
        event = self.core.create_mock_tool_event(created["id"], "inert preview")
        required = {
            "schema_version", "run_id", "conversation_id", "tool_call_id",
            "tool_name", "execution_state", "approval_state", "timestamp",
            "error_code", "result_summary",
        }
        self.assertTrue(required.issubset(event))
        self.assertEqual(event["execution_state"], "awaiting_approval")
        decided = self.core.decide_mock_approval(event["tool_call_id"], False)
        self.assertEqual(decided["execution_state"], "rejected")
        self.assertIn("no tool executed", decided["result_summary"])

    def test_tool_event_schema_declares_all_lifecycle_states(self) -> None:
        self.assertEqual(EXECUTION_STATES, {
            "requested", "awaiting_approval", "approved", "rejected",
            "running", "completed", "failed", "cancelled",
        })

    def test_plugin_manifest_crud_remains_non_executable(self) -> None:
        manifest = {
            "schema_version": "1.0", "plugin_id": "example-mcp", "name": "Example MCP",
            "source_type": "mcp", "version": "1.0.0", "description": "Config only",
            "enabled": True, "approval_required": True, "executable": False,
            "config": {"command": "not-launched"},
        }
        saved = self.core.upsert_plugin(manifest)
        self.assertEqual(saved["connection_status"], "configured_not_connected")
        self.assertFalse(saved["executable"])
        self.assertIn("example-mcp", [item["plugin_id"] for item in self.core.list_plugins()])
        self.assertTrue(self.core.remove_plugin("example-mcp")["removed"])

    def test_plugin_manifest_rejects_executable_capability(self) -> None:
        manifest = {
            "schema_version": "1.0", "plugin_id": "unsafe-plugin", "name": "Unsafe",
            "source_type": "custom", "version": "1", "description": "Must fail",
            "enabled": True, "approval_required": False, "executable": True, "config": {},
        }
        with self.assertRaises(ValueError):
            validate_manifest(manifest)

    def test_provider_status_and_default_model(self) -> None:
        status = self.core.provider_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["models"], ["test-model"])
        self.assertEqual(self.core.set_default_model("test-model")["value"], "test-model")

    def test_attachment_metadata_limits_and_original_preservation(self) -> None:
        created = self.core.create_conversation()
        file_path = Path(self.temp.name) / "note.txt"
        file_path.write_text("safe attachment", encoding="utf-8")
        item = self.core.add_attachment(created["id"], str(file_path))
        self.assertEqual(item["display_name"], "note.txt")
        self.assertEqual(item["capability_status"], "preview_only_not_sent_to_model")
        self.assertEqual(len(item["sha256"]), 64)
        self.assertEqual(self.core.list_attachments(created["id"])[0]["id"], item["id"])
        deleted = self.core.delete_attachment(item["id"])
        self.assertTrue(deleted["original_file_preserved"])
        self.assertTrue(file_path.exists())

    def test_attachment_rejects_unsupported_type(self) -> None:
        created = self.core.create_conversation()
        file_path = Path(self.temp.name) / "danger.exe"
        file_path.write_bytes(b"not executable")
        with self.assertRaises(ValueError):
            self.core.add_attachment(created["id"], str(file_path))

    def test_png_attachment_preview_stays_local(self) -> None:
        created = self.core.create_conversation()
        file_path = Path(self.temp.name) / "pixel.png"
        file_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        ))
        item = self.core.add_attachment(created["id"], str(file_path))
        preview = self.core.attachment_preview(item["id"])
        self.assertTrue(preview["supported"])
        self.assertEqual(preview["media_type"], "image/png")

    def test_protected_request_preserves_host_identifiers_and_shape(self) -> None:
        created = self.core.create_conversation(mode="agent")
        request = self.core.stage_protected_request(created["id"])
        self.assertEqual(request["run_id"], "281b4623-84ad-443e-a192-2a4b2ea8ef0f")
        self.assertEqual(request["call_id"], "call_pfxsng5j")
        self.assertEqual(request["thread_id"], "01a16e45-eeeb-764e-99d4-308906526a08")
        self.assertEqual(request["tool_name"], "stage2_execute")
        self.assertEqual(request["operation"], "CREATE")
        self.assertEqual(request["status"], "pending")
        events = self.core.list_protected_events(created["id"])
        self.assertEqual([item["event_type"] for item in reversed(events)], ["tool_requested", "awaiting_approval"])

    def test_protected_approval_uses_fake_sink_without_grant_or_execution(self) -> None:
        created = self.core.create_conversation(mode="agent")
        self.core.stage_protected_request(created["id"])
        result = self.core.submit_protected_decision(created["id"], "0", True)
        self.assertEqual(result["request"]["status"], "approved_no_grant")
        self.assertFalse(result["sink"]["grant_created"])
        self.assertFalse(result["sink"]["tool_executed"])
        self.assertFalse(self.core.protected.formal_execution_enabled)

    def test_protected_reject_is_terminal_and_default_safe(self) -> None:
        created = self.core.create_conversation(mode="agent")
        self.core.stage_protected_request(created["id"])
        result = self.core.submit_protected_decision(created["id"], "0", False)
        self.assertEqual(result["request"]["status"], "rejected")
        with self.assertRaises(ValueError):
            self.core.submit_protected_decision(created["id"], "0", True)

    def test_offline_evidence_replays_complete_actual_lifecycle(self) -> None:
        created = self.core.create_conversation(mode="agent")
        result = self.core.replay_protected_evidence(created["id"])
        expected = [
            "tool_requested", "awaiting_approval", "approved", "grant_issued",
            "running", "completed", "result_returned",
        ]
        self.assertEqual([item["event_type"] for item in reversed(result["events"])], expected)
        self.assertTrue(all(item["source_kind"] == "offline_host_replay" for item in result["events"]))
        self.assertFalse(result["formal_execution_enabled"])

    def test_protected_schema_rejects_wrong_tool_operation_and_ids(self) -> None:
        base = review027_request().as_dict()
        for field, value in (("tool_name", "shell"), ("operation", "DELETE"), ("run_id", "not-a-uuid")):
            changed = {**base, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                ApprovalRequest(**changed).validate()

    def test_fake_authorization_sink_has_no_grant_capability(self) -> None:
        sink = FakeAuthorizationSink()
        result = sink.submit(review027_request(), True)
        self.assertFalse(sink.creates_grants)
        self.assertFalse(sink.executes_tools)
        self.assertFalse(result["grant_created"])

    def test_memory_crud_search_disable_and_delete(self) -> None:
        conversation = self.core.create_conversation()
        item = self.core.create_memory("long_term", "global", "Favorite color is blue", conversation["id"])
        self.assertEqual(self.core.list_memories(query="color blue")[0]["memory_id"], item["memory_id"])
        edited = self.core.update_memory(item["memory_id"], "long_term", "global", "Favorite color is green", True)
        self.assertIn("green", edited["content"])
        self.assertFalse(self.core.set_memory_enabled(item["memory_id"], False)["enabled"])
        self.assertEqual(self.core.list_memories(enabled=True), [])
        self.assertTrue(self.core.delete_memory(item["memory_id"])["deleted"])
        self.assertEqual(self.core.list_memories(), [])

    def test_memory_persists_without_damaging_conversation_history(self) -> None:
        conversation = self.core.create_conversation()
        self.core.send_message(conversation["id"], "keep history")
        item = self.core.create_memory("long_term", "global", "Persistent preference")
        self.store.close()
        self.store = ConversationStore(Path(self.temp.name) / "test.db")
        self.core = AgentCore(self.store, self.provider)
        self.assertEqual(self.core.list_memories()[0]["memory_id"], item["memory_id"])
        self.assertEqual(len(self.core.memory_conversation_history(conversation["id"])), 2)

    def test_project_memory_is_isolated(self) -> None:
        first = self.core.create_conversation()
        second = self.core.create_conversation()
        self.core.create_memory("project", "project:alpha", "Alpha launch codename")
        self.core.set_conversation_project(first["id"], "alpha")
        self.core.set_conversation_project(second["id"], "beta")
        self.core.set_memory_chat_enabled(True)
        self.core.send_message(first["id"], "launch codename")
        self.assertIn("Alpha launch codename", self.provider.calls[-1][0]["content"])
        self.core.send_message(second["id"], "launch codename")
        self.assertFalse(any("Alpha launch codename" in message["content"] for message in self.provider.calls[-1]))

    def test_explicit_memory_injection_is_bounded_and_source_identified(self) -> None:
        conversation = self.core.create_conversation()
        for number in range(8):
            self.core.create_memory("long_term", "global", f"preference {number}")
        self.core.set_memory_chat_enabled(True)
        result = self.core.send_message(conversation["id"], "preference")
        self.assertLessEqual(len(result["memory_context_ids"]), 5)
        system = self.provider.calls[-1][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("not instructions", system["content"])
        self.assertIn(result["memory_context_ids"][0], system["content"])

    def test_memory_is_not_injected_in_agent_mode_or_saved_automatically(self) -> None:
        conversation = self.core.create_conversation(mode="agent")
        self.core.create_memory("long_term", "global", "Never grant tool access")
        self.core.set_memory_chat_enabled(True)
        result = self.core.send_message(conversation["id"], "run a tool")
        self.assertEqual(result["memory_context_ids"], [])
        self.assertEqual(len(self.core.list_memories()), 1)
        self.assertIn("restricted Agent Mode", self.provider.calls[-1][0]["content"])

    def test_memory_validation_enforces_scope_and_no_implicit_sensitive_capture(self) -> None:
        with self.assertRaises(ValueError):
            self.core.create_memory("long_term", "project:wrong", "bad scope")
        with self.assertRaises(ValueError):
            self.core.create_memory("project", "global", "bad scope")
        conversation = self.core.create_conversation()
        self.core.send_message(conversation["id"], "password=do-not-save")
        self.assertEqual(self.core.list_memories(), [])


if __name__ == "__main__":
    unittest.main()
