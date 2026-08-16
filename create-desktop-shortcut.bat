@echo off
REM Puts a "Planned CRM" shortcut on the desktop, using the company logo.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" make_shortcut.py
) else (
    python make_shortcut.py
)
pause
