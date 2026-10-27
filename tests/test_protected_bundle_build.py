import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BundleBuildTests(unittest.TestCase):
    def module(self):
        path = ROOT / 'scripts/protected_bundle_manifest.py'
        self.assertTrue(path.is_file(), 'manifest builder is missing')
        spec = importlib.util.spec_from_file_location('bundle_manifest', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_inventory_is_complete_and_validator_compatible(self):
        from protected_runtime.management import Binding, validate_manifest
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'protected-host-management.exe').write_bytes(b'abc')
            (root / 'python312.dll').write_bytes(b'dll')
            (root / 'base_library.zip').write_bytes(b'zip')
            (root / 'LICENSE.txt').write_bytes(b'license')
            raw = module.build_manifest(root)
            inventory = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(validate_manifest(raw, hashlib.sha256(raw).hexdigest(), Binding('localagent-management-v1', '0.37.0'), inventory), 'localagent-management-v1')
            self.assertIn(b'"kind":"native_library"', raw)
            self.assertIn(b'"kind":"python_archive"', raw)
            self.assertIn(b'"kind":"license"', raw)
            (root / 'protected-runtime-manifest.json').write_bytes(raw)
            self.assertEqual(module.build_manifest(root), raw)

    def test_rejects_unsafe_paths_and_extra_executables(self):
        module = self.module()
        for name in ['bad name.dll', 'bad~name.dll', 'second.exe', 'CON.txt']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'protected-host-management.exe').write_bytes(b'abc')
                (root / name).write_bytes(b'bad')
                with self.assertRaises(ValueError):
                    module.build_manifest(root)

    def test_rejects_missing_entrypoint(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                module.build_manifest(Path(directory))

if __name__ == '__main__':
    unittest.main()
