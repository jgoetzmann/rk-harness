# Test K5 - HANDOFF section 2.2. The fine-grained PAT in .env must NOT be able to write rk-harness.
# Two probes, both non-destructive:
#   1. the HANDOFF's literal check: PATCH the repo description -> must be HTTP 403
#   2. the intent: `git push --dry-run` to rk-harness with the token -> must be denied,
#      while the same dry-run to rk-work and rk-findings must be accepted.
# The token is never printed.
param([string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"))
$ErrorActionPreference = "Continue"
if (-not (Test-Path $EnvFile)) { Write-Host "K5 FAIL: .env not found at $EnvFile"; exit 2 }
$token = $null
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*GITHUB_TOKEN\s*=\s*(.+?)\s*$') { $token = $Matches[1].Trim('"').Trim("'") }
}
if (-not $token -or $token.StartsWith("<")) { Write-Host "K5 FAIL: GITHUB_TOKEN missing from .env"; exit 2 }
$env:GH_TOKEN = $token
$fail = 0

# Probe 1 - description PATCH (run through cmd so stderr is plain text, not an ErrorRecord).
$out = cmd /c "gh api repos/jgoetzmann/rk-harness --method PATCH -f description=x 2>&1"
$text = ($out | Out-String)
if ($text -match 'HTTP 403') { Write-Host "K5 probe 1 PASS: PATCH rk-harness -> HTTP 403" }
else { Write-Host "K5 probe 1 FAIL: expected HTTP 403, got: $($text.Trim())"; $fail = 1 }

# Probe 2 - content write capability via dry-run push (no refs are updated).
$tmp = Join-Path $env:TEMP ("k5-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
    Push-Location $tmp
    cmd /c "git init -q -b main 2>&1" | Out-Null
    cmd /c "git -c user.email=k5@local -c user.name=k5 commit -q --allow-empty -m probe 2>&1" | Out-Null
    foreach ($r in @("rk-harness", "rk-work", "rk-findings")) {
        $url = "https://x-access-token:$token@github.com/jgoetzmann/$r.git"
        $res = cmd /c "git push --dry-run $url HEAD:refs/heads/k5-probe 2>&1"
        $res = ($res | Out-String) -replace [regex]::Escape($token), "***"
        $denied = ($res -match '403|denied|not accessible|Permission to')
        if ($r -eq "rk-harness") {
            if ($denied) { Write-Host "K5 probe 2 PASS: push to rk-harness denied" }
            else { Write-Host "K5 probe 2 FAIL: the PAT CAN push to rk-harness:`n$($res.Trim())"; $fail = 1 }
        } else {
            if ($denied) { Write-Host "K5 probe 2 WARN: push to $r denied - the PAT cannot update $r either:`n$($res.Trim())"; $fail = 1 }
            else { Write-Host "K5 probe 2 PASS: push to $r accepted (dry-run)" }
        }
    }
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
if ($fail -eq 0) { Write-Host "K5 PASS"; exit 0 }
Write-Host "K5 FAIL"
exit 1
