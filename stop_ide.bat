@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"

set "ROOT_DIR=%~dp0"
set "PYTHON_EXE=%ROOT_DIR%venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [AI Team IDE] ERROR: falta el Python local; no se detendran procesos sin verificar su identidad.
    endlocal
    exit /b 1
)

echo [AI Team IDE] Deteniendo solo procesos registrados por este checkout...
"%PYTHON_EXE%" "%ROOT_DIR%scripts\ide_processes.py" stop
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" echo [AI Team IDE] Procesos propios detenidos.
if not "%EXIT_CODE%"=="0" echo [AI Team IDE] ERROR: identidad no verificable; no se mato el proceso discrepante.
endlocal & exit /b %EXIT_CODE%
