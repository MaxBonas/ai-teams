[CmdletBinding()]
param(
    [switch]$PrintPython,
    [switch]$Quiet,
    [switch]$ForceRecreate,
    [switch]$ReportErrors
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $rootDir "venv"
$venvPython = Join-Path $venvDir "Scripts\\python.exe"
$pyvenvCfg = Join-Path $venvDir "pyvenv.cfg"
$pyprojectPath = Join-Path $rootDir "pyproject.toml"
$stateHashPath = Join-Path $venvDir ".aiteam-pyproject.sha256"

function Write-Info {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host "[ensure_local_venv] $Message"
    }
}

function Get-PyvenvValue {
    param([string]$Key)
    if (-not (Test-Path $pyvenvCfg)) {
        return $null
    }

    foreach ($line in Get-Content $pyvenvCfg) {
        if ($line -like "$Key = *") {
            return $line.Substring($Key.Length + 3).Trim()
        }
    }

    return $null
}

function Test-PythonProcess {
    param(
        [string]$PythonExe,
        [string[]]$Arguments
    )

    if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
        return $false
    }

    try {
        & $PythonExe @Arguments *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-PythonProcessFailure {
    param(
        [string]$PythonExe,
        [string[]]$Arguments
    )

    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        return "python_path_empty"
    }

    if (-not (Test-Path $PythonExe)) {
        return "python_not_found"
    }

    try {
        & $PythonExe @Arguments *> $null
        if ($LASTEXITCODE -eq 0) {
            return ""
        }
        return "exit_code_$LASTEXITCODE"
    } catch {
        $message = $_.Exception.Message
        if ([string]::IsNullOrWhiteSpace($message)) {
            return "exception_without_message"
        }
        return ($message -replace "\s+", " ").Trim()
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

function Get-ProjectDependencyHash {
    if (-not (Test-Path $pyprojectPath)) {
        throw "No se encontro pyproject.toml para calcular el hash de dependencias."
    }
    return Get-FileSha256 -PathValue $pyprojectPath
}

function Read-StoredDependencyHash {
    if (-not (Test-Path $stateHashPath)) {
        return ""
    }
    try {
        return (Get-Content $stateHashPath -Raw -Encoding UTF8).Trim().ToLowerInvariant()
    } catch {
        return ""
    }
}

function Write-StoredDependencyHash {
    param([string]$HashValue)
    Set-Content -LiteralPath $stateHashPath -Value $HashValue -Encoding UTF8
}

function Add-Candidate {
    param(
        [System.Collections.Generic.List[string]]$List,
        [hashtable]$Seen,
        [string]$PathValue
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return
    }

    if (-not $Seen.ContainsKey($PathValue) -and (Test-Path $PathValue)) {
        $Seen[$PathValue] = $true
        $List.Add($PathValue) | Out-Null
    }
}

function Get-BasePythonCandidates {
    $seen = @{}
    $candidates = [System.Collections.Generic.List[string]]::new()

    Add-Candidate -List $candidates -Seen $seen -PathValue (Get-PyvenvValue -Key "executable")

    $homePath = Get-PyvenvValue -Key "home"
    if ($homePath) {
        Add-Candidate -List $candidates -Seen $seen -PathValue (Join-Path $homePath "python.exe")
    }

    $localAppData = $env:LOCALAPPDATA
    if ($localAppData) {
        foreach ($version in @("Python312", "Python311", "Python310")) {
            Add-Candidate -List $candidates -Seen $seen -PathValue (Join-Path $localAppData "Programs\\Python\\$version\\python.exe")
        }
    }

    foreach ($commandName in @("py", "python")) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop
            if ($command.Source -and $command.Source.ToLowerInvariant().EndsWith(".exe")) {
                Add-Candidate -List $candidates -Seen $seen -PathValue $command.Source
            }
        } catch {
        }
    }

    return $candidates
}

function Find-WorkingBasePython {
    $attempts = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in Get-BasePythonCandidates) {
        $failure = Get-PythonProcessFailure -PythonExe $candidate -Arguments @("-c", "import sys")
        if ([string]::IsNullOrWhiteSpace($failure)) {
            return @{
                Python = $candidate
                Attempts = $attempts
            }
        }
        $attempts.Add("${candidate} => ${failure}") | Out-Null
    }

    return @{
        Python = $null
        Attempts = $attempts
    }
}

function Test-VenvHealthy {
    if (-not (Test-PythonProcess -PythonExe $venvPython -Arguments @("-c", "import sys"))) {
        return $false
    }

    if (-not (Test-PythonProcess -PythonExe $venvPython -Arguments @("-c", "import fastapi, uvicorn, pytest, httpx"))) {
        return $false
    }

    return $true
}

function Invoke-PythonChecked {
    param(
        [string]$PythonExe,
        [string[]]$Arguments,
        [string]$StepName,
        [string]$WorkingDirectory = ""
    )

    Write-Info $StepName
    $exitCode = 0
    if ($WorkingDirectory) {
        Push-Location $WorkingDirectory
    }
    try {
        & $PythonExe @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        if ($WorkingDirectory) {
            Pop-Location
        }
    }
    if ($exitCode -ne 0) {
        throw "Fallo durante: $StepName"
    }
}

function Recreate-Venv {
    param([string]$BasePython)

    if ([string]::IsNullOrWhiteSpace($BasePython) -or -not (Test-Path $BasePython)) {
        throw "No se encontro un Python base local para recrear el venv."
    }

    $resolvedRoot = (Resolve-Path $rootDir).Path
    if (Test-Path $venvDir) {
        $resolvedVenv = (Resolve-Path $venvDir).Path
        if (-not $resolvedVenv.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Ruta de venv fuera del workspace: $resolvedVenv"
        }

        Write-Info "Eliminando venv local roto."
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }

    Invoke-PythonChecked -PythonExe $BasePython -Arguments @("-m", "venv", $venvDir) -StepName "Creando venv local"
    Invoke-PythonChecked -PythonExe $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip") -StepName "Actualizando pip"

    if (Test-Path $pyprojectPath) {
        Invoke-PythonChecked -PythonExe $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]") -StepName "Instalando dependencias del proyecto" -WorkingDirectory $rootDir
        Write-StoredDependencyHash -HashValue (Get-ProjectDependencyHash)
    } else {
        throw "No se encontro pyproject.toml para instalar dependencias."
    }
}

function Install-ProjectDependencies {
    if (-not (Test-Path $pyprojectPath)) {
        throw "No se encontro pyproject.toml para instalar dependencias."
    }

    Invoke-PythonChecked -PythonExe $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]") -StepName "Reinstalando dependencias del proyecto" -WorkingDirectory $rootDir
    Write-StoredDependencyHash -HashValue (Get-ProjectDependencyHash)
}

try {
    $currentDependencyHash = Get-ProjectDependencyHash
    $needsRecreate = $ForceRecreate
    $venvLauncherFailure = ""

    if ($needsRecreate) {
        $basePython = $null
    } elseif (-not (Test-PythonProcess -PythonExe $venvPython -Arguments @("-c", "import sys"))) {
        # Venv python is missing or broken — must recreate.
        $needsRecreate = $true
        $basePython = $null
        $venvLauncherFailure = Get-PythonProcessFailure -PythonExe $venvPython -Arguments @("-c", "import sys")
    } elseif ((Read-StoredDependencyHash) -eq $currentDependencyHash) {
        # Fast path: hash matches — venv python exists and deps are up-to-date.
        # Skip the expensive import health check on every start_ide call.
        $needsRecreate = $false
    } elseif (-not (Test-VenvHealthy)) {
        # Hash changed AND venv is unhealthy — reinstall and check recovery.
        Install-ProjectDependencies
        $needsRecreate = -not (Test-VenvHealthy)
    } else {
        # Hash changed but venv is still healthy — reinstall deps.
        Install-ProjectDependencies
    }

    if ($needsRecreate) {
        $basePython = $null
        $basePythonResult = Find-WorkingBasePython
        $basePython = $basePythonResult.Python

        if (-not $basePython) {
            $details = [System.Collections.Generic.List[string]]::new()
            if (-not [string]::IsNullOrWhiteSpace($venvLauncherFailure)) {
                $details.Add("launcher_venv: $venvLauncherFailure") | Out-Null
            }
            foreach ($attempt in $basePythonResult.Attempts) {
                $details.Add($attempt) | Out-Null
            }
            $detailText = if ($details.Count -gt 0) {
                " Detalles: " + ($details -join " | ")
            } else {
                ""
            }
            throw "No se encontro un Python base utilizable o el entorno deniega su ejecucion.$detailText"
        }

        Recreate-Venv -BasePython $basePython
    }

    # After any recreate/reinstall, verify health and persist the hash.
    if ($needsRecreate) {
        if (-not (Test-VenvHealthy)) {
            throw "El venv local sigue sin estar sano despues de la reparacion."
        }
        Write-StoredDependencyHash -HashValue $currentDependencyHash
    }

    if ($PrintPython) {
        Write-Output $venvPython
    } else {
        Write-Info "Venv local listo: $venvPython"
    }
} catch {
    if (-not $Quiet -or $ReportErrors) {
        Write-Error $_
    }
    exit 1
}
