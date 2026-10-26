@echo off
setlocal
cd /d "%~dp0"
set "SETUP_PYTHON=%CGS_PYTHON%"
if not defined SETUP_PYTHON set "SETUP_PYTHON=python"
"%SETUP_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
  echo Install Python 3.12 or newer, or set CGS_PYTHON to its executable.
  pause
  exit /b 1
)
"%SETUP_PYTHON%" -m venv .venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.lock
if errorlevel 1 (
  pause
  exit /b 1
)
if not exist ".env" copy ".env.example" ".env" >nul
echo Setup complete. Run Check Workspace.cmd, then Start CGS Workspace.cmd.
echo P0 needs no API keys. Later live operations require provider configuration.
pause
