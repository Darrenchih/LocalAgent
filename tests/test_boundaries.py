from __future__ import annotations

import unittest
from pathlib import Path


class BoundaryTests(unittest.TestCase):
    def test_ui_does_not_import_model_or_tool_modules(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop_ui"
        source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
        self.assertNotIn("agent_core.providers", source)
        self.assertNotIn("agent_core.tools", source)
        self.assertNotIn("urllib.request", source)
        self.assertNotIn("11434", source)

    def test_protected_approval_ui_is_default_reject_and_ipc_only(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "desktop_ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("Default is Reject", source)
        self.assertIn("Approve (no Grant)", source)
        self.assertIn("fake authorization sink", source)
        self.assertNotIn("issue_grant", source)
        self.assertNotIn("consume_grant", source)
        self.assertNotIn("Broker", source.replace("formal Broker write target", ""))

    def test_memory_ui_is_explicit_and_core_ipc_only(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "desktop_ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("never saved automatically", source)
        self.assertIn("Save explicit memory", source)
        self.assertIn('"create_memory"', source)
        self.assertNotIn("from agent_core.memory", source)


if __name__ == "__main__":
    unittest.main()
