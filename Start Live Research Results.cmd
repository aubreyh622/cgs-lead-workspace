@echo off
setlocal
cd /d "%~dp0"
set "CGS_RESEARCH_DB_PATH=runtime\live-tests\2026-10-23-122805-f1396d\research.sqlite3"
set "CGS_DB_PATH=runtime\live-tests\2026-10-23-122805-f1396d\qualification.sqlite3"
set "CGS_PORT=8768"
if not exist "%CGS_RESEARCH_DB_PATH%" (
  echo The captured local research results are not present. Use Start CGS Workspace.cmd instead.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8768/ and choose Lead research.
echo Saved public-web results are loaded. Starting new research spends provider calls.
call "Start CGS Workspace.cmd"
