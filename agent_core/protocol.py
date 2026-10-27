from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import API_VERSION


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    request_id: str
    action: str
    params: dict[str, Any]

    @classmethod
    def parse(cls, value: Any) -> "Request":
        if not isinstance(value, dict):
            raise ProtocolError("request must be an object")
        if value.get("version") != API_VERSION:
            raise ProtocolError(f"unsupported API version; expected {API_VERSION}")
        request_id = value.get("id")
        action = value.get("action")
        params = value.get("params", {})
        if not isinstance(request_id, str) or not request_id:
            raise ProtocolError("id must be a non-empty string")
        if not isinstance(action, str) or not action:
            raise ProtocolError("action must be a non-empty string")
        if not isinstance(params, dict):
            raise ProtocolError("params must be an object")
        return cls(request_id, action, params)


def success(request_id: str, result: Any) -> dict[str, Any]:
    return {"version": API_VERSION, "id": request_id, "ok": True, "result": result}


def failure(request_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "version": API_VERSION,
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }
