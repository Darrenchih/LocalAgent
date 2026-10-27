from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
import uuid


PROTECTED_EVENT_SCHEMA_VERSION = "1.0"
FIXED_TOOL = "stage2_execute"
FIXED_OPERATION = "CREATE"


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    call_id: str
    thread_id: str
    request_id: str
    tool_name: str
    operation: str
    risk_summary: str
    requested_at: str
    deadline_at: str
    source_kind: str = "offline_host_replay"

    def validate(self) -> None:
        uuid.UUID(self.run_id)
        uuid.UUID(self.thread_id)
        if not self.call_id.startswith("call_"):
            raise ValueError("call_id is not a canonical Host call id")
        if not self.request_id:
            raise ValueError("request_id is required")
        if self.tool_name != FIXED_TOOL:
            raise ValueError("protected adapter accepts only stage2_execute")
        if self.operation != FIXED_OPERATION:
            raise ValueError("protected adapter accepts only CREATE")
        requested = datetime.fromisoformat(self.requested_at)
        deadline = datetime.fromisoformat(self.deadline_at)
        if deadline <= requested:
            raise ValueError("approval deadline must follow the request time")
        if self.source_kind != "offline_host_replay":
            raise ValueError("only offline Host replay evidence is accepted")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return dict(self.__dict__)


class AuthorizationSink(Protocol):
    def submit(self, request: ApprovalRequest, approved: bool) -> dict[str, Any]: ...


class FakeAuthorizationSink:
    """Decision recorder only. It has no Host, Gate, Broker, or Grant capability."""

    creates_grants = False
    executes_tools = False

    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []

    def submit(self, request: ApprovalRequest, approved: bool) -> dict[str, Any]:
        record = {
            "request_id": request.request_id,
            "approved": bool(approved),
            "grant_created": False,
            "tool_executed": False,
            "sink": "fake_authorization_sink",
        }
        self.decisions.append(record)
        return record


def review027_request() -> ApprovalRequest:
    requested = datetime.fromtimestamp(1792851451.727, tz=timezone.utc)
    return ApprovalRequest(
        run_id="281b4623-84ad-443e-a192-2a4b2ea8ef0f",
        call_id="call_pfxsng5j",
        thread_id="01a16e45-eeeb-764e-99d4-308906526a08",
        request_id="0",
        tool_name=FIXED_TOOL,
        operation=FIXED_OPERATION,
        risk_summary="Fixed inert CREATE fixture; no user data or formal Broker write target.",
        requested_at=requested.isoformat(timespec="milliseconds"),
        deadline_at=(requested + timedelta(seconds=90)).isoformat(timespec="milliseconds"),
    )


class ProtectedAgentAdapter:
    """Boundary adapter for validated Host state. Formal execution is deliberately absent."""

    formal_execution_enabled = False

    def __init__(self, store: Any, sink: AuthorizationSink | None = None) -> None:
        self.store = store
        self.sink = sink or FakeAuthorizationSink()

    def stage_request(self, conversation_id: str) -> dict[str, Any]:
        request = review027_request()
        self.store.save_approval_request(conversation_id, request.as_dict(), "pending")
        self._event(conversation_id, request, "tool_requested", "requested", "pending", "item/started")
        self._event(conversation_id, request, "awaiting_approval", "awaiting_approval", "pending", "mcpServer/elicitation/request")
        return self.store.get_approval_request(conversation_id, request.request_id)

    def submit_decision(self, conversation_id: str, request_id: str, approved: bool) -> dict[str, Any]:
        row = self.store.get_approval_request(conversation_id, request_id)
        if row["status"] != "pending":
            raise ValueError("approval request is missing or already decided")
        request = ApprovalRequest(**{key: row[key] for key in ApprovalRequest.__dataclass_fields__})
        request.validate()
        sink_result = self.sink.submit(request, bool(approved))
        status = "approved_no_grant" if approved else "rejected"
        self.store.update_approval_request(conversation_id, request_id, status)
        event_type = "approved" if approved else "rejected"
        result = "Decision recorded by fake sink; no grant created and no tool executed."
        self._event(conversation_id, request, event_type, event_type, event_type, "fake_authorization_sink", result)
        return {"request": self.store.get_approval_request(conversation_id, request_id), "sink": sink_result}

    def replay_completed_evidence(self, conversation_id: str) -> dict[str, Any]:
        request = review027_request()
        self.store.save_approval_request(conversation_id, request.as_dict(), "historical_approved_read_only")
        sequence = (
            ("tool_requested", "requested", "pending", "item/started", None),
            ("awaiting_approval", "awaiting_approval", "pending", "mcpServer/elicitation/request", None),
            ("approved", "approved", "approved", "serverRequest/resolved", "Historical human acceptance replayed; no new decision."),
            ("grant_issued", "approved", "approved", "review027/result.json", "Historical test grant evidence only; no grant issued now."),
            ("running", "running", "approved", "item/started", None),
            ("completed", "completed", "approved", "item/completed", '{"status":"ok","source":"stage2b-test"}'),
            ("result_returned", "completed", "approved", "item/completed", '{"status":"ok","source":"stage2b-test"}'),
        )
        for event_type, execution, approval, source, summary in sequence:
            self._event(conversation_id, request, event_type, execution, approval, source, summary)
        return {
            "request": self.store.get_approval_request(conversation_id, request.request_id),
            "events": self.store.list_protected_events(conversation_id),
            "formal_execution_enabled": False,
        }

    def _event(
        self, conversation_id: str, request: ApprovalRequest, event_type: str,
        execution_state: str, approval_state: str, source_event: str,
        result_summary: str | None = None,
    ) -> None:
        self.store.append_protected_event(conversation_id, {
            **request.as_dict(),
            "event_type": event_type,
            "execution_state": execution_state,
            "approval_state": approval_state,
            "source_event": source_event,
            "result_summary": result_summary,
        })
