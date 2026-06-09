$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $botRoot
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $botRoot "main.py"
$logDir = Join-Path $botRoot "logs"
$lockPath = Join-Path $logDir "run_real.lock"

function Test-BotPythonRunning {
    param(
        [string]$BotRootPath,
        [string]$EntryPath
    )

    $normalizedBotRoot = [System.IO.Path]::GetFullPath($BotRootPath)
    $normalizedEntry = [System.IO.Path]::GetFullPath($EntryPath)
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    foreach ($process in $processes) {
        $commandLine = [string]$process.CommandLine
        if ($commandLine -and $commandLine.Contains($normalizedBotRoot) -and $commandLine.Contains($normalizedEntry)) {
            return $true
        }
    }
    return $false
}

if (!(Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

if (!(Test-Path $mainPath)) {
    throw "main.py not found: $mainPath"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (Test-Path $lockPath) {
    if (Test-BotPythonRunning -BotRootPath $botRoot -EntryPath $mainPath) {
        Write-Output "Daily_bot is already running. Skip duplicate start."
        exit 0
    }
    Remove-Item $lockPath -Force
}

Set-Content -Path $lockPath -Value "pid=$PID`nstarted_at=$(Get-Date -Format o)" -Encoding ascii
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "run_real_$timestamp.log"
$stdoutPath = Join-Path $logDir "run_real_$timestamp.stdout.tmp"
$stderrPath = Join-Path $logDir "run_real_$timestamp.stderr.tmp"

Push-Location $botRoot
try {
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @($mainPath, "--real") `
        -WorkingDirectory $botRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    Wait-Process -Id $process.Id
    $process.Refresh()

    if (Test-Path $stdoutPath) {
        Get-Content $stdoutPath | Set-Content -Path $logPath -Encoding utf8
    }
    else {
        Set-Content -Path $logPath -Value "" -Encoding utf8
    }

    if (Test-Path $stderrPath) {
        Get-Content $stderrPath | Add-Content -Path $logPath -Encoding utf8
    }

    if ($process.ExitCode -ne 0) {
        throw "Daily_bot exited with code $($process.ExitCode). See log: $logPath"
    }
}
finally {
    if (Test-Path $stdoutPath) {
        Remove-Item $stdoutPath -Force
    }
    if (Test-Path $stderrPath) {
        Remove-Item $stderrPath -Force
    }
    if (Test-Path $lockPath) {
        Remove-Item $lockPath -Force
    }
    Pop-Location
}
