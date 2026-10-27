[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SourceBundle,
    [Parameter(Mandatory=$true)][string]$ExpectedManifestSha256,
    [string]$Python=(Get-Command python -ErrorAction Stop).Source
)
$ErrorActionPreference='Stop'
$Project=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SourceBundle=(Resolve-Path -LiteralPath $SourceBundle).Path
$Manifest=Join-Path $SourceBundle 'protected-runtime-manifest.json'
if((Get-FileHash -LiteralPath $Manifest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedManifestSha256){throw 'Explicit approved manifest pin does not match source bundle.'}
$Previous=$env:PYTHONPATH
try {
    $env:PYTHONPATH=$Project
    & $Python -c 'import sys;from pathlib import Path;from protected_runtime.management import validate_manifest;from protected_runtime.release import release_binding;r=Path(sys.argv[1]);raw=(r/"protected-runtime-manifest.json").read_bytes();inv={p.relative_to(r).as_posix():p.read_bytes() for p in r.rglob("*") if p.is_file() and p.name!="protected-runtime-manifest.json"};validate_manifest(raw,sys.argv[2],release_binding(),inv);print("validated",len(inv))' $SourceBundle $ExpectedManifestSha256
    if($LASTEXITCODE -ne 0){throw 'Source bundle validation failed'}
} finally {$env:PYTHONPATH=$Previous}
$Tauri=Join-Path $Project 'desktop_modern\src-tauri'
$Destination=Join-Path $Tauri 'binaries\protected-runtime'
if(Test-Path -LiteralPath $Destination){throw 'Protected build input already exists; refuse to merge stale artifacts. Use a verified clean staging directory.'}
Copy-Item -LiteralPath $SourceBundle -Destination $Destination -Recurse
$ExpectedManifestSha256 | Set-Content -LiteralPath (Join-Path $Tauri 'binaries\protected-runtime.sha256') -Encoding ascii
Write-Output "Prepared protected build input; manifest pin $ExpectedManifestSha256"
