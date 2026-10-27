"""Inspect generated NSIS inputs; optionally stage those exact bytes, without installing."""
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import shutil

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def payload_mapping(script):
    text = Path(script).read_text(encoding='utf-8-sig')
    main = re.search(r'^!define MAINBINARYSRCPATH "([^"]+)"', text, re.M).group(1)
    name = re.search(r'^!define MAINBINARYNAME "([^"]+)"', text, re.M).group(1)
    rows = [(name + '.exe', Path(main))]
    rows += [(target, Path(source)) for target, source in re.findall(r'^\s*File /a "/oname=([^"]+)" "([^"]+)"', text, re.M)]
    seen = set()
    for relative, source in rows:
        path = PureWindowsPath(relative)
        normalized = path.as_posix().casefold()
        if not path.parts or path.root or path.is_absolute() or path.drive or '..' in path.parts or ':' in relative or normalized in seen:
            raise ValueError('unsafe or duplicate installer destination')
        for component in path.parts:
            stem = component.split('.')[0].upper()
            if (not re.fullmatch(r'[A-Za-z0-9_.-]+', component) or component.endswith(('.', ' '))
                    or stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[0-9]', stem)):
                raise ValueError('Windows path alias refused')
        seen.add(normalized)
        if not source.is_file() or source.is_symlink():
            raise ValueError('missing or redirected NSIS build input')
    return text, rows

def check(repo, stage=None):
    root = Path(repo)
    tauri = root / 'desktop_modern/src-tauri'
    text, rows = payload_mapping(tauri / 'target/release/nsis/x64/installer.nsi')
    identity = json.loads((root / 'packaging/protected-runtime-release.json').read_text())
    assert f'!define VERSION "{identity["artifact_version"]}"' in text
    assert '!define INSTALLMODE "currentUser"' in text
    assert '!define INSTALLWEBVIEW2MODE ""' in text  # schema skip emits no install mode
    assert 'installer-hooks.nsh' in text and '!insertmacro NSIS_HOOK_PREUNINSTALL' in text
    hooks = (root / 'packaging/installer-hooks.nsh').read_text()
    assert 'StrCpy $DeleteAppDataCheckboxState 0' in hooks
    bundle = tauri / 'binaries/protected-runtime'
    raw = (bundle / 'protected-runtime-manifest.json').read_bytes()
    pin = (tauri / 'binaries/protected-runtime.sha256').read_text().strip()
    assert hashlib.sha256(raw).hexdigest() == pin
    manifest = json.loads(raw)
    assert all(manifest[k] == identity[k] for k in identity)
    mapping = {PureWindowsPath(target).as_posix(): source for target, source in rows}
    prefix = 'runtime/protected/' + identity['bundle_id'] + '/'
    for entry in manifest['artifacts']:
        file = mapping[prefix + entry['path']]
        assert file.stat().st_size == entry['size'] and sha(file) == entry['sha256']
    assert sha(mapping[prefix + 'protected-runtime-manifest.json']) == pin
    assert 'agent-core.exe' in mapping and 'agent-discovery.exe' in mapping
    assert all('notices/' + part in mapping for part in ('THIRD_PARTY_NOTICES.txt', 'dependency-inventory.json', 'REVIEW_REQUIRED.json'))
    if stage:
        stage = Path(stage)
        if stage.exists():
            raise ValueError('staging destination exists')
        for relative, source in mapping.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.resolve().is_relative_to(stage.resolve()):
                raise ValueError('staging destination escaped selected root')
            shutil.copyfile(source, target)
            assert sha(source) == sha(target)
    return {'validation': 'GENERATED_NSIS_INPUTS_ONLY_NOT_AN_INSTALL_TEST', 'files': len(rows),
            'frozen_artifacts': len(manifest['artifacts']), 'identity': identity, 'manifest_sha256': pin,
            'source_files': [{'destination': t, 'source': str(p), 'sha256': sha(p), 'size': p.stat().st_size} for t, p in rows]}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = check(args.repo, args.stage)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Validated {result["files"]} generated NSIS inputs; no installer executed.')
