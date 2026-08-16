@echo off
REM Run this AFTER moving the project folder somewhere new.
REM It clears anything tied to the old location and sets things up again.
cd /d "%~dp0"
echo.
echo  Repairing the CRM for its new location:
echo  %~dp0
echo.

echo  1. Stopping anything still running from the old folder...
schtasks /End /TN "PlannedRealEstateCRM" >nul 2>&1
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM wscript.exe /T >nul 2>&1

echo  2. Removing the old virtual environment...
if exist ".venv" rmdir /s /q ".venv"

echo  3. Rebuilding it here...
python -m venv .venv
if errorlevel 1 (
    echo.
    echo   Python could not be started. Install it from python.org and tick
    echo   "Add python.exe to PATH", then run this again.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
pip install -r requirements.txt

echo.
echo  Done. Two things left, if you were using them before:
echo    - Right-click install-autostart.bat, Run as administrator
echo    - Double-click create-desktop-shortcut.bat
echo.
echo  Your data in the instance folder moved with you and is untouched.
echo.
pause
