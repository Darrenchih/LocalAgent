param(
    [string]$Python = (Get-Command python -ErrorAction Stop).Source,
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\work\review-042\build\sidecars'),
    [string]$ExpectedPythonSha256 = '5aba6ec903f2e0e946459f98dc45c8129d3f22187f5adac00713d733191d3a3f'
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $projectRoot "desktop_modern\src-tauri\binaries"
$workDir = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $workDir) { throw 'Build output already exists; use a fresh directory.' }
if ((Get-FileHash -LiteralPath $Python -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPythonSha256) { throw 'Python executable pin mismatch.' }
& $Python -c 'import PyInstaller;assert PyInstaller.__version__=="6.22.0"'
if ($LASTEXITCODE -ne 0) { throw 'Pinned PyInstaller check failed.' }
$distDir = Join-Path $workDir "dist"
$targetName = "agent-core-x86_64-pc-windows-msvc.exe"

New-Item -ItemType Directory -Force -Path $outputDir, $workDir | Out-Null
& $Python -m PyInstaller --noconfirm --clean --noupx --onefile --name agent-core `
    --distpath $distDir --workpath (Join-Path $workDir "work") `
    --specpath $workDir --paths $projectRoot `
    (Join-Path $projectRoot "packaging\core_entry.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Copy-Item -LiteralPath (Join-Path $distDir "agent-core.exe") `
    -Destination (Join-Path $outputDir $targetName) -Force
Write-Output (Join-Path $outputDir $targetName)

# Discovery is an independent desktop-only process; it is not a formal Tool Runtime.
& $Python -m PyInstaller --noconfirm --clean --noupx --onefile --name agent-discovery `
    --distpath $distDir --workpath (Join-Path $workDir 'discovery-work') `
    --specpath $workDir --paths $projectRoot `
    (Join-Path $projectRoot 'packaging\discovery_entry.py')
if ($LASTEXITCODE -ne 0) { throw "Discovery PyInstaller failed: $LASTEXITCODE" }
$discovery = Join-Path $outputDir 'agent-discovery-x86_64-pc-windows-msvc.exe'
Copy-Item -LiteralPath (Join-Path $distDir 'agent-discovery.exe') -Destination $discovery -Force
(Get-FileHash -LiteralPath $discovery -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -Encoding ascii (Join-Path $outputDir 'discovery.sha256')
Write-Output $discovery
Get-FileHash -Algorithm SHA256 -LiteralPath $Python,(Join-Path $outputDir $targetName),$discovery | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $workDir 'build-hashes.json')
