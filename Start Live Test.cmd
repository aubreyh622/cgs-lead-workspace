@echo off
setlocal
cd /d "%~dp0"
rem A full live test on its own empty database: real AI (Claude), real web search (Tavily),
rem real Gmail and the Insightly sandbox. The demo database and its port 8800 are not touched.
set "WORKSPACE_PYTHON=%CGS_PYTHON%"
if not defined WORKSPACE_PYTHON if exist ".venv\Scripts\python.exe" set "WORKSPACE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined WORKSPACE_PYTHON (
  echo Run Setup.cmd first. It installs FastAPI and the other dependencies into .venv.
  pause
  exit /b 1
)
set "APP_MODE=live"
set "WEB_MODE=tavily"
set "CGS_V2_DB_PATH=runtime/live-full-test.sqlite3"
set "CGS_V2_PORT=8803"
set "MAIL_PROVIDER=gmail"
set "CRM_MODE=write_approved"
set "CRM_PROVIDER=insightly"
set "AGENT_WORKER=on"
set "AGENT_AI_DAILY_BUDGET=200"
echo CGS Lead Workspace - FULL LIVE TEST (runtime\live-full-test.sqlite3)
echo Real AI, real web search, real Gmail, Insightly sandbox. Emails go only to the address you type.
echo To start again from empty: close this window and delete runtime\live-full-test.sqlite3.
echo Open http://127.0.0.1:8803/
"%WORKSPACE_PYTHON%" -B -m api.main
if errorlevel 1 pause
