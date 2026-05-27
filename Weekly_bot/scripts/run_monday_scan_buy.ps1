$ErrorActionPreference = "Stop"
$BotRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $BotRoot
$PythonPath = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $BotRoot "logs"
$MainPath = Join-Path $BotRoot "main.py"
$LockPath = Join-Path $LogDir "run_monday_scan_buy.lock"

function Test-BotPythonRunning {
    param(
        [string]$BotRootPath,
        [string]$EntryPath,
        [string]$CommandMarker
    )

    $normalizedBotRoot = [System.IO.Path]::GetFullPath($BotRootPath)
    $normalizedEntry = [System.IO.Path]::GetFullPath($EntryPath)
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    foreach ($process in $processes) {
        $commandLine = [string]$process.CommandLine
        if ($commandLine -and $commandLine.Contains($normalizedBotRoot) -and $commandLine.Contains($normalizedEntry) -and $commandLine.Contains($CommandMarker)) {
            return $true
        }
    }
    return $false
}

if (!(Test-Path $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
if (Test-Path $LockPath) {
    if (Test-BotPythonRunning -BotRootPath $BotRoot -EntryPath $MainPath -CommandMarker " buy ") {
        Write-Output "Weekly_bot buy is already running. Skip duplicate start."
        exit 0
    }
    Remove-Item $LockPath -Force
}
Set-Content -Path $LockPath -Value "pid=$PID`nstarted_at=$(Get-Date -Format o)" -Encoding ascii
Set-Location $BotRoot
try {
    & $PythonPath main.py buy --real --data live --log-dir $LogDir
}
finally {
    if (Test-Path $LockPath) {
        Remove-Item $LockPath -Force
    }
}
