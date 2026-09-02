# Host-side kill switch (HANDOFF section 13.4) and pause watchdog (section 13.5). Hand-written (section 16.1).
# Polls every 10 s. All five triggers live here, outside the container.
param(
    [string]$Work = "D:/rk/work",
    [string]$Container = "rk",
    [string]$EnvFile = "D:/rk/.env",
    [int]$PollSeconds = 10,
    [double]$MinFreeGB = 5.0,
    [int]$HeartbeatStaleSeconds = 120,
    [int]$NoCandidateMinutes = 30,
    [string]$Findings = "D:/rk/findings",
    [int]$PushMinutes = 10,
    [int]$CpuHigh = 50,             # pause when non-container host CPU stays above this ...
    [int]$CpuLow = 30,              # ... unpause when it stays below this ...
    [int]$CpuSustainSeconds = 30,   # ... for this long
    [int]$SaturationCheckSeconds = 1800,  # epoch-saturation orchestrator cadence (0 = off)
    [switch]$NoSaturation,
    [switch]$NoBatteryGuard,
    [switch]$Once
)
$ErrorActionPreference = "Continue"

# Epoch saturation orchestrator (owner-delegated, 2026-09-02; rule in docs/ROADMAP.md).
# The decision state lives on disk in rk-work (python side), never in this process.
$HarnessRoot = Split-Path $PSScriptRoot -Parent
$VenvPython = Join-Path $HarnessRoot ".venv/Scripts/python.exe"
$lastSatCheck = [datetime]::MinValue
$freezePending = $false

# Battery guard (owner's rule, 2026-08-29): never run on battery. On battery -> docker pause;
# back on AC -> docker unpause. Pause is atomic, so the run resumes exactly where it was.
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
function On-Battery {
    try { return ([System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -eq "Offline") } catch { return $false }
}
$pausedBattery = $false

# Host-side push (HANDOFF section 2.2 intent): the container only commits into the mounted
# /work and /findings; the host pushes them with the owner's own git credentials, so no GitHub
# token ever enters the container.
function Push-Repo([string]$path) {
    if (-not (Test-Path (Join-Path $path ".git"))) { return }
    $ahead = cmd /c "git -C `"$path`" rev-list --count @{u}..HEAD 2>&1"
    if ($ahead -match '^\d+$' -and [int]$ahead -gt 0) {
        $out = cmd /c "git -C `"$path`" push -q origin HEAD 2>&1"
        if ($LASTEXITCODE -eq 0) { Write-Host "$(Get-Date -Format s) pushed $ahead commit(s) from $path" }
        else { Write-Host "$(Get-Date -Format s) push FAILED for ${path}: $out" }
    }
}
$lastPush = Get-Date

function Read-Cap {
    $cap = 50.0
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content $EnvFile) {
            if ($line -match '^\s*OPENAI_MONTHLY_CAP_USD\s*=\s*([0-9.]+)') { $cap = [double]$Matches[1] }
        }
    }
    return $cap
}

function Get-Spend {
    $p = Join-Path $Work "RUNSTATE.json"
    if (-not (Test-Path $p)) { return 0.0 }
    try { return [double]((Get-Content $p -Raw | ConvertFrom-Json).spend_usd) } catch { return 0.0 }
}

function Get-LastVerifiedAge {
    # minutes since the last "record" event in events.jsonl; $null if no events file
    $p = Join-Path $Work "events.jsonl"
    if (-not (Test-Path $p)) { return $null }
    $last = $null
    foreach ($line in (Get-Content $p -Tail 400)) {
        try {
            $o = $line | ConvertFrom-Json
            if ($o.kind -eq "accepted") { $last = [datetime]::Parse($o.ts).ToUniversalTime() }
        } catch {}
    }
    if ($null -eq $last) { return $null }
    return ((Get-Date).ToUniversalTime() - $last).TotalMinutes
}

function Container-Running {
    $s = docker inspect -f "{{.State.Status}}" $Container 2>$null
    return ($s -eq "running" -or $s -eq "paused")
}

function Container-UptimeSeconds {
    try {
        $st = docker inspect -f "{{.State.StartedAt}}" $Container 2>$null
        if (-not $st) { return 0 }
        return [int]((Get-Date).ToUniversalTime() - [datetime]::Parse($st).ToUniversalTime()).TotalSeconds
    } catch { return 0 }
}

$cap = Read-Cap
$highSince = $null
$lowSince = $null
$alerted = $false
Write-Host "watchdog: container=$Container work=$Work cap=$cap USD poll=${PollSeconds}s heartbeat-stale=${HeartbeatStaleSeconds}s min-free=${MinFreeGB}GB push=${PushMinutes}min cpu-pause=${CpuHigh}/${CpuLow}% for ${CpuSustainSeconds}s battery-guard=$(-not $NoBatteryGuard)"

while ($true) {
    if (-not $Once) { Start-Sleep -Seconds $PollSeconds }
    # 0. Host-side push of completed commits every $PushMinutes (or on every -Once pass).
    if ($Once -or ((Get-Date) - $lastPush).TotalMinutes -ge $PushMinutes) {
        Push-Repo $Work
        Push-Repo $Findings
        $lastPush = Get-Date
    }

    # 0c. Epoch saturation: ask rk_harness.saturation every $SaturationCheckSeconds whether
    # the epoch has stopped producing; on the freeze threshold, drop STOP for a graceful
    # stop, then mark the epoch frozen and push once the container has exited.
    if (-not $NoSaturation -and $SaturationCheckSeconds -gt 0 -and ((Get-Date) - $lastSatCheck).TotalSeconds -ge $SaturationCheckSeconds -and (Test-Path $VenvPython)) {
        $lastSatCheck = Get-Date
        $env:RK_WORK_DIR = $Work
        $env:PYTHONPATH = $HarnessRoot
        $satRaw = & $VenvPython -m rk_harness.saturation --check 2>$null
        $sat = $null
        try { $sat = $satRaw | ConvertFrom-Json } catch {}
        if ($sat -and $sat.action -eq "freeze") {
            Write-Host "$(Get-Date -Format s) SATURATION freeze threshold met ($($sat.consecutive)/$($sat.consecutive_needed) checks, last progress $($sat.hours_since_progress)h ago); dropping STOP for a graceful epoch freeze"
            Set-Content -Path (Join-Path $Work "STOP") -Value "stop" -Encoding ascii
            $freezePending = $true
        } elseif ($sat -and $sat.verdict -ne "FROZEN") {
            Write-Host "$(Get-Date -Format s) saturation: $($sat.verdict) (progress $($sat.hours_since_progress)h ago, checks $($sat.consecutive)/$($sat.consecutive_needed))"
        }
    }
    if ($freezePending) {
        $stF = docker inspect -f "{{.State.Status}}" $Container 2>$null
        if ($stF -ne "running" -and $stF -ne "paused") {
            $env:RK_WORK_DIR = $Work
            $env:PYTHONPATH = $HarnessRoot
            & $VenvPython -m rk_harness.saturation --mark-frozen "no archive progress past the window and falsification concluded" | Out-Null
            Push-Repo $Work
            Push-Repo $Findings
            Write-Host "$(Get-Date -Format s) EPOCH 1 FROZEN: run stopped cleanly, EPOCH_STATUS.json written and pushed"
            $freezePending = $false
        }
    }

    # Pause state is derived from docker every poll, never trusted from this process's own
    # variables: start.ps1 restarts the watchdog, and a fresh instance must adopt a paused
    # container instead of killing it or leaving it frozen.
    $status = (docker inspect -f "{{.State.Status}}" $Container 2>$null)
    if ($status -ne "running" -and $status -ne "paused") { Write-Host "$(Get-Date -Format s) container not running ($status); watchdog idle"; if ($Once) { break }; continue }
    $isPaused = ($status -eq "paused")

    # 0b. Battery: pause while unplugged, resume on AC. Takes precedence over the CPU pause logic.
    if (-not $NoBatteryGuard) {
        if (On-Battery) {
            if (-not $isPaused) {
                Write-Host "$(Get-Date -Format s) on battery -> docker pause"
                docker pause $Container | Out-Null
            }
            $pausedBattery = $true
            if ($Once) { break }; continue
        } elseif ($pausedBattery) {
            Write-Host "$(Get-Date -Format s) back on AC -> docker unpause"
            if ($isPaused) { docker unpause $Container | Out-Null; $isPaused = $false }
            $pausedBattery = $false
        }
    }

    # 1. Killfile: graceful stop at the cycle boundary (the runner polls STOP itself).
    if (Test-Path (Join-Path $Work "STOP")) {
        Write-Host "$(Get-Date -Format s) STOP present; runner will stop at cycle boundary"
    }

    # 2. Heartbeat stale > 120 s -> docker kill.
    $hb = Join-Path $Work "HEARTBEAT"
    if (Test-Path $hb) {
        try {
            $ts = [datetime]::Parse((Get-Content $hb -Raw).Trim()).ToUniversalTime()
            $age = ((Get-Date).ToUniversalTime() - $ts).TotalSeconds
            # Startup grace: never kill a container younger than the staleness threshold - the
            # entrypoint gate runs before the runner's heartbeat thread exists.
            if ($age -gt $HeartbeatStaleSeconds -and -not $isPaused -and (Container-UptimeSeconds) -gt $HeartbeatStaleSeconds) {
                Write-Host "$(Get-Date -Format s) heartbeat stale ${age}s -> docker kill"
                docker kill $Container | Out-Null
                if ($Once) { break }; continue
            }
        } catch { Write-Host "heartbeat unreadable" }
    }

    # 3. No verified candidate in 30 min -> log alert (escalation is the encourager's job).
    $ageMin = Get-LastVerifiedAge
    if ($null -ne $ageMin -and $ageMin -gt $NoCandidateMinutes) {
        if (-not $alerted) { Write-Host "$(Get-Date -Format s) ALERT: no verified candidate for $([int]$ageMin) min"; $alerted = $true }
    } else { $alerted = $false }

    # 4. Spend over cap -> hard stop.
    $spend = Get-Spend
    if ($spend -gt $cap) {
        Write-Host "$(Get-Date -Format s) spend $spend USD > cap $cap -> docker stop"
        docker stop $Container | Out-Null
        if ($Once) { break }; continue
    }

    # 5. Disk free on the work volume < 5 GB -> hard stop.
    $drive = (Get-Item $Work).PSDrive
    $freeGB = [double]$drive.Free / 1GB
    if ($freeGB -lt $MinFreeGB) {
        Write-Host "$(Get-Date -Format s) disk free ${freeGB} GB < $MinFreeGB -> docker stop"
        docker stop $Container | Out-Null
        if ($Once) { break }; continue
    }

    # Pause watchdog (section 13.5): non-container CPU > 50% for 30 s -> pause; < 30% for 30 s -> unpause.
    $total = [double](Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue
    $ctr = 0.0
    try {
        $stats = docker stats --no-stream --format "{{.CPUPerc}}" $Container 2>$null
        if ($stats) { $ctr = [double]($stats.Trim('%')) / [Environment]::ProcessorCount }
    } catch {}
    $host_cpu = [math]::Max(0.0, $total - $ctr)
    $now = Get-Date
    if ($host_cpu -gt $CpuHigh) { if ($null -eq $highSince) { $highSince = $now }; $lowSince = $null }
    elseif ($host_cpu -lt $CpuLow) { if ($null -eq $lowSince) { $lowSince = $now }; $highSince = $null }
    else { $highSince = $null; $lowSince = $null }

    if (-not $isPaused -and $null -ne $highSince -and ($now - $highSince).TotalSeconds -ge $CpuSustainSeconds) {
        Write-Host "$(Get-Date -Format s) host CPU ${host_cpu}% for ${CpuSustainSeconds}s -> docker pause"
        docker pause $Container | Out-Null; $highSince = $null
    } elseif ($isPaused -and $null -ne $lowSince -and ($now - $lowSince).TotalSeconds -ge $CpuSustainSeconds) {
        Write-Host "$(Get-Date -Format s) host CPU ${host_cpu}% for ${CpuSustainSeconds}s -> docker unpause"
        docker unpause $Container | Out-Null; $lowSince = $null
    }
    if ($Once) { break }
}
