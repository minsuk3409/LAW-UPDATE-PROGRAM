@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Please run setup_windows.cmd first.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" windows_launcher.py
if errorlevel 1 (
 echo The program stopped with an error. Please check the message above.
 pause
 exit /b 1
)
