# tests/demo/record_demo.ps1 — Orchestrates screen-recording session for EpiNet pod failure demo
# WHY: Prepares terminal environment, provides 10-second countdown for the operator to trigger
# screen recording (Win+Alt+R), and opens a single Windows Terminal window with side-by-side
# split panes formatted with large fonts and a dark theme for mobile phone readability.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# WHY: Resolve repository root directory to ensure paths work regardless of current working directory.
$repoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repoRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "       EPINET RESILIENCE DEMO — SCREEN RECORDING SESSION" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Repository Root: $repoRoot" -ForegroundColor Gray

# Step 1: Ensure Windows Terminal profile exists with large font and dark theme
# WHY: Font size 18 and high-contrast dark theme ('One Half Dark') ensure text remains
# crisp and easily readable when screen recording is viewed on a mobile device or phone.
$settingsPath = "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
if (Test-Path $settingsPath) {
    try {
        $json = Get-Content $settingsPath -Raw | ConvertFrom-Json
        $profile = $json.profiles.list | Where-Object { $_.name -eq "EpiNet Recording" }
        if (-not $profile) {
            Write-Host "[SETUP] Registering 'EpiNet Recording' high-visibility profile in Windows Terminal..." -ForegroundColor Yellow
            $newProfile = [PSCustomObject]@{
                guid = "{c4a17932-520e-473f-8c3b-74bf9c05e192}"
                name = "EpiNet Recording"
                commandline = "powershell.exe"
                colorScheme = "One Half Dark"
                font = [PSCustomObject]@{
                    size = 18
                }
                fontSize = 18
                hidden = $false
            }
            $json.profiles.list += $newProfile
            $json | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
        }
    } catch {
        Write-Warning "Could not update Windows Terminal settings.json ($_); using default profile."
    }
}

# Step 2: Prompt and 10-second countdown
# WHY: Gives the operator ample time to switch focus or initiate Windows Game Bar recording (Win+Alt+R).
Write-Host ""
Write-Host "Press Win+Alt+R to start recording. Demo starts in 10 seconds." -ForegroundColor Yellow -BackgroundColor Black
Write-Host ""

for ($i = 10; $i -ge 1; $i--) {
    Write-Host "  Starting in $i second(s)..." -ForegroundColor Yellow
    Start-Sleep -Seconds 1
}

Write-Host "`n>>> Launching Windows Terminal side-by-side demo panes... <<<" -ForegroundColor Green

# Step 3: Launch Windows Terminal window with side-by-side split panes
# WHY: LEFT pane watches pods continuously (`kubectl get pods -w`).
# WHY: RIGHT pane executes `failure_demo.py` which uploads the job, tracks timestamps, kills pod mid-computation,
# and verifies self-healing. Both outputs are simultaneously written to disk logs via Tee-Object.
$leftLog = "$repoRoot\tests\demo\left_pane.log"
$rightLog = "$repoRoot\tests\demo\right_pane.log"

# Clean prior logs if present
if (Test-Path $leftLog) { Remove-Item -Force $leftLog }
if (Test-Path $rightLog) { Remove-Item -Force $rightLog }

$leftCmd = "Write-Host '==================== LEFT: K8S POD WATCH ====================' -ForegroundColor Cyan; kubectl get pods -w | Tee-Object -FilePath `"$leftLog`""
$rightCmd = "Write-Host '=================== RIGHT: FAILURE DEMO ====================' -ForegroundColor Green; python -u tests/demo/failure_demo.py | Tee-Object -FilePath `"$rightLog`""

# WHY: wt.exe -w new starts a fresh window. Semicolon backtick-escaped opens vertical split (-V).
wt.exe -w new -p "EpiNet Recording" -d "$repoRoot" powershell.exe -NoExit -Command $leftCmd `; split-pane -V -p "EpiNet Recording" -d "$repoRoot" powershell.exe -NoExit -Command $rightCmd

Write-Host ""
Write-Host "Windows Terminal session opened." -ForegroundColor Cyan
Write-Host "  - LEFT Pane : kubectl get pods -w" -ForegroundColor Gray
Write-Host "  - RIGHT Pane: python tests/demo/failure_demo.py" -ForegroundColor Gray
Write-Host ""
Write-Host "When the demo finishes in the right pane, wait 5s and press Win+Alt+R to stop recording." -ForegroundColor Yellow
