# Host-side kill switch (HANDOFF §13.4) and pause watchdog (§13.5). Hand-written (§16.1).
# Polls every 10 s. All five triggers live here, outside the container.
param(
    [string]$Work = "D:/rk/work",
    [string]$Container = "rk",
    [string]$EnvFile = "D:/rk/.env",
    [int]$PollSeconds = 10,
    [double]$MinFreeGB = 5.0,
    [int]$HeartbeatStaleSeconds = 120,
    [int]$NoCandidateMinutes = 30,
    [switch]$Once
)
$ErrorActionPreference = "Continue"

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

$cap = Read-Cap
$highSince = $null
$lowSince = $null
$paused = $false
$alerted = $false
Write-Host "watchdog: container=$Container work=$Work cap=$cap USD poll=${PollSeconds}s"

while ($true) {
    if (-not $Once) { Start-Sleep -Seconds $PollSeconds }
    if (-not (Container-Running)) { Write-Host "$(Get-Date -Format s) container not running; watchdog idle"; if ($Once) { break }; continue }

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
            if ($age -gt $HeartbeatStaleSeconds -and -not $paused) {
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

    # Pause watchdog (§13.5): non-container CPU > 50% for 30 s -> pause; < 30% for 30 s -> unpause.
    $total = [double](Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue
    $ctr = 0.0
    try {
        $stats = docker stats --no-stream --format "{{.CPUPerc}}" $Container 2>$null
        if ($stats) { $ctr = [double]($stats.Trim('%')) / [Environment]::ProcessorCount }
    } catch {}
    $host_cpu = [math]::Max(0.0, $total - $ctr)
    $now = Get-Date
    if ($host_cpu -gt 50) { if ($null -eq $highSince) { $highSince = $now }; $lowSince = $null }
    elseif ($host_cpu -lt 30) { if ($null -eq $lowSince) { $lowSince = $now }; $highSince = $null }
    else { $highSince = $null; $lowSince = $null }

    if (-not $paused -and $null -ne $highSince -and ($now - $highSince).TotalSeconds -ge 30) {
        Write-Host "$(Get-Date -Format s) host CPU ${host_cpu}% for 30s -> docker pause"
        docker pause $Container | Out-Null; $paused = $true; $highSince = $null
    } elseif ($paused -and $null -ne $lowSince -and ($now - $lowSince).TotalSeconds -ge 30) {
        Write-Host "$(Get-Date -Format s) host CPU ${host_cpu}% for 30s -> docker unpause"
        docker unpause $Container | Out-Null; $paused = $false; $lowSince = $null
    }
    if ($Once) { break }
}
