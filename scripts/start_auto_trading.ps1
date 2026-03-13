param(
    [switch]$Once,
    [switch]$NoStartupRecovery
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "data\runtime"
$logDir = Join-Path $runtimeDir "logs"
$pidPath = Join-Path $runtimeDir "auto_trading.pid"
$stdoutPath = Join-Path $logDir "auto_trading.stdout.log"
$stderrPath = Join-Path $logDir "auto_trading.stderr.log"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidPath) {
    $existingPid = (Get-Content $pidPath -Raw).Trim()
    if ($existingPid) {
        $existingProcess = Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue
        if ($null -ne $existingProcess) {
            Write-Output "auto_trading is already running. pid=$existingPid"
            exit 0
        }
    }
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
}

$pythonArgs = @("-m", "auto_trading")
if ($Once) {
    $pythonArgs += "--once"
}
if ($NoStartupRecovery) {
    $pythonArgs += "--no-startup-recovery"
}
$pythonArgString = [string]::Join(" ", $pythonArgs)
$command = "& { " + '$env:PYTHONPATH=''src''; python ' + $pythonArgString + " }"
$process = Start-Process `
    -FilePath "pwsh" `
    -ArgumentList @("-NoProfile", "-Command", $command) `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

Set-Content -Path $pidPath -Value $process.Id -Encoding utf8

Write-Output "auto_trading started. pid=$($process.Id)"
Write-Output "command=python $pythonArgString"
Write-Output "stdout=$stdoutPath"
Write-Output "stderr=$stderrPath"

