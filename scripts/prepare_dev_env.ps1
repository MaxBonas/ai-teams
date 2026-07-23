[CmdletBinding()]
param(
    [int]$LockTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $rootDir "runtime"
$lockPath = Join-Path $runtimeDir ".bootstrap.lock"
$ownerPath = Join-Path $runtimeDir ".bootstrap.owner.json"
$lockStream = $null
$ownsLock = $false

function Assert-BootstrapInputs {
    $required = @(
        (Join-Path $rootDir "pyproject.toml"),
        (Join-Path $rootDir "requirements-dev.lock"),
        (Join-Path $rootDir "ide-frontend\\package.json"),
        (Join-Path $rootDir "ide-frontend\\package-lock.json"),
        (Join-Path $PSScriptRoot "ensure_local_runtime.ps1"),
        (Join-Path $PSScriptRoot "ensure_local_venv.ps1"),
        (Join-Path $PSScriptRoot "ensure_frontend_deps.ps1"),
        (Join-Path $PSScriptRoot "audit_installation_support.py")
    )
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($missing.Count -gt 0) {
        $names = @($missing | ForEach-Object { Split-Path -Leaf $_ })
        throw "Bootstrap incompleto; faltan inputs versionados: $($names -join ', '). No se modifico runtime."
    }
}

function Invoke-CheckedScript {
    param(
        [string]$PathValue,
        [string]$StepName
    )

    Write-Host "[prepare_dev_env] $StepName..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PathValue
    if ($LASTEXITCODE -ne 0) {
        throw "Fallo durante: $StepName"
    }
}

try {
    Assert-BootstrapInputs
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds($LockTimeoutSeconds)
    while ($null -eq $lockStream) {
        try {
            $lockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
        } catch [System.IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "Bootstrap ocupado por otro proceso durante mas de $LockTimeoutSeconds segundos."
            }
            Start-Sleep -Milliseconds 250
        }
    }

    $owner = [ordered]@{
        schema_version = "bootstrap_owner_v1"
        pid = $PID
        acquired_at = [DateTime]::UtcNow.ToString("o")
    }
    $owner | ConvertTo-Json -Compress | Set-Content -LiteralPath $ownerPath -Encoding UTF8
    $ownsLock = $true

    Invoke-CheckedScript -PathValue (Join-Path $PSScriptRoot "ensure_local_runtime.ps1") -StepName "Validando runtime local"
    Invoke-CheckedScript -PathValue (Join-Path $PSScriptRoot "ensure_local_venv.ps1") -StepName "Validando venv local"
    Invoke-CheckedScript -PathValue (Join-Path $PSScriptRoot "ensure_frontend_deps.ps1") -StepName "Validando dependencias frontend"

    $venvPython = Join-Path $rootDir "venv\\Scripts\\python.exe"
    Write-Host "[prepare_dev_env] Revisando soporte y adapters recomendados..."
    & $venvPython (Join-Path $PSScriptRoot "audit_installation_support.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "No se pudo completar la auditoria de soporte."
    }
    Write-Host "[prepare_dev_env] Entorno local listo."
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
    if ($ownsLock) {
        Remove-Item -LiteralPath $ownerPath -Force -ErrorAction SilentlyContinue
    }
}
