[CmdletBinding()]
param(
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
$configDir = Join-Path $rootDir "config"
$runtimeDir = Join-Path $rootDir "runtime"
$statePath = Join-Path $runtimeDir ".template_sync_state.json"

function Write-Info {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host "[ensure_local_runtime] $Message"
    }
}

function Ensure-Directory {
    param([string]$PathValue)
    if (-not (Test-Path $PathValue)) {
        New-Item -ItemType Directory -Path $PathValue | Out-Null
    }
}

function Copy-IfMissing {
    param(
        [string]$SourcePath,
        [string]$TargetPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Plantilla no encontrada: $SourcePath"
    }

    if (-not (Test-Path $TargetPath)) {
        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath
        Write-Info "Creado $(Split-Path $TargetPath -Leaf) desde plantilla."
    }
}

function Get-FileSha256 {
    param([string]$PathValue)
    $stream = [System.IO.File]::OpenRead($PathValue)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash($stream)
        return ([System.BitConverter]::ToString($hashBytes)).Replace("-", "").ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Load-State {
    if (-not (Test-Path $statePath)) {
        return @{}
    }
    try {
        $raw = Get-Content $statePath -Raw -Encoding UTF8
        $data = ConvertFrom-Json $raw
        if ($null -ne $data) {
            $state = @{}
            foreach ($prop in $data.PSObject.Properties) {
                $entry = @{}
                if ($null -ne $prop.Value) {
                    foreach ($inner in $prop.Value.PSObject.Properties) {
                        $entry[$inner.Name] = [string]$inner.Value
                    }
                }
                $state[$prop.Name] = $entry
            }
            return $state
        }
    } catch {
    }
    return @{}
}

function Save-State {
    param([hashtable]$State)
    $json = $State | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath $statePath -Value $json -Encoding UTF8
}

function Sync-Template {
    param(
        [hashtable]$State,
        [string]$SourcePath,
        [string]$TargetPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Plantilla no encontrada: $SourcePath"
    }

    $name = Split-Path $TargetPath -Leaf
    $sourceHash = Get-FileSha256 -PathValue $SourcePath
    $stateEntry = @{}
    if ($State.ContainsKey($name) -and $State[$name] -is [hashtable]) {
        $stateEntry = $State[$name]
    }

    if (-not (Test-Path $TargetPath)) {
        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath
        $State[$name] = @{
            source_hash = $sourceHash
            target_hash = $sourceHash
            mode = "synced"
            synced_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        Write-Info "Creado $name desde plantilla."
        return
    }

    $targetHash = Get-FileSha256 -PathValue $TargetPath
    if ($targetHash -eq $sourceHash) {
        $State[$name] = @{
            source_hash = $sourceHash
            target_hash = $targetHash
            mode = "synced"
            synced_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        return
    }

    $previousTargetHash = ""
    $previousSourceHash = ""
    $previousMode = ""
    $previousSyncedAt = ""
    if ($stateEntry.ContainsKey("target_hash")) { $previousTargetHash = [string]$stateEntry["target_hash"] }
    if ($stateEntry.ContainsKey("source_hash")) { $previousSourceHash = [string]$stateEntry["source_hash"] }
    if ($stateEntry.ContainsKey("mode")) { $previousMode = [string]$stateEntry["mode"] }
    if ($stateEntry.ContainsKey("synced_at")) { $previousSyncedAt = [string]$stateEntry["synced_at"] }

    if (
        $previousMode -eq "synced" -and
        $previousTargetHash -and
        $targetHash -eq $previousTargetHash
    ) {
        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Force
        $State[$name] = @{
            source_hash = $sourceHash
            target_hash = $sourceHash
            mode = "synced"
            synced_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        Write-Info "Actualizado $name desde plantilla compartida."
        return
    }

    if (
        (-not $previousMode) -or
        (
            $previousMode -eq "untracked_local" -and
            $previousTargetHash -and
            $targetHash -eq $previousTargetHash
        )
    ) {
        $backupPath = "$TargetPath.pre_template_sync.bak"
        if (-not (Test-Path $backupPath)) {
            Copy-Item -LiteralPath $TargetPath -Destination $backupPath
        }
        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Force
        $State[$name] = @{
            source_hash = $sourceHash
            target_hash = $sourceHash
            mode = "synced"
            synced_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        Write-Info "Migrado $name a plantilla compartida. Backup local: $(Split-Path $backupPath -Leaf)"
        return
    }

    $mode = if ($previousSourceHash -and $previousTargetHash -and $targetHash -ne $previousTargetHash) {
        "local_override"
    } else {
        "untracked_local"
    }
    $State[$name] = @{
        source_hash = $sourceHash
        target_hash = $targetHash
        mode = $mode
        synced_at = $previousSyncedAt
    }
    Write-Info "Conservando $name como $mode."
}

try {
    Ensure-Directory -PathValue $runtimeDir
    Ensure-Directory -PathValue (Join-Path $runtimeDir "archive")
    Ensure-Directory -PathValue (Join-Path $runtimeDir "ollama")
    $state = Load-State

    Sync-Template -State $state -SourcePath (Join-Path $configDir "adapters.example.json") -TargetPath (Join-Path $runtimeDir "adapters.json")
    Sync-Template -State $state -SourcePath (Join-Path $configDir "mcp_servers.example.json") -TargetPath (Join-Path $runtimeDir "mcp_servers.json")
    Sync-Template -State $state -SourcePath (Join-Path $configDir "model_catalog.example.json") -TargetPath (Join-Path $runtimeDir "model_catalog.json")
    Save-State -State $state

    Write-Info "Runtime local listo."
} catch {
    if (-not $Quiet) {
        Write-Error $_
    }
    exit 1
}
