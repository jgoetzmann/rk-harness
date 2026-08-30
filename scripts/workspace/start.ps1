# One-command start for the rk run: container + host watchdog.
#   .\start.ps1            start (image must already be built)
#   .\start.ps1 -Build     rebuild the image first (needed after any change under rk-harness)
param([switch]$Build)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$harness  = Join-Path $root "rk-harness"
$work     = Join-Path $root "rk-work"
$findings = Join-Path $root "rk-findings"
$envFile  = Join-Path $harness ".env"

foreach ($p in @($harness, $work, $findings)) { if (-not (Test-Path $p)) { throw "missing: $p" } }
if (-not (Test-Path $envFile)) { throw "missing $envFile (copy rk-harness\.env.example and fill it in)" }
if (Test-Path (Join-Path $work "STOP")) { Remove-Item (Join-Path $work "STOP"); Write-Host "removed stale STOP file" }

# Docker Desktop must be up.
$null = docker info 2>$null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running - start it and retry" }

$runArgs = @{
    Harness  = ($harness -replace '\\','/')
    Work     = ($work -replace '\\','/')
    Findings = ($findings -replace '\\','/')
    EnvFile  = ($envFile -replace '\\','/')
}
if ($Build) { $runArgs.Build = $true }
& (Join-Path $harness "scripts\run.ps1") @runArgs
if ($LASTEXITCODE -ne 0) { throw "run.ps1 failed" }

# Watchdog in its own minimized window (kill switch, pause on load / on battery, push every 10 min).
$existing = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*watchdog.ps1*" -and $_.CommandLine -notlike "*-Once*" }
if ($existing) {
    Write-Host "watchdog already running (pid $($existing[0].ProcessId))"
} else {
    $wd = Join-Path $harness "scripts\watchdog.ps1"
    $argLine = "-NoExit -ExecutionPolicy Bypass -File `"$wd`" -Work `"$($work -replace '\\','/')`" -Findings `"$($findings -replace '\\','/')`" -EnvFile `"$($envFile -replace '\\','/')`""
    Start-Process powershell -ArgumentList $argLine -WindowStyle Minimized
    Write-Host "watchdog started (minimized window)"
}
Write-Host ""
Write-Host "Running. Watch:  docker logs -f rk     events: rk-work\events.jsonl     site: https://jgoetzmann.github.io/rk-findings/"
Write-Host "Stop:    .\stop.ps1   (graceful, at the next cycle boundary)   or   .\stop.ps1 -Force"
