@echo off
REM ===================================================================
REM  Makes the CRM start automatically whenever this computer starts,
REM  and restart itself if it ever stops.
REM
REM  Right-click this file and choose "Run as administrator".
REM ===================================================================
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   This needs administrator rights.
    echo   Close this, right-click install-autostart.bat,
    echo   and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo.
echo  Setting the CRM to start automatically...
echo.

schtasks /Delete /TN "PlannedRealEstateCRM" /F >nul 2>&1

schtasks /Create ^
 /TN "PlannedRealEstateCRM" ^
 /TR "wscript.exe \"%~dp0start-hidden.vbs\"" ^
 /SC ONSTART ^
 /RU "%USERNAME%" ^
 /RL HIGHEST ^
 /DELAY 0001:00 ^
 /F

if errorlevel 1 (
    echo.
    echo   Could not create the task. Use the Startup folder instead:
    echo   press Win+R, type  shell:startup  and put a shortcut to
    echo   start-hidden.vbs in the folder that opens.
) else (
    echo.
    echo   Done. The CRM now starts one minute after this computer
    echo   boots, and restarts itself if it ever stops.
    echo.
    echo   Starting it now so you do not have to reboot...
    start "" wscript.exe "%~dp0start-hidden.vbs"
    echo   Give it 20 seconds, then open http://localhost:5000
)
echo.
pause
