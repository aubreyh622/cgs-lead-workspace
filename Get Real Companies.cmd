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
echo Finds REAL companies on the public web (Tavily) and researches a few of them into Step 4 briefs.
echo Separate database runtime\real-public.sqlite3; the synthetic demo is not touched. Email and CRM are off.
echo Uses Tavily credits (about 10 + up to 22 per company) and AI calls.
echo.
set /p "REQ=What should the agent look for? (Enter = the default request) "
set /p "N=How many companies to research? (Enter = 3) "
if not defined N set "N=3"
if defined REQ (
  "%WORKSPACE_PYTHON%" -B -m api.real_data --request "%REQ%" --pick %N%
) else (
  "%WORKSPACE_PYTHON%" -B -m api.real_data --pick %N%
)
echo.
pause
