@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0"
set "BACKEND_PORT=8010"
set "FRONTEND_PORT=9490"
set "VITE_API_URL=http://127.0.0.1:%BACKEND_PORT%"

pushd "%ROOT_DIR%" >nul 2>nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: No se puede acceder al directorio del proyecto.
    exit /b 1
)

call :resolve_python || goto :fail
call :resolve_npm    || goto :fail
call :ensure_frontend_deps || goto :fail

echo [AI Team IDE] Liberando puertos %BACKEND_PORT% y %FRONTEND_PORT%...
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%

:: ── Backend: /D establece working dir sin necesidad de cd con rutas con espacios
echo [AI Team IDE] Arrancando backend en puerto %BACKEND_PORT%...
start "AI Team Backend" /D "%ROOT_DIR%" cmd /k ""%PYTHON_EXE%" -m uvicorn api.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"

:: ── Frontend: npm esta en PATH, solo necesitamos el working dir correcto
echo [AI Team IDE] Arrancando frontend en puerto %FRONTEND_PORT%...
start "AI Team Frontend" /D "%ROOT_DIR%ide-frontend" cmd /k "set VITE_API_URL=%VITE_API_URL%&& npm run dev -- --host 0.0.0.0 --port %FRONTEND_PORT% --strictPort"

:: ── Esperar a que ambos respondan ────────────────────────────────────────────
echo [AI Team IDE] Esperando servicios...
call :wait_for_http "http://127.0.0.1:%BACKEND_PORT%/openapi.json" 40 "backend"
if errorlevel 1 goto :startup_failed

call :wait_for_http "http://127.0.0.1:%FRONTEND_PORT%" 40 "frontend"
if errorlevel 1 goto :startup_failed

echo.
echo [AI Team IDE] ============================================
echo [AI Team IDE]  Backend  -^>  http://localhost:%BACKEND_PORT%
echo [AI Team IDE]  Frontend -^>  http://localhost:%FRONTEND_PORT%
echo [AI Team IDE]  Cierra las dos ventanas para parar todo.
echo [AI Team IDE] ============================================
echo.
start "" "http://localhost:%FRONTEND_PORT%"
goto :success

:: ─────────────────────────────────────────────────────────────────────────────

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
where npm >nul 2>nul
if errorlevel 1 (
    echo [AI Team IDE] ERROR: npm no encontrado. Instala Node.js.
    exit /b 1
)
exit /b 0

:python_has_uvicorn
"%~1" -c "import uvicorn" >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0

:kill_port
for /f "tokens=5" %%p in ('netstat -aon ^| findstr /R /C:":%~1 "') do (
    if not "%%p"=="" if not "%%p"=="0" (
        taskkill /F /T /PID %%p >nul 2>nul
    )
)
exit /b 0

:wait_for_http
set "_url=%~1"
set "_remaining=%~2"
set "_label=%~3"
:wait_loop
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%_url%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ge 200) { exit 0 } } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 exit /b 0
set /a _remaining-=1
if %_remaining% LEQ 0 (
    echo [AI Team IDE] Timeout esperando %_label%: %_url%
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_loop

:startup_failed
echo.
echo [AI Team IDE] ERROR: Alguno de los servicios no arranco.
echo [AI Team IDE] Revisa las ventanas de Backend y Frontend para ver el error.
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
