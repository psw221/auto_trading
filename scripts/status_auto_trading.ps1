$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "data\runtime"
$logDir = Join-Path $runtimeDir "logs"
$pidPath = Join-Path $runtimeDir "auto_trading.pid"
$stdoutPath = Join-Path $logDir "auto_trading.stdout.log"
$stderrPath = Join-Path $logDir "auto_trading.stderr.log"

if (-not (Test-Path $pidPath)) {
    Write-Output "status=stopped"
    Write-Output "pid=<none>"
    exit 0
}

$pidValue = (Get-Content $pidPath -Raw).Trim()
if (-not $pidValue) {
    Write-Output "status=stopped"
    Write-Output "pid=<empty>"
    exit 0
}

$process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Write-Output "status=stopped"
    Write-Output "pid=$pidValue"
    Write-Output "note=stale_pid_file"
    exit 0
}

Write-Output "status=running"
Write-Output "pid=$pidValue"
Write-Output "process_name=$($process.ProcessName)"
Write-Output "started_at=$($process.StartTime.ToString('yyyy-MM-dd HH:mm:ss'))"
Write-Output "stdout=$stdoutPath"
Write-Output "stderr=$stderrPath"
