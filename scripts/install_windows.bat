@echo off
setlocal EnableExtensions
rem ============================================================
rem  Digital Archaeology - Windows installation
rem  Non-destructive: never deletes an existing .venv and never
rem  touches research data (data\, reports\, logs\, backups\).
rem ============================================================
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
pushd "%ROOT%" || (echo [ERROR] cannot access project root. & exit /b 1)

echo ============================================
echo  Digital Archaeology - install
echo  Project: %CD%
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.11 or newer from
    echo         https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH". Then re-run this script.
    popd
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.11 or newer is required - tested with 3.11.9.
    echo         Install it from https://www.python.org/downloads/
    echo         and re-run this script.
    popd
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do set "PYVER=%%v"
echo [OK] %PYVER%

if exist ".venv\Scripts\python.exe" (
    echo [OK] existing virtual environment found - keeping it unchanged.
) else (
    echo [..] creating virtual environment in .venv
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] could not create the virtual environment.
        echo         Check disk space and folder permissions, then re-run.
        popd
        exit /b 1
    )
    echo [OK] virtual environment created.
)

echo [..] installing declared dependencies from requirements.txt
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] dependency installation failed.
    echo         Check your internet connection and re-run this script.
    echo         The existing virtual environment and all data files
    echo         were left untouched.
    popd
    exit /b 1
)

echo [OK] installation finished.
echo Next steps:
echo     scripts\check_environment.bat
echo     scripts\run_windows.bat
popd
exit /b 0
