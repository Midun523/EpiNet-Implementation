# tests/demo/restore_after_recording.ps1 — Teardown & environment restoration after recording session
# WHY: Restores ml-service to production configuration:
# 1. Resets DEMO_COMPUTE_DELAY_SECONDS=0 (removes artificial delay).
# 2. Re-applies k8s/hpa.yaml to re-enable Horizontal Pod Autoscaler.
# 3. Restores any temporary Windows Terminal profile changes.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# WHY: Resolve repository root directory to ensure paths work from any location.
$repoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repoRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "       EPINET DEMO ENVIRONMENT CLEANUP & RESTORATION" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# Step 1: Restore DEMO_COMPUTE_DELAY_SECONDS=0 on ml-service deployment
# WHY: Returns ml-service to pure compute latency for normal benchmarking and testing.
Write-Host "`n[1/3] Restoring ml-service DEMO_COMPUTE_DELAY_SECONDS=0..." -ForegroundColor Yellow
kubectl set env deployment/ml-service DEMO_COMPUTE_DELAY_SECONDS=0
kubectl rollout status deployment/ml-service --timeout=60s
Write-Host "      ml-service rollout complete (DEMO_COMPUTE_DELAY_SECONDS=0)." -ForegroundColor Green

# Step 2: Re-apply Horizontal Pod Autoscaler (HPA)
# WHY: Re-enables automated horizontal scaling (1-3 replicas based on 60% CPU utilization).
Write-Host "`n[2/3] Re-applying Horizontal Pod Autoscaler (k8s/hpa.yaml)..." -ForegroundColor Yellow
$hpaFile = Join-Path $repoRoot "k8s\hpa.yaml"
kubectl apply -f $hpaFile
Write-Host "      HPA successfully re-created:" -ForegroundColor Green
kubectl get hpa ml-service-hpa

# Step 3: Optional Windows Terminal profile cleanup
# WHY: Removes the temporary 'EpiNet Recording' profile from settings.json to leave terminal clean.
Write-Host "`n[3/3] Checking Windows Terminal profile cleanup..." -ForegroundColor Yellow
$settingsPath = "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
if (Test-Path $settingsPath) {
    try {
        $json = Get-Content $settingsPath -Raw | ConvertFrom-Json
        $before = $json.profiles.list.Count
        $json.profiles.list = @($json.profiles.list | Where-Object { $_.name -ne "EpiNet Recording" })
        if ($json.profiles.list.Count -lt $before) {
            $json | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
            Write-Host "      Cleaned up 'EpiNet Recording' profile from Windows Terminal." -ForegroundColor Green
        } else {
            Write-Host "      No demo profile found in Windows Terminal settings." -ForegroundColor Gray
        }
    } catch {
        Write-Warning "Could not modify Windows Terminal settings.json ($_); skipping."
    }
}

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "Environment successfully restored to normal state!" -ForegroundColor Green
Write-Host "Current Pods:" -ForegroundColor Gray
kubectl get pods
Write-Host "Current HPA:" -ForegroundColor Gray
kubectl get hpa
Write-Host "======================================================================" -ForegroundColor Cyan
