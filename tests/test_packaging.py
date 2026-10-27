from pathlib import Path
import unittest
import json


ROOT = Path(__file__).resolve().parents[1]


class PackagingContractTests(unittest.TestCase):
    def test_embedded_core_entrypoint_calls_existing_server(self) -> None:
        source = (ROOT / "packaging" / "core_entry.py").read_text(encoding="utf-8")
        self.assertIn("from agent_core.server import main", source)
        self.assertIn("raise SystemExit(main())", source)
        self.assertIn('stream.reconfigure(encoding="utf-8", errors="strict")', source)

    def test_build_script_outputs_tauri_target_triple_sidecar(self) -> None:
        source = (ROOT / "scripts" / "build_embedded_core.ps1").read_text(encoding="utf-8")
        self.assertIn("agent-core-x86_64-pc-windows-msvc.exe", source)
        self.assertTrue("PyInstaller" in source or "pyinstaller" in source)

    def test_tauri_bundle_is_current_user_nsis_with_embedded_core(self) -> None:
        config = json.loads((ROOT / "desktop_modern" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        bundle = config["bundle"]
        self.assertTrue(bundle["active"])
        self.assertEqual(bundle["targets"], ["nsis"])
        self.assertEqual(bundle["externalBin"], ["binaries/agent-core", "binaries/agent-discovery"])
        self.assertEqual(bundle["windows"]["nsis"]["installMode"], "currentUser")
        self.assertEqual(bundle["windows"]["nsis"]["startMenuFolder"], "本機 AI 助理")
