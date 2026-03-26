param(
    [int]$Days = 7,
    [int]$ScoreImprovement = 5,
    [switch]$SameDayOnly,
    [int]$Limit = 20
)

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $env:PYTHONPATH = 'src'
    $args = @(
        'scripts/diagnose_ma5_reentry_scores.py',
        '--days', $Days,
        '--score-improvement', $ScoreImprovement,
        '--limit', $Limit
    )
    if ($SameDayOnly) {
        $args += '--same-day-only'
    }
    python @args
}
finally {
    Pop-Location
}
