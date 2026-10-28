@echo off
setlocal
cd /d "%~dp0"
set "WORKSPACE_PYTHON=%CGS_PYTHON%"
if not defined WORKSPACE_PYTHON if exist ".venv\Scripts\python.exe" set "WORKSPACE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined WORKSPACE_PYTHON (
  echo Run Setup.cmd first.
  pause
  exit /b 1
)
echo Autonomous agents check on the synthetic internet: about 25 AI calls and 5 minutes.
echo Three leads, then 8 days and then 5 months of simulated time. Watch the agents find work and do it.
"%WORKSPACE_PYTHON%" -B -m api.check_agents
echo.
pause
