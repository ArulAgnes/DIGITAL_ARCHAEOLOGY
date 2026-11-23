@echo off
setlocal EnableExtensions
rem ============================================================
rem  Digital Archaeology - launch the local dashboard.
rem  Starts Streamlit only. Never scans any folder or drive.
rem ============================================================
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
pushd "%ROOT%" || (echo [ERROR] cannot access project root. & exit /b 1)

if not exist "app.py" (
    echo [ERROR] app.py not found in %CD%.
    echo         Keep the scripts folder inside the project and re-run.
    popd
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] virtual environment missing.
    echo         Run scripts\install_windows.bat first.
    popd
    exit /b 1
)

echo [..] verifying Streamlit is installed
".venv\Scripts\python.exe" -c "import streamlit" 2>nul
if errorlevel 1 (
    echo [ERROR] streamlit is not installed in the virtual environment.
    echo         Run scripts\install_windows.bat first.
    popd
    exit /b 1
)

echo Project   : %CD%
if defined DIGITAL_ARCHAEOLOGY_HOME (
    echo Workspace : %DIGITAL_ARCHAEOLOGY_HOME - override active
) else (
    echo Workspace : %CD% - default
)
echo.
echo Starting the dashboard. Open the URL shown below in your browser:
echo     http://localhost:8501
echo Press Ctrl+C in this window to stop the application.
echo ============================================

".venv\Scripts\python.exe" -m streamlit run app.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Streamlit exited with code %RC%. Read the messages above.
)
popd
exit /b %RC%
