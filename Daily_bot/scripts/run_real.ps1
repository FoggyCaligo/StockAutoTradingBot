param([switch]$CollectOnly)

$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $botRoot
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $botRoot "session_slot_runner.py"
$logDir = Join-Path $botRoot "logs"
$lockPath = Join-Path $logDir "run_real.lock"

function Test-BotPythonRunning {
    param(
        [string]$BotRootPath,
        [string]$EntryPath
    )

    $normalizedBotRoot = [System.IO.Path]::GetFullPath($BotRootPath)
    $entryPaths = @(
        [System.IO.Path]::GetFullPath($EntryPath),
        (Join-Path $normalizedBotRoot "market_trace_collector.py"),
        (Join-Path $normalizedBotRoot "session_slot_runner.py")
    )
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    foreach ($process in $processes) {
        $commandLine = [string]$process.CommandLine
        foreach ($entry in $entryPaths) {
            if ($commandLine -and $commandLine.Contains($entry)) {
                return $true
            }
        }
    }
    return $false
}

if (!(Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

if (!(Test-Path $mainPath)) {
    throw "session_slot_runner.py not found: $mainPath"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (Test-BotPythonRunning -BotRootPath $botRoot -EntryPath $mainPath) {
    Write-Output "Daily_bot trading or market trace collection is already running. Skip duplicate start."
    exit 0
}

if (Test-Path $lockPath) {
    Remove-Item $lockPath -Force
}

Set-Content -Path $lockPath -Value "pid=$PID`nstarted_at=$(Get-Date -Format o)" -Encoding ascii
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "run_real_$timestamp.log"
$stdoutPath = Join-Path $logDir "run_real_$timestamp.stdout.tmp"
$stderrPath = Join-Path $logDir "run_real_$timestamp.stderr.tmp"

Push-Location $botRoot
try {
    $botArguments = @("-u", $mainPath, "--real")
    if ($CollectOnly) {
        $botArguments = @("-u", (Join-Path $botRoot "market_trace_collector.py"), "--real")
    }
    else {
        $botArguments += "--enable-trading"
    }
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList ($botArguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) `
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
