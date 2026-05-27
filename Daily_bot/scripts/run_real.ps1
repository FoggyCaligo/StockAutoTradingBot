$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $botRoot
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $botRoot "main.py"
$logDir = Join-Path $botRoot "logs"

if (!(Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

if (!(Test-Path $mainPath)) {
    throw "main.py not found: $mainPath"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "run_real_$timestamp.log"

Push-Location $botRoot
try {
    & $pythonPath $mainPath --real *>&1 | Tee-Object -FilePath $logPath
}
finally {
    Pop-Location
}
