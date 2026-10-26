@echo off
setlocal
cd /d "%~dp0"
set "WORKSPACE_PYTHON=%CGS_PYTHON%"
if not defined WORKSPACE_PYTHON if exist ".venv\Scripts\python.exe" set "WORKSPACE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined WORKSPACE_PYTHON (
  echo Run Setup.cmd first. It installs FastAPI and the other dependencies into .venv.
  pause
  exit /b 1
)
echo CGS Lead Workspace v2 - synthetic data only, no emails sent, no CRM writes.
echo Open http://127.0.0.1:8800/ for the workspace and http://127.0.0.1:8800/docs for the API.
"%WORKSPACE_PYTHON%" -B -m api.main
if errorlevel 1 pause
