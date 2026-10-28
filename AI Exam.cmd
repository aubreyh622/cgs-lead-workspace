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
echo AI exam: 6 synthetic call notes with known answers.
echo To compare models, type their names separated by spaces (for example: gemini-3.5-flash-lite gemini-3.5-flash).
set "MODELS="
set /p "MODELS=Or just press Enter to use AI_MODEL from .env: "
"%WORKSPACE_PYTHON%" -B -m api.ai_exam %MODELS%
echo.
pause
