@echo off
setlocal EnableExtensions

call "%~dp0python_local.bat" "%~dp0pytest_local_stable.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
