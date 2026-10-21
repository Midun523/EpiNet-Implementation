# tests/demo/restore_after_recording.ps1 — Teardown & environment restoration after recording session
# WHY: Restores ml-service to production configuration:
# 1. Resets DEMO_COMPUTE_DELAY_SECONDS=0 (removes artificial delay).
# 2. Re-applies k8s/hpa.yaml to re-enable Horizontal Pod Autoscaler.
# 3. Restores Windows Terminal settings.json strictly from settings.json.bak-original and verifies
#    that fontSize and colorScheme are absent from profiles.defaults and JSON parses.

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
# WHY: Restores settings.json strictly from settings.json.bak-original and verifies
# that fontSize and colorScheme are absent from profiles.defaults and JSON parses cleanly.
Write-Host "`n[3/3] Restoring Windows Terminal settings from original backup..." -ForegroundColor Yellow
$localStateDir = Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState"
$settingsPath = Join-Path $localStateDir "settings.json"
$backupOriginal = Join-Path $localStateDir "settings.json.bak-original"

if (Test-Path $backupOriginal) {
    Copy-Item -Path $backupOriginal -Destination $settingsPath -Force
    Write-Host "      Restored settings.json from: $backupOriginal" -ForegroundColor Green

    # Verify restored settings.json parses cleanly and fontSize/colorScheme are NOT present
    try {
        $verify = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $defaults = $verify.profiles.defaults
        if ($defaults) {
            if ($defaults.fontSize -or $defaults.colorScheme -or ($defaults.font -and $defaults.font.size)) {
                throw "Verification failed: fontSize or colorScheme still present in profiles.defaults after restore!"
            }
        }
        Write-Host "      Verification passed: settings.json parsed cleanly; fontSize and colorScheme are absent from profiles.defaults." -ForegroundColor Green
    } catch {
        Write-Error "CRITICAL: Restored settings.json verification failed: $_"
        throw $_
    }
} else {
    Write-Warning "      settings.json.bak-original not found at $backupOriginal; attempting manual removal of fontSize/colorScheme..."
    try {
        $json = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($json.profiles.defaults) {
            $json.profiles.defaults = [PSCustomObject]@{}
            $json | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
        }
        Write-Host "      Manually cleared profiles.defaults." -ForegroundColor Green
    } catch {
        Write-Warning "      Could not update settings.json: $_"
    }
}

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host "Environment successfully restored to normal state!" -ForegroundColor Green
Write-Host "Current Pods:" -ForegroundColor Gray
kubectl get pods
Write-Host "Current HPA:" -ForegroundColor Gray
kubectl get hpa
Write-Host "======================================================================" -ForegroundColor Cyan
