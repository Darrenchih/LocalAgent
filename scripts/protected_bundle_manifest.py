"""Inventory an isolated frozen bundle; its digest must be pinned externally."""
import argparse
import hashlib
import json
from pathlib import Path
from protected_runtime.management import Binding, _path, validate_manifest
from protected_runtime.release import release_identity

MANIFEST = 'protected-runtime-manifest.json'
ENTRY = 'protected-host-management.exe'

def build_manifest(root):
    root = Path(root)
    inventory = {}
    artifacts = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlinks are not bundle artifacts')
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if name == MANIFEST:
            continue
        _path(name)
        suffix = path.suffix.lower()
        if suffix == '.exe' and name != ENTRY:
            raise ValueError('unexpected executable')
        kind = ('entrypoint' if name == ENTRY else
                'native_library' if suffix in ('.pyd', '.dll') else
                'python_archive' if suffix in ('.zip', '.pyz') else
                'license' if 'license' in path.name.lower() or 'copying' in path.name.lower() else 'data')
        data = path.read_bytes()
        inventory[name] = data
        artifacts.append(dict(path=name, kind=kind, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    identity = release_identity()
    value = dict(identity, target='x86_64-pc-windows-msvc',
                 roles={'protected-host-management': ENTRY}, artifacts=artifacts)
    raw = (json.dumps(value, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
    validate_manifest(raw, hashlib.sha256(raw).hexdigest(), Binding(value['bundle_id'], value['artifact_version']), inventory)
    return raw

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    raw = build_manifest(args.root)
    (args.root / MANIFEST).write_bytes(raw)
    print(hashlib.sha256(raw).hexdigest())
