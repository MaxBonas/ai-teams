[CmdletBinding()]
param(
    [switch]$SkipPull,
    [switch]$SkipStop,
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
$receiptPath = Join-Path $rootDir "runtime\last_update.json"
$previousRevision = ""
$currentRevision = ""

function Write-UpdateInfo {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host "[update_windows] $Message"
    }
}

function Save-Receipt {
    param(
        [string]$Status,
        [string]$Detail
    )
    $receiptDir = Split-Path -Parent $receiptPath
    if (-not (Test-Path $receiptDir)) {
        New-Item -ItemType Directory -Path $receiptDir | Out-Null
    }
    @{
        schema_version = "windows_update_v1"
        status = $Status
        detail = $Detail
        previous_revision = $previousRevision
        current_revision = $currentRevision
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
}

try {
    Push-Location $rootDir
    try {
        $insideRepo = (& git rev-parse --is-inside-work-tree 2>$null)
        if ($LASTEXITCODE -ne 0 -or "$insideRepo".Trim() -ne "true") {
            throw "Este checkout no es un repositorio Git valido."
        }
        $previousRevision = (& git rev-parse HEAD).Trim()
        $currentRevision = $previousRevision

        $dirty = @(& git status --porcelain --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo comprobar el estado Git."
        }
        if ($dirty.Count -gt 0) {
            throw (
                "Hay cambios versionables sin resolver. Haz commit o revisalos; " +
                "el actualizador no ejecuta stash, reset ni sobrescrituras."
            )
        }

        if (-not $SkipStop) {
            Write-UpdateInfo "Deteniendo servicios antes de cambiar codigo..."
            & (Join-Path $rootDir "stop_ide.bat")
            if ($LASTEXITCODE -ne 0) {
                throw "No se pudieron detener los servicios."
            }
        }

        if (-not $SkipPull) {
            Write-UpdateInfo "Descargando solo una actualizacion fast-forward..."
            & git pull --ff-only
            if ($LASTEXITCODE -ne 0) {
                throw "git pull --ff-only fallo; no se ha forzado ni reescrito el checkout."
            }
            $currentRevision = (& git rev-parse HEAD).Trim()
        }

        Write-UpdateInfo "Reconstruyendo dependencias y fusionando defaults locales..."
        & (Join-Path $rootDir "scripts\prepare_dev_env.bat")
        if ($LASTEXITCODE -ne 0) {
            throw "El bootstrap fallo. Los proyectos, secretos y sesiones locales no se han borrado."
        }

        Save-Receipt -Status "ready_to_start" -Detail "Update and bootstrap completed"
        Write-UpdateInfo "Actualizacion terminada. Inicia con .\start_ide.bat"
    } finally {
        Pop-Location
    }
} catch {
    Save-Receipt -Status "failed" -Detail $_.Exception.Message
    if (-not $Quiet) {
        Write-Error $_
    }
    exit 1
}
