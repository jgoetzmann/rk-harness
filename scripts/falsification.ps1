# HANDOFF section 15 - the falsification experiment. Writes rk-work/falsification.json and prints
# the kill/proceed verdict. Run before anything else.
param([string]$Work = (Join-Path $PSScriptRoot "..\..\rk-work"))
$ErrorActionPreference = "Stop"
$env:RK_WORK_DIR = (Resolve-Path $Work).Path
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Set-Location (Join-Path $PSScriptRoot "..")
& $py -m rk_harness.falsification
exit $LASTEXITCODE
