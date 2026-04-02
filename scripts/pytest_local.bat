@echo off
setlocal EnableExtensions

call "%~dp0python_local.bat" -m pytest %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
