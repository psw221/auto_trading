$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "src"

python scripts/diagnose_market_data_refresh.py @args
