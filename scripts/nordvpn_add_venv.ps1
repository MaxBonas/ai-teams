# nordvpn_add_venv.ps1 — Añade venv de AI Teams al split tunnel de NordVPN
# EJECUTAR COMO ADMINISTRADOR

$settingsPath = "C:\ProgramData\NordVPN\settings\2B256B1C.json"
$venvBase     = "C:\Users\Max\Antigravity Projects\Ai_Teams\venv\Scripts"

$newApps = @(
    @{
        Name        = "python"
        DisplayName = "Python (venv AI Teams)"
        Path        = "$venvBase\python.exe"
        StartupArgs = ""
        AppType     = "native"
        IconPath    = "$venvBase\python.exe"
    },
    @{
        Name        = "uvicorn"
        DisplayName = "uvicorn (AI Teams backend)"
        Path        = "$venvBase\uvicorn.exe"
        StartupArgs = ""
        AppType     = "native"
        IconPath    = "$venvBase\uvicorn.exe"
    }
)

# 1. Cerrar GUI de NordVPN para que no sobreescriba los cambios
Write-Host "Cerrando NordVPN GUI..."
Stop-Process -Name "NordVPN" -Force -ErrorAction SilentlyContinue
Start-Sleep 2

# 2. Detener servicio
Write-Host "Deteniendo nordvpn-service..."
Stop-Service nordvpn-service -Force -ErrorAction SilentlyContinue
Start-Sleep 2

# 3. Backup del settings
Copy-Item $settingsPath "$settingsPath.bak" -Force
Write-Host "Backup guardado: $settingsPath.bak"

# 4. Leer JSON y añadir apps
$json = Get-Content $settingsPath -Raw | ConvertFrom-Json
$apps = [System.Collections.Generic.List[object]]($json.SettingsDto.SplitTunnelingApps)

foreach ($app in $newApps) {
    $exists = $apps | Where-Object { $_.Path -eq $app.Path }
    if ($exists) {
        Write-Host "Ya existe: $($app.DisplayName)"
    } else {
        $apps.Add([PSCustomObject]$app)
        Write-Host "Añadido:   $($app.DisplayName)  ->  $($app.Path)"
    }
}

$json.SettingsDto.SplitTunnelingApps = $apps.ToArray()
$json | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
Write-Host ""
Write-Host "Total apps en split tunnel: $($apps.Count)"

# 5. Arrancar servicio
Write-Host "Arrancando nordvpn-service..."
Start-Service nordvpn-service
Start-Sleep 3

# 6. Abrir NordVPN GUI (leerá el JSON ya actualizado)
Write-Host "Abriendo NordVPN..."
Start-Process "C:\Program Files\NordVPN\NordVPN.exe"

Write-Host ""
Write-Host "Hecho. Reconecta la VPN en la app que acaba de abrirse."
