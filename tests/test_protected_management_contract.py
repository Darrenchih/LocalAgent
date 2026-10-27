"""REVIEW-039: in-process contract tests; no child processes or network."""
import hashlib
import importlib
import io
import json
import unittest

try:
    m = importlib.import_module("protected_runtime.management")
except ModuleNotFoundError:
    m = None

SID = "888cb0db-8dab-4e83-9265-dc0bd2b5b7b1"
RID = "13572080-4e2e-48e4-91d7-c60dd0465809"
RID2 = "f5f7708c-a58c-4910-b701-1507fb02a921"


def frame(method="health", rid=RID, session=SID, **extra):
    return (json.dumps(dict(v=1, id=rid, session=session, method=method,
                            params={}, **extra), separators=(",", ":")) + "\n").encode()


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(m, "management contract has not been implemented")
        self.binding = m.Binding("review039-source-contract", "0.36.0")
        self.host = m.ManagementHost(self.binding)

    def reject(self, payload, code="PROTOCOL_INVALID"):
        with self.assertRaises(m.ProtocolError) as error:
            self.host.handle_frame(payload)
        self.assertEqual(error.exception.code, code)
        self.assertTrue(self.host.closed)

    def test_health_status_and_shutdown_never_enable_execution(self):
        health = json.loads(self.host.handle_frame(frame()))
        self.assertEqual(health["id"], RID)
        self.assertEqual(health["result"], {
            "role": "protected-host-management", "contract_version": 1,
            "bundle_id": "review039-source-contract", "artifact_version": "0.36.0",
            "state": "disabled_ready", "execution_enabled": False,
            "child_roles": {"oi": "not_started", "broker": "not_started"}})
        status = json.loads(self.host.handle_frame(frame("status", RID2)))
        self.assertIs(status["result"]["execution_enabled"], False)
        self.host = m.ManagementHost(self.binding)
        self.host.handle_frame(frame())
        reply = json.loads(self.host.handle_frame(frame("shutdown", RID2)))
        self.assertEqual(reply["result"]["state"], "stopping")
        self.assertTrue(self.host.closed)
        with self.assertRaises(m.ProtocolError):
            self.host.handle_frame(frame("health", RID2))

    def test_forbidden_methods_reply_once_and_close(self):
        for method in ("enable", "approve", "grant", "permit", "dispatch", "tools/call",
                       "turn/start", "exec", "shell"):
            with self.subTest(method=method):
                host = m.ManagementHost(self.binding)
                result = json.loads(host.handle_frame(frame(method)))
                self.assertEqual(result["error"]["code"], "EXECUTION_DISABLED")
                self.assertTrue(host.closed)

    def test_duplicate_nested_keys_nonfinite_and_type_confusion_close(self):
        for payload in (frame().replace(b'"params":{}', b'"params":{"x":1,"x":2}'),
                        frame().replace(b'"v":1', b'"v":true'),
                        frame().replace(b'"v":1', b'"v":1.0'),
                        frame().replace(b'"params":{}', b'"params":{"x":NaN}'),
                        frame().replace(b'"params":{}', b'"params":{"x":1e999}'),
                        frame(extra=True)):
            with self.subTest(payload=payload):
                self.host = m.ManagementHost(self.binding)
                self.reject(payload)

    def test_nonempty_params_is_correlated_error_then_terminal(self):
        reply = json.loads(self.host.handle_frame(
            frame().replace(b'"params":{}', b'"params":{"execution_enabled":true}')))
        self.assertEqual(reply["error"]["code"], "PROTOCOL_INVALID")
        self.assertTrue(self.host.closed)

    def test_bad_framing_and_uuid_are_terminal(self):
        for payload in (frame()[:-1], frame().replace(b"\n", b"\r\n"), b"\n",
                        b"\xef\xbb\xbf" + frame(), b"\xff\n", b"{}\n{}\n",
                        frame(rid=RID.upper()), frame(session="not-a-uuid")):
            with self.subTest(payload=payload):
                self.host = m.ManagementHost(self.binding)
                self.reject(payload)

    def test_first_status_duplicate_id_and_cross_session_are_rejected(self):
        self.reject(frame("status"))
        for next_frame in (frame(), frame("status", RID2, RID)):
            self.host = m.ManagementHost(self.binding)
            self.host.handle_frame(frame())
            self.reject(next_frame)

    def test_limits_are_enforced_before_unbounded_buffering(self):
        limits = m.Limits(frame_bytes=256, depth=3, nodes=10, seen_ids=1)
        self.assertEqual(m.parse_frame(b'{"a":[{}]}\n', limits), {"a": [{}]})
        for raw in (b'{"a":[[{}]]}\n', b"[" + b"0," * 10 + b"0]\n", b" " * 256 + b"\n"):
            with self.assertRaises(m.ProtocolError):
                m.parse_frame(raw, limits)
        decoder = m.FrameDecoder(limits)
        with self.assertRaises(m.ProtocolError):
            decoder.feed(b"x" * 256)
        self.assertTrue(decoder.closed)
        host = m.ManagementHost(self.binding, m.Limits(seen_ids=1))
        host.handle_frame(frame())
        with self.assertRaises(m.ProtocolError) as caught:
            host.handle_frame(frame("status", RID2))
        self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")

    def test_outbound_frames_obey_same_limits(self):
        host = m.ManagementHost(self.binding, m.Limits(frame_bytes=256))
        with self.assertRaises(m.ProtocolError) as error:
            host.handle_frame(frame())
        self.assertEqual(error.exception.code, "RESOURCE_LIMIT")
        self.assertTrue(host.closed)

    def test_manifest_cannot_disguise_extra_executable_as_data(self):
        blob, inventory = manifest()
        value = json.loads(blob)
        value["artifacts"].append({"path": "extra.exe", "kind": "data", "size": 0,
                                    "sha256": hashlib.sha256(b"").hexdigest()})
        inventory["extra.exe"] = b""
        blob = json.dumps(value).encode()
        with self.assertRaises(m.ProtocolError):
            m.validate_manifest(blob, hashlib.sha256(blob).hexdigest(), self.binding, inventory)

    def test_stream_handles_fragments_shutdown_and_truncated_eof(self):
        decoder = m.FrameDecoder()
        self.assertEqual(decoder.feed(frame()[:20]), [])
        self.assertEqual(decoder.feed(frame()[20:]), [frame()])
        decoder.finish()
        output = io.BytesIO()
        m.serve_stream(io.BytesIO(frame() + frame("shutdown", RID2)), output, self.host)
        self.assertEqual(len(output.getvalue().splitlines()), 2)
        output = io.BytesIO()
        self.assertEqual(m.serve_stream(io.BytesIO(frame()[:-2]), output,
                                       m.ManagementHost(self.binding)), 1)
        self.assertEqual(output.getvalue(), b"")

    def test_stream_short_write_cannot_continue_or_report_success(self):
        class ShortWriter(io.BytesIO):
            def write(self, data):
                return super().write(data[:1])
        out = ShortWriter()
        result = m.serve_stream(io.BytesIO(frame() + frame("status", RID2)), out, self.host)
        self.assertEqual(result, 1)
        self.assertTrue(self.host.closed)
        self.assertEqual(out.getvalue(), b"{")

    def test_windows_short_names_and_spaced_device_alias_are_refused(self):
        for path in ("HOST~1.exe", "CON .exe", "dir/NUL .txt"):
            blob, inventory = manifest(path=path)
            with self.subTest(path=path), self.assertRaises(m.ProtocolError):
                m.validate_manifest(blob, hashlib.sha256(blob).hexdigest(), self.binding, inventory)

    def test_exact_frame_size_and_container_depth_boundaries(self):
        limits = m.Limits(frame_bytes=128, depth=2, nodes=4)
        self.assertEqual(m.parse_frame(b"{}" + b" " * 125 + b"\n", limits), {})
        for raw in (b"{}" + b" " * 126 + b"\n", b"[[[]]]\n", b"[0,0,0,0]\n"):
            with self.assertRaises(m.ProtocolError):
                m.parse_frame(raw, limits)
        self.assertEqual(m.parse_frame(b"[0,0,0]\n", limits), [0, 0, 0])

    def test_manifest_exact_identity_collection_and_bytes(self):
        blob, inventory = manifest()
        digest = hashlib.sha256(blob).hexdigest()
        self.assertEqual(m.validate_manifest(blob, digest, self.binding, inventory),
                         "review039-source-contract")
        for changed in ({}, {**inventory, "extra.dll": b"evil"}, {"host.exe": b"changed"}):
            with self.assertRaises(m.ProtocolError):
                m.validate_manifest(blob, digest, self.binding, changed)
        with self.assertRaises(m.ProtocolError):
            m.validate_manifest(blob + b" ", digest, self.binding, inventory)

    def test_manifest_rejects_windows_aliases_and_schema_expansion(self):
        for path in ("../host.exe", "C:/host.exe", "//server/share", "a\\host.exe",
                     "a:stream", "a/./host.exe", "a/../host.exe", "CON.exe",
                     "dir/file. ", "a//host.exe", "a\x00.exe", "a/COM1.txt"):
            blob, inventory = manifest(path=path)
            with self.subTest(path=path), self.assertRaises(m.ProtocolError):
                m.validate_manifest(blob, hashlib.sha256(blob).hexdigest(), self.binding, inventory)
        for change in ({"execution_mode": "enabled"}, {"contract_version": True},
                       {"artifact_version": "different"}, {"unknown": 1}):
            blob, inventory = manifest(change=change)
            with self.assertRaises(m.ProtocolError):
                m.validate_manifest(blob, hashlib.sha256(blob).hexdigest(), self.binding, inventory)

    def test_manifest_case_collisions_and_inconsistent_role_are_refused(self):
        blob, inventory = manifest()
        value = json.loads(blob)
        value["artifacts"].append({**value["artifacts"][0], "path": "HOST.exe"})
        value["roles"]["broker"] = "host.exe"
        bad = json.dumps(value).encode()
        with self.assertRaises(m.ProtocolError):
            m.validate_manifest(bad, hashlib.sha256(bad).hexdigest(), self.binding, inventory)


def manifest(path="host.exe", change=None):
    data = b"inert artifact bytes, not executable"
    value = {"schema": 1, "contract_version": 1,
             "bundle_id": "review039-source-contract", "artifact_version": "0.36.0",
             "target": "x86_64-pc-windows-msvc", "execution_mode": "disabled",
             "roles": {"protected-host-management": path},
             "artifacts": [{"path": path, "kind": "entrypoint", "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest()}]}
    value.update(change or {})
    return json.dumps(value).encode(), {path: data}


if __name__ == "__main__":
    unittest.main()
