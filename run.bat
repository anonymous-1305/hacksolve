@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 run.py %*
  goto end
)
where python >nul 2>nul
if not errorlevel 1 (
  python run.py %*
  goto end
)
echo Python 3.10 or newer is required. Get it from https://www.python.org/downloads/ and tick "Add Python to PATH".
:end
pause
