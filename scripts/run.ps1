# Container wrapper - HANDOFF section 13.2. Hand-written (HANDOFF section 16.1).
# Builds the image, creates the restricted bridge, and starts the container. Resource flags and
# runner settings come from the workspace config.json via start.ps1; the defaults below are the
# handoff's values.
param(
    [string]$Harness  = "D:/rk/harness",
    [string]$Work     = "D:/rk/work",
    [string]$Findings = "D:/rk/findings",
    [string]$EnvFile  = "D:/rk/.env",
    [string]$CodexAuth = (Join-Path $env:USERPROFILE ".codex\auth.json"),
    [string]$Llm = "auto",          # auto | codex | on | off
    [string]$Model = "",
    [double]$Cpus = 4,
    [double]$MemoryGB = 6,
    [int]$PidsLimit = 512,
    [int]$CpuShares = 256,
    [double]$ScratchGB = 2,
    [int]$EvalBudget = 200,
    [int]$LlmEveryCycles = 5,
    [int]$CodexUsageCap = 80,
    [int]$EnumPerCycle = 500,
    [int]$MaxMinutes = 0,
    [int]$MaxCycles = 0,
    [bool]$Site = $true,
    [bool]$GitCommit = $true,
    [string]$Phase = "",
    [switch]$Build
)
$ErrorActionPreference = "Stop"

foreach ($p in @($Harness, $Work, $Findings)) {
    if (-not (Test-Path $p)) { Write-Error "missing directory: $p"; exit 2 }
}
if (-not (Test-Path $EnvFile)) { Write-Error "missing env file: $EnvFile (copy .env.example)"; exit 2 }
if (-not (Test-Path (Join-Path $Harness "VERIFIER_HASH"))) {
    Write-Error "no VERIFIER_HASH pinned in $Harness - run: python -m rk_harness.verifier_hash --pin"; exit 2
}

if ($Build) {
    docker build -t rk-harness:latest $Harness
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

$nets = docker network ls --format "{{.Name}}"
if ($nets -notcontains "rk-net") {
    docker network create --driver bridge rk-net | Out-Null
    Write-Host "created rk-net; apply egress allowlist with scripts/network.sh inside WSL (HANDOFF section 13.3)"
}

$existing = docker ps -a --format "{{.Names}}"
if ($existing -contains "rk") { docker rm -f rk | Out-Null }

# The container never receives GITHUB_TOKEN: pushes happen on the host (scripts/watchdog.ps1).
$ContainerEnv = (& (Join-Path $PSScriptRoot "container_env.ps1") -EnvFile $EnvFile | Select-Object -Last 1)

$mounts = @(
    "-v", "${Harness}:/harness:ro",
    "-v", "${Work}:/work",
    "-v", "${Findings}:/findings"
)
$haveAuth = Test-Path $CodexAuth
if ($haveAuth) { $mounts += @("-v", "${CodexAuth}:/root/.codex/auth.json:ro") }
else { Write-Host "note: $CodexAuth not found; Codex OAuth not mounted (authenticate on the host first)" }
if ($Llm -eq "auto") { if ($haveAuth) { $Llm = "codex" } else { $Llm = "off" } }
if ($Llm -eq "codex" -and -not $haveAuth) { Write-Host "warning: RK_LLM=codex requested but auth.json is missing; directives will fall back"; }
Write-Host "LLM mode: RK_LLM=$Llm$(if ($Model) { " model=$Model" })"

$envFlags = @(
    "-e", "RK_LLM=$Llm",
    "-e", "RK_SITE=$(if ($Site) { 'on' } else { 'off' })",
    "-e", "RK_GIT_COMMIT=$(if ($GitCommit) { 'on' } else { 'off' })",
    "-e", "RK_EVAL_BUDGET=$EvalBudget",
    "-e", "RK_LLM_EVERY_CYCLES=$LlmEveryCycles",
    "-e", "RK_CODEX_USAGE_CAP=$CodexUsageCap",
    "-e", "RK_ENUM_PER_CYCLE=$EnumPerCycle",
    "-e", "RK_MAX_MINUTES=$MaxMinutes",
    "-e", "RK_MAX_CYCLES=$MaxCycles"
)
if ($Model) { $envFlags += @("-e", "RK_LLM_MODEL=$Model") }
if ($Phase -ne "") { $envFlags += @("-e", "RK_PHASE=$Phase") }

$mem = "{0}g" -f $MemoryGB
$scratch = "/scratch:size={0}g" -f $ScratchGB
Write-Host "resources: cpus=$Cpus memory=$mem pids-limit=$PidsLimit cpu-shares=$CpuShares tmpfs=$scratch"
Write-Host "limits: max_minutes=$MaxMinutes max_cycles=$MaxCycles eval_budget=$EvalBudget enum_per_cycle=$EnumPerCycle llm_every=$LlmEveryCycles cycles codex_cap=$CodexUsageCap%"

docker run -d --name rk `
  --cpus=$Cpus --memory=$mem --pids-limit=$PidsLimit --cpu-shares=$CpuShares `
  --tmpfs $scratch `
  @mounts `
  --env-file $ContainerEnv `
  @envFlags `
  --network rk-net `
  rk-harness:latest
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "rk started."
