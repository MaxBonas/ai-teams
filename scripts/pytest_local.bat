@echo off
setlocal EnableExtensions
set "PYTHONDONTWRITEBYTECODE=1"

call "%~dp0python_local.bat" "%~dp0cleanup_test_artifacts.py"
call "%~dp0python_local.bat" -m pytest -p no:cacheprovider %*
set "EXIT_CODE=%ERRORLEVEL%"
call "%~dp0python_local.bat" "%~dp0cleanup_test_artifacts.py"
endlocal & exit /b %EXIT_CODE%
