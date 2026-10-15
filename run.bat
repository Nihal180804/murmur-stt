@echo off
REM Launch Murmur (no console window) using the local venv if present.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" murmur.py
) else (
    start "" pythonw murmur.py
)
