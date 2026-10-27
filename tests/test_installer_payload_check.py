import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nsis_check', ROOT / 'scripts/check_installer_payload.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class InstallerPayloadCheckTests(unittest.TestCase):
    def script(self, directory, names):
        root = Path(directory)
        source = root / 'source.exe'
        source.write_bytes(b'local test bytes')
        text = f'!define MAINBINARYSRCPATH "{source}"\n!define MAINBINARYNAME "app"\n'
        for name in names:
            text += f'File /a "/oname={name}" "{source}"\n'
        script = root / 'installer.nsi'
        script.write_text(text, encoding='utf-8')
        return script

    def test_destination_cannot_escape_staging_root(self):
        for destination in ['../escape.dll', r'\escape.dll', '/escape.dll', r'C:escape.dll', r'C:\escape.dll', '.', '.. /escape.dll', 'foo./a.dll', 'CON.dll', 'AUX', 'COM1.txt', 'LPT9.lib']:
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as folder:
                script = self.script(folder, [destination])
                with self.assertRaises(ValueError):
                    module.payload_mapping(script)

    def test_normalized_duplicate_destinations_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            script = self.script(folder, ['runtime/a.dll', r'RUNTIME\a.dll'])
            with self.assertRaises(ValueError):
                module.payload_mapping(script)

    def test_complete_regular_file_mapping_is_read_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            _, rows = module.payload_mapping(self.script(folder, ['runtime/a.dll']))
            self.assertEqual([name for name, _ in rows], ['app.exe', 'runtime/a.dll'])
            self.assertEqual([source.read_bytes() for _, source in rows], [b'local test bytes'] * 2)
