param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..\..')
$git = 'C:\Program Files\Git\cmd\git.exe'
$kst = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
$date = [TimeZoneInfo]::ConvertTimeFromUtc([datetime]::UtcNow, $kst).ToString('yyyyMMdd')
$path = "Daily_bot/logs/market_traces_$date.csv"
$log = Join-Path (Get-Location).Path "Daily_bot\logs\push_market_trace_$date.tmp"

function Invoke-Git {
    & $git @args
    if ($LASTEXITCODE -ne 0) { throw "git $($args[0]) failed with exit code $LASTEXITCODE" }
}

function Write-RunLog($message) {
    $line = "$(Get-Date -Format o) $message"
    if ($DryRun) { Write-Host $line }
    else { Add-Content -LiteralPath $log -Value $line }
}

$index = Join-Path ([IO.Path]::GetTempPath()) ("market-trace-" + [guid]::NewGuid())
try {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-RunLog "No CSV for $date"
        exit 0
    }
    $env:GIT_INDEX_FILE = $index
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'Never'

    Invoke-Git fetch --no-tags --no-write-fetch-head origin +refs/heads/main:refs/remotes/origin/main
    $parent = Invoke-Git rev-parse refs/remotes/origin/main
    Invoke-Git read-tree $parent
    Invoke-Git add --force -- $path
    $tree = Invoke-Git write-tree
    if ($tree -eq (Invoke-Git rev-parse "$parent^{tree}")) {
        Write-RunLog "$path is already on origin/main"
        exit 0
    }
    $changed = @(Invoke-Git diff-tree --no-commit-id --name-only -r $parent $tree)
    if ($changed.Count -ne 1 -or $changed[0] -cne $path) {
        throw 'Unexpected paths in the market trace commit'
    }
    $size = [long](Invoke-Git cat-file -s "$($tree):$path")
    if ($size -gt 100MB) { throw "$path exceeds the GitHub 100 MiB limit" }
    $commit = Invoke-Git commit-tree $tree -p $parent -m "Add market trace for $date"
    if ($DryRun) {
        Invoke-Git push --dry-run origin "$($commit):refs/heads/main"
        Write-RunLog "Dry run verified $path ($size bytes)"
    } else {
        Invoke-Git push origin "$($commit):refs/heads/main"
        Write-RunLog "Pushed $commit ($path)"
    }
} catch {
    Write-RunLog "ERROR $($_.Exception.Message)"
    exit 1
} finally {
    Remove-Item -LiteralPath $index -ErrorAction SilentlyContinue
}
