"""Collect observed local license evidence; never download, build, or execute artifacts.

Python 3.12+, optionally the locally installed PyInstaller archive reader. Output is
an evidence inventory, NOT an SPDX interpretation, legal clearance or exact SBOM.
"""
from __future__ import annotations

import argparse
from collections import Counter
from email.parser import Parser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import tomllib


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def evidence(path):
    path = Path(path)
    return {'path': str(path.resolve()), 'sha256': digest(path), 'size': path.stat().st_size}


def license_files(root, declared=()):
    """Only bounded package-local reads; never follow nested dependency symlinks."""
    root = Path(root).resolve()
    found = set()
    for name in declared:
        candidate = (root / name).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            found.add(candidate)
    for current, dirs, files in os.walk(root, followlinks=False):
        relative = Path(current).relative_to(root)
        dirs[:] = [d for d in dirs if d not in {'node_modules', '.git', 'target'}
                   and len(relative.parts) < 3 and not (Path(current) / d).is_symlink()]
        for name in files:
            if re.match(r'^(licen[cs]e|copying|copyright|notice|authors)([._-]|$)', name, re.I):
                candidate = (Path(current) / name).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    found.add(candidate)
    return sorted(found)


def pnpm_entries(text):
    """Read only v9 packages mapping keys, rejecting unsupported lock formats."""
    if not re.search(r"^lockfileVersion: ['\"]?9\.0['\"]?$", text, re.M):
        raise ValueError('Only pnpm lockfile v9.0 supported')
    section = False
    result = []
    for line in text.splitlines():
        if line == 'packages:':
            section = True
            continue
        if section and line and not line.startswith(' '):
            break
        if section and re.match(r'^  \S.*:$', line):
            key = line.strip()[:-1].strip("'\"")
            name, version = key.rsplit('@', 1)
            result.append((name, version))
    if not result:
        raise ValueError('No pnpm packages found')
    return result


class Collector:
    def __init__(self, repo, output, cargo, python_home):
        self.repo = Path(repo).resolve()
        self.output = Path(output).resolve()
        self.cargo = Path(cargo).resolve()
        self.python_home = Path(python_home).resolve()
        self.records = []
        self.inputs = []
        self.artifacts = []
        self.texts = {}
        self.issues = []

    def attach(self, record, files):
        record['license_texts'] = []
        for path in files:
            blob = path.read_bytes()
            sha = hashlib.sha256(blob).hexdigest()
            # Content-addressed output avoids unsafe dependency-supplied filenames.
            target = f'licenses/{sha}.txt'
            self.texts[sha] = blob
            record['license_texts'].append({**evidence(path), 'bundled_path': target})
        record['evidence_status'] = ('observed_metadata_and_text' if record.get('license_metadata') and files
                                     else 'missing_text' if record.get('license_metadata')
                                     else 'unknown_license_metadata')
        record['legal_status'] = 'manual_confirmation_required'
        self.records.append(record)

    def rust(self):
        lock = self.repo / 'desktop_modern/src-tauri/Cargo.lock'
        self.inputs.append(evidence(lock))
        for item in tomllib.loads(lock.read_text('utf-8'))['package']:
            record = {'ecosystem': 'cargo', 'name': item['name'], 'version': item['version'],
                      'scope': 'lockfile_superset_build_dev_target_inclusion_unresolved',
                      'locked_source': item.get('source'), 'locked_checksum': item.get('checksum')}
            if not item.get('source'):
                record['scope'] = 'first_party_workspace_package'
                record['evidence_status'] = 'first_party_not_third_party'
                self.records.append(record)
                continue
            roots = sorted((self.cargo / 'registry/src').glob(f'*/{item["name"]}-{item["version"]}'))
            if roots:
                root = roots[0]
                manifest = root / 'Cargo.toml'
                package = tomllib.loads(manifest.read_text('utf-8'))['package']
                record['metadata_source'] = evidence(manifest)
                record['license_metadata'] = package.get('license')
                record['repository'] = package.get('repository')
                archives = sorted((self.cargo / 'registry/cache').glob(f'*/{root.name}.crate'))
                if archives:
                    record['cached_archive'] = evidence(archives[0])
                    record['cached_archive_matches_lock_checksum'] = digest(archives[0]) == item.get('checksum')
                record['cache_selection'] = 'first_sorted_matching_name_version; registry provenance requires confirmation'
                self.attach(record, license_files(root, [package['license-file']] if package.get('license-file') else []))
            else:
                self.attach(record, [])

    def frontend(self):
        lock = self.repo / 'desktop_modern/pnpm-lock.yaml'
        self.inputs.append(evidence(lock))
        cache = self.repo / 'desktop_modern/node_modules/.pnpm'
        index = {}
        for manifest in sorted(cache.glob('*/node_modules/*/package.json')) + sorted(cache.glob('*/node_modules/@*/*/package.json')):
            # Read metadata only; no package entry point is imported.
            data = json.loads(manifest.read_text('utf-8'))
            index.setdefault((data.get('name'), data.get('version')), (manifest, data))
        for name, version in pnpm_entries(lock.read_text('utf-8')):
            record = {'ecosystem': 'pnpm', 'name': name, 'version': version,
                      'scope': 'lockfile_superset_build_dev_optional_inclusion_unresolved'}
            pair = index.get((name, version))
            if pair:
                manifest, data = pair
                record['metadata_source'] = evidence(manifest)
                record['license_metadata'] = data.get('license', data.get('licenses'))
                record['repository'] = data.get('repository')
                self.attach(record, license_files(manifest.parent))
            else:
                self.attach(record, [])

    def python(self):
        for path in ['python.exe', 'python312.dll', 'LICENSE.txt']:
            candidate = self.python_home / path
            if candidate.is_file():
                self.inputs.append(evidence(candidate))
        self.attach({'ecosystem': 'python', 'name': 'CPython',
                     'version': sys.version.split()[0] if Path(sys.base_prefix).resolve() == self.python_home else 'unknown',
                     'license_metadata': 'Python LICENSE.txt (multiple component terms; no SPDX normalization)',
                     'scope': 'build_interpreter_candidate_not_binary_provenance'},
                    [self.python_home / 'LICENSE.txt'] if (self.python_home / 'LICENSE.txt').is_file() else [])
        site = self.python_home / 'Lib/site-packages'
        for name in ['pyinstaller', 'pyinstaller-hooks-contrib', 'packaging', 'pefile', 'altgraph', 'pywin32-ctypes']:
            candidates = sorted(site.glob(name.replace('-', '_') + '-*.dist-info'))
            if not candidates:
                candidates = sorted(site.glob(name + '-*.dist-info'))
            record = {'ecosystem': 'python-build-tool', 'name': name,
                      'scope': 'local_build_tool_candidate_not_asserted_bundled', 'version': 'unknown'}
            if candidates:
                root = candidates[0]
                metadata = root / 'METADATA'
                data = Parser().parsestr(metadata.read_text('utf-8'))
                record.update(version=data.get('Version'), license_metadata=data.get('License-Expression') or data.get('License'),
                              license_classifiers=data.get_all('Classifier', []), metadata_source=evidence(metadata))
                self.attach(record, license_files(root, data.get_all('License-File', [])))
            else:
                self.attach(record, [])

    def binaries(self):
        root = self.repo / 'desktop_modern/src-tauri/binaries'
        try:
            from PyInstaller.archive.readers import CArchiveReader
        except ImportError:
            CArchiveReader = None
            self.issues.append('PyInstaller archive reader unavailable; onefile payload not inventoried')
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in {'.exe', '.dll', '.pyd', '.zip', '.json'}:
                continue
            record = {**evidence(path), 'relative_path': path.relative_to(root).as_posix()}
            self.artifacts.append(record)
            if path.suffix.lower() == '.exe' and CArchiveReader:
                try:
                    archive = CArchiveReader(str(path))
                    record['pyinstaller_archive_entries'] = sorted(archive.toc)
                    record['embedded_binaries'] = []
                    for name in sorted(archive.toc):
                        if Path(name).suffix.lower() in {'.dll', '.pyd'}:
                            payload = archive.extract(name)  # returns bytes, writes no files
                            record['embedded_binaries'].append({'name': name, 'size': len(payload),
                                                               'sha256': hashlib.sha256(payload).hexdigest()})
                except Exception as exc:
                    record['archive_error'] = f'{type(exc).__name__}: {exc}'
                    self.issues.append(f'Archive inspection incomplete: {record["relative_path"]}')
        for record in self.artifacts:
            entries = record.get('embedded_binaries', [])
            if Path(record['path']).suffix.lower() in {'.dll', '.pyd'}:
                entries = [{'name': Path(record['path']).name, 'sha256': record['sha256']}]
            for entry in entries:
                name = entry['name'].lower()
                if name.startswith(('api-ms-win-', 'vcruntime', 'ucrtbase', 'msvcp')):
                    family = 'Microsoft runtime; redistribution grant and required notices not confirmed'
                elif name.startswith(('libcrypto', 'libssl')):
                    family = 'OpenSSL candidate; exact version and component license mapping not confirmed'
                elif name.startswith(('libffi',)):
                    family = 'libffi candidate; exact version and notice mapping not confirmed'
                else:
                    family = 'Python/runtime extension; exact component mapping not confirmed'
                entry['license_review'] = family

    def run(self):
        # Never erase an existing output directory or overwrite unrelated data.
        if self.output.exists():
            raise FileExistsError('Choose a fresh output directory; existing output is never overwritten')
        self.rust()
        self.frontend()
        self.python()
        self.binaries()
        for relative in ['scripts/build_embedded_core.ps1', 'scripts/build_protected_runtime.ps1',
                         'packaging/core_entry.py', 'packaging/discovery_entry.py', 'packaging/protected_host_entry.py']:
            if (self.repo / relative).is_file():
                self.inputs.append(evidence(self.repo / relative))
        self.issues.extend([
            'Manual legal review required for every component and license expression, alternatives, attribution and source obligations.',
            'Lockfile supersets include unused build/dev/optional/platform packages; exact shipped dependency closure not established.',
            'Cache metadata and license text are observed evidence, not proof of source-to-binary provenance; verify artifact linkage.',
            'Microsoft runtime DLL redistribution terms and notices unresolved; local Python license is not a blanket DLL redistribution grant.',
            'OpenSSL/libffi/SQLite and other Python extension component versions and applicable notices require exact binary mapping. Python LICENSE.txt is collected but completeness is not asserted.',
            'NSIS installer engine/plugins, WebView2 bootstrapper/runtime and native Rust dependencies may need additional notices; installer payload audit remains required.',
            'Frozen Python PYZ/base_library module-to-distribution closure is not established; build-tool inventory does not prove runtime dependency completeness.',
        ])
        counts = dict(Counter(r.get('evidence_status', 'unknown') for r in self.records))
        inventory = {'schema_version': 1, 'public_release_status': 'BLOCKED_PENDING_LEGAL_AND_PAYLOAD_REVIEW',
                     'collector_sha256': digest(__file__), 'network_used': False, 'artifacts_executed': False,
                     'scope': 'observed_local_metadata_and_texts_conservative_lockfile_superset',
                     'source_inputs': self.inputs, 'dependency_status_counts': counts,
                     'dependencies': self.records, 'runtime_artifacts': self.artifacts, 'release_blockers': self.issues}
        self.output.mkdir(parents=True)
        (self.output / 'licenses').mkdir()
        for sha, blob in self.texts.items():
            (self.output / 'licenses' / (sha + '.txt')).write_bytes(blob)
        (self.output / 'dependency-inventory.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + '\n', 'utf-8')
        lines = ['THIRD-PARTY NOTICES — OBSERVED LOCAL EVIDENCE', '',
                 'PUBLIC RELEASE BLOCKED pending legal and final payload review.',
                 'This collection preserves observed license texts without interpreting or granting rights.',
                 'See dependency-inventory.json for versions, source hashes, missing evidence and runtime DLL inventory.',
                 'Build/dev/platform dependencies are included conservatively; this is not an exact shipped SBOM.', '', 'UNRESOLVED RELEASE BLOCKERS:']
        lines.extend('- ' + issue for issue in self.issues)
        for record in self.records:
            lines.extend(['', f'[{record["ecosystem"]}] {record["name"]} {record["version"]}',
                          'Observed license metadata: ' + json.dumps(record.get('license_metadata'), ensure_ascii=False),
                          'Evidence: ' + record.get('evidence_status', 'unknown'),
                          'Legal determination: manual confirmation required'])
            for item in record.get('license_texts', []):
                lines.append(f'License/notice source: {item["path"]}; SHA256 {item["sha256"]}; copy {item["bundled_path"]}')
        # Include each exact text once, so the single notices file is useful on its own.
        for sha, blob in sorted(self.texts.items()):
            lines.extend(['', f'===== BEGIN OBSERVED TEXT SHA256 {sha} =====',
                          blob.decode('utf-8', errors='replace'), f'===== END OBSERVED TEXT SHA256 {sha} ====='])
        (self.output / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(lines) + '\n', 'utf-8')
        summary = {'public_release_status': inventory['public_release_status'], 'dependencies': len(self.records),
                   'unique_license_texts': len(self.texts), 'runtime_artifacts': len(self.artifacts),
                   'dependency_status_counts': counts}
        (self.output / 'REVIEW_REQUIRED.json').write_text(json.dumps(summary, indent=2) + '\n', 'utf-8')
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, help='Fresh directory; default packaging/release-notices')
    parser.add_argument('--cargo-home', type=Path, default=Path.home() / '.cargo')
    parser.add_argument('--python-home', type=Path, default=Path(sys.base_prefix))
    args = parser.parse_args()
    output = args.output or args.repo / 'packaging/release-notices'
    print(json.dumps(Collector(args.repo, output, args.cargo_home, args.python_home).run(), indent=2))


if __name__ == '__main__':
    main()
