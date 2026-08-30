# Workspace wrappers

Copies of the two scripts that live at the workspace root (one level above rk-harness):
`start.ps1` (container + watchdog) and `stop.ps1` (graceful/forced stop + final push).
They resolve rk-harness, rk-work and rk-findings relative to their own location, so copy
them to the directory that contains the three checkouts.
