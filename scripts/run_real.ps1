$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $repoRoot "main.py"
$logDir = Join-Path $repoRoot "logs"

if (!(Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

if (!(Test-Path $mainPath)) {
    throw "main.py not found: $mainPath"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "run_real_$timestamp.log"

Push-Location $repoRoot
try {
    & $pythonPath $mainPath --real *>&1 | Tee-Object -FilePath $logPath
}
finally {
    Pop-Location
}
