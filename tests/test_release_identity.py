import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ReleaseIdentityTests(unittest.TestCase):
    def test_single_release_identity_drives_default_manifest(self):
        from protected_runtime.release import release_identity, release_binding
        identity = release_identity()
        self.assertEqual(identity['bundle_id'], 'localagent-management-v1')
        self.assertEqual(identity['artifact_version'], '0.37.0')
        self.assertEqual(identity['execution_mode'], 'disabled')
        self.assertEqual(identity['contract_version'], 1)
        binding = release_binding()
        from protected_runtime.management import validate_manifest
        spec = importlib.util.spec_from_file_location('release_manifest', ROOT / 'scripts/protected_bundle_manifest.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'protected-host-management.exe').write_bytes(b'entry')
            raw = module.build_manifest(root)
            inventory = {'protected-host-management.exe': b'entry'}
            self.assertEqual(validate_manifest(raw, hashlib.sha256(raw).hexdigest(), binding, inventory), identity['bundle_id'])
            self.assertEqual(json.loads(raw)['artifact_version'], identity['artifact_version'])

    def test_release_rejects_execution_enable_or_unknown_contract(self):
        from protected_runtime.release import validate_release_identity, release_identity
        for field, value in [('execution_mode', 'enabled'), ('contract_version', 2), ('bundle_id', 'review040-management-v1')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_release_identity(dict(release_identity(), **{field: value}))

if __name__ == '__main__':
    unittest.main()
