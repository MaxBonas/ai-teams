#!/usr/bin/env pwsh
<#
.SYNOPSIS
    AI Teams CLI launcher for Windows.

.DESCRIPTION
    Activates the local virtualenv and runs 'aiteam <args>'.
    Equivalent to: .\venv\Scripts\python.exe -m aiteam.cli <args>

.EXAMPLE
    .\aiteam.ps1 serve
    .\aiteam.ps1 serve --port 9000 --reload
    .\aiteam.ps1 dev
    .\aiteam.ps1 status
    .\aiteam.ps1 project list
    .\aiteam.ps1 project create "My App" --task "Build landing page"
    .\aiteam.ps1 issue list --status blocked
    .\aiteam.ps1 issue create "Fix login bug" --role engineer
    .\aiteam.ps1 run list --limit 10
    .\aiteam.ps1 run trigger lead <issue-id>
    .\aiteam.ps1 heartbeat --once
    .\aiteam.ps1 system-check
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PassThru
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot

# ── Locate Python ─────────────────────────────────────────────────────────────

$python = $null

$candidates = @(
    (Join-Path $root "venv\Scripts\python.exe"),
    (Join-Path $root "venv\bin\python")
)

foreach ($c in $candidates) {
    if (Test-Path $c) {
        $python = $c
        break
    }
}

if (-not $python) {
    # Fall back to system Python
    $python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $python) {
        $python = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
    }
}

if (-not $python) {
    Write-Error "Python not found. Create the virtualenv first:`n  python -m venv venv`n  .\venv\Scripts\pip install -e ."
    exit 1
}

# ── Ensure setup scripts have run ─────────────────────────────────────────────

$ensureScript = Join-Path $root "scripts\ensure_local_runtime.ps1"
if (Test-Path $ensureScript) {
    & $ensureScript -Quiet
}

# ── Show help when called with no arguments ───────────────────────────────────

if ($PassThru.Count -eq 0) {
    $PassThru = @("--help")
}

# ── Run the CLI ───────────────────────────────────────────────────────────────

$env:PYTHONPATH = $root
& $python -m aiteam.cli @PassThru
exit $LASTEXITCODE
