# Builds the env file the container receives: everything in .env EXCEPT GITHUB_TOKEN.
# The GitHub credential stays on the host; the host watchdog pushes rk-work / rk-findings with the
# owner's own credentials (HANDOFF section 2.2 intent: the agent must never hold a token that can
# write rk-harness -- so it holds none at all). Prints the path of the filtered file.
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"),
    [string]$OutFile = (Join-Path $env:TEMP "rk-container.env")
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $EnvFile)) { Write-Error "env file not found: $EnvFile"; exit 2 }
$kept = @()
$dropped = 0
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*GITHUB_TOKEN\s*=') { $dropped += 1; continue }
    $kept += $line
}
Set-Content -Path $OutFile -Value $kept -Encoding ascii
Write-Host "container env written to $OutFile ($($kept.Count) lines kept, GITHUB_TOKEN lines dropped: $dropped)"
Write-Output $OutFile
