$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "data\runtime"
$pidPath = Join-Path $runtimeDir "auto_trading.pid"

if (-not (Test-Path $pidPath)) {
    Write-Output "auto_trading is not running."
    exit 0
}

$pidValue = (Get-Content $pidPath -Raw).Trim()
if (-not $pidValue) {
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output "auto_trading pid file was empty and has been removed."
    exit 0
}

$process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output "auto_trading process was not running. stale pid file removed."
    exit 0
}

$notifyCommand = @"
from auto_trading.config.settings import load_settings
from auto_trading.notifications.telegram import TelegramNotifier
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.system_events import SystemEventsRepository

settings = load_settings()
db = Database(settings.db_path)
db.initialize()
system_events = SystemEventsRepository(db)
notifier = TelegramNotifier(settings, system_events)
notifier.send_system_event(
    {
        'message': 'auto_trading stopped by launcher',
        'severity': 'INFO',
        'component': 'launcher'
    }
)
"@

try {
    $env:PYTHONPATH = 'src'
    python -c $notifyCommand
} catch {
}

Stop-Process -Id $process.Id -Force
Remove-Item $pidPath -Force -ErrorAction SilentlyContinue

Write-Output "auto_trading stopped. pid=$($process.Id)"
