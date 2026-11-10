@echo off
setlocal
cd /d "%~dp0"
rem The live demo for clients: real AI (Claude), real web search (Tavily), real Gmail and the Insightly sandbox,
rem on its own database with a few companies researched in advance. The demo database (8800) and the full
rem live test (8803) are not touched.
set "WORKSPACE_PYTHON=%CGS_PYTHON%"
if not defined WORKSPACE_PYTHON if exist ".venv\Scripts\python.exe" set "WORKSPACE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined WORKSPACE_PYTHON (
  echo Run Setup.cmd first. It installs FastAPI and the other dependencies into .venv.
  pause
  exit /b 1
)
set "APP_MODE=live"
set "WEB_MODE=tavily"
set "CGS_V2_DB_PATH=runtime/live-demo.sqlite3"
set "CGS_V2_PORT=8804"
set "MAIL_PROVIDER=gmail"
set "CRM_MODE=write_approved"
set "CRM_PROVIDER=insightly"
set "AGENT_WORKER=on"
set "AGENT_AI_DAILY_BUDGET=200"
echo CGS Lead Workspace - LIVE DEMO (runtime\live-demo.sqlite3)
echo Real AI, real web search, real Gmail, Insightly sandbox. Emails go only to the address you type.
echo Keep this window open during the demo. Open http://127.0.0.1:8804/
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 4; Start-Process http://127.0.0.1:8804/"
"%WORKSPACE_PYTHON%" -B -m api.main
if errorlevel 1 pause
