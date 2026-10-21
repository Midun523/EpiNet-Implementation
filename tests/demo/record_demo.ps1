# tests/demo/record_demo.ps1 — Orchestrates screen-recording session for EpiNet pod failure demo
# WHY: Prepares terminal environment, provides 10-second countdown for the operator to trigger
# screen recording (Win+Alt+R), and splits Windows Terminal into side-by-side panes formatted
# with large fonts and a dark theme for mobile phone readability.
#
# MODES:
#   -SameWindow (Default): Runs within the current focused Windows Terminal window by splitting
#                          the current pane, ensuring Win+Alt+R (Game Bar) recording does not lose focus.
#   -NewWindow           : Launches a brand new Windows Terminal window with two split panes.

[CmdletBinding(DefaultParameterSetName = "SameWindow")]
param(
    [Parameter(ParameterSetName = "SameWindow")]
    [switch]$SameWindow = $true,

    [Parameter(ParameterSetName = "NewWindow")]
    [switch]$NewWindow
)

$ErrorActionPreference = "Stop"

if ($NewWindow) {
    $SameWindow = $false
}

# WHY: Resolve repository root directory to ensure paths work regardless of current working directory.
$repoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repoRoot

$modeDesc = if ($SameWindow) { "SameWindow (Default - splits focused WT window)" } else { "NewWindow (Launches fresh WT window)" }
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "       EPINET RESILIENCE DEMO -- SCREEN RECORDING SESSION" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Repository Root : $repoRoot" -ForegroundColor Gray
Write-Host "Window Mode     : $modeDesc" -ForegroundColor Gray

# Step 1: Safe Profile Backup and Injection
# WHY: Keep ONE original backup (settings.json.bak-original) created only if it does not exist
# and only if profiles.defaults has no fontSize/colorScheme. Never back up an already-modified file.
$localStateDir = Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState"
$settingsPath = Join-Path $localStateDir "settings.json"
$backupOriginal = Join-Path $localStateDir "settings.json.bak-original"

if (Test-Path $settingsPath) {
    $raw = Get-Content $settingsPath -Raw -Encoding UTF8
    $json = $raw | ConvertFrom-Json
    $defaults = $json.profiles.defaults

    $hasModifications = $false
    if ($defaults) {
        if ($defaults.fontSize -or $defaults.colorScheme -or ($defaults.font -and $defaults.font.size)) {
            $hasModifications = $true
        }
    }

    # 1. Back up settings.json ONLY if bak-original does not exist and settings are not already modified
    if (-not (Test-Path $backupOriginal)) {
        if (-not $hasModifications) {
            Copy-Item -Path $settingsPath -Destination $backupOriginal -Force
            Write-Host ""
            Write-Host "[SAFETY] Created original backup at: $backupOriginal" -ForegroundColor Green
        } else {
            Write-Warning "[SAFETY] settings.json already has fontSize/colorScheme in profiles.defaults; refusing to overwrite or create bak-original from modified file!"
        }
    } else {
        Write-Host ""
        Write-Host "[SAFETY] Preserving existing original backup: $backupOriginal" -ForegroundColor Gray
    }

    # 2. Inject large font (size 18) and dark theme ('One Half Dark') into profiles.defaults
    if (-not $json.profiles.defaults) {
        $json.profiles | Add-Member -MemberType NoteProperty -Name 'defaults' -Value ([PSCustomObject]@{}) -Force
    }
    $json.profiles.defaults = [PSCustomObject]@{
        fontSize = 18
        colorScheme = "One Half Dark"
        font = [PSCustomObject]@{ size = 18 }
    }
    $json | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

    # 3. Verify that modified settings.json parses cleanly
    try {
        $verify = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($verify.profiles.defaults.fontSize -ne 18 -or $verify.profiles.defaults.colorScheme -ne "One Half Dark") {
            throw "Verification failed: fontSize or colorScheme not set in profiles.defaults."
        }
        $count = $verify.profiles.list.Count
        Write-Host "[SAFETY] Verified settings.json parsed successfully ($count profiles loaded, fontSize=18, theme='One Half Dark')." -ForegroundColor Green
    } catch {
        Write-Error "CRITICAL: settings.json verification failed! Restoring original backup..."
        if (Test-Path $backupOriginal) {
            Copy-Item -Path $backupOriginal -Destination $settingsPath -Force
        }
        throw $_
    }
} else {
    Write-Warning "Windows Terminal settings.json not found at $settingsPath; proceeding with defaults."
}

# Clean prior logs if present
$leftLog = Join-Path $repoRoot "tests\demo\left_pane.log"
$rightLog = Join-Path $repoRoot "tests\demo\right_pane.log"
if (Test-Path $leftLog) { Clear-Content $leftLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $rightLog) { Clear-Content $rightLog -Force -ErrorAction SilentlyContinue }

$leftScript = Join-Path $repoRoot "tests\demo\left_pane.ps1"
$rightScript = Join-Path $repoRoot "tests\demo\right_pane.ps1"

# Step 2: Prompt and 10-second countdown
# WHY: Gives operator time to press Win+Alt+R on the focused window.
Write-Host ""
Write-Host "**********************************************************************" -ForegroundColor Yellow -BackgroundColor Black
Write-Host "Press Win+Alt+R to start recording. Demo starts in 10 seconds." -ForegroundColor Yellow -BackgroundColor Black
Write-Host "**********************************************************************" -ForegroundColor Yellow -BackgroundColor Black
Write-Host ""

for ($i = 10; $i -ge 1; $i--) {
    Write-Host "  Starting in $i second(s)..." -ForegroundColor Yellow
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "Starting demo panes..." -ForegroundColor Green

# Step 3: Launch panes according to selected mode
$wtProcess = Get-Process WindowsTerminal -ErrorAction SilentlyContinue

if ($SameWindow -and $wtProcess) {
    # WHY: SameWindow mode splits the existing focused Windows Terminal window (-w 0),
    # so Game Bar recording (which captures the focused window) remains active and recording uninterrupted.
    Write-Host "Mode: SameWindow -- Splitting current focused window..." -ForegroundColor Cyan
    
    # Spawn the right pane vertically in the current window
    Start-Process wt.exe -ArgumentList @(
        "-w", "0",
        "split-pane", "-V",
        "-d", "$repoRoot",
        "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $rightScript
    )
    
    # Run the left pane watch command in the current pane
    & $leftScript
} else {
    # Fallback or explicit -NewWindow mode:
    # WHY: Launches new WT window with two split panes side-by-side.
    Write-Host "Mode: NewWindow -- Launching new Windows Terminal window with side-by-side panes..." -ForegroundColor Cyan
    $wtArgs = @(
        "-w", "new",
        "-d", "$repoRoot",
        "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $leftScript,
        ";",
        "split-pane", "-V",
        "-d", "$repoRoot",
        "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $rightScript
    )
    Start-Process wt.exe -ArgumentList $wtArgs
    
    Write-Host ""
    Write-Host "Windows Terminal session opened with side-by-side split panes." -ForegroundColor Cyan
    Write-Host "  - LEFT Pane : kubectl get pods -w" -ForegroundColor Gray
    Write-Host "  - RIGHT Pane: python tests/demo/failure_demo.py" -ForegroundColor Gray
    Write-Host ""
    Write-Host "When the demo finishes in the right pane, wait 5s and press Win+Alt+R to stop recording." -ForegroundColor Yellow
}
