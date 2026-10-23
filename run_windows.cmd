@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto setup
".venv\Scripts\python.exe" -c "from app.main import create_app; import uvicorn" >nul 2>nul
if errorlevel 1 goto setup
goto launch
:setup
echo Required packages are missing. Running setup now...
call setup_windows.cmd --no-pause
if errorlevel 1 goto failed
:launch
".venv\Scripts\python.exe" windows_launcher.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Setup or startup failed. Please report the error above without your API key.
pause
exit /b 1
