@echo off
setlocal
cd /d "%~dp0"
set "WORKSPACE_PYTHON=%CGS_PYTHON%"
if not defined WORKSPACE_PYTHON if exist ".venv\Scripts\python.exe" set "WORKSPACE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined WORKSPACE_PYTHON if exist ".local-python" set /p WORKSPACE_PYTHON=<".local-python"
if not defined WORKSPACE_PYTHON (
  echo Run Setup.cmd first, or set CGS_PYTHON.
  pause
  exit /b 1
)
"%WORKSPACE_PYTHON%" -B workspace.py check
pause
