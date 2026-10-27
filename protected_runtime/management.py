"""Disabled management protocol; no authorization, native process, or tool APIs.

Stream I/O below is bounded framing only. Native cancellation, watchdogs,
verified process identity and artifact pins deliberately belong to REVIEW-040.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re
import uuid


class ProtocolError(ValueError):
    def __init__(self, code="PROTOCOL_INVALID"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Limits:
    frame_bytes: int = 65536
    depth: int = 16
    nodes: int = 8192
    seen_ids: int = 65536

    def __post_init__(self):
        for name, ceiling in (("frame_bytes", 65536), ("depth", 16),
                              ("nodes", 8192), ("seen_ids", 65536)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ProtocolError("RESOURCE_LIMIT")


@dataclass(frozen=True)
class Binding:
    bundle_id: str
    artifact_version: str

    def __post_init__(self):
        for value in (self.bundle_id, self.artifact_version):
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
                raise ProtocolError("VERSION_MISMATCH")


def _fail(*_args):
    raise ProtocolError()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _strict_json(raw, *, byte_limit, limits):
    if type(raw) is not bytes or not raw or len(raw) > byte_limit:
        _fail()
    try:
        text = raw.decode("utf-8", "strict")
        # Bound nesting before invoking the standard parser (including invalid JSON).
        depth, quoted, escaped = 0, False, False
        for ch in text:
            if quoted:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted = True
            elif ch in "[{":
                depth += 1
                if depth > limits.depth:
                    _fail()
            elif ch in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_fail)
        stack, count = [value], 0
        while stack:
            item = stack.pop()
            count += 1
            if count > limits.nodes:
                _fail()
            if isinstance(item, dict):
                for key in item:
                    key.encode("utf-8", "strict")
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, float) and not math.isfinite(item):
                _fail()
            elif isinstance(item, str):
                item.encode("utf-8", "strict")
        return value
    except (UnicodeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError() from None


def parse_frame(raw, limits=Limits()):
    if (type(raw) is not bytes or not raw.endswith(b"\n") or len(raw) > limits.frame_bytes
            or b"\n" in raw[:-1] or b"\r" in raw or raw.startswith(b"\xef\xbb\xbf")):
        _fail()
    return _strict_json(raw[:-1], byte_limit=limits.frame_bytes - 1, limits=limits)


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        _fail()
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        _fail()
    if parsed.version != 4 or str(parsed) != value:
        _fail()
    return value


class FrameDecoder:
    """Incremental framing. A malformed/unfinished frame poisons this decoder."""
    def __init__(self, limits=Limits()):
        self.limits = limits
        self.buffer = bytearray()
        self.closed = False

    def feed(self, chunk):
        if self.closed or type(chunk) is not bytes:
            _fail()
        frames = []
        for byte in chunk:
            self.buffer.append(byte)
            if (byte == 13 or len(self.buffer) > self.limits.frame_bytes
                    or (len(self.buffer) == self.limits.frame_bytes and byte != 10)):
                self.closed = True
                self.buffer.clear()
                _fail()
            if byte == 10:
                frames.append(bytes(self.buffer))
                self.buffer.clear()
        return frames

    def finish(self):
        self.closed = True
        if self.buffer:
            self.buffer.clear()
            _fail()


class ManagementHost:
    def __init__(self, binding, limits=Limits()):
        if not isinstance(binding, Binding):
            _fail()
        self.binding = binding
        self.limits = limits
        self.session = None
        self.seen = set()
        self.closed = False

    def handle_frame(self, raw):
        try:
            return self._handle(raw)
        except ProtocolError:
            self.closed = True
            raise

    def _handle(self, raw):
        if self.closed:
            _fail()
        request = parse_frame(raw, self.limits)
        if (type(request) is not dict or set(request) != {"v", "id", "session", "method", "params"}
                or type(request["v"]) is not int or request["v"] != 1):
            _fail()
        rid, session = _uuid(request["id"]), _uuid(request["session"])
        if rid in self.seen or (self.session is not None and session != self.session):
            _fail()
        if len(self.seen) >= self.limits.seen_ids:
            raise ProtocolError("RESOURCE_LIMIT")
        self.seen.add(rid)
        method, params = request["method"], request["params"]
        if type(method) is not str:
            _fail()
        code = None
        if method not in ("health", "status", "shutdown"):
            code = "EXECUTION_DISABLED"
        elif type(params) is not dict or params:
            code = "PROTOCOL_INVALID"
        elif self.session is None and method != "health":
            _fail()
        response = {"v": 1, "id": rid, "session": session}
        if code:
            self.closed = True
            response["error"] = {"code": code, "message": code}
        else:
            self.session = session
            self.closed = method == "shutdown"
            response["result"] = {
                "role": "protected-host-management", "contract_version": 1,
                "bundle_id": self.binding.bundle_id,
                "artifact_version": self.binding.artifact_version,
                "state": "stopping" if self.closed else "disabled_ready",
                "execution_enabled": False,
                "child_roles": {"oi": "not_started", "broker": "not_started"}}
        encoded = (json.dumps(response, separators=(",", ":")) + "\n").encode()
        try:
            parse_frame(encoded, self.limits)
        except ProtocolError:
            raise ProtocolError("RESOURCE_LIMIT") from None
        return encoded


def serve_stream(source, destination, host):
    """Bounded synchronous adapter, not a native deadline/cancellation guarantee."""
    decoder = FrameDecoder(host.limits)
    try:
        while not host.closed:
            chunk = source.read1(4096) if hasattr(source, "read1") else source.read(4096)
            if not chunk:
                decoder.finish()
                host.closed = True
                return 0
            for raw in decoder.feed(chunk):
                response = host.handle_frame(raw)
                if destination.write(response) != len(response):
                    raise OSError("short management write")
                destination.flush()
                if host.closed:
                    break
        return 0
    except (ProtocolError, OSError):
        host.closed = True
        return 1


def _path(value):
    if type(value) is not str or len(value) > 240:
        _fail()
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    for part in parts:
        if (not part or part in (".", "..") or part[-1] in " ."
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                or any(ord(ch) < 32 or ch in '\\:*?"<>|' for ch in part)
                or part.split(".")[0].upper() in reserved
                or not part.isascii()):
            _fail()
    return value.lower()


def validate_manifest(raw, expected_digest, binding, inventory):
    """Pure bytes/inventory check only: does not inspect a filesystem or pin files."""
    if (type(raw) is not bytes or len(raw) > 1048576 or type(expected_digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            or hashlib.sha256(raw).hexdigest() != expected_digest):
        raise ProtocolError("INTEGRITY_MISMATCH")
    value = _strict_json(raw, byte_limit=1048576, limits=Limits(nodes=8192))
    required = {"schema", "contract_version", "bundle_id", "artifact_version", "target",
                "execution_mode", "roles", "artifacts"}
    if (type(value) is not dict or set(value) != required
            or type(value["schema"]) is not int or value["schema"] != 1
            or type(value["contract_version"]) is not int or value["contract_version"] != 1
            or value["execution_mode"] != "disabled"
            or value["target"] != "x86_64-pc-windows-msvc"):
        _fail()
    if (value["bundle_id"], value["artifact_version"]) != (binding.bundle_id, binding.artifact_version):
        raise ProtocolError("VERSION_MISMATCH")
    roles, artifacts = value["roles"], value["artifacts"]
    if type(roles) is not dict or set(roles) != {"protected-host-management"}:
        raise ProtocolError("ROLE_MISMATCH")
    if type(artifacts) is not list or not 1 <= len(artifacts) <= 4096 or type(inventory) is not dict:
        _fail()
    if len(inventory) != len(artifacts):
        raise ProtocolError("INTEGRITY_MISMATCH")
    keys, entrypoints = set(), []
    for item in artifacts:
        if type(item) is not dict or set(item) != {"path", "size", "sha256", "kind"}:
            _fail()
        key = _path(item["path"])
        if key in keys or key == "protected-runtime-manifest.json":
            _fail()
        keys.add(key)
        if item["kind"] not in ("entrypoint", "native_library", "python_archive", "data", "license"):
            _fail()
        if item["kind"] == "entrypoint":
            entrypoints.append(item["path"])
        if key.endswith(".exe") != (item["kind"] == "entrypoint"):
            _fail()
        if (type(item["size"]) is not int or not 0 <= item["size"] <= 0xFFFFFFFFFFFFFFFF
                or type(item["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            _fail()
        data = inventory.get(item["path"])
        if type(data) is not bytes or len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ProtocolError("INTEGRITY_MISMATCH")
    if len(entrypoints) != 1 or roles["protected-host-management"] != entrypoints[0]:
        raise ProtocolError("ROLE_MISMATCH")
    if set(inventory) != {item["path"] for item in artifacts}:
        raise ProtocolError("INTEGRITY_MISMATCH")
    return binding.bundle_id
