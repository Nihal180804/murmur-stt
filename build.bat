@echo off
REM Build Murmur into a one-click installer.
REM   1. creates/uses .venv and installs deps + PyInstaller
REM   2. generates app.ico
REM   3. runs PyInstaller (onedir) -> dist\Murmur\
REM   4. runs Inno Setup (if installed) -> Output\Murmur-Setup.exe
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || (echo venv failed & pause & exit /b 1)
)
set PY=.venv\Scripts\python.exe

echo Installing dependencies...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt pyinstaller || (echo pip failed & pause & exit /b 1)

echo Generating icon...
"%PY%" make_icon.py

echo Running PyInstaller...
rmdir /s /q build dist 2>nul
"%PY%" -m PyInstaller Murmur.spec || (echo PyInstaller failed & pause & exit /b 1)

echo.
echo App built at dist\Murmur\Murmur.exe

REM --- Try to build the installer with Inno Setup ---------------------------
set ISCC=
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%P set ISCC=%%P

if defined ISCC (
    echo Building installer with Inno Setup...
    %ISCC% installer.iss || (echo Inno Setup failed & pause & exit /b 1)
    echo.
    echo Installer ready: Output\Murmur-Setup.exe
) else (
    echo.
    echo Inno Setup not found. Install it from https://jrsoftware.org/isdl.php
    echo then run:  ISCC.exe installer.iss
    echo The standalone app is already usable at dist\Murmur\Murmur.exe
)

echo.
pause
