@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 goto missing_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install
:use_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.lock
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from app.main import create_app; import uvicorn"
if errorlevel 1 goto failed
echo.
echo Setup complete. Double-click run_windows.cmd to start.
if /i not "%~1"=="--no-pause" pause
exit /b 0
:missing_python
echo.
echo Python 3.12 or newer is required.
echo Install Python from https://www.python.org/downloads/windows/
echo During installation, enable "Add python.exe to PATH" if offered.
echo Then run setup_windows.cmd again.
if /i not "%~1"=="--no-pause" pause
exit /b 1
:failed
echo.
echo Setup failed. Please keep this window open and report the error above.
if /i not "%~1"=="--no-pause" pause
exit /b 1
