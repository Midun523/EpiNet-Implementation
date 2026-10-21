# tests/demo/right_pane.ps1 — Failure Demo Execution for EpiNet Screen Recording
# WHY: Executes failure_demo.py with unbuffered output and logs to right_pane.log.

$ErrorActionPreference = "Continue"
$repoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repoRoot

$logPath = Join-Path $PSScriptRoot "right_pane.log"
$demoScript = Join-Path $PSScriptRoot "failure_demo.py"

Write-Host "=================== RIGHT: FAILURE DEMO ====================" -ForegroundColor Green
Write-Host "Running failure demo (logging to $logPath)..." -ForegroundColor Gray

python -u $demoScript | Tee-Object -FilePath $logPath
