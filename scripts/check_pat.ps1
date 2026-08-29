# Test K5 — HANDOFF §2.2. The fine-grained PAT in .env must NOT be able to write rk-harness.
# Actually attempts a PATCH with that token; anything other than HTTP 403 is a failure.
param([string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"))
$ErrorActionPreference = "Stop"
if (-not (Test-Path $EnvFile)) { Write-Error ".env not found at $EnvFile"; exit 2 }
$token = $null
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*GITHUB_TOKEN\s*=\s*(.+?)\s*$') { $token = $Matches[1].Trim('"').Trim("'") }
}
if (-not $token) { Write-Error "GITHUB_TOKEN missing from .env"; exit 2 }
$env:GH_TOKEN = $token
$out = & gh api "repos/jgoetzmann/rk-harness" --method PATCH -f description=x 2>&1
$code = $LASTEXITCODE
$text = ($out | Out-String)
if ($code -ne 0 -and $text -match 'HTTP 403') {
    Write-Host "K5 PASS: PAT cannot write rk-harness (HTTP 403)"
    exit 0
}
Write-Host "K5 FAIL: expected HTTP 403, got exit $code`n$text"
exit 1
