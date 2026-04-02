@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0.."

echo [prepare_dev_env] Validando runtime local...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_local_runtime.ps1"
if errorlevel 1 exit /b 1

echo [prepare_dev_env] Validando venv local...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_local_venv.ps1"
if errorlevel 1 exit /b 1

echo [prepare_dev_env] Validando dependencias frontend...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_frontend_deps.ps1"
if errorlevel 1 exit /b 1

echo [prepare_dev_env] Entorno local listo.
endlocal
exit /b 0
