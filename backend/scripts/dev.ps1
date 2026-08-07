<#
.SYNOPSIS
    Reliable backend dev-server restart for Windows.

.DESCRIPTION
    Stops any process holding the API port AND any orphaned uvicorn `--reload`
    worker/child processes, waits for the port to be released (Windows keeps it
    in TIME_WAIT after a kill), then starts a fresh uvicorn.

    This exists because `uvicorn --reload` on Windows routinely leaves orphaned
    multiprocessing children behind on restart. A stale child can keep serving
    the OLD code on the port while your "restarted" server fails to bind — so
    code edits silently don't take effect. Always restart via this script.

.PARAMETER Port
    API port to free + bind. Default 8000.

.PARAMETER NoStart
    Only stop stale processes and free the port; don't start a new server.
    Useful for CI / scripted cleanup.

.EXAMPLE
    ./scripts/dev.ps1
    ./scripts/dev.ps1 -Port 8000
    ./scripts/dev.ps1 -NoStart
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

# backend/ is the parent of this script's folder — run uvicorn from there so
# `app.main:app` and PYTHONPATH=. resolve.
$BackendDir = Split-Path -Parent $PSScriptRoot

function Get-PortOwners {
    param([int]$Port)
    try {
        return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

# Collect every PID worth killing: whoever owns the port, plus any python
# process whose command line looks like our uvicorn server or a reload child,
# plus the children of those (the orphan-prone multiprocessing workers).
function Get-StalePids {
    param([int]$Port)

    $targets = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($owner in Get-PortOwners -Port $Port) { [void]$targets.Add([int]$owner) }

    $py = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $py) {
        if ($p.CommandLine -and ($p.CommandLine -match 'uvicorn' -or $p.CommandLine -match 'app\.main:app')) {
            [void]$targets.Add([int]$p.ProcessId)
        }
    }
    # Pull in children of anything already targeted (reload workers fork these).
    foreach ($p in $py) {
        if ($p.ParentProcessId -and $targets.Contains([int]$p.ParentProcessId)) {
            [void]$targets.Add([int]$p.ProcessId)
        }
    }
    return $targets
}

Write-Host "[dev] freeing port $Port and clearing stale uvicorn processes..." -ForegroundColor Cyan

$killed = @()
# Two passes: killing a parent can orphan a child that only then becomes
# attributable; the second pass sweeps those up.
for ($pass = 0; $pass -lt 2; $pass++) {
    foreach ($procId in Get-StalePids -Port $Port) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  killing PID $procId (started $($proc.StartTime))" -ForegroundColor Yellow
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            $killed += $procId
        }
    }
}
if (-not $killed) { Write-Host "  (nothing stale found)" -ForegroundColor DarkGray }

# Wait for the listener to actually disappear (TIME_WAIT can linger).
$deadline = (Get-Date).AddSeconds(30)
while ((Get-PortOwners -Port $Port).Count -gt 0) {
    if ((Get-Date) -gt $deadline) {
        Write-Error "Port $Port is still held after 30s. Owners: $((Get-PortOwners -Port $Port) -join ', '). Investigate before starting."
        exit 1
    }
    Start-Sleep -Milliseconds 300
}
Write-Host "[dev] port $Port is free." -ForegroundColor Green

if ($NoStart) { return }

# Warn when the dev database is behind the migration head.
#
# A local DB whose `alembic_version` has fallen behind fails in a way that reads
# like a code bug, not a schema one: the model declares a column the table does
# not have, so EVERY query touching that table 500s while `/health` stays green
# and the tests (which build their schema from the models) all pass. That cost
# real debugging time — the Member Coverage pane rendered blank with no error.
# Warn, never block: a drifted DB is still usable for most work, and a dev
# server that refuses to start is worse than one that tells you why.
Push-Location $BackendDir
try {
    $env:PYTHONPATH = "."
    # Alembic logs its INFO lines to STDERR and prints the revision to STDOUT.
    # Two Windows-PowerShell traps here, and both silently turned this check into
    # a permanent "couldn't check":
    #   1. `2>&1` wraps each stderr line in an ErrorRecord (NativeCommandError).
    #   2. This script sets `$ErrorActionPreference = "Stop"`, which promotes that
    #      to a TERMINATING error — so even `2>$null` throws.
    # Relaxing the preference for the duration of the two calls fixes both; only
    # stdout is parsed, so the INFO noise is irrelevant either way.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $current = (& uv run alembic current 2>$null | Select-String -Pattern '^[0-9a-f]{12}' |
        Select-Object -First 1 | ForEach-Object { $_.ToString().Split(' ')[0] })
    $head = (& uv run alembic heads 2>$null | Select-String -Pattern '^[0-9a-f]{12}' |
        Select-Object -First 1 | ForEach-Object { $_.ToString().Split(' ')[0] })
    $ErrorActionPreference = $prevEap
    if ($current -and $head -and ($current -ne $head)) {
        Write-Host "[dev] WARNING: database is BEHIND the migration head." -ForegroundColor Red
        Write-Host "        db:   $current" -ForegroundColor Yellow
        Write-Host "        head: $head" -ForegroundColor Yellow
        Write-Host "        Run 'uv run alembic upgrade head'. Symptoms of ignoring this:" -ForegroundColor DarkGray
        Write-Host "        endpoints 500 with 'no such column' while /health stays 200." -ForegroundColor DarkGray
    }
} catch {
    Write-Host "[dev] (couldn't check migration state: $_)" -ForegroundColor DarkGray
} finally {
    Pop-Location
}

Write-Host "[dev] starting uvicorn (app.main:app --reload) on port $Port ..." -ForegroundColor Cyan
Push-Location $BackendDir
try {
    $env:PYTHONPATH = "."
    & uv run uvicorn app.main:app --reload --port $Port
} finally {
    Pop-Location
}
