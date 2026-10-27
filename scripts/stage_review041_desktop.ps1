[CmdletBinding()]
param([string]$OutputRoot=(Join-Path $PSScriptRoot '..\work\review-041\桌面 整合版本'))
$ErrorActionPreference='Stop'
$Project=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Tauri=Join-Path $Project 'desktop_modern\src-tauri'
$OutputRoot=[IO.Path]::GetFullPath($OutputRoot)
if(Test-Path -LiteralPath $OutputRoot){throw 'Staging directory exists; choose a new isolated directory.'}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Tauri 'target\release\windows-ollama-agent-desktop.exe') -Destination $OutputRoot
Copy-Item -LiteralPath (Join-Path $Tauri 'binaries\agent-core-x86_64-pc-windows-msvc.exe') -Destination (Join-Path $OutputRoot 'agent-core.exe')
Copy-Item -LiteralPath (Join-Path $Tauri 'binaries\agent-discovery-x86_64-pc-windows-msvc.exe') -Destination (Join-Path $OutputRoot 'agent-discovery.exe')
$Runtime=Join-Path $OutputRoot 'runtime\protected\review040-management-v1'
New-Item -ItemType Directory -Path (Split-Path -Parent $Runtime) -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Tauri 'binaries\protected-runtime') -Destination $Runtime -Recurse
$Rows=Get-ChildItem -LiteralPath $OutputRoot -Recurse -File | ForEach-Object {
    [pscustomobject]@{path=[IO.Path]::GetRelativePath($OutputRoot,$_.FullName);size=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$Rows | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $OutputRoot 'stage-manifest.json') -Encoding utf8
Write-Output $OutputRoot
