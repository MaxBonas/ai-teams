[CmdletBinding()]
param(
    [switch]$Quiet,
    [string]$RootDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = if ($RootDir) {
    [System.IO.Path]::GetFullPath($RootDir)
} else {
    Split-Path -Parent $PSScriptRoot
}
$configDir = Join-Path $rootDir "config"
$runtimeDir = Join-Path $rootDir "runtime"
$statePath = Join-Path $runtimeDir ".template_sync_state.json"
$baselineDir = Join-Path $runtimeDir ".template_baselines"

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

function ConvertTo-Hashtable {
    param($Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $table = @{}
        foreach ($key in $Value.Keys) {
            $table[[string]$key] = ConvertTo-Hashtable $Value[$key]
        }
        return $table
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $table = @{}
        foreach ($property in $Value.PSObject.Properties) {
            $table[$property.Name] = ConvertTo-Hashtable $property.Value
        }
        return $table
    }
    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        return @($Value | ForEach-Object { ConvertTo-Hashtable $_ })
    }
    return $Value
}

function Merge-DefaultsWithOverride {
    param(
        [hashtable]$Defaults,
        [hashtable]$Override
    )
    $merged = @{}
    foreach ($key in $Defaults.Keys) {
        $merged[$key] = $Defaults[$key]
    }
    foreach ($key in $Override.Keys) {
        if (
            $merged.ContainsKey($key) -and
            $merged[$key] -is [hashtable] -and
            $Override[$key] -is [hashtable]
        ) {
            $merged[$key] = Merge-DefaultsWithOverride -Defaults $merged[$key] -Override $Override[$key]
        } else {
            $merged[$key] = $Override[$key]
        }
    }
    return $merged
}

function Test-DeepEqual {
    param($Left, $Right)
    if ($null -eq $Left -or $null -eq $Right) {
        return $null -eq $Left -and $null -eq $Right
    }
    if ($Left -is [hashtable] -and $Right -is [hashtable]) {
        if ($Left.Count -ne $Right.Count) { return $false }
        foreach ($key in $Left.Keys) {
            if (-not $Right.ContainsKey($key)) { return $false }
            if (-not (Test-DeepEqual $Left[$key] $Right[$key])) { return $false }
        }
        return $true
    }
    $leftEnumerable = $Left -is [System.Collections.IEnumerable] -and -not ($Left -is [string])
    $rightEnumerable = $Right -is [System.Collections.IEnumerable] -and -not ($Right -is [string])
    if ($leftEnumerable -or $rightEnumerable) {
        if (-not ($leftEnumerable -and $rightEnumerable)) { return $false }
        $leftItems = @($Left)
        $rightItems = @($Right)
        if ($leftItems.Count -ne $rightItems.Count) { return $false }
        for ($index = 0; $index -lt $leftItems.Count; $index++) {
            if (-not (Test-DeepEqual $leftItems[$index] $rightItems[$index])) {
                return $false
            }
        }
        return $true
    }
    return $Left -eq $Right
}

function Get-LocalOverrides {
    param(
        [hashtable]$Baseline,
        [hashtable]$Local
    )
    $overrides = @{}
    foreach ($key in $Local.Keys) {
        if (-not $Baseline.ContainsKey($key)) {
            $overrides[$key] = $Local[$key]
            continue
        }
        if ($Baseline[$key] -is [hashtable] -and $Local[$key] -is [hashtable]) {
            $nested = Get-LocalOverrides -Baseline $Baseline[$key] -Local $Local[$key]
            if ($nested.Count -gt 0) {
                $overrides[$key] = $nested
            }
            continue
        }
        if (-not (Test-DeepEqual $Baseline[$key] $Local[$key])) {
            $overrides[$key] = $Local[$key]
        }
    }
    return $overrides
}

function Read-JsonHashtable {
    param([string]$PathValue)
    try {
        return ConvertTo-Hashtable (Get-Content $PathValue -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        throw "JSON local invalido; se conserva sin cambios: $PathValue"
    }
}

function Write-JsonHashtable {
    param(
        [string]$PathValue,
        [hashtable]$Value
    )
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $PathValue -Encoding UTF8
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
    $baselinePath = Join-Path $baselineDir $name
    $sourceHash = Get-FileSha256 -PathValue $SourcePath
    $stateEntry = @{}
    if ($State.ContainsKey($name) -and $State[$name] -is [hashtable]) {
        $stateEntry = $State[$name]
    }

    if (-not (Test-Path $TargetPath)) {
        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath
        Copy-Item -LiteralPath $SourcePath -Destination $baselinePath -Force
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
        Copy-Item -LiteralPath $SourcePath -Destination $baselinePath -Force
        $State[$name] = @{
            source_hash = $sourceHash
            target_hash = $targetHash
            mode = "synced"
            synced_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        return
    }

    $previousTargetHash = ""
    $previousMode = ""
    if ($stateEntry.ContainsKey("target_hash")) { $previousTargetHash = [string]$stateEntry["target_hash"] }
    if ($stateEntry.ContainsKey("mode")) { $previousMode = [string]$stateEntry["mode"] }

    if (
        $previousMode -eq "synced" -and
        $previousTargetHash -and
        $targetHash -eq $previousTargetHash
    ) {
        Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Force
        Copy-Item -LiteralPath $SourcePath -Destination $baselinePath -Force
        $State[$name] = @{
            source_hash = $sourceHash
            target_hash = $sourceHash
            mode = "synced"
            synced_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        Write-Info "Actualizado $name desde plantilla compartida."
        return
    }

    # Instalaciones anteriores a template_sync no tienen estado. Nunca se
    # reemplaza su JSON: la plantilla aporta solo claves ausentes y el valor
    # local gana en cada conflicto. La misma regla actualiza overrides conocidos.
    $defaults = Read-JsonHashtable -PathValue $SourcePath
    $local = Read-JsonHashtable -PathValue $TargetPath
    $localOverrides = $local
    if (Test-Path $baselinePath) {
        $baseline = Read-JsonHashtable -PathValue $baselinePath
        $localOverrides = Get-LocalOverrides -Baseline $baseline -Local $local
    }
    $merged = Merge-DefaultsWithOverride -Defaults $defaults -Override $localOverrides
    $backupPath = "$TargetPath.pre_template_sync.bak"
    if (-not (Test-Path $backupPath)) {
        Copy-Item -LiteralPath $TargetPath -Destination $backupPath
    }
    Write-JsonHashtable -PathValue $TargetPath -Value $merged
    Copy-Item -LiteralPath $SourcePath -Destination $baselinePath -Force
    $mergedHash = Get-FileSha256 -PathValue $TargetPath
    $mode = if ($mergedHash -eq $sourceHash) { "synced" } else { "local_override" }
    $State[$name] = @{
        source_hash = $sourceHash
        target_hash = $mergedHash
        mode = $mode
        synced_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    Write-Info "Actualizado $name por merge conservador ($mode). Backup: $(Split-Path $backupPath -Leaf)"
}

try {
    Ensure-Directory -PathValue $runtimeDir
    Ensure-Directory -PathValue (Join-Path $runtimeDir "archive")
    Ensure-Directory -PathValue (Join-Path $runtimeDir "ollama")
    Ensure-Directory -PathValue $baselineDir
    $state = Load-State

    Sync-Template -State $state -SourcePath (Join-Path $configDir "control_plane.example.json") -TargetPath (Join-Path $runtimeDir "control_plane.json")
    Sync-Template -State $state -SourcePath (Join-Path $configDir "agents.example.json") -TargetPath (Join-Path $runtimeDir "agents.json")
    Save-State -State $state

    Write-Info "Runtime local listo."
} catch {
    if (-not $Quiet) {
        Write-Error $_
    }
    exit 1
}
