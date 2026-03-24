$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "src"

python scripts/reconcile_eod_fills.py @args
