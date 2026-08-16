@echo off
REM Stops the CRM, including the automatic restarter.
echo Stopping the CRM...
schtasks /End /TN "PlannedRealEstateCRM" >nul 2>&1
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM wscript.exe /T >nul 2>&1
echo Stopped. Start it again with start-office.bat or install-autostart.bat.
pause
