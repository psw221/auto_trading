$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "src"

python scripts/show_strategy_targets.py
