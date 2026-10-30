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
if not exist "runtime\real-public.sqlite3" (
  echo No real data yet. Run "Get Real Companies.cmd" first.
  pause
  exit /b 1
)
set "APP_MODE=live"
set "WEB_MODE=tavily"
set "CGS_V2_DB_PATH=runtime/real-public.sqlite3"
set "CGS_V2_PORT=8801"
set "MAIL_PROVIDER=off"
set "CRM_MODE=disabled"
set "CRM_PROVIDER=off"
echo CGS Lead Workspace - REAL public company data (runtime\real-public.sqlite3). Email and CRM are off.
echo The background agents start paused here, so they spend no search credits on their own.
echo Open http://127.0.0.1:8801/docs (the synthetic workspace keeps port 8800).
"%WORKSPACE_PYTHON%" -B -m api.main
if errorlevel 1 pause
