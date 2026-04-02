[CmdletBinding()]
param(
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $rootDir "ide-frontend"
$nodeModulesDir = Join-Path $frontendDir "node_modules"
$statePath = Join-Path $nodeModulesDir ".aiteam-lock.sha256"

function Write-Info {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host "[ensure_frontend_deps] $Message"
    }
}

function Resolve-NpmCommand {
    foreach ($candidate in @("npm.cmd", "npm")) {
        try {
            $cmd = Get-Command $candidate -ErrorAction Stop
            if ($cmd.Source) {
                return $cmd.Source
            }
        } catch {
        }
    }
    throw "npm no encontrado."
}

function Get-InputHash {
    function Get-FileSha256Compat {
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

    $paths = @(
        (Join-Path $frontendDir "package.json"),
        (Join-Path $frontendDir "package-lock.json")
    ) | Where-Object { Test-Path $_ }

    if (-not $paths) {
        throw "No se encontraron manifests del frontend."
    }

    $material = foreach ($path in $paths) {
        "{0}:{1}" -f (Split-Path $path -Leaf), (Get-FileSha256Compat -PathValue $path)
    }
    $joined = [string]::Join("`n", $material)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($joined)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hashBytes)).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Invoke-Npm {
    param(
        [string]$NpmCmd,
        [string[]]$Arguments,
        [string]$StepName
    )

    Write-Info $StepName
    Push-Location $frontendDir
    try {
        & $NpmCmd @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Fallo durante: $StepName"
        }
    } finally {
        Pop-Location
    }
}

try {
    $npmCmd = Resolve-NpmCommand
    $inputHash = Get-InputHash
    $storedHash = ""
    if (Test-Path $statePath) {
        $storedHash = (Get-Content $statePath -Raw -Encoding UTF8).Trim().ToLowerInvariant()
    }

    $needsInstall = -not (Test-Path $nodeModulesDir)
    if (-not $needsInstall -and $storedHash -ne $inputHash) {
        $needsInstall = $true
    }

    if ($needsInstall) {
        $lockPath = Join-Path $frontendDir "package-lock.json"
        if (Test-Path $lockPath) {
            try {
                Invoke-Npm -NpmCmd $npmCmd -Arguments @("ci", "--prefer-offline", "--no-fund", "--no-audit") -StepName "Instalando dependencias frontend"
            } catch {
                Write-Info "package-lock desfasado; fallback a npm install."
                Invoke-Npm -NpmCmd $npmCmd -Arguments @("install", "--prefer-offline", "--no-fund", "--no-audit") -StepName "Actualizando dependencias frontend"
            }
        } else {
            Invoke-Npm -NpmCmd $npmCmd -Arguments @("install", "--prefer-offline", "--no-fund", "--no-audit") -StepName "Instalando dependencias frontend"
        }
    }

    if (-not (Test-Path $nodeModulesDir)) {
        throw "node_modules no existe despues de instalar dependencias."
    }

    Set-Content -LiteralPath $statePath -Value $inputHash -Encoding UTF8
    Write-Info "Frontend listo."
} catch {
    if (-not $Quiet) {
        Write-Error $_
    }
    exit 1
}
