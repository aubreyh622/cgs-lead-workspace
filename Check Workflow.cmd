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
echo Full workflow check on the synthetic internet.
echo Companies with simulated client conversations: Ardent Castings, Northwind Fluid Systems, Silverline Medical Devices,
echo   Pioneer Valve, Ridgeline Composites, Orchard Lane Foods, Kessler Motion Controls, Westford Glass, Larkfield Paper, Bellhaven Packaging.
echo Each company has a story: approved, still collecting evidence, nurture or closed. The check shows whether the result matches.
set "COMPANY="
set /p "COMPANY=Type part of a company name, or press Enter for a random one: "
"%WORKSPACE_PYTHON%" -B -m api.check_workflow %COMPANY%
echo.
pause
