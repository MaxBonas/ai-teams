@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "PYTHON_EXE="

for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%ensure_local_venv.ps1" -PrintPython -Quiet 2^>nul`) do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
)

if not defined PYTHON_EXE (
    echo [python_local] ERROR: no se pudo preparar el venv local.
    exit /b 1
)

"%PYTHON_EXE%" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
