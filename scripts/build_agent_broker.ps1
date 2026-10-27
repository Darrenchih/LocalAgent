param([string]$OutputRoot)
$ErrorActionPreference = 'Stop'
$python = (Get-Command python -ErrorAction Stop).Source
if ((Get-FileHash -LiteralPath $python -Algorithm SHA256).Hash.ToLowerInvariant() -ne '5aba6ec903f2e0e946459f98dc45c8129d3f22187f5adac00713d733191d3a3f') { throw 'Pinned Python executable integrity mismatch' }
$project = Split-Path $PSScriptRoot -Parent
$build = if ($OutputRoot) { [System.IO.Path]::GetFullPath($OutputRoot) } else { Join-Path $project 'work\agent-integration\build' }
if (Test-Path -LiteralPath $build) { throw 'Fresh build directory required' }
$version = & $python -c "import PyInstaller; print(PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '6.22.0') { throw 'PyInstaller 6.22.0 required' }
New-Item -ItemType Directory -Path $build | Out-Null
Push-Location $project
try {
    & $python -m PyInstaller --noconfirm --clean --noupx --onedir --name agent-broker --paths $project --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'pyinstaller') --specpath $build (Join-Path $project 'packaging\agent_broker_entry.py')
    if ($LASTEXITCODE -ne 0) { throw 'Frozen build failed' }
    $env:AGENT_BROKER_BUNDLE = Join-Path $build 'dist\agent-broker'
    @'
import hashlib,json,os,pathlib
from protected_runtime.agent_broker import IDENTITY
root=pathlib.Path(os.environ['AGENT_BROKER_BUNDLE'])
artifacts=[]
for p in sorted(root.rglob('*')):
    if p.is_symlink(): raise ValueError('symlink in bundle')
    if not p.is_file(): continue
    name=p.relative_to(root).as_posix()
    suffix=p.suffix.lower()
    if suffix=='.exe' and name!='agent-broker.exe': raise ValueError('unexpected executable')
    kind='entrypoint' if name=='agent-broker.exe' else 'native_library' if suffix in ('.dll','.pyd') else 'python_archive' if suffix in ('.zip','.pyz') else 'license' if 'license' in name.lower() else 'data'
    data=p.read_bytes()
    artifacts.append(dict(path=name,kind=kind,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
manifest=dict(IDENTITY,target='x86_64-pc-windows-msvc',roles={'protected-agent-broker':'agent-broker.exe'},artifacts=artifacts)
raw=(json.dumps(manifest,separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')
(root/'protected-runtime-manifest.json').write_bytes(raw)
(root.parent/'agent-broker.sha256').write_text(hashlib.sha256(raw).hexdigest()+'\n',encoding='ascii')
'@ | & $python -
    if ($LASTEXITCODE -ne 0) { throw 'Manifest generation failed' }
    $binaries = Join-Path $build 'binaries'
    New-Item -ItemType Directory -Path $binaries | Out-Null
    Copy-Item -LiteralPath $env:AGENT_BROKER_BUNDLE -Destination $binaries -Recurse
    Copy-Item -LiteralPath (Join-Path $build 'dist\agent-broker.sha256') -Destination (Join-Path $binaries 'agent-broker.sha256')
    Write-Output $binaries
} finally { Pop-Location; Remove-Item Env:\AGENT_BROKER_BUNDLE -ErrorAction SilentlyContinue }
