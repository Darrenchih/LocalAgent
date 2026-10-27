"""Offline evidence-collection boundary tests; no builds or artifact execution."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('release_notices', Path(__file__).resolve().parents[1] / 'scripts/collect_release_notices.py')
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class ReleaseNoticesTests(unittest.TestCase):
    def test_pnpm_reads_packages_not_snapshots_or_importers(self):
        text = """lockfileVersion: '9.0'
importers:
  .:
packages:
  '@scope/name@1.2.3':
    resolution: {integrity: sha512-test}
  plain@2.0.0:
    resolution: {integrity: sha512-test}
snapshots:
  'plain@2.0.0(peer@3)': {}
"""
        self.assertEqual(notices.pnpm_entries(text), [('@scope/name', '1.2.3'), ('plain', '2.0.0')])

    def test_unknown_lock_format_fails_closed(self):
        with self.assertRaises(ValueError):
            notices.pnpm_entries("lockfileVersion: '6.0'\npackages:\n  a@1:\n")

    def test_license_file_escape_and_dependency_tree_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'package'
            root.mkdir()
            (Path(tmp) / 'outside.txt').write_text('private')
            (root / 'LICENSE').write_text('observed')
            dependency = root / 'node_modules/other'
            dependency.mkdir(parents=True)
            (dependency / 'LICENSE').write_text('unrelated')
            self.assertEqual(notices.license_files(root, ['../outside.txt']), [root / 'LICENSE'])

    def test_missing_metadata_never_infers_license_from_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            license_file = root / 'LICENSE'
            license_file.write_text('MIT')
            collector = notices.Collector(root, root / 'out', root, root)
            record = {'name': 'unknown', 'version': '1'}
            collector.attach(record, [license_file])
            self.assertEqual(record['evidence_status'], 'unknown_license_metadata')
            self.assertEqual(record['legal_status'], 'manual_confirmation_required')
            self.assertNotIn('license_metadata', record)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'out'
            output.mkdir()
            sentinel = output / 'keep.txt'
            sentinel.write_text('unchanged')
            collector = notices.Collector(root, output, root, root)
            with self.assertRaises(FileExistsError):
                collector.run()
            self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_minimal_offline_inventory_preserves_bytes_and_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            tauri = repo / 'desktop_modern/src-tauri'
            tauri.mkdir(parents=True)
            (tauri / 'Cargo.lock').write_text('version = 4\n[[package]]\nname = "missing"\nversion = "1.0.0"\nsource = "registry+local"\nchecksum = "abc"\n')
            (repo / 'desktop_modern/pnpm-lock.yaml').write_text("lockfileVersion: '9.0'\npackages:\n  unknown@1.0.0:\n    resolution: {}\nsnapshots:\n")
            python_home = root / 'python'
            python_home.mkdir()
            original = b'license\r\nraw-byte-\xff\r\n'
            (python_home / 'LICENSE.txt').write_bytes(original)
            output = root / 'out'
            summary = notices.Collector(repo, output, root / 'cargo', python_home).run()
            inventory = json.loads((output / 'dependency-inventory.json').read_text('utf-8'))
            self.assertEqual(summary['public_release_status'], 'BLOCKED_PENDING_LEGAL_AND_PAYLOAD_REVIEW')
            self.assertFalse(inventory['artifacts_executed'])
            self.assertFalse(inventory['network_used'])
            python = next(r for r in inventory['dependencies'] if r['name'] == 'CPython')
            copied = output / python['license_texts'][0]['bundled_path']
            self.assertEqual(copied.read_bytes(), original)
            self.assertGreater(len(inventory['release_blockers']), 0)
            unknown = next(r for r in inventory['dependencies'] if r['name'] == 'missing')
            self.assertEqual(unknown['evidence_status'], 'unknown_license_metadata')


if __name__ == '__main__':
    unittest.main()
