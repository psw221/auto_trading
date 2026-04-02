$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "data\runtime"
$pidPath = Join-Path $runtimeDir "auto_trading.pid"

function Get-AutoTradingRuntimeProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and (
            $_.CommandLine -like '*python.exe" -m auto_trading*' -or
            $_.CommandLine -like '*python -m auto_trading*'
        )
    }
}

$trackedPid = $null
if (Test-Path $pidPath) {
    $pidValue = (Get-Content $pidPath -Raw).Trim()
    if ($pidValue) {
        $trackedPid = [int]$pidValue
    } else {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    }
}

$runtimeProcesses = @(Get-AutoTradingRuntimeProcesses)
if ($null -eq $trackedPid -and $runtimeProcesses.Count -eq 0) {
    Write-Output 'auto_trading is not running.'
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

$pythonIds = [System.Collections.Generic.HashSet[int]]::new()
if ($null -ne $trackedPid) {
    $null = $pythonIds.Add($trackedPid)
}
foreach ($process in $runtimeProcesses) {
    $null = $pythonIds.Add([int]$process.ProcessId)
}

$parentIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($processId in $pythonIds) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" | Select-Object -First 1
    if ($null -ne $processInfo -and [int]$processInfo.ParentProcessId -gt 0) {
        $null = $parentIds.Add([int]$processInfo.ParentProcessId)
    }
}

foreach ($processId in $pythonIds) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

foreach ($parentId in $parentIds) {
    $parent = Get-Process -Id $parentId -ErrorAction SilentlyContinue
    if ($null -ne $parent -and $parent.ProcessName -eq 'pwsh') {
        Stop-Process -Id $parentId -Force -ErrorAction SilentlyContinue
    }
}

Remove-Item $pidPath -Force -ErrorAction SilentlyContinue

$stoppedIds = @($pythonIds | Sort-Object)
if ($stoppedIds.Count -eq 0) {
    Write-Output 'auto_trading is not running.'
} else {
    Write-Output ("auto_trading stopped. pid=" + ($stoppedIds -join ','))
}
