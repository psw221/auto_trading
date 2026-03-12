$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "src"

python scripts/show_runtime_dashboard.py
