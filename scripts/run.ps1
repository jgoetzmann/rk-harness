# Container wrapper — HANDOFF §13.2. Hand-written (HANDOFF §16.1).
# Builds the image, creates the restricted bridge, and starts the container with the exact
# flags from the handoff. Paths default to D:\rk\{harness,work,findings}.
param(
    [string]$Harness  = "D:/rk/harness",
    [string]$Work     = "D:/rk/work",
    [string]$Findings = "D:/rk/findings",
    [string]$EnvFile  = "D:/rk/.env",
    [string]$CodexAuth = (Join-Path $env:USERPROFILE ".codex\auth.json"),
    [switch]$Build
)
$ErrorActionPreference = "Stop"

foreach ($p in @($Harness, $Work, $Findings)) {
    if (-not (Test-Path $p)) { Write-Error "missing directory: $p"; exit 2 }
}
if (-not (Test-Path $EnvFile)) { Write-Error "missing env file: $EnvFile (copy .env.example)"; exit 2 }
if (-not (Test-Path (Join-Path $Harness "VERIFIER_HASH"))) {
    Write-Error "no VERIFIER_HASH pinned in $Harness — run: python -m rk_harness.verifier_hash --pin"; exit 2
}

if ($Build) {
    docker build -t rk-harness:latest $Harness
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

$nets = docker network ls --format "{{.Name}}"
if ($nets -notcontains "rk-net") {
    docker network create --driver bridge rk-net | Out-Null
    Write-Host "created rk-net; apply egress allowlist with scripts/network.sh inside WSL (HANDOFF §13.3)"
}

$existing = docker ps -a --format "{{.Names}}"
if ($existing -contains "rk") { docker rm -f rk | Out-Null }

$mounts = @(
    "-v", "${Harness}:/harness:ro",
    "-v", "${Work}:/work",
    "-v", "${Findings}:/findings"
)
if (Test-Path $CodexAuth) {
    $mounts += @("-v", "${CodexAuth}:/root/.codex/auth.json:ro")
} else {
    Write-Host "note: $CodexAuth not found; Codex OAuth not mounted (authenticate on the host first)"
}

docker run -d --name rk `
  --cpus=4 --memory=6g --pids-limit=512 --cpu-shares=256 `
  --tmpfs /scratch:size=2g `
  @mounts `
  --env-file $EnvFile `
  --network rk-net `
  rk-harness:latest
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "rk started. Watchdog: scripts/watchdog.ps1 -Work $Work"
