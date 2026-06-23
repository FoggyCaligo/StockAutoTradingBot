$ErrorActionPreference = "Stop"
$BotRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $BotRoot
$PythonPath = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $BotRoot "logs"
$LockPath = Join-Path $LogDir "run_refresh_universe.lock"
$ScriptPath = Join-Path $BotRoot "scripts\refresh_kospi200_universe.py"

if (!(Test-Path $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
if (Test-Path $LockPath) {
    Remove-Item $LockPath -Force
}
Set-Content -Path $LockPath -Value "pid=$PID`nstarted_at=$(Get-Date -Format o)" -Encoding ascii
Set-Location $BotRoot
try {
    & $PythonPath $ScriptPath
}
finally {
    if (Test-Path $LockPath) {
        Remove-Item $LockPath -Force
    }
}
