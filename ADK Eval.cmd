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
where agents-cli >nul 2>&1
if errorlevel 1 (
  echo This check needs Google's agents-cli. Install it once with:  uv tool install google-agents-cli
  pause
  exit /b 1
)
echo ADK eval: the quality-check cases on the original agent loop and on Google ADK, graded and compared by agents-cli.
echo Everything is synthetic. Each repetition runs every case once on each loop: about 40 minutes and a few dollars of AI.
set "REPS=1"
set /p "REPS=How many repetitions (press Enter for 1, the full check uses 3): "
"%WORKSPACE_PYTHON%" -B -m api.adk_eval pair --reps %REPS%
echo.
echo The result is in parity.txt in the newest runtime\eval\...-adk folder; agents-cli's reports are in its grades folder.
pause
