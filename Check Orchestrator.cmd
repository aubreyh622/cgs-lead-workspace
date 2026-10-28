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
echo Manager-as-orchestrator check on the synthetic internet: about 40 AI calls and 5 minutes.
echo The manager's triage, memory, one message per owner, a returned pack, and lessons a person approves.
"%WORKSPACE_PYTHON%" -B -m api.check_orchestrator
echo.
pause
