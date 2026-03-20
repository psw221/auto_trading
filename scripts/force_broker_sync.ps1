$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = 'src'
python scripts/force_broker_sync.py $args
