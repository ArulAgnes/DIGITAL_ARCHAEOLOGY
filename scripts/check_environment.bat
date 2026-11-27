@echo off
setlocal EnableExtensions
rem ============================================================
rem  Digital Archaeology - environment check (read-only).
rem  Reports Python, virtual environment, dependencies,
rem  workspace configuration and directory access.
rem ============================================================
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
pushd "%ROOT%" || (echo [ERROR] cannot access project root. & exit /b 1)
set "PY=%CD%\.venv\Scripts\python.exe"

if exist "%PY%" (
    echo [OK] using virtual environment: %CD%\.venv
) else (
    echo [WARN] virtual environment not found - checking with system Python.
    set "PY=python"
)

"%PY%" "%SCRIPT_DIR%check_environment.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ACTION REQUIRED] Fix the items marked [MISSING] above.
    echo                   Usually: scripts\install_windows.bat
)
popd
exit /b %RC%
