# tests/demo/left_pane.ps1 — Continuous Kubernetes Pod Watch for EpiNet Failure Demo
# WHY: Streams real-time pod lifecycle changes to console and simultaneously logs to left_pane.log.

$ErrorActionPreference = "Continue"
$repoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repoRoot

$logPath = Join-Path $PSScriptRoot "left_pane.log"
Write-Host "==================== LEFT: K8S POD WATCH ====================" -ForegroundColor Cyan
Write-Host "Watching pods (logging to $logPath)..." -ForegroundColor Gray

kubectl get pods -w | Tee-Object -FilePath $logPath
