[CmdletBinding()]
param(
    [string]$SettingsFile = "",
    [string[]]$PythonPath = @(),
    [switch]$Apply
)

# Utilidad opcional para instalaciones que necesiten excluir Python de NordVPN.
# Por defecto solo muestra el plan. La escritura exige -Apply y PowerShell admin.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $SettingsFile) {
    $SettingsRoot = Join-Path $env:ProgramData "NordVPN\settings"
    $SettingsFile = Get-ChildItem -LiteralPath $SettingsRoot -Filter "*.json" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $SettingsFile -or -not (Test-Path -LiteralPath $SettingsFile -PathType Leaf)) {
    throw "No se encontró un settings JSON de NordVPN. Indícalo con -SettingsFile."
}

$Candidates = [System.Collections.Generic.List[string]]::new()
foreach ($Path in $PythonPath) {
    if ($Path) {
        $Candidates.Add($Path)
    }
}
$Candidates.Add((Join-Path $RepoRoot "venv\Scripts\python.exe"))
foreach ($CommandName in @("python.exe", "py.exe")) {
    $Resolved = Get-Command $CommandName -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if ($Resolved) {
        $Candidates.Add($Resolved)
    }
}

$ValidApps = @(
    $Candidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
        ForEach-Object { (Resolve-Path -LiteralPath $_).Path } |
        Sort-Object -Unique
)
if ($ValidApps.Count -eq 0) {
    throw "No se encontró ningún ejecutable Python válido para excluir."
}

$Json = Get-Content -LiteralPath $SettingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Json.SettingsDto) {
    throw "El JSON no contiene SettingsDto; no se modificará."
}
$ExistingApps = @($Json.SettingsDto.SplitTunnelingApps | Where-Object { $_ })
$MergedApps = @($ExistingApps + $ValidApps | Sort-Object -Unique)

Write-Host "NordVPN split tunneling (utilidad opcional)" -ForegroundColor Cyan
Write-Host "Settings: $SettingsFile"
Write-Host "Se conservarán $($ExistingApps.Count) exclusiones existentes."
Write-Host "Python detectado:"
$ValidApps | ForEach-Object { Write-Host "  $_" }

if (-not $Apply) {
    Write-Host "Dry-run: no se cambió nada. Repite desde PowerShell admin con -Apply." -ForegroundColor Yellow
    exit 0
}

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "-Apply requiere PowerShell ejecutado como administrador."
}

$BackupFile = "$SettingsFile.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Copy-Item -LiteralPath $SettingsFile -Destination $BackupFile
Write-Host "Backup recuperable: $BackupFile" -ForegroundColor Green

$ServiceStopped = $false
try {
    $Service = Get-Service -Name "nordvpn-service" -ErrorAction Stop
    if ($Service.Status -ne "Stopped") {
        Stop-Service -Name "nordvpn-service" -Force
        $Service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(15))
        $ServiceStopped = $true
    }

    $Json.SettingsDto.IsSplitTunnelingEnabled = $true
    $Json.SettingsDto.SplitTunnelingMode = "vpnDisabledForApps"
    $Json.SettingsDto.SplitTunnelingApps = $MergedApps
    $Json | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath $SettingsFile -Encoding UTF8
}
catch {
    Copy-Item -LiteralPath $BackupFile -Destination $SettingsFile -Force
    throw "No se pudo aplicar la configuración; se restauró el backup. $($_.Exception.Message)"
}
finally {
    if ($ServiceStopped) {
        Start-Service -Name "nordvpn-service"
    }
}

Write-Host "Configuración aplicada; exclusiones totales: $($MergedApps.Count)." -ForegroundColor Green
