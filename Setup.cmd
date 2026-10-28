@echo off
setlocal
cd /d "%~dp0"
rem Find Python 3.12+: CGS_PYTHON, then "python" on PATH, then the Windows "py" launcher.
set "SETUP_PYTHON="
if defined CGS_PYTHON (
  "%CGS_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "SETUP_PYTHON=%CGS_PYTHON%"
)
if not defined SETUP_PYTHON (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "SETUP_PYTHON=python"
)
if not defined SETUP_PYTHON (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)"') do set "SETUP_PYTHON=%%P"
)
if not defined SETUP_PYTHON (
  echo Python 3.12 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and run Setup.cmd again.
  pause
  exit /b 1
)
echo Using Python: %SETUP_PYTHON%
if not exist ".venv\Scripts\python.exe" (
  "%SETUP_PYTHON%" -m venv .venv
  if errorlevel 1 (
    echo Could not create the .venv folder.
    pause
    exit /b 1
  )
)
echo Installing packages. This takes a minute or two...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.lock
if errorlevel 1 (
  echo Package installation failed. Send a screenshot of this window.
  pause
  exit /b 1
)
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo Setup complete. Next: put your GEMINI_API_KEY in the .env file, then double-click Check AI.cmd.
pause
