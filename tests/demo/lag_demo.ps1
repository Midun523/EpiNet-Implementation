# tests/demo/lag_demo.ps1 — Grafana screenshot helper for Kafka consumer lag demonstration
# WHY: Facilitates capturing clear Grafana dashboard screenshots of:
# 1. Backpressure accumulation (lag climbing across Kafka partitions while ml-service is scaled to 0).
# 2. Queue drain (lag dropping back to 0 as ml-service scales back up and processes queued jobs).

[CmdletBinding()]
param(
    [string]$BackendUrl = "http://localhost:30081",
    [string]$BenchmarkCsv = "tests/bench/data/benchmark_2mb.csv",
    [int]$WaitSeconds = 40
)

$ErrorActionPreference = "Stop"

# WHY: Ensure script runs relative to repo root regardless of launch location.
$repoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repoRoot

$csvPath = Join-Path $repoRoot $BenchmarkCsv
if (-not (Test-Path $csvPath)) {
    throw "Benchmark CSV not found at: $csvPath"
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "       EPINET KAFKA CONSUMER LAG SCREENSHOT HELPER" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Target Backend: $BackendUrl" -ForegroundColor Gray
Write-Host "Payload Source: $csvPath (2 MB)" -ForegroundColor Gray

# (a) Scale ml-service to 0
# WHY: Halting consumer replicas causes incoming messages on 'analysis.requested' to queue up,
# directly driving up kafka_consumergroup_lag in kafka-exporter and Prometheus.
Write-Host "`n[STEP 1] Scaling ml-service deployment to 0 replicas..." -ForegroundColor Yellow
kubectl scale deployment/ml-service --replicas=0
kubectl rollout status deployment/ml-service --timeout=60s

# (b) Upload 5 files of 2 MB from tests/bench/data
# WHY: Submitting 5 jobs distributes messages across the 3 partitions of 'analysis.requested',
# creating an unmistakable multi-series spike in the Grafana consumer lag panel.
Write-Host "`n[STEP 2] Uploading 5 files of 2 MB to trigger consumer lag..." -ForegroundColor Yellow
for ($i = 1; $i -le 5; $i++) {
    Write-Host "  Uploading payload $i/5..." -ForegroundColor Gray
    $resp = curl.exe --noproxy "*" -s -F "file=@$csvPath" "$BackendUrl/api/uploads"
    Write-Host "    Backend response: $resp" -ForegroundColor DarkGray
}

# (c) Wait 40 seconds and print "Take your lag screenshot now"
# WHY: 40 seconds provides ample time for Prometheus (3s scrape interval) and Grafana
# to render multiple data points of elevated lag across all partitions.
Write-Host "`n[STEP 3] Waiting $WaitSeconds seconds for Prometheus/Grafana to record accumulated lag..." -ForegroundColor Yellow
for ($s = $WaitSeconds; $s -ge 1; $s--) {
    Write-Progress -Activity "Accumulating Kafka Lag" -Status "$s seconds remaining..." -PercentComplete ((($WaitSeconds - $s) / $WaitSeconds) * 100)
    Start-Sleep -Seconds 1
}
Write-Progress -Activity "Accumulating Kafka Lag" -Completed

Write-Host ""
Write-Host "**********************************************************************" -ForegroundColor Green
Write-Host ">>> Take your lag screenshot now <<<" -ForegroundColor Green
Write-Host "Grafana Panel: 'Kafka Consumer Lag (Backend & ML)' on http://localhost:3000" -ForegroundColor Cyan
Write-Host "**********************************************************************" -ForegroundColor Green
Write-Host ""

# (d) Wait for operator to press Enter, scale back to 1, and print "Take your drain screenshot now"
# WHY: Interactive pause gives the operator total control over when they take the screenshot
# before initiating the drain phase.
$null = Read-Host "Press [Enter] when ready to scale ml-service back to 1 and observe queue drain"

Write-Host "`n[STEP 4] Scaling ml-service back to 1 replica to process queued messages..." -ForegroundColor Yellow
kubectl scale deployment/ml-service --replicas=1
kubectl rollout status deployment/ml-service --timeout=60s

Write-Host "`n[STEP 5] ml-service pod is running and actively consuming queued messages." -ForegroundColor Yellow
Write-Host "Watch the lag lines in Grafana descend back to 0." -ForegroundColor Gray
Write-Host ""
Write-Host "**********************************************************************" -ForegroundColor Green
Write-Host ">>> Take your drain screenshot now <<<" -ForegroundColor Green
Write-Host "**********************************************************************" -ForegroundColor Green
Write-Host ""
