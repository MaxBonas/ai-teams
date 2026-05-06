# nordvpn_split_tunnel.ps1
# Configura NordVPN Split Tunneling para excluir Python del VPN
# Ejecutar como Administrador: Right-click > Run as Administrator
# O desde PowerShell admin: .\scripts\nordvpn_split_tunnel.ps1

$ErrorActionPreference = "Stop"
$SettingsFile = "C:\ProgramData\NordVPN\settings\2B256B1C.json"

# Apps a excluir del VPN (usarán conexión directa, sin VPN)
$AppsToExclude = @(
    "C:\Python312\python.exe",
    "C:\Users\Max\Antigravity Projects\Ai_Teams\venv\Scripts\python.exe",
    "C:\Windows\py.exe"
)

Write-Host "`nNordVPN Split Tunnel Setup" -ForegroundColor Cyan
Write-Host "==========================" -ForegroundColor Cyan

# Verificar que el archivo existe
if (-not (Test-Path $SettingsFile)) {
    Write-Host "ERROR: No se encuentra $SettingsFile" -ForegroundColor Red
    exit 1
}

# Backup
$BackupFile = "$SettingsFile.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Copy-Item $SettingsFile $BackupFile
Write-Host "Backup creado: $BackupFile" -ForegroundColor Green

# Leer config actual
$json = Get-Content $SettingsFile -Raw | ConvertFrom-Json

# Mostrar estado actual
Write-Host "`nEstado actual:" -ForegroundColor Yellow
Write-Host "  IsSplitTunnelingEnabled: $($json.SettingsDto.IsSplitTunnelingEnabled)"
Write-Host "  SplitTunnelingMode:      $($json.SettingsDto.SplitTunnelingMode)"
Write-Host "  SplitTunnelingApps:      $($json.SettingsDto.SplitTunnelingApps.Count) apps"

# Detener servicio NordVPN
Write-Host "`nDeteniendo nordvpn-service..." -ForegroundColor Yellow
try {
    Stop-Service -Name "nordvpn-service" -Force
    Start-Sleep -Seconds 2
    Write-Host "Servicio detenido." -ForegroundColor Green
} catch {
    Write-Host "AVISO: No se pudo detener el servicio: $_" -ForegroundColor Yellow
    Write-Host "Intentando editar de todas formas..." -ForegroundColor Yellow
}

# Filtrar solo los ejecutables que existen
$ValidApps = $AppsToExclude | Where-Object { Test-Path $_ }
Write-Host "`nApps validas encontradas:" -ForegroundColor Cyan
$ValidApps | ForEach-Object { Write-Host "  $_" -ForegroundColor White }

# Aplicar configuracion
$json.SettingsDto.IsSplitTunnelingEnabled = $true
$json.SettingsDto.SplitTunnelingMode = "vpnDisabledForApps"
$json.SettingsDto.SplitTunnelingApps = $ValidApps

# Guardar
$json | ConvertTo-Json -Depth 10 | Set-Content $SettingsFile -Encoding UTF8
Write-Host "`nConfiguracion guardada." -ForegroundColor Green

# Reiniciar servicio
Write-Host "Reiniciando nordvpn-service..." -ForegroundColor Yellow
try {
    Start-Service -Name "nordvpn-service"
    Start-Sleep -Seconds 3
    $status = (Get-Service -Name "nordvpn-service").Status
    Write-Host "Servicio: $status" -ForegroundColor Green
} catch {
    Write-Host "ERROR reiniciando servicio: $_" -ForegroundColor Red
    Write-Host "Reinicia manualmente el servicio NordVPN." -ForegroundColor Yellow
}

Write-Host "`nListo! Python ahora bypasea el VPN." -ForegroundColor Green
Write-Host "Prueba providers desde el futuro adapter registry v2 cuando este conectado a ejecucion real." -ForegroundColor Cyan
Write-Host "`nSi no funciona, hazlo manual en NordVPN > Ajustes > Split Tunneling:" -ForegroundColor Yellow
Write-Host "  1. Activar Split Tunneling" -ForegroundColor White
Write-Host "  2. Modo: 'Deshabilitar VPN para apps seleccionadas'" -ForegroundColor White
Write-Host "  3. Añadir: python.exe, python3.exe, py.exe" -ForegroundColor White
