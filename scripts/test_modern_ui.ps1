$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ModernRoot = Join-Path $ProjectRoot "desktop_modern"
$Cargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"

$VsWhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $VsWhere) {
    $VsRoot = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    $VsDevCmd = Join-Path $VsRoot "Common7\Tools\VsDevCmd.bat"
    $DevEnvironment = cmd.exe /d /c "`"$VsDevCmd`" -arch=x64 -host_arch=x64 >nul && set"
    foreach ($Line in $DevEnvironment) {
        if ($Line -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
}
$env:Path = "$(Split-Path -Parent $Cargo);$env:Path"

Push-Location $ModernRoot
try {
    pnpm test
    pnpm typecheck
    pnpm build
    & $Cargo test --manifest-path (Join-Path $ModernRoot "src-tauri\Cargo.toml")
} finally {
    Pop-Location
}

Push-Location $ProjectRoot
try {
    python -m unittest discover -s tests -v
} finally {
    Pop-Location
}
