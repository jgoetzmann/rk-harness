# Opens a dedicated terminal window with the live status view (rk_harness.watch).
# The window is read-only and independent of the run: the server is the Docker container, so
# Ctrl+C here only stops the view (the window then offers to restart it), never the run.
#   .\watcher.ps1              open the window
#   .\watcher.ps1 -Here        run the view in this terminal instead
#   .\watcher.ps1 -Once        print one snapshot and exit
param([switch]$Here, [switch]$Once)
$root = $PSScriptRoot
$harness = Join-Path $root "rk-harness"
$py = Join-Path $harness ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:RK_WORK_DIR = (Join-Path $root "rk-work") -replace '\\','/'
$env:RK_FINDINGS_DIR = (Join-Path $root "rk-findings") -replace '\\','/'
$env:RK_CONFIG = (Join-Path $root "config.json") -replace '\\','/'
$env:PYTHONPATH = $harness
$env:PYTHONWARNINGS = "ignore::UserWarning"

if ($Once) { & $py -m rk_harness.watch --once; exit $LASTEXITCODE }
if ($Here) { & $py -m rk_harness.watch; exit $LASTEXITCODE }

$inner = @"
`$host.UI.RawUI.WindowTitle = 'rk watcher (read-only; Ctrl+C stops only this view)'
cmd /c 'mode con: cols=170 lines=70' | Out-Null
`$env:PYTHONWARNINGS = 'ignore::UserWarning'
`$env:RK_WORK_DIR = '$($env:RK_WORK_DIR)'; `$env:RK_FINDINGS_DIR = '$($env:RK_FINDINGS_DIR)'; `$env:RK_CONFIG = '$($env:RK_CONFIG)'; `$env:PYTHONPATH = '$harness'
while (`$true) {
  & '$py' -m rk_harness.watch
  Write-Host ''
  Write-Host 'watcher stopped (the run is unaffected). Press Enter to restart the view, or close this window.' -ForegroundColor Yellow
  `$null = Read-Host
}
"@
$tmp = Join-Path $env:TEMP "rk-watcher-inner.ps1"
Set-Content -Path $tmp -Value $inner -Encoding ascii
Start-Process powershell -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$tmp`""
Write-Host "watcher window opened"
