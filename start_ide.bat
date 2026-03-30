@echo off
setlocal EnableExtensions

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

call :resolve_python || goto :fail
call :resolve_npm    || goto :fail
call :ensure_frontend_deps || goto :fail
call :ensure_log_dir || goto :fail

echo [AI Team IDE] Liberando puertos %BACKEND_PORT% y %FRONTEND_PORT%...
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%

echo [AI Team IDE] Arrancando backend en puerto %BACKEND_PORT%...
set "PYTHONUNBUFFERED=1"
set "START_WD=%ROOT_DIR%"
set "START_EXE=%PYTHON_EXE%"
set "START_ARGS=-m uvicorn api.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"
set "START_STDOUT=%BACKEND_LOG%"
set "START_STDERR=%BACKEND_ERR_LOG%"
call :start_process "backend" || goto :fail

echo [AI Team IDE] Arrancando frontend en puerto %FRONTEND_PORT%...
set "VITE_API_URL=%VITE_API_URL%"
set "START_WD=%ROOT_DIR%ide-frontend"
set "START_EXE=%NPM_CMD%"
set "START_ARGS=run dev -- --host 0.0.0.0 --port %FRONTEND_PORT% --strictPort"
set "START_STDOUT=%FRONTEND_LOG%"
set "START_STDERR=%FRONTEND_ERR_LOG%"
call :start_process "frontend" || goto :fail

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
start "" "http://localhost:%FRONTEND_PORT%"
goto :success

:ensure_frontend_deps
if not exist "%ROOT_DIR%ide-frontend\node_modules" (
    echo [AI Team IDE] node_modules no encontrado, ejecutando npm install...
    pushd "%ROOT_DIR%ide-frontend" >nul
    npm install --prefer-offline --no-fund --no-audit
    if errorlevel 1 (
        popd >nul
        echo [AI Team IDE] ERROR: npm install fallido.
        exit /b 1
    )
    popd >nul
    echo [AI Team IDE] Dependencias instaladas.
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
if exist "%ROOT_DIR%venv\Scripts\python.exe" (
    call :python_has_uvicorn "%ROOT_DIR%venv\Scripts\python.exe"
    if not errorlevel 1 (
        set "PYTHON_EXE=%ROOT_DIR%venv\Scripts\python.exe"
        echo [AI Team IDE] Usando venv Python.
        exit /b 0
    )
    echo [AI Team IDE] WARNING: venv Python encontrado pero uvicorn no instalado.
)
where python >nul 2>nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: Python no encontrado. Instala Python o crea venv en .\venv
    exit /b 1
)
set "PYTHON_EXE=python"
call :python_has_uvicorn "%PYTHON_EXE%"
if errorlevel 1 (
    echo [AI Team IDE] ERROR: uvicorn no encontrado. Ejecuta: pip install uvicorn
    exit /b 1
)
echo [AI Team IDE] Usando Python del PATH.
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

:python_has_uvicorn
"%~1" -c "import uvicorn" >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0

:start_process
powershell -NoProfile -Command "$p = Start-Process -FilePath $env:START_EXE -ArgumentList $env:START_ARGS -WorkingDirectory $env:START_WD -RedirectStandardOutput $env:START_STDOUT -RedirectStandardError $env:START_STDERR -PassThru; if ($p) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se pudo lanzar %~1.
    echo [AI Team IDE] Revisa %START_STDOUT% y %START_STDERR%
    exit /b 1
)
exit /b 0

:kill_port
for /f "tokens=5" %%p in ('netstat -aon ^| findstr /R /C:":%~1 "') do (
    if not "%%p"=="" if not "%%p"=="0" (
        taskkill /F /T /PID %%p >nul 2>nul
    )
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
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%
goto :fail

:success
popd >nul
endlocal
exit /b 0

:fail
popd >nul
endlocal
exit /b 1
