from __future__ import annotations

import unittest

from agent_core.protocol import ProtocolError, Request


class ProtocolTests(unittest.TestCase):
    def test_versioned_request(self) -> None:
        request = Request.parse({"version": "1.0", "id": "a", "action": "health", "params": {}})
        self.assertEqual(request.action, "health")

    def test_wrong_version_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            Request.parse({"version": "2", "id": "a", "action": "health"})

    def test_params_must_be_object(self) -> None:
        with self.assertRaises(ProtocolError):
            Request.parse({"version": "1.0", "id": "a", "action": "health", "params": []})


if __name__ == "__main__":
    unittest.main()
