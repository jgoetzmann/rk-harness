# Stop the rk run.
#   .\stop.ps1          graceful: drop the STOP killfile, wait for the cycle boundary, stop the watchdog
#   .\stop.ps1 -Force   docker stop now (at most one cycle is lost; state replays on restart)
param([switch]$Force, [int]$WaitMinutes = 20)
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$work = Join-Path $root "rk-work"

$running = (docker inspect -f "{{.State.Status}}" rk 2>$null)
if ($running -eq "paused") { docker unpause rk | Out-Null; $running = "running" }
if ($running -eq "running") {
    if ($Force) {
        docker stop rk | Out-Null
        Write-Host "container stopped (forced)"
    } else {
        Set-Content -Path (Join-Path $work "STOP") -Value "stop" -Encoding ascii
        Write-Host "STOP written; waiting for the runner to finish its cycle (up to $WaitMinutes min)..."
        $deadline = (Get-Date).AddMinutes($WaitMinutes)
        while ((Get-Date) -lt $deadline -and (docker inspect -f "{{.State.Status}}" rk 2>$null) -eq "running") { Start-Sleep 10 }
        if ((docker inspect -f "{{.State.Status}}" rk 2>$null) -eq "running") {
            Write-Host "still running after $WaitMinutes min; forcing"; docker stop rk | Out-Null
        }
        Remove-Item (Join-Path $work "STOP") -ErrorAction SilentlyContinue
        Write-Host "container stopped at a cycle boundary"
    }
} else {
    Write-Host "container is not running ($running)"
}

$wd = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*watchdog.ps1*" -and $_.CommandLine -notlike "*-Once*" }
foreach ($p in $wd) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "watchdog stopped (pid $($p.ProcessId))" }

# Push anything the container committed but the watchdog had not pushed yet.
foreach ($r in @("rk-work", "rk-findings")) {
    $path = Join-Path $root $r
    $ahead = cmd /c "git -C `"$path`" rev-list --count @{u}..HEAD 2>&1"
    if ($ahead -match '^\d+$' -and [int]$ahead -gt 0) { cmd /c "git -C `"$path`" push -q origin HEAD 2>&1" | Out-Null; Write-Host "pushed $ahead commit(s) from $r" }
}
