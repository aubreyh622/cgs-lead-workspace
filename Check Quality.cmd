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
echo Quality check: how good is what the agents produce? Synthetic cases with known answers.
echo It uses the AI model in .env and the synthetic internet. A full run takes about 20 minutes and makes a few hundred AI requests.
echo Parts: evidence research outreach discovery. Add --limit 2 for a quick look at the first two cases of each part.
set "PARTS="
set /p "PARTS=Type the parts to run, or just press Enter for all four: "
"%WORKSPACE_PYTHON%" -B -m api.check_quality %PARTS%
echo.
pause
