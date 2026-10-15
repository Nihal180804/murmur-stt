@echo off
REM One-time setup: create a virtual environment, install deps, generate the logo.
cd /d "%~dp0"

echo Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo Failed to create venv. Make sure Python 3.9-3.12 is installed and on PATH.
    pause
    exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency install failed.
    pause
    exit /b 1
)

echo Generating logo...
".venv\Scripts\python.exe" make_icon.py

echo.
echo Done. Start the app with run.bat
pause
