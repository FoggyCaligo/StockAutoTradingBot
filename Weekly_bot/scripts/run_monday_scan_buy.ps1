$ErrorActionPreference = "Stop"
$BotRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $BotRoot
$PythonPath = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $BotRoot "logs"

if (!(Test-Path $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Set-Location $BotRoot
& $PythonPath main.py buy --real --data live --log-dir $LogDir
