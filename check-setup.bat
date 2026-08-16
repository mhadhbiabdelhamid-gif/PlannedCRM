@echo off
REM Double-click this if the CRM will not start. It checks everything and
REM explains what is wrong. The window stays open so you can read it.
cd /d "%~dp0"
echo Checking your setup...
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" diagnose.py
) else (
    python diagnose.py
)
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   Python could not be started at all.
    echo.
    echo   That means Python is either not installed, or was
    echo   installed without "Add Python to PATH" ticked.
    echo.
    echo   Fix: go to python.org/downloads, install Python 3.12,
    echo   and TICK "Add python.exe to PATH" on the first screen.
    echo   Then run this file again.
    echo ============================================================
)
echo.
pause
