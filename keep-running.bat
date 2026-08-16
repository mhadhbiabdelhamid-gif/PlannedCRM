@echo off
REM ===================================================================
REM  Keeps the CRM running. If it ever stops, this restarts it in 5
REM  seconds. Everything it prints also goes to instance\server.log,
REM  so a crash can be read about afterwards.
REM
REM  Use install-autostart.bat to have Windows run this at startup.
REM ===================================================================
cd /d "%~dp0"
title Planned Real Estate CRM - keep running (do not close)

if not exist "instance" mkdir instance

:loop
echo.
echo [%date% %time%] Starting the CRM...
echo [%date% %time%] Starting the CRM... >> "instance\server.log"

REM Use the virtual environment only if it still works from this location.
set PY=python
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import flask, waitress" >nul 2>&1
    if not errorlevel 1 set PY=.venv\Scripts\python.exe
)
%PY% serve_office.py >> "instance\server.log" 2>&1

echo [%date% %time%] The CRM stopped. Restarting in 5 seconds...
echo [%date% %time%] The CRM stopped. Restarting in 5 seconds... >> "instance\server.log"
timeout /t 5 /nobreak > nul
goto loop
