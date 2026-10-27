[CmdletBinding()]
param(
    [string]$Python = (Get-Command python -ErrorAction Stop).Source,
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\work\review-042\build\frozen'),
    [string]$ExpectedPythonSha256 = '5aba6ec903f2e0e946459f98dc45c8129d3f22187f5adac00713d733191d3a3f'
)
$ErrorActionPreference = 'Stop'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Python).Hash.ToLowerInvariant() -ne $ExpectedPythonSha256) { throw 'Python executable pin mismatch.' }
$Repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $OutputRoot) { throw 'OutputRoot already exists; choose a new isolated directory.' }
New-Item -ItemType Directory -Path $OutputRoot | Out-Null
$Evidence = Join-Path $OutputRoot 'build-evidence'
New-Item -ItemType Directory -Path $Evidence | Out-Null
$PreviousPythonPath = $env:PYTHONPATH
Push-Location $Repository
try {
    & $Python -c 'import sys,PyInstaller,platform; assert sys.version_info[:2]==(3,12); assert PyInstaller.__version__=="6.22.0"; assert platform.machine()=="AMD64"; print(sys.executable); print(sys.version); print("PyInstaller",PyInstaller.__version__)' *> (Join-Path $Evidence 'versions.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned build runtime check failed' }
    Get-FileHash -Algorithm SHA256 -LiteralPath $Python | Format-List *> (Join-Path $Evidence 'python-sha256.txt')
    & $Python -m PyInstaller --noconfirm --clean --onedir --console --noupx --name protected-host-management --paths $Repository --add-data ((Join-Path $Repository 'packaging\protected-runtime-release.json')+';.') --distpath (Join-Path $OutputRoot 'dist') --workpath (Join-Path $OutputRoot 'build') --specpath $Evidence (Join-Path $Repository 'packaging\protected_host_entry.py') *> (Join-Path $Evidence 'pyinstaller.log')
    if ($LASTEXITCODE -ne 0) { throw 'Frozen build failed; inspect pyinstaller.log' }
    $Bundle = Join-Path $OutputRoot 'dist\protected-host-management'
    $env:PYTHONPATH = $Repository
    & $Python (Join-Path $PSScriptRoot 'protected_bundle_manifest.py') $Bundle *> (Join-Path $Evidence 'manifest-sha256.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Manifest creation failed' }
    & $Python -m pip show pyinstaller pyinstaller-hooks-contrib packaging pefile altgraph pywin32-ctypes *> (Join-Path $Evidence 'build-dependencies.txt')
    $Licenses = Join-Path $Evidence 'licenses'
    New-Item -ItemType Directory -Path $Licenses | Out-Null
    $PythonLicense = Join-Path (Split-Path $Python) 'LICENSE.txt'
    if (Test-Path $PythonLicense) { Copy-Item -LiteralPath $PythonLicense -Destination (Join-Path $Licenses 'Python-LICENSE.txt') }
    'Local build provenance only. Python license copied when present. Full transitive runtime and build-tool notice review is not completed; this evidence is not an installer or a redistribution license audit.' | Set-Content (Join-Path $Licenses 'LIMITATIONS.txt')
    Write-Output "Bundle: $Bundle"
    Get-Content (Join-Path $Evidence 'manifest-sha256.txt')
} finally { $env:PYTHONPATH = $PreviousPythonPath; Pop-Location }

