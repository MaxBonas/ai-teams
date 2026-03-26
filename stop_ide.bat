@echo off
setlocal EnableExtensions

set "BACKEND_PORT=8010"
set "FRONTEND_PORT=9490"

echo [AI Team IDE] Stopping services...
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%
call :kill_signature "uvicorn api.main:app"
call :kill_signature "vite --port %FRONTEND_PORT%"
echo [AI Team IDE] Done.
endlocal
exit /b 0

:kill_port
set "_port=%~1"
set "_killed=0"
for /f "tokens=5" %%p in ('netstat -aon ^| findstr /R /C:":%_port% "') do (
    if not "%%p"=="" if not "%%p"=="0" (
        taskkill /F /T /PID %%p >nul 2>nul
        if not errorlevel 1 (
            set "_killed=1"
            echo [AI Team IDE] Killed PID %%p on port %_port%.
        )
    )
)
if "%_killed%"=="0" echo [AI Team IDE] No process found on port %_port%.
exit /b 0

:kill_signature
set "_signature=%~1"
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $sig='%_signature%'; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like ('*' + $sig + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
exit /b 0
