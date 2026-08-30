# HANDOFF section 2.1 - creates the three GitHub repos and enables Pages for rk-findings.
# Run once, by a human, from the directory that should contain the clones.
# Acceptance: all three repos cloneable; https://jgoetzmann.github.io/rk-findings/ returns 200 within 10 minutes.
$ErrorActionPreference = "Stop"
gh auth status
foreach ($r in @("rk-harness","rk-work","rk-findings")) {
    gh repo create "jgoetzmann/$r" --public --clone
}
Set-Location rk-findings
New-Item -ItemType Directory -Force -Path docs | Out-Null
"# rk-findings" | Out-File -Encoding utf8 docs/index.md
git add -A; git commit -m "init"; git push
gh api -X POST "repos/jgoetzmann/rk-findings/pages" `
  -f "source[branch]=main" -f "source[path]=/docs"
Set-Location ..
