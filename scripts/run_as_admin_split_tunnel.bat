@echo off
:: Auto-elevates el script de NordVPN split tunnel con UAC
powershell -NoProfile -Command "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%~dp0nordvpn_split_tunnel.ps1""' -Verb RunAs -Wait"
echo.
echo Hecho. Cierra esta ventana.
pause
