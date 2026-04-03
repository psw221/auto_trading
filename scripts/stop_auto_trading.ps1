$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "data\runtime"
$pidPath = Join-Path $runtimeDir "auto_trading.pid"

if (-not (Test-Path $pidPath)) {
    Write-Output 'auto_trading is not running.'
    exit 0
}

$pidValue = (Get-Content $pidPath -Raw).Trim()
if (-not $pidValue) {
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output 'auto_trading is not running.'
    exit 0
}

$process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output "auto_trading is not running. stale pid=$pidValue"
    exit 0
}

Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
Remove-Item $pidPath -Force -ErrorAction SilentlyContinue

Write-Output "auto_trading stopped. pid=$pidValue"
