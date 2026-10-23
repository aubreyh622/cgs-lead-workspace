@echo off
setlocal
cd /d "%~dp0"
set "WORKSPACE_PYTHON=%CGS_PYTHON%"
if not defined WORKSPACE_PYTHON if exist ".venv\Scripts\python.exe" set "WORKSPACE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined WORKSPACE_PYTHON if exist ".local-python" set /p WORKSPACE_PYTHON=<".local-python"
if not defined WORKSPACE_PYTHON (
  echo Run Setup.cmd first, or set CGS_PYTHON to an environment with project dependencies.
  pause
  exit /b 1
)
echo OFFLINE SYNTHETIC DEMO - no external APIs, real contacts, mail or CRM writes.
echo Separate demo data: runtime\offline-demo
echo Open http://127.0.0.1:8767/ after the ready message. Ctrl+C stops the demo.
"%WORKSPACE_PYTHON%" -B demo.py
if errorlevel 1 pause
