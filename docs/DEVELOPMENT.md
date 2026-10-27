# Development and recovery

## Prerequisites

Windows x64, Python 3.12, Rust stable MSVC, Visual Studio C++ Build Tools with Windows SDK, Node.js20.19+ or22.12+ and pnpm10. Microsoft WebView2 Runtime must already be installed for UI launch. Ollama is separate and needed only for real model requests. No model download is performed by these steps.

Clone into any new directory, then from its root:

```powershell
python -m unittest discover -s tests -v
python run_core.py --stdio --db "$env:TEMP/localagent-recovery.db"
```

The first command can exercise Windows test fixtures in the historical authorized Agent root when it exists; record skips/failures rather than creating a wider root or weakening checks. The Core accepts version1.0 JSONL requests such as {"version":"1.0","id":"1","action":"health","params":{}}. Close stdin to exit. Use a fresh DB path, not private historical data.

```powershell
cd desktop_modern
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
pnpm build
```

Native/Frozen development additionally requires Python3.12.5 x64 and PyInstaller6.22.0. The historical build scripts pin the Python executable SHA256 and refuse a mismatch; choose the matching interpreter, do not bypass integrity checks. Their publication-only defaults resolve Python from PATH rather than a person's profile. Two opt-in Native tests use LOCALAGENT_TEST_PYTHON for an explicit absolute test interpreter. Production authority code is unchanged.

The Native build also requires a freshly reviewed `packaging/release-notices-final` resource directory before the Tauri command. Historical private notice inventories are excluded from Git. Generate observed evidence using `scripts/collect_release_notices.py` and complete its reported review obligations before redistribution. The Native recipe below is **NOT VERIFIED** end to end in migration; do not interpret it as a one-command cleared release.

From repository root in a Developer PowerShell:

```powershell
python -m pip install -r requirements-build.txt
./scripts/build_embedded_core.ps1 -Python (Get-Command python).Source -OutputRoot "$PWD/work/fresh-sidecars"
./scripts/build_protected_runtime.ps1 -Python (Get-Command python).Source -OutputRoot "$PWD/work/fresh-management"
$pin=(Get-Content ./work/fresh-management/build-evidence/manifest-sha256.txt -Raw).Trim()
./scripts/prepare_protected_desktop.ps1 -SourceBundle ./work/fresh-management/dist/protected-host-management -ExpectedManifestSha256 $pin -Python (Get-Command python).Source
./scripts/build_agent_broker.ps1 -OutputRoot "$PWD/work/fresh-broker"
Copy-Item ./work/fresh-broker/binaries/agent-broker ./desktop_modern/src-tauri/binaries/agent-broker -Recurse
Copy-Item ./work/fresh-broker/binaries/agent-broker.sha256 ./desktop_modern/src-tauri/binaries/agent-broker.sha256
cd desktop_modern
pnpm tauri build --no-bundle
```

Tauri's configuration references packaging/release-notices-final for distribution resources. A fresh redistribution review/notice collection is required before packaging; those private historical notice inventories are intentionally not copied into Git. The existing scripts/collect_release_notices.py can collect observed dependency evidence into a fresh directory; it deliberately does not grant legal clearance. The no-bundle executable still requires its sidecars and approved manifest resources staged beside it; scripts/stage_review041_desktop.ps1 documents historical layout but is not an automatic S release approval.

This recipe documents historical tools; a complete Native rebuild and general Agent portability must be recorded separately as VERIFIED or NOT VERIFIED. No claim of reproducible identical binaries is made. Downloaded Frozen recovery cannot be verified while the Frozen Release is withheld.
