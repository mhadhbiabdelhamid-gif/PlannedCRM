@echo off
REM Starts the CRM for the office. Safe to run after moving the folder:
REM a virtual environment left behind by a different location is rebuilt.
cd /d "%~dp0"

set NEEDVENV=0
if not exist ".venv\Scripts\python.exe" set NEEDVENV=1

REM A moved .venv still has the old path baked in, so test it before trusting it.
if "%NEEDVENV%"=="0" (
    ".venv\Scripts\python.exe" -c "import flask, waitress" >nul 2>&1
    if errorlevel 1 (
        echo The virtual environment is from the old folder location. Rebuilding...
        rmdir /s /q ".venv"
        set NEEDVENV=1
    )
)

if "%NEEDVENV%"=="1" (
    echo Setting up. This takes a minute the first time...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo   Python could not be started. Install it from python.org and
        echo   tick "Add python.exe to PATH" on the first screen.
        echo.
        pause
        exit /b 1
    )
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip >nul 2>&1
    pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

python serve_office.py
pause
