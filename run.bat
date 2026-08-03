@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   Report2Statistics Launch Script
echo ==================================================
echo.

:: [1/5] Check Python
echo [1/5] Checking Python environment...
py -3.13 --version >nul 2>&1
if !errorlevel! equ 0 (
    set PYTHON_CMD=py -3.13
    echo Found Python 3.13 via launcher.
) else (
    python --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo Error: Python not found!
        echo Please install Python 3.11 or higher from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set PYTHON_CMD=python
    echo Found default system Python.
)

:: [2/5] Check/create folders
echo [2/5] Verifying directory structure...
if not exist input mkdir input
if not exist output mkdir output
if not exist local_mem mkdir local_mem
echo Folders 'input/', 'output/', and 'local_mem/' are ready.

:: [3/5] Check/install orchestrator dependencies
echo [3/5] Checking orchestrator dependencies...
!PYTHON_CMD! -c "import fastmcp, fitz, matplotlib, pptx, google.genai, dotenv" >nul 2>&1
if !errorlevel! neq 0 (
    echo Missing dependencies. Installing via requirements.txt...
    !PYTHON_CMD! -m pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo Error: Dependency installation failed.
        echo Please run: !PYTHON_CMD! -m pip install -r requirements.txt manually.
        pause
        exit /b 1
    )
)
echo All dependencies verified.

:: [4/5] Synchronize optional scan-agent files
echo [4/5] Synchronizing optional scan-agent files...
if exist scan-agent (
    !PYTHON_CMD! agents/setup_agent.py >nul
)

:: [5/5] Launch
echo [5/5] Launching Orchestrator Server...
echo.
echo Clearing any existing server running on port 8080...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8080 ^| findstr LISTENING') do (
    taskkill /f /pid %%a >nul 2>&1
)
echo Starting orchestrator in a dedicated window...
start "Report2Plot Orchestrator" /D "%~dp0" cmd /c "!PYTHON_CMD! agents/orchestrator.py --watch"

set APP_URL=
for /f "usebackq delims=" %%U in (`!PYTHON_CMD! agents/wait_for_server.py`) do (
    set APP_URL=%%U
)

if not defined APP_URL (
    echo Error: Orchestrator did not become ready on ports 8080-8179.
    echo Check the "Report2Plot Orchestrator" window for Python errors.
    pause
    exit /b 1
)

:server_ready
echo Orchestrator ready at !APP_URL!
echo Opening browser...
start "" "!APP_URL!"

set HISTORY_URL=!APP_URL:login_page.html=history_page.html!
if exist local_mem\*.json (
    echo Opening assignment history in a new browser tab...
    start "" "!HISTORY_URL!"
)

echo.
echo ==================================================
echo   Report2Plot launched successfully.
echo   The orchestrator is running in its own window.
echo ==================================================
echo.
exit /b 0
