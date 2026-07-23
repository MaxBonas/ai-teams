@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "PYTHON_EXE="
set "ENSURE_LOG=%TEMP%\aiteam_ensure_local_venv_%RANDOM%_%RANDOM%.log"

for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%ensure_local_venv.ps1" -PrintPython -Quiet -ReportErrors 2^>"%ENSURE_LOG%"`) do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
)

if not defined PYTHON_EXE (
    echo [python_local] ERROR: no se pudo preparar el venv local.
    if exist "%ENSURE_LOG%" (
        type "%ENSURE_LOG%"
        del "%ENSURE_LOG%" >nul 2>nul
    )
    exit /b 1
)

if exist "%ENSURE_LOG%" del "%ENSURE_LOG%" >nul 2>nul
"%PYTHON_EXE%" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
