@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"

rem ── AI Team IDE launcher ──────────────────────────────────────────────────
rem  Backend  : Python uvicorn  → http://localhost:8010  (api.main:app --reload)
rem  Frontend : Vite dev server → http://localhost:9490  (ide-frontend)
rem  Logs     : runtime\ide_logs\  (backend.log / frontend.log)
rem  Stop     : stop_ide.bat
rem  Note     : --reload restarts the heartbeat on file changes; stale runs are
rem             recovered automatically via reconcile_stale_runs() on startup.
rem ─────────────────────────────────────────────────────────────────────────

set "ROOT_DIR=%~dp0"
set "BACKEND_PORT=8010"
set "FRONTEND_PORT=9490"
set "VITE_API_URL=http://127.0.0.1:%BACKEND_PORT%"
set "LOG_DIR=%ROOT_DIR%runtime\ide_logs"
set "BACKEND_LOG=%LOG_DIR%\backend.log"
set "BACKEND_ERR_LOG=%LOG_DIR%\backend.err.log"
set "FRONTEND_LOG=%LOG_DIR%\frontend.log"
set "FRONTEND_ERR_LOG=%LOG_DIR%\frontend.err.log"

pushd "%ROOT_DIR%" >nul 2>nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se puede acceder al directorio del proyecto.
    exit /b 1
)

call :prepare_environment || goto :fail
call :resolve_python || goto :fail
call :resolve_npm    || goto :fail
call :ensure_log_dir || goto :fail
call :assert_registry_clear || goto :fail
call :assert_port_free %BACKEND_PORT% || goto :fail
call :assert_port_free %FRONTEND_PORT% || goto :fail

set "BACKEND_PID="
set "FRONTEND_PID="

echo [AI Team IDE] Arrancando backend en puerto %BACKEND_PORT%...
set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"
set "START_WD=%ROOT_DIR%"
set "START_EXE=%PYTHON_EXE%"
set "START_ARGS=-m uvicorn api.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"
set "START_STDOUT=%BACKEND_LOG%"
set "START_STDERR=%BACKEND_ERR_LOG%"
call :start_process "backend" BACKEND_PID || goto :startup_failed
"%PYTHON_EXE%" "%ROOT_DIR%scripts\ide_processes.py" register-one --role backend --pid %BACKEND_PID% --port %BACKEND_PORT% >nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se pudo registrar el backend.
    goto :startup_failed
)

echo [AI Team IDE] Arrancando frontend en puerto %FRONTEND_PORT%...
set "VITE_API_URL=%VITE_API_URL%"
set "START_WD=%ROOT_DIR%ide-frontend"
set "START_EXE=%NPM_CMD%"
set "START_ARGS=run dev -- --host 0.0.0.0 --port %FRONTEND_PORT% --strictPort"
set "START_STDOUT=%FRONTEND_LOG%"
set "START_STDERR=%FRONTEND_ERR_LOG%"
call :start_process "frontend" FRONTEND_PID || goto :startup_failed
"%PYTHON_EXE%" "%ROOT_DIR%scripts\ide_processes.py" register-one --role frontend --pid %FRONTEND_PID% --port %FRONTEND_PORT% >nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se pudo registrar el frontend.
    goto :startup_failed
)

echo [AI Team IDE] Esperando servicios...
call :wait_backend_ready
if errorlevel 1 goto :startup_failed

call :wait_frontend_ready
if errorlevel 1 goto :startup_failed

echo.
echo [AI Team IDE] ============================================
echo [AI Team IDE]  Backend  -^>  http://localhost:%BACKEND_PORT%
echo [AI Team IDE]  Frontend -^>  http://localhost:%FRONTEND_PORT%
echo [AI Team IDE]  Logs     -^>  runtime\ide_logs\
echo [AI Team IDE]  Para todo con stop_ide.bat
echo [AI Team IDE] ============================================
echo.
if /I not "%AITEAM_NO_BROWSER%"=="1" powershell.exe -NoProfile -Command "Start-Process -FilePath 'http://localhost:%FRONTEND_PORT%'" >nul 2>nul
goto :success

:prepare_environment
call "%ROOT_DIR%scripts\prepare_dev_env.bat"
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se pudo preparar el entorno local.
    exit /b 1
)
exit /b 0

:ensure_log_dir
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se pudo crear %LOG_DIR%
    exit /b 1
)
exit /b 0

:resolve_python
set "PYTHON_EXE=%ROOT_DIR%venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [AI Team IDE] ERROR: falta el Python local despues del bootstrap.
    exit /b 1
)
echo [AI Team IDE] Usando Python local del proyecto.
exit /b 0

:resolve_npm
set "NPM_CMD="
for /f "delims=" %%i in ('where npm.cmd 2^>nul') do if not defined NPM_CMD set "NPM_CMD=%%i"
if not defined NPM_CMD for /f "delims=" %%i in ('where npm 2^>nul') do if not defined NPM_CMD set "NPM_CMD=%%i"
if not defined NPM_CMD (
    echo [AI Team IDE] ERROR: npm no encontrado. Instala Node.js.
    exit /b 1
)
echo [AI Team IDE] Usando npm en %NPM_CMD%
exit /b 0

:start_process
set "STARTED_PID="
set "START_PID_FILE=%LOG_DIR%\.%~1.pid.tmp"
if exist "%START_PID_FILE%" del /q "%START_PID_FILE%" >nul 2>nul
powershell.exe -NoProfile -Command "$p = Start-Process -FilePath $env:START_EXE -ArgumentList $env:START_ARGS -WorkingDirectory $env:START_WD -RedirectStandardOutput $env:START_STDOUT -RedirectStandardError $env:START_STDERR -WindowStyle Hidden -PassThru; if ($p) { Set-Content -LiteralPath $env:START_PID_FILE -Value $p.Id -Encoding ASCII } else { exit 1 }" >nul 2>nul
if exist "%START_PID_FILE%" set /p STARTED_PID=<"%START_PID_FILE%"
if exist "%START_PID_FILE%" del /q "%START_PID_FILE%" >nul 2>nul
if not defined STARTED_PID (
    echo [AI Team IDE] ERROR: No se pudo obtener el PID de %~1.
    exit /b 1
)
set "%~2=%STARTED_PID%"
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se pudo lanzar %~1.
    echo [AI Team IDE] Revisa %START_STDOUT% y %START_STDERR%
    exit /b 1
)
exit /b 0

:assert_registry_clear
"%PYTHON_EXE%" "%ROOT_DIR%scripts\ide_processes.py" assert-clear >nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: ya existe una sesion propia viva o un registro invalido.
    echo [AI Team IDE] Ejecuta stop_ide.bat y revisa runtime\ide_processes.json.
    exit /b 1
)
exit /b 0

:assert_port_free
powershell.exe -NoProfile -Command "$listener=$null; try { $listener=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,%~1); $listener.Start(); exit 0 } catch { exit 1 } finally { if($null -ne $listener){$listener.Stop()} }" >nul 2>nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: el puerto %~1 esta ocupado por otro proceso; no se terminara automaticamente.
    exit /b 1
)
exit /b 0

:wait_backend_ready
powershell -NoProfile -Command "$deadline = (Get-Date).AddSeconds(40); while ((Get-Date) -lt $deadline) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%BACKEND_PORT%/openapi.json' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ge 200) { exit 0 } } catch {} Start-Sleep -Seconds 1 }; exit 1" >nul 2>nul
if errorlevel 1 echo [AI Team IDE] Timeout esperando backend: http://127.0.0.1:%BACKEND_PORT%/openapi.json
exit /b %errorlevel%

:wait_frontend_ready
powershell -NoProfile -Command "$deadline = (Get-Date).AddSeconds(40); while ((Get-Date) -lt $deadline) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%FRONTEND_PORT%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ge 200) { exit 0 } } catch {} Start-Sleep -Seconds 1 }; exit 1" >nul 2>nul
if errorlevel 1 echo [AI Team IDE] Timeout esperando frontend: http://127.0.0.1:%FRONTEND_PORT%
exit /b %errorlevel%

:startup_failed
echo.
echo [AI Team IDE] ERROR: Alguno de los servicios no arranco.
echo [AI Team IDE] Revisa runtime\ide_logs\backend*.log y frontend*.log.
if exist "%ROOT_DIR%runtime\ide_processes.json" (
    "%PYTHON_EXE%" "%ROOT_DIR%scripts\ide_processes.py" stop >nul 2>nul
) else (
    if defined FRONTEND_PID taskkill.exe /F /T /PID %FRONTEND_PID% >nul 2>nul
    if defined BACKEND_PID taskkill.exe /F /T /PID %BACKEND_PID% >nul 2>nul
)
goto :fail

:success
popd >nul
endlocal
exit /b 0

:fail
popd >nul
endlocal
exit /b 1
