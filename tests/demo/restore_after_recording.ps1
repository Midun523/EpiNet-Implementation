# tests/demo/restore_after_recording.ps1 — Teardown & environment restoration after recording session
# WHY: Restores ml-service to production configuration:
# 1. Resets DEMO_COMPUTE_DELAY_SECONDS=0 (removes artificial delay).
# 2. Re-applies k8s/hpa.yaml to re-enable Horizontal Pod Autoscaler.
# 3. Restores Windows Terminal settings.json from the latest timestamped backup and verifies parsing.

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

# Step 3: Safe Restoration of Windows Terminal settings.json
# WHY: Requirement 2: Restores settings.json from the backup created by record_demo.ps1
# and verifies the JSON parses cleanly.
Write-Host "`n[3/3] Restoring Windows Terminal settings from backup..." -ForegroundColor Yellow
$localStateDir = Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState"
$settingsPath = Join-Path $localStateDir "settings.json"

if (Test-Path $localStateDir) {
    $backups = Get-ChildItem -Path $localStateDir -Filter "settings.json.bak-*" | Sort-Object LastWriteTime -Descending
    if ($backups -and $backups.Count -gt 0) {
        $latestBackup = $backups[0].FullName
        Write-Host "      Restoring from latest backup: $latestBackup" -ForegroundColor Gray
        Copy-Item -Path $latestBackup -Destination $settingsPath -Force
        
        # Verify restored JSON parses cleanly
        try {
            $verify = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Host "      Verification passed: settings.json restored and parsed cleanly ($($verify.profiles.list.Count) profiles)." -ForegroundColor Green
        } catch {
            Write-Error "CRITICAL: Restored settings.json failed to parse: $_"
            throw $_
        }
    } else {
        # Fallback: remove 'EpiNet Recording' profile manually if no backup file is present
        Write-Host "      No backup file found; falling back to profile removal..." -ForegroundColor Gray
        try {
            $json = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $json.profiles.list = @($json.profiles.list | Where-Object { $_.name -ne "EpiNet Recording" })
            $json | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
            Write-Host "      Cleaned up 'EpiNet Recording' profile from settings.json." -ForegroundColor Green
        } catch {
            Write-Warning "Could not update settings.json: $_"
        }
    }
}

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "Environment successfully restored to normal state!" -ForegroundColor Green
Write-Host "Current Pods:" -ForegroundColor Gray
kubectl get pods
Write-Host "Current HPA:" -ForegroundColor Gray
kubectl get hpa
Write-Host "======================================================================" -ForegroundColor Cyan
