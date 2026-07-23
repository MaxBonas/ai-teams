@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"

rem PowerShell se lanza explicitamente: nunca se depende de la asociacion .ps1.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare_dev_env.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
