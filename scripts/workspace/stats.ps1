# Writes stats.txt in this folder: is the run alive, what is it doing, what is this
# machine doing. Open stats.txt in Notepad; you do not need a terminal to read it.
#
#   .\stats.ps1              write it once and print the verdict
#   .\stats.ps1 -Loop        keep it current in this window (Ctrl+C to stop)
#   .\stats.ps1 -Loop -Background   same, in its own minimized window
#   .\stats.ps1 -Interval 60        seconds between refreshes (default 20)
#   .\stats.ps1 -NoGpu       skip the nvidia-smi probe
#
# Read-only with respect to the run: it never writes into rk-work and never touches the
# container. Safe to run while the run is live, stopped, or missing.
param(
    [switch]$Loop,
    [switch]$Background,
    [switch]$NoGpu,
    [switch]$NoDocker,
    [int]$Interval = 20
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$harness = Join-Path $root "rk-harness"
$py = Join-Path $harness ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$env:RK_WORK_DIR = (Join-Path $root "rk-work") -replace '\\','/'
$env:RK_FINDINGS_DIR = (Join-Path $root "rk-findings") -replace '\\','/'
$env:RK_CONFIG = (Join-Path $root "config.json") -replace '\\','/'
$env:PYTHONPATH = $harness
$env:PYTHONWARNINGS = "ignore::UserWarning"
$env:PYTHONIOENCODING = "utf-8"

$out = Join-Path $root "stats.txt"
$argsList = @("-m", "rk_harness.status", "--out", $out)
if ($NoGpu) { $argsList += "--no-gpu" }
if ($NoDocker) { $argsList += "--no-docker" }

if ($Background) {
    # Relaunch this same script in its own window so the caller gets their prompt back.
    $self = $MyInvocation.MyCommand.Path
    $inner = "-NoExit -ExecutionPolicy Bypass -File `"$self`" -Loop -Interval $Interval"
    if ($NoGpu) { $inner += " -NoGpu" }
    if ($NoDocker) { $inner += " -NoDocker" }
    Start-Process powershell -ArgumentList $inner -WindowStyle Minimized
    Write-Host "stats writer started in its own minimized window (every $Interval s)"
    Write-Host "  file: $out"
    exit 0
}

if ($Loop) {
    $host.UI.RawUI.WindowTitle = "rk stats writer (read-only; Ctrl+C stops only this)"
    Write-Host "writing $out every $Interval s. Ctrl+C stops this writer, not the run."
    # The file declares how often it is refreshed so a reader can tell a stale file from
    # a fresh one without doing arithmetic.
    $argsList += @("--refresh", "$Interval")
    while ($true) {
        try { & $py @argsList } catch { Write-Host "stats write failed: $_" -ForegroundColor Yellow }
        Start-Sleep -Seconds $Interval
    }
}

& $py @argsList
exit $LASTEXITCODE
