# One-command start for the rk run: container + host watchdog, configured by config.json.
#   .\start.ps1            start (image must already be built)
#   .\start.ps1 -Build     rebuild the image first (needed after any change under rk-harness)
# Edit settings with:  python configure.py explain | show | set key=value
param([switch]$Build)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$harness  = Join-Path $root "rk-harness"
$work     = Join-Path $root "rk-work"
$findings = Join-Path $root "rk-findings"
$envFile  = Join-Path $harness ".env"
$cfgFile  = Join-Path $root "config.json"

foreach ($p in @($harness, $work, $findings)) { if (-not (Test-Path $p)) { throw "missing: $p" } }
if (-not (Test-Path $envFile)) { throw "missing $envFile (copy rk-harness\.env.example and fill it in)" }
if (Test-Path (Join-Path $work "STOP")) { Remove-Item (Join-Path $work "STOP"); Write-Host "removed stale STOP file" }

# Settings (config.json; defaults are the handoff values).
$cfg = @{ container = @{}; run = @{}; watchdog = @{}; watcher = @{}; stats = @{} }
if (Test-Path $cfgFile) {
    $json = Get-Content $cfgFile -Raw | ConvertFrom-Json
    foreach ($sec in @("container", "run", "watchdog", "watcher", "stats")) {
        if ($json.PSObject.Properties[$sec]) {
            foreach ($prop in $json.$sec.PSObject.Properties) { $cfg[$sec][$prop.Name] = $prop.Value }
        }
    }
}
function Cfg($sec, $name, $default) { if ($cfg[$sec].ContainsKey($name) -and $null -ne $cfg[$sec][$name]) { return $cfg[$sec][$name] } else { return $default } }

$null = docker info 2>$null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running - start it and retry" }

$runArgs = @{
    Harness   = ($harness -replace '\\','/')
    Work      = ($work -replace '\\','/')
    Findings  = ($findings -replace '\\','/')
    EnvFile   = ($envFile -replace '\\','/')
    Llm       = [string](Cfg "run" "llm" "auto")
    Model     = [string](Cfg "run" "llm_model" "")
    Cpus      = [double](Cfg "container" "cpus" 4)
    MemoryGB  = [double](Cfg "container" "memory_gb" 6)
    PidsLimit = [int](Cfg "container" "pids_limit" 512)
    CpuShares = [int](Cfg "container" "cpu_shares" 256)
    ScratchGB = [double](Cfg "container" "scratch_tmpfs_gb" 2)
    EvalBudget   = [int](Cfg "run" "eval_budget" 200)
    LlmEveryCycles = [int](Cfg "run" "llm_every_cycles" 5)
    CodexUsageCap  = [int](Cfg "run" "codex_usage_cap_percent" 80)
    LitEvery       = [int](Cfg "run" "litreview_every_cycles" 50)
    InterpretEvery = [int](Cfg "run" "interpret_every_cycles" 25)
    SidetrackEvery = [int](Cfg "run" "sidetrack_every_cycles" 0)
    SidetrackMaxSeconds = [int](Cfg "run" "sidetrack_max_seconds" 180)
    SidetrackTracks = [string](Cfg "run" "sidetrack_tracks" "both")
    EnumPerCycle = [int](Cfg "run" "enum_per_cycle" 500)
    SearchPolicy = [string](Cfg "run" "search_policy" "empty")
    PolicyBlockCycles = [int](Cfg "run" "policy_block_cycles" 100)
    MaxMinutes   = [int](Cfg "run" "auto_stop_minutes" 0)
    MaxCycles    = [int](Cfg "run" "auto_stop_cycles" 0)
    Site         = [bool](Cfg "run" "site" $true)
    GitCommit    = [bool](Cfg "run" "git_commit" $true)
}
$phase = Cfg "run" "initial_phase" $null
if ($null -ne $phase) { $runArgs.Phase = [string]$phase }
if ($Build) { $runArgs.Build = $true }
& (Join-Path $harness "scripts\run.ps1") @runArgs
if ($LASTEXITCODE -ne 0) { throw "run.ps1 failed" }

# Watchdog: always restarted so config changes take effect. Own minimized window.
$existing = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*watchdog.ps1*" -and $_.CommandLine -notlike "*-Once*" }
foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped previous watchdog (pid $($p.ProcessId))" }
$wd = Join-Path $harness "scripts\watchdog.ps1"
$wdArgs = "-NoExit -ExecutionPolicy Bypass -File `"$wd`"" +
    " -Work `"$($work -replace '\\','/')`" -Findings `"$($findings -replace '\\','/')`" -EnvFile `"$($envFile -replace '\\','/')`"" +
    " -PollSeconds $([int](Cfg 'watchdog' 'poll_seconds' 10))" +
    " -HeartbeatStaleSeconds $([int](Cfg 'watchdog' 'heartbeat_stale_seconds' 120))" +
    " -MinFreeGB $([double](Cfg 'watchdog' 'min_free_gb' 5))" +
    " -NoCandidateMinutes $([int](Cfg 'watchdog' 'no_candidate_minutes' 30))" +
    " -PushMinutes $([int](Cfg 'watchdog' 'push_minutes' 10))" +
    " -CpuHigh $([int](Cfg 'watchdog' 'cpu_pause_high_percent' 70))" +
    " -CpuLow $([int](Cfg 'watchdog' 'cpu_pause_low_percent' 30))" +
    " -CpuSustainSeconds $([int](Cfg 'watchdog' 'cpu_pause_sustain_seconds' 30))" +
    " -CpuHighAvg $([int](Cfg 'watchdog' 'cpu_pause_high_avg_percent' 60))" +
    " -CpuLowAvg $([int](Cfg 'watchdog' 'cpu_pause_low_avg_percent' 40))" +
    " -CpuAvgWindowSeconds $([int](Cfg 'watchdog' 'cpu_avg_window_seconds' 300))" +
    " -SaturationCheckSeconds $([int](Cfg 'watchdog' 'saturation_check_seconds' 1800))" +
    " -PeerContainers `"$([string](Cfg 'watchdog' 'peer_containers' 'rk'))`"" +
    " -StatsTimeoutMs $([int](Cfg 'watchdog' 'docker_stats_timeout_ms' 4000))" +
    " -MinFreeSystemGB $([double](Cfg 'watchdog' 'min_free_system_gb' 0))"
if (-not [bool](Cfg "watchdog" "battery_guard" $true)) { $wdArgs += " -NoBatteryGuard" }
if ([bool](Cfg "watchdog" "auto_freeze" $false)) { $wdArgs += " -AutoFreeze" }
$wdLog = Join-Path $root "watchdog.log"
$wdArgs += " -LogFile `"$wdLog`""
Start-Process powershell -ArgumentList $wdArgs -WindowStyle Minimized
Write-Host "watchdog started (minimized window; battery guard $(if ([bool](Cfg 'watchdog' 'battery_guard' $true)) { 'on' } else { 'off' }))"

# The stats writer. A one-shot stats.txt states its own shelf life; a writer on a timer
# keeps it inside that deadline without anyone remembering to run it.
$statsPath = Join-Path $root "stats.ps1"
$statsPattern = [System.Management.Automation.WildcardPattern]::Escape($statsPath)
$oldStats = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$statsPattern*" -and $_.CommandLine -like "*-Loop*" }
foreach ($p in $oldStats) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped previous stats writer (pid $($p.ProcessId))" }
$statsArgs = "-NoExit -ExecutionPolicy Bypass -File `"$statsPath`" -Loop -Interval $([int](Cfg 'stats' 'interval_seconds' 60))"
if (-not [bool](Cfg "stats" "gpu" $false)) { $statsArgs += " -NoGpu" }
Start-Process powershell -ArgumentList $statsArgs -WindowStyle Minimized
Write-Host "stats writer started (rewrites $(Join-Path $root 'stats.txt') every $([int](Cfg 'stats' 'interval_seconds' 60))s)"
Write-Host ""
Write-Host "Running.  Live view: .\watcher.ps1     log: docker logs -f rk     site: https://jgoetzmann.github.io/rk-findings/"
Write-Host "Stop:     .\stop.ps1 (graceful)  or  .\stop.ps1 -Force      Settings: python configure.py show"
Write-Host "watchdog log: $wdLog"
