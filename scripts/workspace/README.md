# Workspace wrappers

Copies of the files that live at the workspace root (one level above rk-harness):
`start.ps1` (container + watchdog from config.json), `stop.ps1` (graceful/forced stop + final
push), `watcher.ps1` (live status window running `python -m rk_harness.watch`), `stats.ps1`
(writes stats.txt: is it running, what is it doing, what is this machine doing; `-Loop
-Background` keeps it current), `configure.py` (edits config.json; `python configure.py
explain`), and `config.example.json` (the defaults).
They resolve rk-harness, rk-work and rk-findings relative to their own location, so copy them
to the directory that contains the three checkouts and rename `config.example.json` to
`config.json`.
